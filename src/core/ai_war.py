"""ИИ армий: оборона столицы и своих земель, выбор целей по выгоде и шансам, распределение по фронтам.

ИИ пользуется теми же приказами, что и игрок (armies.order_move и т. д.), поэтому подчиняется тем же
правилам: закрытые границы, снабжение, предел операций. Решения принимаются не каждый день и в рамках
суточного бюджета приказов, чтобы ускоренное время не создавало зависаний.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config import AI_ARMY_BUDGET, SETTINGS
from . import armies as am

if TYPE_CHECKING:
    from .country import Country
    from .game_state import GameState

log = logging.getLogger(__name__)
PERIOD = {"easy": 6, "normal": 4, "hard": 2}
ODDS_NEEDED = {"easy": 1.5, "normal": 1.05, "hard": 0.85}


def _level(state: "GameState") -> tuple[str, int]:
    sc = state.scenario
    return str(sc.get("difficulty", SETTINGS.difficulty)), int(sc.get("ai_activity", SETTINGS.ai_activity))


def due(state: "GameState", c: "Country") -> bool:
    """Пора ли стране принимать решения по армиям (с разбросом по дням, чтобы нагрузка не склеивалась)."""
    diff, act = _level(state)
    period = max(1, round(PERIOD.get(diff, 4) / (0.6, 1.0, 1.6)[max(0, min(2, act))]))
    return (state.date.toordinal() + sum(map(ord, c.iso))) % period == 0


def enemy_power(state: "GameState", rid: str, who: str) -> float:
    """Оценка силы обороны региона rid против державы who: вражеские армии + укрепления и местность."""
    p = 0.0
    for a in state.armies.values():
        if a.region == rid and a.progress <= 0 and am.hostile(state, who, a.owner):
            p += a.strength * am.eff(state, a)
    holder = state.countries.get(am.ctrl_of(state, rid))
    fort = holder.fort if holder else 0
    base = 0.6 + 0.25 * fort + (1.5 if rid == am.capital_of(state, state.owner.get(rid, "")) else 0.0)
    return (p + base) * state.geo.terrain_of(rid)[1]


def plan(state: "GameState", c: "Country", budget: list[int]) -> int:
    """Решения одной державы: возвращает число отданных приказов (budget[0] — остаток суточного бюджета)."""
    diff, _ = _level(state)
    need = ODDS_NEEDED.get(diff, 1.05)
    arms = [a for a in am.armies_of(state, c.iso)]
    if not arms:
        return 0
    issued = 0
    idle = [a for a in arms if not a.path and not a.fight and a.progress <= 0]
    if not c.at_war_with:                                   # мир: армии возвращаются в свои земли
        for a in idle:
            if budget[0] <= 0:
                break
            if not am.friendly(state, c.iso, am.ctrl_of(state, a.region)):
                ok, _ = am.order_move(state, c.iso, a.id, am.capital_of(state, c.iso))
                if ok:
                    issued += 1
                    budget[0] -= 1
        return issued
    # концентрация: мелкие армии в одном регионе сливаются, чтобы наступать сильным кулаком
    byreg: dict[str, list[am.Army]] = {}
    for a in idle:
        byreg.setdefault(a.region, []).append(a)
    for rid, grp in byreg.items():
        if len(grp) >= 2 and sum(x.strength for x in grp) <= min(8, am.MAX_STACK):
            ok, _ = am.merge_armies(state, c.iso, [x.id for x in grp])
            if ok:
                idle = [x for x in idle if x.id in state.armies and x.region != rid or x.id == min(y.id for y in grp)]
    # отдых: армии с низким снабжением/моралью отходят в свои земли
    for a in list(idle):
        if (a.morale < 35 or a.supply < 35) and not am.friendly(state, c.iso, am.ctrl_of(state, a.region)):
            if budget[0] > 0 and am._retreat(state, a):
                issued += 1
                budget[0] -= 1
                idle.remove(a)
    # оборона: свои регионы под ударом (столица — главный приоритет)
    threatened: list[tuple[float, str]] = []
    hostile_regions = {x.region for x in state.armies.values() if x.progress <= 0 and am.hostile(state, c.iso, x.owner)}
    for rid, o in state.owner.items():
        if not (am.friendly(state, c.iso, o) and o == c.iso):
            continue
        held_by_enemy = am.hostile(state, c.iso, am.ctrl_of(state, rid))
        near = rid in hostile_regions or any(n in hostile_regions for n in state.geo.adj.get(rid, ()))
        if held_by_enemy or near:
            threatened.append((am.region_value(state, rid) * (2.0 if held_by_enemy else 1.0), rid))
    threatened.sort(reverse=True)
    assigned: set[int] = set()
    for _, rid in threatened[:3]:
        if budget[0] <= 0 or not idle:
            break
        free = [a for a in idle if a.id not in assigned and a.region != rid]
        if not free:
            continue
        a = min(free, key=lambda x: state.geo.dist_km(x.region, rid))
        if state.geo.dist_km(a.region, rid) > 4500:
            continue
        ok, _ = am.order_move(state, c.iso, a.id, rid)
        if ok:
            assigned.add(a.id)
            issued += 1
            budget[0] -= 1
    # наступление: выбираем цель по ценности/расстоянию/шансам, не отправляя все армии в одну точку
    claims: dict[str, float] = {}
    for a in sorted((x for x in idle if x.id not in assigned and x.strength >= 1.0), key=lambda x: -x.strength):
        if budget[0] <= 0:
            break
        if a.morale < 45 or a.supply < 45:
            continue
        best = None
        mine = a.strength * am.eff(state, a)
        for rid, hops in _targets(state, a):
            if hops > 5:
                continue
            ep = max(0.2, enemy_power(state, rid, c.iso))
            got = claims.get(rid, 0.0)
            if got >= ep * 1.6:                               # цель уже «насыщена» силами — берём другую
                continue
            odds = (mine + got) / ep
            if odds < need:
                continue
            score = am.region_value(state, rid) * min(odds, 2.0) / (1.0 + 0.6 * hops) * (0.6 if got > 0 else 1.0)
            if best is None or score > best[0]:
                best = (score, rid)
        if best is None:
            continue
        ok, _ = am.order_move(state, c.iso, a.id, best[1])
        if ok:
            claims[best[1]] = claims.get(best[1], 0.0) + mine
            issued += 1
            budget[0] -= 1
    return issued


def _targets(state: "GameState", a: "am.Army") -> list[tuple[str, int]]:
    """Вражеские регионы вблизи армии (по сухопутным связям, а для островных держав — по морю): (id, шагов)."""
    who = a.owner
    out: list[tuple[str, int]] = []
    seen = {a.region}
    frontier = [a.region]
    for depth in range(1, 6):
        nxt = []
        for u in frontier:
            for v in state.geo.adj.get(u, ()):
                if v in seen:
                    continue
                seen.add(v)
                if not am.can_enter(state, who, v):
                    continue
                if am.hostile(state, who, am.ctrl_of(state, v)) or am.hostile(state, who, state.owner.get(v, "")):
                    if am.ctrl_of(state, v) != who:
                        out.append((v, depth))
                        continue
                nxt.append(v)
        frontier = nxt
        if len(out) >= 6:
            break
    if not out and am.has_fleet(state, who) and a.region in state.geo.coastal:
        for d, v in state.geo.sea_links(a.region)[:60]:
            if d > 3500:
                break
            if am.hostile(state, who, am.ctrl_of(state, v)) and am.can_enter(state, who, v):
                out.append((v, 4))
                if len(out) >= 4:
                    break
    return out
