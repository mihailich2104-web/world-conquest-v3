"""Статистика кампании и предупреждения: помесячные срезы по странам людей, потеря экономики при оккупации."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .game_state import GameState

SERIES_FIELDS = ("date", "owned", "controlled", "army", "gdp", "treasury", "techs", "won", "lost", "casualties")
WARN_EVERY = 20


def econ_update(state: "GameState") -> None:
    """Доля экономики, сохранённая под оккупацией: потерянные регионы (и особенно столица) режут доходы."""
    own: dict[str, float] = {}
    lost: dict[str, float] = {}
    for rid, o in state.owner.items():
        v = 1.0 + min(3.0, state.geo.area.get(rid, 5.0) / 40.0)
        own[o] = own.get(o, 0.0) + v
        if state.ctrl.get(rid, o) != o:
            lost[o] = lost.get(o, 0.0) + v
    for c in state.countries.values():
        if not c.alive:
            continue
        share = lost.get(c.iso, 0.0) / own[c.iso] if own.get(c.iso) else 0.0
        mult = 1.0 - 0.8 * share
        cap = c.capital_region
        if cap and state.ctrl.get(cap, c.iso) != c.iso:
            mult *= 0.85
        c.econ_mult = round(max(0.2, mult), 3)


def record(state: "GameState") -> None:
    """Помесячный срез показателей стран людей (для панели прогресса)."""
    for iso in state.human_list():
        c = state.countries.get(iso)
        if c is None:
            continue
        row = [state.date.toordinal(), len(state.owned(iso)), len(state.controlled(iso)), c.army,
               round(c.gdp, 1), round(c.treasury, 1), len(c.technologies),
               state.stats["battles_won"].get(iso, 0), state.stats["battles_lost"].get(iso, 0),
               round(state.stats.get("casualties", {}).get(iso, 0.0), 1)]
        ser = state.series.setdefault(iso, [])
        if ser and ser[-1][0] == row[0]:
            ser[-1] = row
        else:
            ser.append(row)
        if len(ser) > 1500:
            del ser[: len(ser) - 1500]


def period_rows(state: "GameState", iso: str, days: int) -> list[list[float]]:
    """Срезы за последние days суток (days <= 0 — вся кампания)."""
    ser = state.series.get(iso, [])
    if days <= 0 or not ser:
        return list(ser)
    lim = state.date.toordinal() - days
    return [r for r in ser if r[0] >= lim] or ser[-2:]


def summary(state: "GameState", iso: str, days: int) -> dict[str, Any]:
    """Сводка для панели: текущее значение и изменение за период."""
    rows = period_rows(state, iso, days)
    if not rows:
        return {}
    first, last = rows[0], rows[-1]
    out = {f: last[i] for i, f in enumerate(SERIES_FIELDS)}
    out["d_owned"] = last[1] - first[1]
    out["d_army"] = last[3] - first[3]
    out["d_gdp"] = round(last[4] - first[4], 1)
    out["d_techs"] = last[6] - first[6]
    out["d_won"] = last[7] - first[7]
    out["d_lost"] = last[8] - first[8]
    out["d_cas"] = round(last[9] - first[9], 1)
    return out


def _warn(state: "GameState", iso: str, key: str, text: str) -> None:
    w = state.stats.setdefault("warn", {})
    k = f"{iso}:{key}"
    now = state.date.toordinal()
    if now - w.get(k, -999) >= WARN_EVERY:
        w[k] = now
        state.log_event(text, iso, True, kind="warn", isos=[iso])


def warnings(state: "GameState") -> None:
    """Предупреждения людям: дефицит бюджета, нехватка снабжения, угроза столице, чрезмерные расходы."""
    from . import armies as am
    from .economy import daily_income, net_income, upkeep
    for iso in state.human_list():
        c = state.countries.get(iso)
        if c is None or not c.alive:
            continue
        net = net_income(c)
        if net < 0 and c.treasury < -net * 15:
            _warn(state, iso, "deficit", f"Дефицит бюджета: {net:.1f}/день, казны хватит на {max(0, int(c.treasury / -net))} дн.")
        inc = daily_income(c)
        if inc > 0 and upkeep(c) > 0.7 * inc:
            _warn(state, iso, "military", f"Военные расходы {upkeep(c) / inc * 100:.0f}% дохода — экономика перегружена")
        cap = c.capital_region
        if cap:
            hostile_near = any(a.progress <= 0 and am.hostile(state, iso, a.owner) and
                               (a.region == cap or a.region in state.geo.adj.get(cap, ())) for a in state.armies.values())
            if hostile_near:
                _warn(state, iso, "capital", "Столица под угрозой: вражеская армия у её границ")
        for a in am.armies_of(state, iso):
            if a.supply < 30:
                _warn(state, iso, f"sup{a.id}", f"{a.name}: недостаточное снабжение ({a.supply:.0f}%)")
