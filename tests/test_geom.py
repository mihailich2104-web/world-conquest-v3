"""Проверка быстрой геометрии: заливка collapsed-полигона внутри тайла совпадает с заливкой полного кольца,
а стоимость заливки (вершины × высота) ограничена. Запуск: python tests/test_geom.py"""
import json
import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from map.geom import CHUNK, Path as GPath, fill_cost, polyline_runs, ring_polygon, to_pixels  # noqa: E402
from map.province import lonlat_to_world  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "src" / "data" / "provinces.geojson"


def load_rings(dense: int = 0):
    """Кольца стран в мировых пикселях; dense > 0 — делит каждое ребро (имитация карты 50m: больше вершин)."""
    rng = random.Random(7)
    out = []
    for f in json.loads(DATA.read_text(encoding="utf-8"))["features"]:
        if f["properties"]["iso"] in ("ATA", "ATF"):
            continue
        g = f["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        for poly in polys:
            ring = [lonlat_to_world(x, y) for x, y in poly[0]]
            for _ in range(dense):
                new = []
                for a, b in zip(ring, ring[1:]):
                    dx, dy = b[0] - a[0], b[1] - a[1]
                    j = rng.uniform(-0.12, 0.12)
                    new += [a, ((a[0] + b[0]) / 2 - dy * j, (a[1] + b[1]) / 2 + dx * j)]
                new.append(ring[-1])
                ring = new
            out.append((f["properties"]["iso"], ring))
    return out


def raster(poly, w, h):
    """Чётно-нечётная заливка по центрам пикселей (как построчная заливка pygame): множество (x, y)."""
    px = set()
    n = len(poly)
    for y in range(h):
        yc = y + 0.5
        xs = []
        for i in range(n):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % n]
            if (y1 > yc) != (y2 > yc):
                xs.append(x1 + (yc - y1) * (x2 - x1) / (y2 - y1))
        xs.sort()
        for k in range(0, len(xs) - 1, 2):
            a, b = xs[k], xs[k + 1]
            for x in range(max(0, math.ceil(a - 0.5)), min(w, math.ceil(b - 0.5))):
                px.add((x, y))
    return px


def check_equal(rings, zooms, n_rects, size=48, seed=1):
    rng = random.Random(seed)
    bad = tested = 0
    for _ in range(n_rects):
        z = rng.choice(zooms)
        iso, ring = rng.choice(rings)
        vx, vy = rng.choice(ring)                              # прямоугольник у границы
        tx, ty = int(vx * z) // size - rng.randint(0, 1), int(vy * z) // size - rng.randint(0, 1)
        ox, oy = tx * size, ty * size
        rect = (ox / z, oy / z, (ox + size) / z, (oy + size) / z)
        gp = GPath(ring)
        poly = ring_polygon(gp, rect, margin=2.0)
        full = raster(to_pixels(ring, z, ox, oy), size, size)
        got = raster(to_pixels(poly, z, ox, oy), size, size) if poly else set()
        tested += 1
        if full != got:
            bad += 1
            print("  расхождение:", iso, "zoom", z, "тайл", tx, ty, "разница", len(full ^ got), "px")
    return bad, tested


def main():
    rings = load_rings()
    print(f"колец: {len(rings)}, вершин: {sum(len(r) for _, r in rings)}")
    bad, n = check_equal(rings, (2.0, 3.3, 5.0, 8.0), 400)
    assert bad == 0, f"{bad}/{n} тайлов отличаются от полного кольца"
    print(f"110m: {n} тайлов совпадают с полным кольцом пиксель в пиксель")
    dense = load_rings(dense=3)
    print(f"плотная карта (имитация 50m): вершин {sum(len(r) for _, r in dense)}")
    bad, n = check_equal(dense, (2.0, 4.0, 8.0), 250, seed=5)
    assert bad == 0, f"плотная карта: {bad}/{n}"
    print(f"плотная: {n} тайлов совпадают")

    # модель стоимости: целая страна против отсечённого полигона (zoom 8, вид 1280×720)
    worst_old = worst_new = 0
    tile = 256
    for z in (2.0, 4.0, 8.0):
        for iso, ring in dense:
            if len(ring) < 2000:
                continue
            old = fill_cost([(x * z, y * z) for x, y in ring])
            gp = GPath(ring)
            cx, cy = ring[len(ring) // 2]
            ox, oy = int(cx * z) // tile * tile, int(cy * z) // tile * tile
            rect = (ox / z, oy / z, (ox + tile) / z, (oy + tile) / z)
            poly = ring_polygon(gp, rect)
            new = fill_cost(to_pixels(poly, z, ox, oy)) if poly else 0
            worst_old, worst_new = max(worst_old, old), max(worst_new, new)
    print(f"худший случай стоимости заливки: было {worst_old:,} → стало {worst_new:,} (вершины×высота)")
    assert worst_new * 20 < worst_old and worst_new < 1_500_000

    # скорость подготовки полигона тайла
    t0 = time.perf_counter()
    cnt = 0
    for iso, ring in dense:
        if len(ring) < 2000:
            continue
        gp = GPath(ring)
        for _ in range(20):
            cx, cy = random.choice(ring)
            rect = (cx - 16, cy - 16, cx + 16, cy + 16)
            ring_polygon(gp, rect)
            cnt += 1
    dt = (time.perf_counter() - t0) / max(cnt, 1) * 1000
    print(f"подготовка полигона тайла: {dt:.2f} мс в среднем для колец >2000 вершин")
    assert dt < 3.0

    # ломаные: видимые участки лежат внутри расширенного прямоугольника
    ring = max(dense, key=lambda r: len(r[1]))[1]
    gp = GPath(ring)
    rect = (ring[10][0] - 30, ring[10][1] - 30, ring[10][0] + 30, ring[10][1] + 30)
    runs = polyline_runs(gp, rect)
    assert runs and sum(len(r) for r in runs) < len(ring)
    inside = sum(1 for r in runs for p in r if rect[0] <= p[0] <= rect[2] and rect[1] <= p[1] <= rect[3])
    allin = sum(1 for p in ring if rect[0] <= p[0] <= rect[2] and rect[1] <= p[1] <= rect[3])
    assert inside >= allin, "потеряны видимые точки ломаной"
    print(f"GEOM OK (CHUNK={CHUNK})")


if __name__ == "__main__":
    main()
