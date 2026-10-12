"""Дельта-синхронизация LAN: у каждого клиента хранится, что ему уже отправлено; шлются только изменения.

Без сокетов и pygame — тестируется отдельно. Полный снимок (full) отправляется при входе, возвращении и по
запросу resync; дальше идут дельты с номерами seq/base: клиент принимает дельту, только если base совпадает с
последним применённым, иначе просит новый снимок. Снимок собирается в главном потоке хоста атомарно, поэтому
изменения «во время передачи» не теряются: базовая линия клиента фиксируется в тот же момент.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from core.armies import Army
from core.country import Country
from core.game_state import GameState
from core.war import War, rebuild_links


def _h(obj: Any) -> int:
    return hash(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


class Baseline:
    """Что клиент уже знает."""
    __slots__ = ("seq", "countries", "owner", "armies", "log_n", "cap_n", "map_rev", "stats_h", "series_n",
                 "synced")

    def __init__(self) -> None:
        self.seq = 0
        self.countries: dict[str, int] = {}
        self.owner: dict[str, str] = {}
        self.armies: dict[int, int] = {}
        self.log_n = 0
        self.cap_n = 0
        self.map_rev = -1
        self.stats_h = 0
        self.series_n = -1
        self.synced = False


class Tick:
    """Срез состояния хоста, общий для всех клиентов в этом кадре рассылки."""

    def __init__(self, state: GameState) -> None:
        self.state = state
        self.countries = {iso: c.to_dict() for iso, c in state.countries.items()}
        self.c_h = {iso: _h(d) for iso, d in self.countries.items()}
        self.armies = {aid: a.to_dict() for aid, a in state.armies.items()}
        self.a_h = {aid: _h(d) for aid, d in self.armies.items()}
        self.stats_h = _h([state.stats, state.truces])
        self.series_n = sum(len(v) for v in state.series.values())


def full_message(state: GameState, clock: dict[str, Any], base: Baseline, tick: Tick, you: Optional[str],
                 vote: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Полный снимок; фиксирует базовую линию клиента."""
    base.seq += 1
    base.countries = dict(tick.c_h)
    base.owner = dict(state.owner)
    base.armies = dict(tick.a_h)
    base.log_n, base.cap_n, base.map_rev = state.log_seq, state.cap_seq, state.map_rev
    base.stats_h, base.series_n, base.synced = tick.stats_h, tick.series_n, True
    return {"t": "full", "seq": base.seq, "state": state.to_dict(), "clock": clock, "you": you, "vote": vote}


def delta_message(state: GameState, clock: dict[str, Any], base: Baseline, tick: Tick,
                  vote: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Изменения с момента последней отправки этому клиенту."""
    prev = base.seq
    base.seq += 1
    msg: dict[str, Any] = {
        "t": "delta", "seq": base.seq, "base": prev, "date": state.date.toordinal(), "clock": clock,
        "over": state.game_over, "humans": sorted(state.humans), "wars": [w.to_dict() for w in state.wars],
        "offers": state.offers, "battles": state.battles, "occ": state.occupying, "vote": vote,
        "mrev": state.map_rev,
    }
    ch = [tick.countries[i] for i, h in tick.c_h.items() if base.countries.get(i) != h]
    if ch:
        msg["c"] = ch
    base.countries = dict(tick.c_h)
    if state.map_rev != base.map_rev or state.owner != base.owner:
        msg["own"] = {r: o for r, o in state.owner.items() if base.owner.get(r) != o}
        msg["ctrl"] = state.ctrl
        base.owner, base.map_rev = dict(state.owner), state.map_rev
    ach = [tick.armies[i] for i, h in tick.a_h.items() if base.armies.get(i) != h]
    gone = [i for i in base.armies if i not in tick.a_h]
    if ach:
        msg["a"] = ach
    if gone:
        msg["ar"] = gone
    base.armies = dict(tick.a_h)
    new_log = [e for e in state.log if e.get("n", 0) > base.log_n]
    if new_log:
        msg["log"] = new_log
    base.log_n = state.log_seq
    new_cap = [e for e in state.captures if e.get("q", 0) > base.cap_n]
    if new_cap:
        msg["cap"] = new_cap
    base.cap_n = state.cap_seq
    if tick.stats_h != base.stats_h:
        msg["stats"], msg["truces"] = state.stats, state.truces
        base.stats_h = tick.stats_h
    if tick.series_n != base.series_n:
        msg["series"] = state.series
        base.series_n = tick.series_n
    return msg


def apply_delta(state: GameState, msg: dict[str, Any]) -> None:
    """Применяет дельту к состоянию клиента (проверку seq/base делает вызывающий)."""
    from datetime import date
    state.date = date.fromordinal(int(msg["date"]))
    state.game_over = msg.get("over")
    state.humans = set(msg.get("humans", ()))
    state.offers = msg.get("offers", [])
    state.battles = msg.get("battles", {})
    state.occupying = msg.get("occ", {})
    for d in msg.get("c", ()):
        state.countries[d["iso"]] = Country.from_dict(d)
    state.wars = [War.from_dict(w) for w in msg.get("wars", ())]
    rebuild_links(state)
    if "own" in msg:
        state.owner.update(msg["own"])
        state.ctrl = {k: v for k, v in msg.get("ctrl", {}).items() if k in state.owner}
        state.map_rev = int(msg.get("mrev", state.map_rev))
        state.map_dirty = True
        state.invalidate_cache()
    for d in msg.get("a", ()):
        state.armies[d["id"]] = Army.from_dict(d)
    for aid in msg.get("ar", ()):
        state.armies.pop(aid, None)
    for e in msg.get("log", ()):
        state.log.append(e)
        state.log_seq = max(state.log_seq, e.get("n", 0))
    del state.log[:-300]
    for e in msg.get("cap", ()):
        state.captures.append(e)
        state.cap_seq = max(state.cap_seq, e.get("q", 0))
    del state.captures[:-600]
    if "stats" in msg:
        state.stats = msg["stats"]
        state.truces = msg.get("truces", state.truces)
    if "series" in msg:
        state.series = msg["series"]
