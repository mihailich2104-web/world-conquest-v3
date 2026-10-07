"""Случайные события: кризисы, подъём производства, реформы, политические и военные события.

Все решения принимаются через state.rng, поэтому события детерминированы и воспроизводятся
из сохранения; в LAN-игре их генерирует только хост.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .country import Country
    from .game_state import GameState

log = logging.getLogger(__name__)
EVENT_CHANCE = 0.0025        # шанс события для одной страны за день
GLOBAL_EVENT_CHANCE = 0.0012  # шанс мирового события за день


@dataclass(frozen=True)
class Event:
    """Описание события: условие, вес и применение."""
    id: str
    title: str
    text: str
    weight: float
    apply: Callable[["GameState", "Country"], None]
    cond: Callable[["Country"], bool] = lambda c: True


def _add_mod(c: "Country", key: str, days: int, value: float) -> None:
    c.modifiers.append([key, days, value])


def _crisis(s, c):
    c.treasury *= 0.85
    c.stability = max(0.0, c.stability - 5)
    _add_mod(c, "income", 30, -0.25)


def _boom(s, c):
    _add_mod(c, "industry", 40, 0.25)
    c.stability = min(100.0, c.stability + 2)


def _reform(s, c):
    c.stability = min(100.0, c.stability + 6)
    c.base_stability = min(90.0, c.base_stability + 1)


def _protests(s, c):
    c.stability = max(0.0, c.stability - 8)
    _add_mod(c, "income", 20, -0.1)


def _coup(s, c):
    c.stability = max(0.0, c.stability - 15)
    c.morale = max(0.4, c.morale - 0.15)
    c.treasury *= 0.9


def _recruits(s, c):
    c.manpower += c.population * 2.0
    c.morale = min(1.4, c.morale + 0.05)


def _incident(s, c):
    c.morale = max(0.4, c.morale - 0.08)
    c.treasury = max(0.0, c.treasury - 20)


def _drill(s, c):
    c.morale = min(1.4, c.morale + 0.1)
    c.treasury = max(0.0, c.treasury - 10)


def _harvest(s, c):
    _add_mod(c, "food", 60, 0.5)
    c.resources["food"] += 15


def _drought(s, c):
    _add_mod(c, "food", 60, -0.6)
    c.stability = max(0.0, c.stability - 2)


def _breakthrough(s, c):
    if c.researching:
        c.researching[0][1] += 25.0
    else:
        c.treasury += 30


def _trade_boom(s, c):
    _add_mod(c, "income", 30, 0.2)


def _resources(s, c):
    c.resources["steel"] += 25
    c.resources["oil"] += 15


EVENTS: tuple[Event, ...] = (
    Event("crisis", "Экономический кризис", "Банковский кризис ударил по казне и налоговым сборам.", 1.0, _crisis),
    Event("boom", "Рост производства", "Заводы работают с рекордной загрузкой.", 1.2, _boom,
          lambda c: c.factories >= 2),
    Event("reform", "Реформы", "Правительство провело успешные реформы, стабильность растёт.", 1.0, _reform),
    Event("protests", "Массовые протесты", "Недовольство налогами выплеснулось на улицы.", 0.9, _protests,
          lambda c: c.tax_rate > 0.3 or c.stability < 50),
    Event("coup", "Попытка переворота", "Армия и политики столкнулись, страна нестабильна.", 0.4, _coup,
          lambda c: c.stability < 30),
    Event("recruits", "Призывная кампания", "Добровольцы пополнили людские ресурсы.", 0.8, _recruits),
    Event("incident", "Военный инцидент", "Учения закончились скандалом, мораль армии упала.", 0.6, _incident,
          lambda c: c.army > 0),
    Event("drill", "Масштабные учения", "Армия прошла учения: мораль выросла.", 0.8, _drill, lambda c: c.army > 0),
    Event("harvest", "Богатый урожай", "Рекордный урожай пополнил запасы продовольствия.", 0.8, _harvest),
    Event("drought", "Засуха", "Неурожай сократил производство продовольствия.", 0.7, _drought),
    Event("breakthrough", "Научный прорыв", "Учёные ускорили исследования.", 0.7, _breakthrough),
    Event("trade_boom", "Торговый подъём", "Внешняя торговля резко выросла.", 0.8, _trade_boom,
          lambda c: len(c.trade) > 0),
    Event("resources", "Новое месторождение", "Разведчики недр открыли месторождение.", 0.6, _resources),
)
BY_ID = {e.id: e for e in EVENTS}


def _global_crisis(state: "GameState") -> None:
    for c in state.alive_countries():
        c.treasury *= 0.92
        _add_mod(c, "income", 25, -0.12)
    state.log_event("Мировой финансовый кризис: доходы всех стран снижены", None, True)


def fire(state: "GameState", c: "Country", ev: Event) -> None:
    """Применяет событие к стране и пишет в журнал, если страна — человек."""
    ev.apply(state, c)
    if state.is_human(c.iso):
        state.log_event(f"{ev.title}: {ev.text}", c.iso, True)


def tick_events(state: "GameState") -> None:
    """Раз в день: случайные события стран и редкие мировые события."""
    rng = state.rng
    if rng.random() < GLOBAL_EVENT_CHANCE:
        _global_crisis(state)
    for c in state.alive_countries():
        if rng.random() >= EVENT_CHANCE:
            continue
        pool = [e for e in EVENTS if e.cond(c)]
        if not pool:
            continue
        total = sum(e.weight for e in pool)
        x, acc = rng.random() * total, 0.0
        for e in pool:
            acc += e.weight
            if x <= acc:
                fire(state, c, e)
                break
