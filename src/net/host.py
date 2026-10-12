"""Хост LAN-игры: лобби и партия, подключение к уже идущей игре, возвращение игрока, проверка сообщений.

Хост авторитетен: клиенты шлют только короткие проверяемые сообщения и команды, мир считается у хоста.
Сетевые потоки лишь читают/пишут сокеты и кладут события в очередь; всё остальное — в главном потоке.
У каждого клиента своя очередь отправки и поток-писатель, поэтому медленный клиент не тормозит игру.
"""
from __future__ import annotations

import logging
import queue
import re
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from config import NET_DEFAULT_PORT, NET_MAX_PLAYERS, NET_VERSION
from .discovery import DiscoveryResponder
from .protocol import MAX_CLIENT_MSG, ProtocolError, encode, local_ip, recv_msg
from .sync import Baseline

log = logging.getLogger(__name__)
RESERVE_SECONDS = 300.0          # сколько держится место отключившегося игрока
IDLE_TIMEOUT = 25.0              # тишина от клиента дольше — соединение считается мёртвым (клиент шлёт ping)
OUT_QUEUE = 120                  # длина очереди отправки; переполнение = клиент слишком медленный
RATE_LIMIT = 80                  # сообщений в секунду от одного клиента
TOKEN_RE = re.compile(r"^[0-9a-f]{8,32}$")
KNOWN = {"pick", "ready", "claim", "cmd", "chat", "resync", "time", "vote", "ping"}


@dataclass
class Player:
    """Участник лобби/игры."""
    pid: int
    name: str
    ready: bool = False
    iso: Optional[str] = None
    conn: Optional[socket.socket] = None
    out: Optional[socket.socket] = None
    outq: Optional["queue.Queue[Optional[bytes]]"] = None
    token: str = ""
    base: Baseline = field(default_factory=Baseline)
    stamps: list[float] = field(default_factory=list)
    bad: int = 0
    joined_play: bool = False


