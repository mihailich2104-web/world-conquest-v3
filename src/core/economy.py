"""Экономика: ВВП, налоги, промышленность, ресурсы, торговля, строительство."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config import BUILD_NAMES, COSTS, RESOURCE_NAMES
from .research import tech_bonus

if TYPE_CHECKING:
    from .country import Country

log = logging.getLogger(__name__)

MAX_CONSTRUCTION = 3


def tax_income(c: "Country") -> float:
    """Налоговые поступления в день (до торговых бонусов)."""
    base = 2.0 + c.gdp ** 0.6 * 0.3
    stab = 0.5 + c.stability / 100.0
    mult = 1 + tech_bonus(c, "industry") + tech_bonus(c, "economy") + c.modifier("income")
    return base * (c.tax_rate / 0.25) * max(0.1, mult) * stab


def trade_income(c: "Country") -> float:
    """Доход от торговых соглашений (+5% за каждого партнёра)."""
    return tax_income(c) * 0.05 * len(c.trade)


def daily_income(c: "Country") -> float:
    """Валовой дневной доход казны: налоги + торговля."""
    return tax_income(c) + trade_income(c)


def upkeep(c: "Country") -> float:
    """Дневное содержание армии."""
    return 0.3 * (c.army + c.navy * 1.5 + c.airforce * 1.2)


def net_income(c: "Country") -> float:
    """Чистый дневной доход (доход − содержание)."""
    return daily_income(c) - upkeep(c)


def production_output(c: "Country") -> float:
    """Промышленное производство (условные очки в день): фабрики, технологии, события."""
    return (c.gdp ** 0.5 * 0.2 + c.factories * 1.5) * (1 + tech_bonus(c, "industry") + c.modifier("industry"))


def report(c: "Country") -> dict[str, float]:
    """Сводка экономики для интерфейса."""
    return {"tax": tax_income(c), "trade": trade_income(c), "income": daily_income(c), "upkeep": upkeep(c),
            "net": net_income(c), "production": production_output(c)}


def resource_delta(c: "Country") -> dict[str, float]:
    """Дневной прирост ресурсов (может быть отрицательным)."""
    ind = 1 + tech_bonus(c, "industry") + c.modifier("industry")
    base = c.gdp ** 0.5
    oil_use = 0.05 * c.army if c.at_war_with else 0.0
    return {
        "steel": (base * 0.2 * c.steel_mult + c.factories * 0.8) * ind,
        "oil": base * 0.1 * c.oil_mult - oil_use,
        "food": c.population ** 0.5 * 0.5 * c.food_mult * (1 + c.modifier("food")) - c.population * 0.02 * 0.5,
        "electronics": (base * 0.05 + c.factories * 0.3) * ind,
    }


def can_afford(c: "Country", cost: dict[str, float], n: int = 1) -> tuple[bool, str]:
    """Проверяет, хватает ли казны, ресурсов и людей на n единиц."""
    for key, val in cost.items():
        if key == "days":
            continue
        have = c.treasury if key == "treasury" else c.manpower if key == "manpower" else c.resources.get(key, 0)
        if have < val * n:
            label = {"treasury": "казны", "manpower": "людских ресурсов"}.get(key, RESOURCE_NAMES.get(key, key))
            return False, f"Не хватает {label}: нужно {val * n:.0f}, есть {have:.0f}"
    return True, ""


def pay(c: "Country", cost: dict[str, float], n: int = 1) -> None:
    """Списывает стоимость n единиц."""
    for key, val in cost.items():
        if key == "days":
            continue
        if key == "treasury":
            c.treasury -= val * n
        elif key == "manpower":
            c.manpower -= val * n
        else:
            c.resources[key] -= val * n


def start_construction(c: "Country", kind: str) -> tuple[bool, str]:
    """Ставит в очередь строительство фабрики или укрепления."""
    if kind not in BUILD_NAMES:
        return False, "Неизвестный тип строительства"
    if len(c.construction) >= MAX_CONSTRUCTION:
        return False, f"Очередь строительства заполнена ({MAX_CONSTRUCTION})"
    if kind == "fort" and c.fort + c.pending("fort") >= 5:
        return False, "Достигнут максимум укреплений (5)"
    cost = COSTS[kind]
    ok, why = can_afford(c, cost)
    if not ok:
        return False, why
    pay(c, cost)
    c.construction.append([kind, int(cost["days"])])
    return True, f"Начато строительство: {BUILD_NAMES[kind]} ({int(cost['days'])} дн.)"


def production_tick(c: "Country") -> list[str]:
    """Один игровой день экономики страны: доход, ресурсы, рост, строительство."""
    msgs: list[str] = []
    c.treasury += net_income(c)
    if c.treasury < 0:
        c.treasury = 0.0
        c.stability = max(0.0, c.stability - 0.2)
    for k, dv in resource_delta(c).items():
        c.resources[k] = max(0.0, c.resources.get(k, 0.0) + dv)
    if c.resources["food"] <= 0:
        c.stability = max(0.0, c.stability - 0.1)
    # рост ВВП и населения
    growth = 0.00005 * (c.stability - 40) / 20 + 0.00002 * len(c.trade)
    c.gdp *= 1 + growth
    infra = tech_bonus(c, "infra")
    c.population *= 1 + 0.00002 * (1 + infra * 4)
    cap = c.population * 1000 * 0.1 * (1 + infra)
    c.manpower = min(cap, c.manpower + c.population * 0.1 * (1 + infra))
    # стабильность стремится к базовой, высокий налог её снижает
    target = c.base_stability - max(0.0, c.tax_rate - 0.3) * 100
    c.stability += (target - c.stability) * 0.02
    c.stability = max(0.0, min(100.0, c.stability))
    c.morale = min(1.2, c.morale + 0.002) if not c.at_war_with else c.morale
    # временные эффекты событий
    for m in list(c.modifiers):
        m[1] -= 1
        if m[1] <= 0:
            c.modifiers.remove(m)
    # строительство (инфраструктура ускоряет)
    for item in list(c.construction):
        item[1] -= 1 + (1 if infra >= 0.2 and item[1] % 3 == 0 else 0)
        if item[1] <= 0:
            c.construction.remove(item)
            if item[0] == "factory":
                c.factories += 1
            elif item[0] == "fort":
                c.fort += 1
            msgs.append(f"{c.name}: построено — {BUILD_NAMES[item[0]]}")
    return msgs
