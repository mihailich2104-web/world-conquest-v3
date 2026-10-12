"""Игровая сессия без графики: часы, ход, команды игрока и сетевая синхронизация.

Режимы: single (одиночная игра), host (LAN-хост: авторитетен), client (LAN-клиент: не считает мир, а применяет
снимки и дельты хоста). Время в LAN определяет только хост: клиент может лишь попросить паузу/скорость —
тогда запускается голосование большинства. Всё, что касается времени и сети, живёт здесь и тестируется без pygame.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from config import NET_SNAPSHOT_INTERVAL
from . import commands
from .clock import GameClock
from .game_state import GameState
from .turn_manager import TurnManager

log = logging.getLogger(__name__)
HEARTBEAT = 1.0
VOTE_SECONDS = 15.0


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
        self.vote: Optional[dict[str, Any]] = None  # активное голосование (на хосте — с дедлайном, на клиенте — для показа)
        self._vote_yes: set[int] = set()
        self._info_t = 0.0
        self.acks: list[tuple[int, bool, str]] = []  # ответы хоста на команды клиента
        self.notices: list[str] = []                 # короткие сообщения интерфейсу (вход/выход игроков)
        self.sync_seq = 0
        self.synced = mode != "client"               # клиент синхронизирован после первого полного снимка
        self._resync_sent = False
        self.syncs = 0                               # сколько полных снимков принято (для тестов/интерфейса)
        self.claim_error = ""                        # причина отказа при выборе страны в идущей партии
        self.free: list[dict[str, Any]] = list(getattr(client, "free", []) or [])   # свободные страны для входа
        if host is not None:
            host.free_provider = self.free_countries
            self._update_info()

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
        """Пауза/продолжение: одиночная игра и хост — сразу; клиент — просьба (голосование)."""
        if self.is_client:
            if self.client:
                self.client.time_request("resume" if self.clock.paused else "pause")
            return
        self.vote = None
        self.clock.toggle_pause()
        self._dirty = True

    def set_speed(self, idx: int) -> None:
        """Скорость 1X..16X; выбор скорости снимает паузу. Клиент лишь просит — решает голосование."""
        if self.is_client:
            if self.client:
                self.client.time_request("speed", idx)
            return
        self.vote = None
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
        """Команда локального игрока. Клиент отправляет хосту (ответ придёт в acks)."""
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
            assert self.client is not None
            self.client.tick()
            self._pump_client()
            return 0
        if self.mode == "host":
            self._pump_host()
            self._tick_vote()
        days = 0
        if self.state.player_iso or self.multiplayer:
            days = self.clock.update(dt)
            if days:
                self._advance(days)
        if self.mode == "host":
            self._send_net()
        return days

    # ---------- хост ----------
    def free_countries(self) -> list[dict[str, Any]]:
        """Страны, которые может занять новый игрок: существующие, не люди, не удерживаемые за отключившимися."""
        taken = set(self.state.human_list()) | (set(self.host.reserved_isos()) if self.host else set())
        rows = []
        for c in self.state.alive_countries():
            if c.iso in taken:
                continue
            rows.append({"iso": c.iso, "name": c.name, "regions": len(self.state.owned(c.iso)), "army": c.army,
                         "gdp": round(c.gdp, 1), "war": bool(c.at_war_with)})
        rows.sort(key=lambda r: (-r["regions"], -r["gdp"]))
        return rows[:80]

    def _update_info(self) -> None:
        if self.host is not None:
            self.host.extra_info = {"scenario": str(self.state.scenario.get("name", "")),
                                    "date": self.state.date.isoformat()}

    def begin_play(self) -> None:
        """Хост начинает партию из лобби: всем клиентам уходит стартовый снимок с их страной."""
        assert self.host is not None
        self.state.humans = set(self.host.humans())
        self.host.begin()
        for p in self.host.remote_players():
            self._send_full(p, "start")
        self._update_info()
        self._dirty = True

    def _send_full(self, p: Any, kind: str = "full") -> None:
        from net.sync import Tick, full_message
        msg = full_message(self.state, self.clock.to_dict(), p.base, Tick(self.state), p.iso, self._vote_view())
        msg["t"] = kind
        self.host.send_to(p.pid, msg)

    def _pump_host(self) -> None:
        assert self.host is not None
        st = self.state
        for ev in self.host.pump():
            kind = ev[0]
            if kind == "cmd":
                _, pid, iso, cid, cmd = ev
                p = self.host.players.get(pid)
                last = self.host.cmd_ids.get(p.token, 0) if p else 0
                if p is not None and cid <= last:
                    self.host.send_to(pid, {"t": "ack", "id": cid, "ok": False, "msg": "Повтор команды проигнорирован"})
                    continue
                if p is not None:
                    self.host.cmd_ids[p.token] = cid
                ok, msg = commands.execute(st, iso, cmd)
                st.log_event(("" if ok else "✖ ") + msg, iso)
                self.host.send_to(pid, {"t": "ack", "id": cid, "ok": ok, "msg": msg})
                self._dirty = True
                self.rev += 1
            elif kind == "chat":
                self._add_chat(ev[2], ev[3])
            elif kind == "joined":
                _, pid, name, resumed, iso = ev
                p = self.host.players.get(pid)
                if resumed and p is not None and iso and st.countries.get(iso) and st.countries[iso].alive:
                    st.humans.add(iso)
                    st.log_event(f"Игрок {name} вернулся в игру — {st.countries[iso].name}", iso, True, kind="net",
                                 isos=[iso])
                    self._send_full(p)
                    self.notices.append(f"{name} вернулся в игру")
                elif resumed and p is not None:
                    self.host.kick(pid, "Ваша страна больше не существует")
                self._dirty = True
            elif kind == "claim":
                self._on_claim(ev[1], ev[2])
            elif kind == "resync":
                p = self.host.players.get(ev[1])
                if p is not None:
                    self._send_full(p)
            elif kind == "time":
                self._on_time(ev[1], ev[2], ev[3], ev[4])
            elif kind == "vote":
                self._on_vote(ev[1], ev[3])
            elif kind == "left":
                _, pid, iso, name, reserved = ev
                self._vote_yes.discard(pid)
                if iso and iso in st.humans:
                    st.humans.discard(iso)
                    tail = " (место удержано 5 минут)" if reserved else ""
                    st.log_event(f"Игрок {name} отключился — {st.countries[iso].name} под управлением ИИ{tail}",
                                 iso, True, kind="net", isos=[iso])
                    self.notices.append(f"{name} отключился")
                    self._dirty = True
            elif kind == "expired":
                _, iso, name = ev
                if iso in st.countries:
                    st.log_event(f"Место {st.countries[iso].name} освобождено — страну может занять другой игрок",
                                 None, False, kind="net")

    def _on_claim(self, pid: int, iso: str) -> None:
        """Новый игрок занимает страну в идущей партии: проверка и стартовый снимок текущего состояния."""
        assert self.host is not None
        st, p = self.state, self.host.players.get(pid)
        if p is None or p.iso is not None:
            return
        c = st.countries.get(iso)
        reason = ""
        if c is None or not c.alive:
            reason = "Такой страны нет или она уже уничтожена"
        elif iso in st.human_list() or any(q.iso == iso for q in self.host.players.values()):
            reason = "Страну уже занял другой игрок"
        elif iso in self.host.reserved_isos():
            reason = "Страна удерживается за отключившимся игроком"
        if reason:
            self.host.send_to(pid, {"t": "claim_fail", "reason": reason,
                                    "free": self.free_countries()})
            return
        self.host.assign(pid, iso)
        st.humans.add(iso)
        st.log_event(f"К партии подключился игрок {p.name}: {c.name}", iso, True, kind="net", isos=[iso])
        self.notices.append(f"К партии подключился {p.name} ({c.name})")
        self._send_full(p)
        self._dirty = True
        self.rev += 1

    # ---------- голосование о времени ----------
    def _vote_view(self) -> Optional[dict[str, Any]]:
        v = self.vote
        if not v:
            return None
        return {"kind": v["kind"], "value": v.get("value", 0), "by": v["by"], "yes": len(self._vote_yes),
                "need": v["need"], "left": max(0, int(v["until"] - time.monotonic()))}

    def _humans_online(self) -> int:
        return 1 + len(self.host.synced_players()) if self.host else 1

    def _apply_time(self, kind: str, value: int) -> None:
        if kind == "pause":
            self.clock.paused = True
        elif kind == "resume":
            self.clock.paused = False
        elif kind == "speed":
            self.clock.set_speed(value)
            self.clock.paused = False
        self._dirty = True

    def _on_time(self, pid: int, iso: str, op: str, value: int) -> None:
        """Просьба клиента о паузе/скорости: одному игроку — сразу, иначе голосование большинства."""
        p = self.host.players.get(pid) if self.host else None
        if p is None:
            return
        if self.vote:
            self.host.send_to(pid, {"t": "notice", "text": "Голосование уже идёт"})
            return
        n = self._humans_online()
        if n <= 1:
            self._apply_time(op, value)
            return
        self.vote = {"kind": op, "value": value, "by": p.name, "need": n // 2 + 1,
                     "until": time.monotonic() + VOTE_SECONDS}
        self._vote_yes = {pid}
        self.state.log_event(f"Голосование: {p.name} предлагает {self._vote_text(op, value)}", iso, False, kind="net")
        self._check_vote()

    @staticmethod
    def _vote_text(kind: str, value: int) -> str:
        from config import SPEEDS
        if kind == "speed":
            return f"скорость {SPEEDS[max(0, min(len(SPEEDS) - 1, value))]}X"
        return "поставить на паузу" if kind == "pause" else "продолжить игру"

    def cast_vote(self, yes: bool) -> None:
        """Голос локального игрока (хост — напрямую, клиент — сообщением)."""
        if self.is_client:
            if self.client:
                self.client.vote(yes)
        else:
            self._on_vote(0, yes)

    def _on_vote(self, pid: int, yes: bool) -> None:
        if not self.vote:
            return
        if yes:
            self._vote_yes.add(pid)
        else:
            self._vote_yes.discard(pid)
        self._check_vote()

    def _check_vote(self) -> None:
        v = self.vote
        if v and len(self._vote_yes) >= v["need"]:
            self._apply_time(v["kind"], v.get("value", 0))
            self.state.log_event(f"Голосование принято: {self._vote_text(v['kind'], v.get('value', 0))}", None, False,
                                 kind="net")
            self.vote = None
            self._vote_yes = set()

    def _tick_vote(self) -> None:
        if self.vote and time.monotonic() > self.vote["until"]:
            self.state.log_event("Голосование отклонено: не набрано большинство", None, False, kind="net")
            self.vote = None
            self._vote_yes = set()
            self._dirty = True

    # ---------- рассылка ----------
    def _send_net(self) -> None:
        assert self.host is not None
        if self.host.phase != "play":
            return
        if not ((self._dirty and self._since >= NET_SNAPSHOT_INTERVAL) or self._since >= HEARTBEAT):
            return
        from net.sync import Tick, delta_message
        players = self.host.synced_players()
        self._since, self._dirty = 0.0, False
        if time.monotonic() - self._info_t > 1.0:
            self._info_t = time.monotonic()
            self._update_info()
        if not players:
            return
        tick = Tick(self.state)
        clock, vote = self.clock.to_dict(), self._vote_view()
        for p in players:
            self.host.send_to(p.pid, delta_message(self.state, clock, p.base, tick, vote))

    # ---------- клиент ----------
    def _pump_client(self) -> None:
        assert self.client is not None
        from net.sync import apply_delta
        for m in self.client.pump():
            t = m.get("t")
            if t in ("full", "start"):
                self._apply_full(m)
            elif t == "delta":
                if not self.synced or m.get("base") != self.sync_seq:
                    if not self._resync_sent:
                        self._resync_sent = True
                        self.synced = False
                        self.client.resync()
                    continue
                apply_delta(self.state, m)
                self.clock.load(m.get("clock", {}))
                self.vote = m.get("vote")
                self.sync_seq = int(m["seq"])
                self.rev += 1
            elif t == "ack":
                self.acks.append((int(m.get("id", 0)), bool(m.get("ok")), str(m.get("msg", ""))[:200]))
                del self.acks[:-20]
            elif t == "claim_fail":
                self.claim_error = str(m.get("reason", "Страну занять нельзя"))[:160]
                self.free = list(m.get("free", self.free))
            elif t == "notice":
                self.notices.append(str(m.get("text", ""))[:120])
            elif t == "chat":
                self.chat_log.append((str(m.get("name", "?"))[:24], str(m.get("text", ""))[:200]))
                del self.chat_log[:-50]
            elif t == "reject":
                self.closed_reason = str(m.get("reason", "Хост отключил вас"))
            elif t == "closed":
                self.closed_reason = self.closed_reason or "Соединение с хостом потеряно"

    def _apply_full(self, m: dict[str, Any]) -> None:
        """Применяет полный снимок хоста: актуальное состояние мира, часы, голосование."""
        self.state.load_dict(m["state"])
        if m.get("you"):
            self.state.player_iso = m["you"]
        self.clock.load(m.get("clock", {}))
        self.vote = m.get("vote")
        self.sync_seq, self.synced, self._resync_sent = int(m.get("seq", 0)), True, False
        self.syncs += 1
        self.rev += 1

    def close(self) -> None:
        """Закрывает сетевые соединения."""
        if self.host:
            self.host.close()
        if self.client:
            self.client.close()