class Host:
    """TCP-сервер LAN-партии."""

    def __init__(self, name: str, valid_isos: set[str], port: int = NET_DEFAULT_PORT) -> None:
        self.name = name or "Хост"
        self.valid_isos = valid_isos
        self.port = port
        self.ip = local_ip()
        self.phase = "lobby"                     # lobby | play
        self.allow_join = True                   # можно ли подключаться во время партии
        self.max_players = NET_MAX_PLAYERS
        self.players: dict[int, Player] = {0: Player(0, self.name)}
        self.reserved: dict[str, dict[str, Any]] = {}      # токен → {iso, name, until}: места отключившихся
        self.cmd_ids: dict[str, int] = {}                  # токен → последний выполненный id команды
        self.free_provider: Callable[[], list[dict[str, Any]]] = lambda: []
        self.extra_info: dict[str, Any] = {}               # сценарий/дата для автопоиска
        self.scenario_id = "modern"                        # сценарий лобби (клиенты строят такую же карту)
        self._next_pid = 1
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._srv: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._disc = DiscoveryResponder(self._info)
        self.discovery_ok = False
        self._changed = False

    # ---------- запуск ----------
    def start(self) -> int:
        """Открывает порт (при занятости берёт свободный) и запускает приём. Возвращает порт."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("", self.port))
        except OSError:
            srv.bind(("", 0))
        srv.listen(16)
        srv.settimeout(0.5)
        self.port = srv.getsockname()[1]
        self._srv = srv
        threading.Thread(target=self._accept_loop, name="wc-accept", daemon=True).start()
        self.discovery_ok = self._disc.start()
        log.info("LAN-хост: %s:%d", self.ip, self.port)
        return self.port

    def status(self) -> str:
        """lobby — ждёт игроков; play — идёт партия, можно войти; full — мест нет/вход закрыт."""
        if self.phase == "lobby":
            return "lobby" if len(self.players) < self.max_players else "full"
        return "play" if self.allow_join and len(self.players) < self.max_players else "full"

    def _info(self) -> dict[str, Any]:
        d = {"v": NET_VERSION, "name": self.name, "port": self.port, "players": len(self.players),
             "max": self.max_players, "started": self.phase != "lobby", "status": self.status()}
        d.update(self.extra_info)
        return d

    def close(self) -> None:
        """Останавливает сервер и закрывает все соединения."""
        self._stop.set()
        self._disc.stop()
        for p in list(self.players.values()):
            self._close_player(p)
        if self._srv:
            try:
                self._srv.close()
            except OSError:
                pass

    # ---------- сетевые потоки ----------
    def _accept_loop(self) -> None:
        assert self._srv is not None
        while not self._stop.is_set():
            try:
                conn, addr = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._reader, args=(conn, addr), name="wc-reader", daemon=True).start()

    def _reader(self, conn: socket.socket, addr: tuple) -> None:
        conn.settimeout(8.0)
        pid: Optional[int] = None
        try:
            hello = recv_msg(conn, MAX_CLIENT_MSG)
            if hello.get("t") != "hello":
                raise ProtocolError("ожидался hello")
            if hello.get("v") != NET_VERSION:
                conn.sendall(encode({"t": "reject", "reason": "Версии игры не совпадают"}))
                return
            name = str(hello.get("name", "Игрок")).strip()[:24] or "Игрок"
            tok = hello.get("token")
            token = tok if isinstance(tok, str) and TOKEN_RE.match(tok) else ""
            holder: dict[str, Any] = {}
            ev = threading.Event()
            self._q.put(("join", conn, name, token, holder, ev))
            ev.wait(5.0)
            pid = holder.get("pid")
            if pid is None:
                return
            conn.settimeout(IDLE_TIMEOUT)
            while not self._stop.is_set():
                self._q.put(("msg", pid, recv_msg(conn, MAX_CLIENT_MSG)))
        except (OSError, ConnectionError, ProtocolError):
            pass
        finally:
            if pid is not None:
                self._q.put(("leave", pid))
            else:
                try:
                    conn.close()
                except OSError:
                    pass

    def _writer(self, p: Player) -> None:
        """Поток-писатель клиента: отправка из очереди; сбой = отключение."""
        assert p.outq is not None and p.out is not None
        while True:
            frame = p.outq.get()
            if frame is None:
                return
            try:
                p.out.sendall(frame)
            except OSError:
                self._q.put(("leave", p.pid))
                return

    @staticmethod
    def _close_player(p: Player) -> None:
        if p.outq is not None:
            try:
                p.outq.put_nowait(None)
            except queue.Full:
                pass
        if p.conn:
            try:
                p.conn.shutdown(socket.SHUT_RDWR)       # сообщает собеседнику и будит поток-читатель
            except OSError:
                pass
        for sock in (p.out, p.conn):
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass

    # ---------- отправка ----------
    def _send(self, p: Player, obj: dict[str, Any], frame: Optional[bytes] = None) -> None:
        if p.conn is None or p.outq is None:
            return
        try:
            p.outq.put_nowait(frame if frame is not None else encode(obj))
        except queue.Full:
            log.warning("Клиент %s не успевает принимать данные — отключён", p.name)
            self._q.put(("leave", p.pid))

    def broadcast(self, obj: dict[str, Any], only_synced: bool = False) -> None:
        """Отправляет сообщение всем клиентам (кадр кодируется один раз)."""
        frame = encode(obj)
        for p in list(self.players.values()):
            if only_synced and not p.base.synced:
                continue
            self._send(p, obj, frame)

    def send_to(self, pid: int, obj: dict[str, Any]) -> None:
        """Отправляет сообщение одному клиенту."""
        p = self.players.get(pid)
        if p:
            self._send(p, obj)

    def remote_players(self) -> list[Player]:
        """Подключённые клиенты (без самого хоста)."""
        return [p for p in self.players.values() if p.conn is not None]

    def synced_players(self) -> list[Player]:
        """Клиенты, получившие полный снимок и ждущие дельт."""
        return [p for p in self.remote_players() if p.base.synced]

    # ---------- лобби ----------
    def lobby_view(self) -> list[dict[str, Any]]:
        """Список игроков для интерфейса."""
        return [{"pid": p.pid, "name": p.name, "ready": p.ready or p.pid == 0 and p.iso is not None,
                 "iso": p.iso, "host": p.pid == 0, "online": p.pid == 0 or p.conn is not None}
                for p in self.players.values()]

    def _lobby_msg(self) -> dict[str, Any]:
        return {"t": "lobby", "players": self.lobby_view(), "scenario": self.scenario_id}

    def _changed_lobby(self) -> None:
        self._changed = True
        self.broadcast(self._lobby_msg())

    def pick(self, pid: int, iso: str) -> bool:
        """Выбор страны игроком в лобби (уникальность проверяет хост)."""
        if self.phase != "lobby" or iso not in self.valid_isos:
            return False
        if any(p.iso == iso and p.pid != pid for p in self.players.values()):
            return False
        p = self.players.get(pid)
        if p is None:
            return False
        p.iso = iso
        p.ready = p.ready if pid else True
        self._changed_lobby()
        return True

    def set_ready(self, pid: int, ready: bool) -> None:
        """Готовность игрока (без выбранной страны — нельзя)."""
        p = self.players.get(pid)
        if p and self.phase == "lobby" and (p.iso or not ready):
            p.ready = ready
            self._changed_lobby()

    def can_start(self) -> tuple[bool, str]:
        """Можно ли начинать: у всех выбрана страна и клиенты готовы."""
        for p in self.players.values():
            if not p.iso:
                return False, f"{p.name}: не выбрана страна"
            if p.pid and not p.ready:
                return False, f"{p.name}: не готов"
        return True, ""

    def begin(self) -> None:
        """Переводит комнату в режим партии (снимки клиентам рассылает сессия)."""
        self.phase = "play"

    def humans(self) -> list[str]:
        """Страны всех подключённых людей."""
        return sorted(p.iso for p in self.players.values() if p.iso)

    # ---------- возвращение и места ----------
    def assign(self, pid: int, iso: str) -> None:
        """Закрепляет страну за игроком, вошедшим в идущую партию."""
        p = self.players.get(pid)
        if p:
            p.iso = iso

    def reserved_isos(self) -> set[str]:
        """Страны, места которых удерживаются за отключившимися."""
        return {r["iso"] for r in self.reserved.values()}

    def kick(self, pid: int, reason: str = "") -> None:
        """Отключает клиента."""
        p = self.players.get(pid)
        if p and p.conn:
            if reason:
                self._send(p, {"t": "reject", "reason": reason})
            self._q.put(("leave", pid))

    # ---------- главный поток ----------
    def pump(self) -> list[tuple]:
        """Обрабатывает очередь. События для приложения:
        ("lobby",), ("joined", pid, name, resumed, iso), ("claim", pid, iso), ("cmd", pid, iso, cid, cmd),
        ("chat", pid, name, text), ("resync", pid), ("time", pid, iso, op, value), ("vote", pid, iso, yes),
        ("left", pid, iso, name, reserved), ("expired", iso, name)."""
        out: list[tuple] = []
        now = time.monotonic()
        for tok in [t for t, r in self.reserved.items() if r["until"] < now]:
            r = self.reserved.pop(tok)
            out.append(("expired", r["iso"], r["name"]))
        while True:
            try:
                ev = self._q.get_nowait()
            except queue.Empty:
                break
            kind = ev[0]
            if kind == "join":
                self._admit(ev, out)
            elif kind == "msg":
                self._on_msg(ev[1], ev[2], out)
            elif kind == "leave":
                p = self.players.pop(ev[1], None)
                if p:
                    self._close_player(p)
                    reserved = False
                    if self.phase == "lobby":
                        self._changed_lobby()
                    elif p.iso and p.token:
                        self.reserved[p.token] = {"iso": p.iso, "name": p.name, "until": time.monotonic() + RESERVE_SECONDS}
                        reserved = True
                    out.append(("left", p.pid, p.iso, p.name, reserved))
        return out

    def _reject(self, conn: socket.socket, reason: str, flag: threading.Event) -> None:
        try:
            conn.sendall(encode({"t": "reject", "reason": reason}))
        except OSError:
            pass
        flag.set()

    def _admit(self, ev: tuple, out: list[tuple]) -> None:
        """Решает, пускать ли новое соединение: лобби, вход в идущую партию или возвращение по токену."""
        _, conn, name, token, holder, flag = ev
        resume = self.reserved.get(token) if (token and self.phase == "play") else None
        if token:                                              # старое «зависшее» соединение того же игрока
            for old in [p for p in self.players.values() if p.token == token and p.conn is not None]:
                self.kick(old.pid)
        if self.phase == "lobby":
            if len(self.players) >= self.max_players:
                return self._reject(conn, "Нет свободных мест", flag)
        elif resume is None:
            if not self.allow_join:
                return self._reject(conn, "Хост закрыл подключение во время партии", flag)
            if len(self.players) >= self.max_players:
                return self._reject(conn, "Нет свободных мест", flag)
        pid = self._next_pid
        self._next_pid += 1
        send_sock = conn.dup()
        send_sock.settimeout(5.0)                              # таймаут только на отправку
        p = Player(pid, name, conn=conn, out=send_sock, outq=queue.Queue(OUT_QUEUE),
                   token=token if resume else secrets.token_hex(8), joined_play=self.phase == "play")
        self.players[pid] = p
        threading.Thread(target=self._writer, args=(p,), name="wc-writer", daemon=True).start()
        holder["pid"] = pid
        flag.set()
        welcome: dict[str, Any] = {"t": "welcome", "pid": pid, "name": self.name, "token": p.token,
                                   "phase": self.phase, "resumed": resume is not None}
        welcome.update(self.extra_info)
        if resume is not None:
            p.iso = resume["iso"]
            self.reserved.pop(token, None)
            welcome["iso"] = p.iso
        elif self.phase == "play":
            welcome["free"] = self.free_provider()
        self._send(p, welcome)
        if self.phase == "lobby":
            self._changed_lobby()
            out.append(("lobby",))
        else:
            out.append(("joined", pid, name, resume is not None, p.iso))

    def _rate_ok(self, p: Player) -> bool:
        now = time.monotonic()
        p.stamps = [t for t in p.stamps if now - t < 1.0]
        p.stamps.append(now)
        return len(p.stamps) <= RATE_LIMIT

    def _on_msg(self, pid: int, msg: dict[str, Any], out: list[tuple]) -> None:
        p = self.players.get(pid)
        if p is None:
            return
        t = msg.get("t")
        if t not in KNOWN or not self._rate_ok(p):
            p.bad += 1
            if p.bad > 300:
                self.kick(pid, "Слишком много некорректных сообщений")
            return
        if t == "ping":
            self._send(p, {"t": "pong"})
        elif t == "pick" and isinstance(msg.get("iso"), str):
            if self.pick(pid, msg["iso"]):
                out.append(("lobby",))
        elif t == "ready" and isinstance(msg.get("ready"), bool):
            self.set_ready(pid, msg["ready"])
            out.append(("lobby",))
        elif t == "claim" and self.phase == "play" and p.iso is None and isinstance(msg.get("iso"), str) \
                and len(msg["iso"]) <= 16:
            out.append(("claim", pid, msg["iso"]))
        elif t == "chat" and self.phase == "play" and isinstance(msg.get("text"), str) and msg["text"].strip():
            out.append(("chat", pid, p.name, msg["text"].strip()[:200]))
        elif t == "cmd" and self.phase == "play" and p.iso and isinstance(msg.get("cmd"), dict) \
                and isinstance(msg.get("id"), int) and not isinstance(msg.get("id"), bool):
            out.append(("cmd", pid, p.iso, msg["id"], msg["cmd"]))
        elif t == "resync" and self.phase == "play" and p.iso:
            p.base.synced = False
            out.append(("resync", pid))
        elif t == "time" and self.phase == "play" and p.iso and msg.get("op") in ("pause", "resume", "speed"):
            v = msg.get("value")
            out.append(("time", pid, p.iso, msg["op"], v if isinstance(v, int) and not isinstance(v, bool) else 0))
        elif t == "vote" and self.phase == "play" and p.iso and isinstance(msg.get("yes"), bool):
            out.append(("vote", pid, p.iso, msg["yes"]))
        else:
            p.bad += 1
