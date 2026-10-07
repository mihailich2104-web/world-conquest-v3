"""Армия: дивизии, флот, авиация, снабжение, рекрутинг, боевая мощь."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config import COSTS, UNIT_NAMES
from .economy import can_afford, pay
from .research import tech_bonus

if TYPE_CHECKING:
    from .country import Country

log = logging.getLogger(__name__)

MAX_TRAINING = 8
_ATTR = {"army": "army", "navy": "navy", "air": "airforce"}


def recruit(c: "Country", kind: str, n: int = 1) -> tuple[bool, str]:
    """Ставит в очередь обучение n единиц (army/navy/air)."""
    if kind not in _ATTR:
        return False, "Неизвестный тип юнита"
    if len(c.training) + n > MAX_TRAINING:
        return False, f"Очередь обучения заполнена ({MAX_TRAINING})"
    cost = COSTS[kind]
    ok, why = can_afford(c, cost, n)
    if not ok:
        return False, why
    pay(c, cost, n)
    for _ in range(n):
        c.training.append([kind, int(cost["days"])])
    return True, f"Начат найм: {n} × {UNIT_NAMES[kind]} ({int(cost['days'])} дн.)"


def training_tick(c: "Country") -> list[str]:
    """Один день обучения; завершённые юниты поступают в строй."""
    msgs: list[str] = []
    for item in list(c.training):
        item[1] -= 1
        if item[1] <= 0:
            c.training.remove(item)
            attr = _ATTR[item[0]]
            setattr(c, attr, getattr(c, attr) + 1)
            msgs.append(f"{c.name}: готова {UNIT_NAMES[item[0]]}")
    return msgs


def supply_factor(c: "Country") -> float:
    """Снабжение: нехватка нефти/стали в войне снижает боевую мощь."""
    f = 1.0
    if c.at_war_with:
        if c.resources.get("oil", 0) < 1:
            f *= 0.6
        if c.resources.get("steel", 0) < 1:
            f *= 0.8
        f = min(1.0, f * (1 + tech_bonus(c, "infra") * 0.5))
    return f


def combat_power(c: "Country", defending: bool = False) -> float:
    """Σ(юниты × множитель технологий × мораль), для защиты — с бонусом укреплений."""
    ground = c.army * (1 + tech_bonus(c, "infantry") + tech_bonus(c, "tanks"))
    air = c.airforce * 0.6 * (1 + tech_bonus(c, "air"))
    navy = c.navy * 0.3 * (1 + tech_bonus(c, "navy"))
    power = (ground + air + navy) * c.morale * supply_factor(c)
    if defending:
        power *= fort_bonus(c)
    return power


def attack_power(c: "Country") -> float:
    """Наступательная сила страны."""
    return combat_power(c)


def defense_power(c: "Country") -> float:
    """Оборонительная сила (с укреплениями)."""
    return combat_power(c, defending=True)


def fort_bonus(c: "Country") -> float:
    """Бонус защитника от укреплений."""
    return 1.2 + 0.1 * c.fort


def lose_units(c: "Country", frac: float, rng) -> int:
    """Потери: каждая дивизия/корабль/эскадрилья гибнет с шансом; возвращает число потерь."""
    lost = 0
    for attr in ("army", "navy", "airforce"):
        cur = getattr(c, attr)
        exp = cur * frac
        n = int(exp) + (1 if rng.random() < exp - int(exp) else 0)
        n = min(n, cur)
        setattr(c, attr, cur - n)
        lost += n
    return lost
