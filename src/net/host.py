"""Хост LAN-игры: лобби, приём команд, рассылка снимков мира. Хост авторитетен."""
from __future__ import annotations

import logging
import queue
import socket
import threading
from dataclasses import dataclass
from typing import Any, Optional

from config import NET_DEFAULT_PORT, NET_MAX_PLAYERS, NET_VERSION
from .discovery import DiscoveryResponder
from .protocol import ProtocolError, encode, local_ip, recv_msg

log = logging.getLogger(__name__)


@dataclass
class Player:
    """Участник лобби/игры."""
    pid: int
    name: str
    ready: bool = False
    iso: Optional[str] = None
    conn: Optional[socket.socket] = None   # None — сам хост (читает поток-читатель, без таймаута)
    lock: Optional[threading.Lock] = None
    out: Optional[socket.socket] = None    # дубликат сокета для отправки (свой таймаут, не ломает чтение)


class Host:
    """TCP-сервер. Сетевые потоки только кладут события в очередь; всё остальное — в главном потоке."""

    def __init__(self, name: str, valid_isos: set[str], port: int = NET_DEFAULT_PORT) -> None:
        self.name = name or "Хост"
        self.valid_isos = valid_isos
        self.port = port
        self.ip = local_ip()
        self.phase = "lobby"                     # lobby | play
        self.players: dict[int, Player] = {0: Player(0, self.name)}
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
        srv.listen(NET_MAX_PLAYERS)
        srv.settimeout(0.5)
        self.port = srv.getsockname()[1]
        self._srv = srv
        threading.Thread(target=self._accept_loop, name="wc-accept", daemon=True).start()
        self.discovery_ok = self._disc.start()
        log.info("LAN-хост: %s:%d", self.ip, self.port)
        return self.port

    def _info(self) -> dict[str, Any]:
        return {"v": NET_VERSION, "name": self.name, "port": self.port, "players": len(self.players),
                "max": NET_MAX_PLAYERS, "started": self.phase != "lobby"}

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
        pid = None
        try:
            hello = recv_msg(conn)
            if hello.get("t") != "hello":
                raise ProtocolError("ожидался hello")
            if hello.get("v") != NET_VERSION:
                conn.sendall(encode({"t": "reject", "reason": "Версии игры не совпадают"}))
                return
            holder: dict[str, Any] = {}
            ev = threading.Event()
            self._q.put(("join", conn, str(hello.get("name", "Игрок"))[:24], holder, ev))
            ev.wait(5.0)
            pid = holder.get("pid")
            if pid is None:
                return
            conn.settimeout(None)
            while not self._stop.is_set():
                self._q.put(("msg", pid, recv_msg(conn)))
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

    @staticmethod
    def _close_player(p: Player) -> None:
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
        if p.conn is None or p.out is None:
            return
        try:
            data = frame if frame is not None else encode(obj)
            with p.lock:  # type: ignore[arg-type]
                p.out.sendall(data)
        except OSError as exc:
            log.warning("Отправка игроку %s не удалась: %s", p.name, exc)
            self._q.put(("leave", p.pid))

    def broadcast(self, obj: dict[str, Any]) -> None:
        """Отправляет сообщение всем клиентам (кадр кодируется один раз)."""
        frame = encode(obj)
        for p in list(self.players.values()):
            self._send(p, obj, frame)

    def send_to(self, pid: int, obj: dict[str, Any]) -> None:
        """Отправляет сообщение одному клиенту."""
        p = self.players.get(pid)
        if p:
            self._send(p, obj)

    # ---------- лобби ----------
    def lobby_view(self) -> list[dict[str, Any]]:
        """Список игроков для интерфейса."""
        return [{"pid": p.pid, "name": p.name, "ready": p.ready or p.pid == 0 and p.iso is not None,
                 "iso": p.iso, "host": p.pid == 0} for p in self.players.values()]

    def _lobby_msg(self) -> dict[str, Any]:
        return {"t": "lobby", "players": self.lobby_view()}

    def _changed_lobby(self) -> None:
        self._changed = True
        self.broadcast(self._lobby_msg())

    def pick(self, pid: int, iso: str) -> bool:
        """Выбор страны игроком (уникальность проверяет хост)."""
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

    def begin(self, state_dict: dict[str, Any], clock: dict[str, Any]) -> None:
        """Переводит всех в игру: каждый клиент получает снимок и свою страну."""
        self.phase = "play"
        for p in list(self.players.values()):
            if p.pid:
                self._send(p, {"t": "start", "state": state_dict, "clock": clock, "you": p.iso})

    def humans(self) -> list[str]:
        """Страны всех подключённых людей."""
        return sorted(p.iso for p in self.players.values() if p.iso)

    # ---------- главный поток ----------
    def pump(self) -> list[tuple]:
        """Обрабатывает очередь. Возвращает события для приложения:
        ("lobby",), ("cmd", pid, iso, cmd), ("left", pid, iso, name)."""
        out: list[tuple] = []
        while True:
            try:
                ev = self._q.get_nowait()
            except queue.Empty:
                break
            kind = ev[0]
            if kind == "join":
                _, conn, name, holder, flag = ev
                if self.phase != "lobby" or len(self.players) >= NET_MAX_PLAYERS:
                    try:
                        conn.sendall(encode({"t": "reject", "reason": "Игра уже началась или нет мест"}))
                    except OSError:
                        pass
                    flag.set()
                    continue
                pid = self._next_pid
                self._next_pid += 1
                send_sock = conn.dup()
                send_sock.settimeout(3.0)                # таймаут только на отправку
                self.players[pid] = Player(pid, name, conn=conn, lock=threading.Lock(), out=send_sock)
                holder["pid"] = pid
                flag.set()
                self._send(self.players[pid], {"t": "welcome", "pid": pid, "name": self.name})
                self._changed_lobby()
                out.append(("lobby",))
            elif kind == "msg":
                self._on_msg(ev[1], ev[2], out)
            elif kind == "leave":
                p = self.players.pop(ev[1], None)
                if p:
                    self._close_player(p)
                    if self.phase == "lobby":
                        self._changed_lobby()
                    out.append(("left", p.pid, p.iso, p.name))
        return out

    def _on_msg(self, pid: int, msg: dict[str, Any], out: list[tuple]) -> None:
        p = self.players.get(pid)
        if p is None:
            return
        t = msg.get("t")
        if t == "pick" and isinstance(msg.get("iso"), str):
            if self.pick(pid, msg["iso"]):
                out.append(("lobby",))
        elif t == "ready" and isinstance(msg.get("ready"), bool):
            self.set_ready(pid, msg["ready"])
            out.append(("lobby",))
        elif t == "chat" and self.phase == "play" and isinstance(msg.get("text"), str) and msg["text"].strip():
            out.append(("chat", pid, p.name, msg["text"].strip()[:200]))
        elif t == "cmd" and self.phase == "play" and p.iso and isinstance(msg.get("cmd"), dict):
            out.append(("cmd", pid, p.iso, msg["cmd"]))
