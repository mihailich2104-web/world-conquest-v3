"""Геоданные для игровой логики (без pygame): регионы, расстояния, соседи, местность, морские связи.

Строится один раз при загрузке карты и не сохраняется в партиях — состояние игры хранит только
владельцев/контролёров регионов и армии, а география всегда берётся с карты.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .terrain import TERRAIN, classify

EARTH_KM = 6371.0
NAVAL_RANGE_KM = 7000.0          # дальность морской переброски между портами
Pt = tuple[float, float]


def haversine(a: Pt, b: Pt) -> float:
    """Расстояние по большому кругу в км между точками (lon, lat)."""
    la1, la2 = math.radians(a[1]), math.radians(b[1])
    dl = math.radians(b[0] - a[0])
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(h)))


@dataclass
class Geo:
    """Справочник по регионам карты."""
    center: dict[str, Pt] = field(default_factory=dict)         # регион → (lon, lat)
    country_of: dict[str, str] = field(default_factory=dict)    # регион → страна по данным карты
    name: dict[str, str] = field(default_factory=dict)
    area: dict[str, float] = field(default_factory=dict)        # кв. градусы (условная площадь)
    adj: dict[str, list[str]] = field(default_factory=dict)     # соседи по суше/узким проливам
    coastal: set[str] = field(default_factory=set)
    terrain: dict[str, str] = field(default_factory=dict)
    cap_region: dict[str, str] = field(default_factory=dict)    # страна → регион исторической столицы
    cap_pos: dict[str, Pt] = field(default_factory=dict)        # страна → (lon, lat) столицы
    _dist: dict[tuple[str, str], float] = field(default_factory=dict, repr=False)
    _sea: Optional[dict[str, list[tuple[float, str]]]] = field(default=None, repr=False)

    @classmethod
    def build(cls, provs: Iterable[Any], adjacency: dict[str, list[str]], coastal: Iterable[str] = ()) -> "Geo":
        """Из списка Province карты."""
        g = cls()
        for p in provs:
            ctr = p.center or p.centroid
            g.center[p.iso] = (float(ctr[0]), float(ctr[1]))
            g.country_of[p.iso] = p.country or p.iso
            g.name[p.iso] = p.name
            g.area[p.iso] = float(p.area)
            g.terrain[p.iso] = classify(*g.center[p.iso])
            if getattr(p, "is_capital", False) or (p.capital and p.country not in g.cap_region):
                g.cap_region[p.country or p.iso] = p.iso
                if p.capital:
                    g.cap_pos[p.country or p.iso] = (float(p.capital[0]), float(p.capital[1]))
        g.adj = {k: list(v) for k, v in adjacency.items()}
        g.coastal = set(coastal)
        return g

    @classmethod
    def simple(cls, adjacency: dict[str, list[str]]) -> "Geo":
        """Запасная география (тесты, старые сохранения): регионы = страны, координаты по сетке."""
        g = cls()
        for i, k in enumerate(sorted(adjacency)):
            g.center[k] = (float((i * 13) % 340 - 170), float((i * 7) % 120 - 50))
            g.country_of[k] = k
            g.name[k] = k
            g.area[k] = 10.0
            g.terrain[k] = "plains"
            g.cap_region[k] = k
        g.adj = {k: list(v) for k, v in adjacency.items()}
        g.coastal = set(adjacency)
        return g

    # ---------- расстояния ----------
    def dist_km(self, a: str, b: str) -> float:
        """Расстояние между центрами регионов (км), с кэшем."""
        key = (a, b) if a < b else (b, a)
        d = self._dist.get(key)
        if d is None:
            d = haversine(self.center[a], self.center[b]) if a in self.center and b in self.center else 1000.0
            self._dist[key] = d
        return d

    def terrain_of(self, rid: str) -> tuple[float, float, float, str]:
        """(скорость, бонус обороны, штраф снабжения, название) местности региона."""
        return TERRAIN.get(self.terrain.get(rid, "plains"), TERRAIN["plains"])

    # ---------- море ----------
    def sea_links(self, rid: str) -> list[tuple[float, str]]:
        """Порты, достижимые морем из региона (дальность, id), отсортированные по дальности."""
        if self._sea is None:
            self._sea = self._build_sea()
        return self._sea.get(rid, [])

    def _build_sea(self) -> dict[str, list[tuple[float, str]]]:
        ports = sorted(self.coastal)
        sea: dict[str, list[tuple[float, str]]] = {p: [] for p in ports}
        for i, a in enumerate(ports):
            na = set(self.adj.get(a, ()))
            for b in ports[i + 1:]:
                if b in na or self.country_of.get(a) == self.country_of.get(b) and False:
                    continue
                d = self.dist_km(a, b)
                if d <= NAVAL_RANGE_KM:
                    sea[a].append((d, b))
                    sea[b].append((d, a))
        for lst in sea.values():
            lst.sort()
        return sea

    def regions_of(self, country: str) -> list[str]:
        """Регионы страны по данным карты."""
        return [r for r, c in self.country_of.items() if c == country]
