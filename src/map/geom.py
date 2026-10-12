"""Быстрая геометрия карты (без pygame): кольца и ломаные с «чанками» для отсечки по прямоугольнику.

Почему это нужно. pygame.draw.polygon заливает полигон построчно: стоимость ~ (высота полигона в пикселях)
× (число вершин), причём высота считается по ВСЕМУ полигону, а не по видимой части. При приближении целая
страна (Россия, Канада) превращается в полигон высотой в тысячи пикселей с сотнями/тысячами вершин, и каждый
кадр сдвига карты стоил десятки миллисекунд. Здесь кольцо режется на чанки по CHUNK вершин с их рамками;
для заданного прямоугольника (тайла/экрана) невидимые чанки заменяются короткими «мостами» снаружи
прямоугольника. Чётность пересечений со всеми строками внутри прямоугольника сохраняется, поэтому заливка
внутри него идентична заливке полного кольца, а размер полигона ограничен видимой частью.
"""
from __future__ import annotations

from typing import Optional

CHUNK = 48
Pt = tuple[float, float]
Rect = tuple[float, float, float, float]      # x0, y0, x1, y1

_LEFT, _RIGHT, _TOP, _BOTTOM = 1, 2, 4, 8


class Path:
    """Кольцо или ломаная в мировых пикселях + рамки чанков (i0, i1, x0, y0, x1, y1), i1 включительно."""
    __slots__ = ("pts", "ch", "bbox")

    def __init__(self, pts: list[Pt]) -> None:
        self.pts = pts
        n = len(pts)
        ch: list[tuple[int, int, float, float, float, float]] = []
        i = 0
        while i < n - 1:
            j = min(i + CHUNK, n - 1)
            seg = pts[i:j + 1]
            xs = [p[0] for p in seg]
            ys = [p[1] for p in seg]
            ch.append((i, j, min(xs), min(ys), max(xs), max(ys)))
            i = j
        self.ch = ch
        if ch:
            self.bbox: Rect = (min(c[2] for c in ch), min(c[3] for c in ch),
                               max(c[4] for c in ch), max(c[5] for c in ch))
        elif pts:
            self.bbox = (pts[0][0], pts[0][1], pts[0][0], pts[0][1])
        else:
            self.bbox = (0.0, 0.0, 0.0, 0.0)


def rects_touch(a: Rect, b: Rect) -> bool:
    """Пересекаются ли два прямоугольника."""
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _clampf(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _bridge(first: Pt, last: Pt, mask: int, X0: float, Y0: float, X1: float, Y1: float) -> list[Pt]:
    """Точки обхода невидимого участка снаружи прямоугольника (по общей стороне mask)."""
    if mask & _LEFT:
        x = X0 - 1.0
        return [(x, _clampf(first[1], Y0 - 1, Y1 + 1)), (x, _clampf(last[1], Y0 - 1, Y1 + 1))]
    if mask & _RIGHT:
        x = X1 + 1.0
        return [(x, _clampf(first[1], Y0 - 1, Y1 + 1)), (x, _clampf(last[1], Y0 - 1, Y1 + 1))]
    if mask & _TOP:
        y = Y0 - 1.0
        return [(_clampf(first[0], X0 - 1, X1 + 1), y), (_clampf(last[0], X0 - 1, X1 + 1), y)]
    y = Y1 + 1.0
    return [(_clampf(first[0], X0 - 1, X1 + 1), y), (_clampf(last[0], X0 - 1, X1 + 1), y)]


def _inside(x: float, y: float, poly: list[Pt]) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def ring_polygon(path: Path, rect: Rect, margin: float = 2.0) -> Optional[list[Pt]]:
    """Полигон для заливки кольца внутри rect (мировые координаты) или None, если кольцо rect не затрагивает.

    Возвращаемые точки — вершины видимых чанков + мосты через невидимые участки; заливка внутри rect
    совпадает с заливкой полного кольца (проверяется тестами)."""
    X0, Y0, X1, Y1 = rect[0] - margin, rect[1] - margin, rect[2] + margin, rect[3] + margin
    b = path.bbox
    if b[2] < X0 or b[0] > X1 or b[3] < Y0 or b[1] > Y1 or not path.ch:
        return None
    pts = path.pts
    out: list[Pt] = []
    visible = False
    run_mask = 0
    run_first = run_last = 0
    last_ci = len(path.ch) - 1

    def flush(final: bool) -> None:
        nonlocal run_mask
        if run_mask:
            f, l = pts[run_first], pts[run_last]
            out.append(f)
            out.extend(_bridge(f, l, run_mask, X0, Y0, X1, Y1))
            if final:
                out.append(l)
            run_mask = 0

    for ci, (i0, i1, cx0, cy0, cx1, cy1) in enumerate(path.ch):
        m = ((_LEFT if cx1 < X0 else 0) | (_RIGHT if cx0 > X1 else 0)
             | (_TOP if cy1 < Y0 else 0) | (_BOTTOM if cy0 > Y1 else 0))
        if m:
            if run_mask & m:
                run_mask &= m
                run_last = i1
            else:
                flush(False)
                run_mask, run_first, run_last = m, i0, i1
        else:
            flush(False)
            visible = True
            out.extend(pts[i0:i1])
            if ci == last_ci:
                out.append(pts[i1])
        if ci == last_ci and m:
            flush(True)
    if not visible:                                   # ни один чанк не виден: rect целиком внутри кольца или снаружи
        cx, cy = (X0 + X1) / 2.0, (Y0 + Y1) / 2.0
        if len(out) >= 3 and _inside(cx, cy, out):
            return [(X0, Y0), (X1, Y0), (X1, Y1), (X0, Y1)]
        return None
    return out if len(out) >= 3 else None


def polyline_runs(path: Path, rect: Rect, margin: float = 2.0) -> list[list[Pt]]:
    """Видимые участки ломаной внутри rect (по чанкам; каждый участок — непрерывный список точек)."""
    X0, Y0, X1, Y1 = rect[0] - margin, rect[1] - margin, rect[2] + margin, rect[3] + margin
    b = path.bbox
    if b[2] < X0 or b[0] > X1 or b[3] < Y0 or b[1] > Y1:
        return []
    runs: list[list[Pt]] = []
    a = -1
    bi = 0
    for i0, i1, cx0, cy0, cx1, cy1 in path.ch:
        if cx1 < X0 or cx0 > X1 or cy1 < Y0 or cy0 > Y1:
            if a >= 0:
                runs.append(path.pts[a:bi + 1])
                a = -1
        else:
            if a < 0:
                a = i0
            bi = i1
    if a >= 0:
        runs.append(path.pts[a:bi + 1])
    return runs


def fill_cost(poly: list[tuple[float, float]]) -> int:
    """Модель стоимости pygame.draw.polygon: (число вершин) × (высота в пикселях)."""
    if not poly:
        return 0
    ys = [p[1] for p in poly]
    return len(poly) * (int(max(ys)) - int(min(ys)) + 1)


def to_pixels(pts: list[Pt], z: float, ox: int, oy: int) -> list[tuple[int, int]]:
    """Мировые точки → целые координаты в системе тайла: округление глобальное, швы между тайлами не расходятся."""
    return [(int(x * z + 0.5) - ox, int(y * z + 0.5) - oy) for x, y in pts]
