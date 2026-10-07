"""Класс страны: состояние, стартовая инициализация и делегирующие методы действий."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # только для аннотаций, чтобы избежать циклических импортов
    from .game_state import GameState

log = logging.getLogger(__name__)

SET_FIELDS = ("at_war_with", "allies", "pacts", "trade")


@dataclass
class Country:
    """Государство с экономикой, армией, технологиями и дипломатией."""

    iso: str
    name: str
    continent: str = ""
    capital: str = ""
    population: float = 1.0          # млн человек
    gdp: float = 1.0                 # млрд $
    manpower: float = 0.0            # тыс. человек
    stability: float = 60.0          # 0..100
    base_stability: float = 60.0
    ideology: str = "neutral"
    color: tuple[int, int, int] = (150, 150, 150)
    treasury: float = 0.0
    army: int = 0                    # дивизии
    navy: int = 0                    # корабли
    airforce: int = 0                # эскадрильи
    morale: float = 1.0
    factories: int = 1
    fort: int = 0
    tax_rate: float = 0.25
    oil_mult: float = 1.0
    steel_mult: float = 1.0
    food_mult: float = 1.0
    resources: dict[str, float] = field(
        default_factory=lambda: {"steel": 0.0, "oil": 0.0, "food": 0.0, "electronics": 0.0})
    technologies: list[str] = field(default_factory=list)
    relations: dict[str, int] = field(default_factory=dict)
    at_war_with: set[str] = field(default_factory=set)
    allies: set[str] = field(default_factory=set)
    pacts: set[str] = field(default_factory=set)      # пакты о ненападении
    trade: set[str] = field(default_factory=set)      # торговые соглашения
    construction: list[list[Any]] = field(default_factory=list)   # [вид, дней осталось]
    training: list[list[Any]] = field(default_factory=list)       # [вид, дней осталось]
    researching: list[list[Any]] = field(default_factory=list)    # [id технологии, прогресс]
    alive: bool = True
    modifiers: list[list[Any]] = field(default_factory=list)      # [ключ, дней осталось, значение] — эффекты событий

    # ---------- создание ----------
    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Country":
        """Создаёт страну из записи countries.json и считает стартовые показатели."""
        pop, gdp = float(d["population"]), float(d["gdp"])
        army = max(1, int(math.sqrt(pop) * 1.5))
        c = cls(
            iso=d["iso"], name=d["name"], continent=d.get("continent", ""),
            capital=d.get("capital", ""), population=pop, gdp=gdp,
            manpower=float(d.get("manpower", pop * 50)),
            stability=float(d.get("stability", 60)), base_stability=float(d.get("stability", 60)),
            ideology=d.get("ideology", "neutral"), color=tuple(d.get("color", (150, 150, 150))),  # type: ignore[arg-type]
            army=army, navy=int(army * 0.2), airforce=int(army * 0.15),
            factories=max(1, int(gdp ** 0.4 / 3)),
            oil_mult=float(d.get("oil_mult", 1)), steel_mult=float(d.get("steel_mult", 1)),
            food_mult=float(d.get("food_mult", 1)),
        )
        base = gdp ** 0.5
        c.resources = {"steel": 20 + base * 3, "oil": 15 + base * 2,
                       "food": 30 + pop ** 0.5 * 5, "electronics": 15 + base * 1.5}
        return c

    # ---------- сериализация ----------
    def to_dict(self) -> dict[str, Any]:
        """Преобразует страну в JSON-совместимый словарь."""
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        for k in SET_FIELDS:
            d[k] = sorted(d[k])
        d["color"] = list(self.color)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Country":
        """Восстанавливает страну из словаря сохранения."""
        d = dict(d)
        for k in SET_FIELDS:
            d[k] = set(d.get(k, []))
        d["color"] = tuple(d["color"])
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    # ---------- действия (делегируют в модули домена) ----------
    def production_tick(self) -> list[str]:
        """Один день экономики и строительства; возвращает сообщения о событиях."""
        from . import economy
        return economy.production_tick(self)

    def recruit(self, kind: str = "army", n: int = 1) -> tuple[bool, str]:
        """Ставит в очередь найм юнитов (army/navy/air)."""
        from . import military
        return military.recruit(self, kind, n)

    def build(self, kind: str) -> tuple[bool, str]:
        """Ставит в очередь строительство (factory/fort)."""
        from . import economy
        return economy.start_construction(self, kind)

    def research(self, tech_id: str) -> tuple[bool, str]:
        """Начинает изучение технологии."""
        from . import research
        return research.start_research(self, tech_id)

    def declare_war(self, state: "GameState", target_iso: str) -> tuple[bool, str]:
        """Объявляет войну другой стране."""
        from . import diplomacy
        return diplomacy.declare_war(state, self.iso, target_iso)

    def propose_peace(self, state: "GameState", target_iso: str) -> tuple[bool, str]:
        """Предлагает мир противнику."""
        from . import diplomacy
        return diplomacy.propose_peace(state, self.iso, target_iso)

    # ---------- вспомогательное ----------
    def pending(self, kind: str) -> int:
        """Сколько единиц данного вида ещё в очереди (строительство + обучение)."""
        return sum(1 for k, _ in self.construction if k == kind) + \
            sum(1 for k, _ in self.training if k == kind)

    def modifier(self, key: str) -> float:
        """Суммарное значение активных временных эффектов (событий) с данным ключом."""
        return sum(float(m[2]) for m in self.modifiers if m[0] == key)

    def init_start(self) -> None:
        """Выставляет стартовую казну пропорционально доходам страны."""
        from . import economy
        self.treasury = max(150.0, economy.daily_income(self) * 20)
