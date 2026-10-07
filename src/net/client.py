"""LAN-клиент: подключается к хосту, шлёт готовность/выбор/команды, получает снимки мира."""
from __future__ import annotations

import logging
import queue
import socket
import threading
from typing import Any

from config import NET_VERSION
from .protocol import ProtocolError, encode, recv_msg

log = logging.getLogger(__name__)


class ClientError(Exception):
    """Не удалось подключиться."""


class Client:
    """Читает сообщения в отдельном потоке; главный поток забирает их через pump()."""

    def __init__(self, name: str) -> None:
        self.name = name or "Игрок"
        self.pid = -1
        self.sock: socket.socket | None = None
        self._q: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._lock = threading.Lock()
        self.closed = False
        self.host_name = ""

    def connect(self, ip: str, port: int, timeout: float = 4.0) -> None:
        """Подключается и проходит рукопожатие. Бросает ClientError с понятным текстом."""
        try:
            s = socket.create_connection((ip, int(port)), timeout=timeout)
        except (OSError, ValueError) as exc:
            raise ClientError(f"Не удалось подключиться к {ip}:{port} ({exc})") from exc
        try:
            s.sendall(encode({"t": "hello", "v": NET_VERSION, "name": self.name}))
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
        s.settimeout(None)
        self.sock = s
        threading.Thread(target=self._reader, name="wc-client", daemon=True).start()

    def _reader(self) -> None:
        assert self.sock is not None
        try:
            while True:
                self._q.put(recv_msg(self.sock))
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

    def pick(self, iso: str) -> None:
        """Выбрать страну в лобби."""
        self.send({"t": "pick", "iso": iso})

    def ready(self, value: bool) -> None:
        """Готовность."""
        self.send({"t": "ready", "ready": value})

    def command(self, cmd: dict[str, Any]) -> None:
        """Отправить игровую команду (хост проверит и выполнит)."""
        self.send({"t": "cmd", "cmd": cmd})

    def chat(self, text: str) -> None:
        """Отправить сообщение в чат."""
        self.send({"t": "chat", "text": text})

    def pump(self) -> list[dict[str, Any]]:
        """Забирает накопленные сообщения; из подряд идущих снимков остаётся только последний."""
        out: list[dict[str, Any]] = []
        while True:
            try:
                m = self._q.get_nowait()
            except queue.Empty:
                break
            if out and m.get("t") == "snap" and out[-1].get("t") == "snap":
                out[-1] = m
            else:
                out.append(m)
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
