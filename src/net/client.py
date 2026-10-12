"""LAN-клиент: подключается к хосту (в лобби или в идущую партию), шлёт команды, получает снимки и дельты."""
from __future__ import annotations

import logging
import queue
import socket
import threading
import time
from typing import Any, Optional

from config import NET_VERSION
from .protocol import ProtocolError, encode, recv_msg

log = logging.getLogger(__name__)
PING_EVERY = 4.0
DEAD_AFTER = 16.0           # тишина от хоста дольше — считаем соединение потерянным (хост шлёт дельты/heartbeat)


class ClientError(Exception):
    """Не удалось подключиться."""


class Client:
    """Читает сообщения в отдельном потоке; главный поток забирает их через pump()."""

    def __init__(self, name: str) -> None:
        self.name = name or "Игрок"
        self.pid = -1
        self.token = ""
        self.phase = "lobby"             # фаза хоста при подключении: lobby | play
        self.resumed = False             # вошли по токену (возвращение в свою страну)
        self.iso: Optional[str] = None   # страна, закреплённая за нами (при возвращении)
        self.free: list[dict[str, Any]] = []   # свободные страны для входа в идущую партию
        self.info: dict[str, Any] = {}   # описание партии из welcome (сценарий, дата)
        self.sock: Optional[socket.socket] = None
        self._q: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._lock = threading.Lock()
        self.closed = False
        self.host_name = ""
        self._cmd_id = 0
        self._last_rx = time.monotonic()
        self._last_ping = 0.0

    def connect(self, ip: str, port: int, timeout: float = 4.0, token: str = "") -> None:
        """Подключается и проходит рукопожатие. Бросает ClientError с понятным текстом."""
        try:
            s = socket.create_connection((ip, int(port)), timeout=timeout)
        except (OSError, ValueError) as exc:
            raise ClientError(f"Не удалось подключиться к {ip}:{port} ({exc})") from exc
        try:
            s.sendall(encode({"t": "hello", "v": NET_VERSION, "name": self.name, "token": token}))
            msg = recv_msg(s)
        except (OSError, ConnectionError, ProtocolError) as exc:
            s.close()
            raise ClientError(f"Ошибка рукопожатия: {exc}") from exc
        if msg.get("t") == "reject":
            s.close()
            raise ClientError(str(msg.get("reason", "Хост отклонил подключение")))
        if msg.get("t") != "welcome":
            s.close()
            raise ClientError("Неожиданный ответ хоста")
        self.pid, self.host_name = int(msg["pid"]), str(msg.get("name", ""))
        self.token = str(msg.get("token", ""))
        self.phase = str(msg.get("phase", "lobby"))
        self.resumed = bool(msg.get("resumed"))
        self.iso = msg.get("iso")
        self.free = list(msg.get("free", []))
        self.info = {k: msg[k] for k in ("scenario", "scenario_id", "date", "status") if k in msg}
        s.settimeout(None)
        self.sock = s
        self._last_rx = time.monotonic()
        threading.Thread(target=self._reader, name="wc-client", daemon=True).start()

    def _reader(self) -> None:
        assert self.sock is not None
        try:
            while True:
                m = recv_msg(self.sock)
                self._last_rx = time.monotonic()
                self._q.put(m)
        except (OSError, ConnectionError, ProtocolError) as exc:
            if not self.closed:
                log.warning("Соединение с хостом закрыто: %s", exc)
        self.closed = True
        self._q.put({"t": "closed"})

    def send(self, obj: dict[str, Any]) -> bool:
        """Отправляет сообщение хосту."""
        if self.sock is None or self.closed:
            return False
        try:
            with self._lock:
                self.sock.sendall(encode(obj))
            return True
        except OSError:
            self.closed = True
            return False

    def tick(self) -> None:
        """Раз в кадр: ping хосту и обнаружение «тихого» обрыва (хост пропал без закрытия сокета)."""
        now = time.monotonic()
        if self.closed or self.sock is None:
            return
        if now - self._last_ping > PING_EVERY:
            self._last_ping = now
            self.send({"t": "ping"})
        if now - self._last_rx > DEAD_AFTER:
            log.warning("Хост не отвечает %.0f с — соединение закрыто", now - self._last_rx)
            self.close()
            self._q.put({"t": "closed"})

    def pick(self, iso: str) -> None:
        """Выбрать страну в лобби."""
        self.send({"t": "pick", "iso": iso})

    def ready(self, value: bool) -> None:
        """Готовность."""
        self.send({"t": "ready", "ready": value})

    def claim(self, iso: str) -> None:
        """Занять свободную страну в идущей партии."""
        self.send({"t": "claim", "iso": iso})

    def command(self, cmd: dict[str, Any]) -> int:
        """Отправить игровую команду (хост проверит и выполнит ровно один раз). Возвращает id команды."""
        self._cmd_id += 1
        self.send({"t": "cmd", "id": self._cmd_id, "cmd": cmd})
        return self._cmd_id

    def time_request(self, op: str, value: int = 0) -> None:
        """Просьба о паузе/продолжении/скорости (решает хост или голосование)."""
        self.send({"t": "time", "op": op, "value": int(value)})

    def vote(self, yes: bool) -> None:
        """Голос в текущем голосовании."""
        self.send({"t": "vote", "yes": bool(yes)})

    def chat(self, text: str) -> None:
        """Отправить сообщение в чат."""
        self.send({"t": "chat", "text": text})

    def resync(self) -> None:
        """Запросить новый полный снимок."""
        self.send({"t": "resync"})

    def pump(self) -> list[dict[str, Any]]:
        """Забирает накопленные сообщения по порядку (дельты нельзя пропускать)."""
        out: list[dict[str, Any]] = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        return out

    def close(self) -> None:
        """Закрывает соединение."""
        self.closed = True
        if self.sock:
            for fn in (lambda: self.sock.shutdown(socket.SHUT_RDWR), self.sock.close):  # shutdown будит читающий поток
                try:
                    fn()
                except OSError:
                    pass
