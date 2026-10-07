"""Протокол: сообщение = 4 байта длины (big-endian) + 1 байт флага (0 — JSON, 1 — zlib-JSON) + данные."""
from __future__ import annotations

import json
import socket
import struct
import zlib
from typing import Any, Optional

MAX_MSG = 32 * 1024 * 1024
COMPRESS_OVER = 512


class ProtocolError(Exception):
    """Нарушение протокола (слишком большое или повреждённое сообщение)."""


def encode(obj: dict[str, Any]) -> bytes:
    """Сериализует сообщение в кадр."""
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    flag = 0
    if len(raw) > COMPRESS_OVER:
        raw, flag = zlib.compress(raw, 1), 1
    return struct.pack(">IB", len(raw) + 1, flag) + raw


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("соединение закрыто")
        buf += chunk
    return bytes(buf)


def recv_msg(sock: socket.socket) -> dict[str, Any]:
    """Читает одно сообщение (блокирующе). Бросает ConnectionError/ProtocolError."""
    (length,) = struct.unpack(">I", _recv_exact(sock, 4))
    if length < 1 or length > MAX_MSG:
        raise ProtocolError(f"недопустимая длина сообщения: {length}")
    body = _recv_exact(sock, length)
    flag, data = body[0], body[1:]
    try:
        if flag == 1:
            data = zlib.decompress(data)
        obj = json.loads(data.decode("utf-8"))
    except (zlib.error, ValueError) as exc:
        raise ProtocolError(f"повреждённое сообщение: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("сообщение должно быть объектом")
    return obj


def send_raw(sock: socket.socket, frame: bytes) -> None:
    """Отправляет готовый кадр целиком."""
    sock.sendall(frame)


def local_ip() -> str:
    """Локальный IP в сети (без отправки пакетов); при ошибке — 127.0.0.1."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        s.close()


def first(d: dict, key: str, typ: type, default: Optional[Any] = None) -> Any:
    """Безопасно достаёт поле нужного типа."""
    v = d.get(key, default)
    return v if isinstance(v, typ) else default
