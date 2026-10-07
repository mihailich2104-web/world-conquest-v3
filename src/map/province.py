"""Провинция карты: геометрия страны в градусах и в мировых пикселях."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import math

from config import MAP_SCALE

Ring = list[tuple[float, float]]

# Проекция Миллера (цилиндрическая): меньше искажений у полюсов, чем у равнопромежуточной.
# Видимая область обрезана по широте: Антарктида в игре не участвует.
LAT_TOP = 84.0
LAT_BOTTOM = -57.0
_K = MAP_SCALE * 180.0 / math.pi          # пикселей на радиан (по долготе 1° = MAP_SCALE px)


def _miller_y(lat: float) -> float:
    """Ордината Миллера (радианы) для широты в градусах."""
    lat = max(-85.0, min(85.0, lat))
    return 1.25 * math.log(math.tan(math.pi / 4 + 0.4 * math.radians(lat)))


_Y_TOP = _miller_y(LAT_TOP)
WORLD_W = 360.0 * MAP_SCALE
WORLD_H = (_Y_TOP - _miller_y(LAT_BOTTOM)) * _K


def lonlat_to_world(lon: float, lat: float) -> tuple[float, float]:
    """Проекция Миллера: (lon, lat) → мировые пиксели."""
    return (lon + 180.0) * MAP_SCALE, (_Y_TOP - _miller_y(lat)) * _K


def world_to_lonlat(x: float, y: float) -> tuple[float, float]:
    """Обратная проекция: мировые пиксели → (lon, lat)."""
    ym = _Y_TOP - y / _K
    lat = math.degrees(2.5 * math.atan(math.exp(0.8 * ym)) - 0.625 * math.pi)
    return x / MAP_SCALE - 180.0, lat


def ring_area(ring: Ring) -> float:
    """Площадь кольца (формула шнурования), всегда ≥ 0."""
    s = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % len(ring)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def ring_centroid(ring: Ring) -> tuple[float, float]:
    """Центр масс кольца; для вырожденных колец — среднее вершин."""
    a = cx = cy = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % len(ring)]
        cr = x1 * y2 - x2 * y1
        a += cr
        cx += (x1 + x2) * cr
        cy += (y1 + y2) * cr
    if abs(a) < 1e-12:
        n = len(ring)
        return sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n
    return cx / (3 * a), cy / (3 * a)


def point_in_ring(x: float, y: float, ring: Ring) -> bool:
    """Алгоритм луча: лежит ли точка внутри кольца."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-18) + xi:
            inside = not inside
        j = i
    return inside


@dataclass
class Province:
    """Провинция = страна на карте 110m (мультиполигон без дыр)."""
    iso: str
    name: str
    continent: str
    polygons: list[Ring]
    wpolys: list[Ring] = field(default_factory=list)     # мировые пиксели
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)  # lon/lat
    wbbox: tuple[float, float, float, float] = (0, 0, 0, 0)  # мировые пиксели
    centroid: tuple[float, float] = (0.0, 0.0)               # lon/lat крупнейшей части
    area: float = 0.0
    holes: list[Ring] = field(default_factory=list)          # внутренние кольца (анклавы) — только для границ
    capital: Optional[tuple[float, float]] = None            # (lon, lat) настоящей столицы
    main_wbbox: tuple[float, float, float, float] = (0, 0, 0, 0)   # рамка крупнейшей части (для подписей)

    def finalize(self) -> "Province":
        """Считает проекцию, рамки, центроид и площадь."""
        pts = [p for r in self.polygons for p in r]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))
        self.wpolys = [[lonlat_to_world(lo, la) for lo, la in r] for r in self.polygons]
        wx, wy = [p[0] for r in self.wpolys for p in r], [p[1] for r in self.wpolys for p in r]
        self.wbbox = (min(wx), min(wy), max(wx), max(wy))
        biggest = max(self.polygons, key=ring_area)
        self.area = sum(ring_area(r) for r in self.polygons)
        self.centroid = ring_centroid(biggest)
        bw = [lonlat_to_world(lo, la) for lo, la in biggest]
        self.main_wbbox = (min(p[0] for p in bw), min(p[1] for p in bw), max(p[0] for p in bw), max(p[1] for p in bw))
        return self

    def contains(self, lon: float, lat: float) -> bool:
        """Попадает ли точка (lon, lat) в провинцию."""
        b = self.bbox
        if not (b[0] <= lon <= b[2] and b[1] <= lat <= b[3]):
            return False
        return any(point_in_ring(lon, lat, r) for r in self.polygons)

    @property
    def world_centroid(self) -> tuple[float, float]:
        """Центроид в мировых пикселях."""
        return lonlat_to_world(*self.centroid)

    @property
    def capital_world(self) -> tuple[float, float]:
        """Столица в мировых пикселях (настоящие координаты; если их нет — центроид)."""
        return lonlat_to_world(*(self.capital or self.centroid))
