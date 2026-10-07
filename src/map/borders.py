"""Границы по общим рёбрам: граница между двумя провинциями рисуется, только если у них разные владельцы.

Когда страна захвачена, её провинции получают владельца-завоевателя — общие границы исчезают,
и территория сливается с завоевателем в единое целое (остаётся только береговая линия).
Модуль не зависит от pygame и тестируется отдельно.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable, Optional

from .province import Province, lonlat_to_world

log = logging.getLogger(__name__)
Q = 1000.0                 # квантование координат (≈100 м) для сопоставления общих рёбер
SEAM = "#seam"             # ребро по линии смены дат (±180°) — никогда не рисуется


@dataclass(slots=True)
class Arc:
    """Непрерывная цепочка рёбер кольца провинции iso с одним и тем же соседом nb."""
    iso: str
    nb: Optional[str]                      # None — береговая линия, SEAM — шов карты
    pts: list[tuple[float, float]]         # мировые пиксели
    bbox: tuple[float, float, float, float]


def _key(a: tuple[float, float], b: tuple[float, float]) -> tuple:
    qa = (round(a[0] * Q), round(a[1] * Q))
    qb = (round(b[0] * Q), round(b[1] * Q))
    return (qa, qb) if qa <= qb else (qb, qa)


def build_arcs(provs: Iterable[Province]) -> list[Arc]:
    """Строит дуги границ. Общая граница двух провинций представлена одной дугой."""
    provs = list(provs)
    owners: dict[tuple, list[str]] = {}
    rings: list[tuple[str, list[tuple[float, float]]]] = []
    for p in provs:
        for ring in (*p.polygons, *p.holes):
            if len(ring) < 3:
                continue
            rings.append((p.iso, ring))
            for i in range(len(ring) - 1):
                owners.setdefault(_key(ring[i], ring[i + 1]), []).append(p.iso)
    arcs: list[Arc] = []
    for iso, ring in rings:
        cur_nb: object = ...
        cur: list[tuple[float, float]] = []

        def flush() -> None:
            if len(cur) >= 2 and cur_nb is not ...:
                nb = cur_nb
                if nb is None or nb == SEAM or iso < nb:         # дубликат с другой стороны не нужен
                    wp = [lonlat_to_world(lo, la) for lo, la in cur]
                    xs, ys = [q[0] for q in wp], [q[1] for q in wp]
                    arcs.append(Arc(iso, nb, wp, (min(xs), min(ys), max(xs), max(ys))))  # type: ignore[arg-type]

        for i in range(len(ring) - 1):
            a, b = ring[i], ring[i + 1]
            if abs(a[0]) >= 179.99 and abs(b[0]) >= 179.99 and abs(a[0] - b[0]) < 1e-6:
                nb: Optional[str] = SEAM
            else:
                others = [o for o in owners[_key(a, b)] if o != iso]
                nb = others[0] if others else None
            if nb != cur_nb:
                flush()
                cur = [a]
                cur_nb = nb
            cur.append(b)
        flush()
    log.info("Границы: %d дуг", len(arcs))
    return arcs


def arc_kind(arc: Arc, owner: dict[str, str]) -> Optional[str]:
    """'coast' — береговая линия, 'border' — граница разных владельцев, None — не рисуется
    (шов карты или граница внутри одной державы)."""
    if arc.nb is None:
        return "coast"
    if arc.nb == SEAM:
        return None
    return "border" if owner.get(arc.iso, arc.iso) != owner.get(arc.nb, arc.nb) else None


def territory_edge(arc: Arc, owner: dict[str, str], who: str) -> bool:
    """Лежит ли дуга на внешней границе территории державы who (для подсветки выбранной страны)."""
    mine = owner.get(arc.iso, arc.iso) == who
    if arc.nb is None:
        return mine
    if arc.nb == SEAM:
        return False
    return mine != (owner.get(arc.nb, arc.nb) == who)


def border_point(arcs: Iterable[Arc], owner: dict[str, str], a: str, d: str,
                 p0: tuple[float, float], p1: tuple[float, float]) -> Optional[tuple[float, float]]:
    """Точка общей границы держав a и d, ближайшая к прямой p0→p1 (где армия пересекает границу)."""
    best, best_d = None, 1e18
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    ln = dx * dx + dy * dy or 1.0
    for arc in arcs:
        if arc.nb is None or arc.nb == SEAM:
            continue
        oa, ob = owner.get(arc.iso, arc.iso), owner.get(arc.nb, arc.nb)
        if {oa, ob} != {a, d}:
            continue
        step = max(1, len(arc.pts) // 40)
        for x, y in arc.pts[::step]:
            t = max(0.0, min(1.0, ((x - p0[0]) * dx + (y - p0[1]) * dy) / ln))
            dist = math.hypot(x - (p0[0] + t * dx), y - (p0[1] + t * dy))
            if dist < best_d:
                best, best_d = (x, y), dist
    return best
