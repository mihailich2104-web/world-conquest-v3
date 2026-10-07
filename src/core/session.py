"""Игровая сессия без графики: часы, ход, команды игрока и сетевая синхронизация.

Режимы: single (одиночная игра), host (LAN-хост: авторитетен), client (LAN-клиент: не считает мир,
а применяет снимки хоста). Всё, что касается времени и сети, живёт здесь и тестируется без pygame.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from config import NET_SNAPSHOT_INTERVAL
from . import commands
from .clock import GameClock
from .game_state import GameState
from .turn_manager import TurnManager

log = logging.getLogger(__name__)
HEARTBEAT = 1.0


class Session:
    """Единая точка управления партией для интерфейса."""

    def __init__(self, state: GameState, mode: str = "single", host: Any = None, client: Any = None) -> None:
        self.state, self.mode, self.host, self.client = state, mode, host, client
        self.clock = GameClock(paused=state.tutorial)
        self.tm: Optional[TurnManager] = TurnManager(state) if mode != "client" else None
        self.closed_reason: Optional[str] = None
        self._since = 0.0
        self._dirty = True
        self.days_total = 0
        self.chat_log: list[tuple[str, str]] = []   # (имя, текст) — только для LAN
        self.rev = 0                              # растёт при любом изменении мира (для обновления интерфейса)

    # ---------- свойства ----------
    @property
    def is_client(self) -> bool:
        """Клиент LAN (не управляет временем)."""
        return self.mode == "client"

    @property
    def multiplayer(self) -> bool:
        """Идёт ли LAN-партия."""
        return self.mode != "single"

    # ---------- время ----------
    def toggle_pause(self) -> None:
        """Пауза/продолжение (только хост или одиночная игра)."""
        if not self.is_client:
            self.clock.toggle_pause()
            self._dirty = True

    def set_speed(self, idx: int) -> None:
        """Скорость 1X..16X (только хост или одиночная игра); выбор скорости снимает паузу."""
        if not self.is_client:
            self.clock.set_speed(idx)
            self.clock.paused = False
            self._dirty = True

    def step_day(self) -> None:
        """Ручной шаг на один день (одиночная игра на паузе)."""
        if self.tm and not self.multiplayer and not self.state.game_over:
            self.tm.next_turn()
            self.days_total += 1
            self._dirty = True
            self.rev += 1

    def _advance(self, days: int) -> None:
        assert self.tm is not None
        for _ in range(days):
            if self.state.game_over and not self.multiplayer:
                break
            self.tm.next_turn()
            self.days_total += 1
        self._dirty = True
        self.rev += 1

    # ---------- команды ----------
    def do(self, cmd: dict[str, Any]) -> Optional[tuple[bool, str]]:
        """Команда локального игрока. Клиент отправляет хосту (результат придёт в журнале снимка)."""
        if self.is_client:
            if self.client:
                self.client.command(cmd)
            return None
        iso = self.state.player_iso
        if not iso:
            return False, "Страна не выбрана"
        res = commands.execute(self.state, iso, cmd)
        self._dirty = True
        self.rev += 1
        return res

    def send_chat(self, name: str, text: str) -> None:
        """Сообщение в чат: клиент шлёт хосту, хост добавляет у себя и рассылает всем."""
        text = text.strip()[:200]
        if not text or not self.multiplayer:
            return
        if self.is_client:
            if self.client:
                self.client.chat(text)
        elif self.host:
            self._add_chat(name, text)

    def _add_chat(self, name: str, text: str) -> None:
        self.chat_log.append((name, text))
        del self.chat_log[:-50]
        self.rev += 1
        if self.host:
            self.host.broadcast({"t": "chat", "name": name, "text": text})

    # ---------- кадр ----------
    def update(self, dt: float) -> int:
        """Один кадр: сеть, игровое время, рассылка. Возвращает число прошедших дней."""
        self._since += dt
        if self.mode == "client":
            self._pump_client()
            return 0
        if self.mode == "host":
            self._pump_host()
        days = 0
        if self.state.player_iso or self.multiplayer:
            days = self.clock.update(dt)
            if days:
                self._advance(days)
        if self.mode == "host":
            self._send_snapshots()
        return days

    def _pump_host(self) -> None:
        assert self.host is not None
        for ev in self.host.pump():
            if ev[0] == "cmd":
                _, pid, iso, cmd = ev
                ok, msg = commands.execute(self.state, iso, cmd)
                self.state.log_event(("" if ok else "✖ ") + msg, iso)
                self._dirty = True
                self.rev += 1
            elif ev[0] == "chat":
                self._add_chat(ev[2], ev[3])
            elif ev[0] == "left":
                _, pid, iso, name = ev
                if iso and iso in self.state.humans:
                    self.state.humans.discard(iso)
                    self.state.log_event(f"Игрок {name} покинул игру — {self.state.countries[iso].name} под управлением ИИ",
                                         None, True)
                    self._dirty = True

    def _send_snapshots(self) -> None:
        assert self.host is not None
        if self.host.phase != "play":
            return
        if (self._dirty and self._since >= NET_SNAPSHOT_INTERVAL) or self._since >= HEARTBEAT:
            self.host.broadcast({"t": "snap", "state": self.state.to_dict(), "clock": self.clock.to_dict()})
            self._since, self._dirty = 0.0, False

    def _pump_client(self) -> None:
        assert self.client is not None
        for m in self.client.pump():
            t = m.get("t")
            if t == "snap":
                self.state.load_dict(m["state"])
                self.clock.load(m.get("clock", {}))
                self.rev += 1
            elif t == "chat":
                self.chat_log.append((str(m.get("name", "?"))[:24], str(m.get("text", ""))[:200]))
                del self.chat_log[:-50]
            elif t == "closed":
                self.closed_reason = "Соединение с хостом потеряно"

    def close(self) -> None:
        """Закрывает сетевые соединения."""
        if self.host:
            self.host.close()
        if self.client:
            self.client.close()
