"""Автопоиск LAN-серверов по UDP: клиент рассылает запрос, хосты отвечают своими данными."""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
from typing import Any, Callable

from config import NET_DISCOVERY_PORT, NET_VERSION
from .protocol import local_ip

log = logging.getLogger(__name__)
PROBE = b"WORLD_CONQUEST_DISCOVER"


class DiscoveryResponder:
    """Поток хоста: отвечает на поисковые запросы описанием игры."""

    def __init__(self, info: Callable[[], dict[str, Any]], port: int = NET_DISCOVERY_PORT) -> None:
        self.info, self.port = info, port
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """Запускает ответчик; False, если UDP-порт занят (поиск тогда недоступен, ручной ввод работает)."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", self.port))
            s.settimeout(0.5)
        except OSError as exc:
            log.warning("Автопоиск недоступен: %s", exc)
            return False
        self._sock = s
        self._thread = threading.Thread(target=self._loop, name="wc-discovery", daemon=True)
        self._thread.start()
        return True

    def _loop(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                break
            if data.startswith(PROBE):
                try:
                    self._sock.sendto(json.dumps(self.info()).encode("utf-8"), addr)
                except OSError:
                    pass

    def stop(self) -> None:
        """Останавливает поток."""
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass


def reachable(ip: str, port: int, timeout: float = 0.6) -> bool:
    """Можно ли реально установить TCP-соединение с сервером (устаревшие ответы не показываем как доступные)."""
    try:
        with socket.create_connection((ip, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def discover(timeout: float = 1.2, port: int = NET_DISCOVERY_PORT, check: bool = True) -> list[dict[str, Any]]:
    """Ищет серверы в локальной сети. Возвращает [{ip, port, name, players, max, started, status, scenario, date}];
    при check=True недоступные серверы отбрасываются."""
    found: dict[tuple[str, int], dict[str, Any]] = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.2)
        ip = local_ip()
        targets = ["255.255.255.255", "127.0.0.1"]
        if ip.count(".") == 3:
            targets.append(ip.rsplit(".", 1)[0] + ".255")
        for t in targets:
            try:
                s.sendto(PROBE + b"|" + str(NET_VERSION).encode(), (t, port))
            except OSError:
                pass
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, addr = s.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                info = json.loads(data.decode("utf-8"))
                if info.get("v") == NET_VERSION:
                    info["ip"] = addr[0]
                    found[(addr[0], int(info["port"]))] = info
            except (ValueError, KeyError, TypeError):
                continue
    finally:
        s.close()
    res = list(found.values())
    if check:
        res = [i for i in res if reachable(i["ip"], int(i["port"]))]
    for i in res:
        i.setdefault("status", "play" if i.get("started") else "lobby")
    return sorted(res, key=lambda i: (i.get("status") == "full", str(i.get("name", ""))))
