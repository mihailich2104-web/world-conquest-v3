"""Рендерер карты без SDL (Pillow-замена pygame): тайлы совпадают с прямой отрисовкой, стоимость заливки ограничена,
выбор региона точен, обновление владельцев локально, оверлеи армий не падают. Запуск: python tests/test_render.py"""
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import pg_shim  # noqa: E402

pg = pg_shim.install()
from config import COUNTRIES_PATH  # noqa: E402
from core import armies as am, diplomacy as dip  # noqa: E402
from core.game_state import GameState  # noqa: E402
from map.camera import Camera  # noqa: E402
from map.geom import to_pixels  # noqa: E402
from map.map_loader import load_world_geo  # noqa: E402
from map import map_renderer as mr  # noqa: E402

CS = json.loads(Path(COUNTRIES_PATH).read_text(encoding="utf-8"))
PROVS, GEO, _ = load_world_geo(CS)
PT = [(p.iso, p.name, p.continent) for p in PROVS]


def settle(r, st, screen, cam, max_frames=80):
    """Рисуем кадры, пока все тайлы вида не готовы; возвращает число кадров."""
    for i in range(1, max_frames):
        r.draw(screen, st, None, None)
        if r._base is not None or i > 3 and r._tile_ver == r._tile_ver and not _missing(r, screen, cam):
            return i
    return max_frames


def _missing(r, screen, cam):
    zk = int(round(cam.zoom * mr.ZQ))
    sx0, sy0 = r._origin()
    w, h = screen.get_size()
    keys = [(zk, tx, ty) for ty in range(sy0 // mr.TILE, (sy0 + h - 1) // mr.TILE + 1)
            for tx in range(sx0 // mr.TILE, (sx0 + w - 1) // mr.TILE + 1)]
    return [k for k in keys if k not in r._tiles]


def reference(r, st, cam, size):
    """Эталон: те же слои, но без отсечения — целые кольца и дуги."""
    z = cam.zoom
    ox, oy = r._origin()
    s = pg.Surface(size)
    for j in range(0, size[1], 8):
        k = max(0.0, min(1.0, (oy + j) / z / mr.WORLD_H))
        s.fill(tuple(int(mr.OCEAN_TOP[i] + (mr.OCEAN_BOTTOM[i] - mr.OCEAN_TOP[i]) * k) for i in range(3)), (0, j, size[0], 8))
    for gx in r._grid_x:
        x = int(gx * z + 0.5) - ox
        if 0 <= x < size[0]:
            pg.draw.line(s, mr.GRID, (x, 0), (x, size[1]))
    for gy in r._grid_y:
        y = int(gy * z + 0.5) - oy
        if 0 <= y < size[1]:
            pg.draw.line(s, mr.GRID, (0, y), (size[0], y))
    if not r.lite:
        hw = int(3 + min(z, 3.0))
        for ai, a in enumerate(r.arcs):
            if r._kind[ai] == mr.K_COAST and len(a.pts) >= 2:
                pg.draw.lines(s, mr.SHALLOW, False, to_pixels(a.pts, z, ox, oy), hw)
    for p in r.order:
        for ring in p.wpolys:
            pts = to_pixels(ring, z, ox, oy)
            if len(pts) >= 3:
                pg.draw.polygon(s, r._fill[p.iso], pts)
    lw = 2 if z >= 4.0 else 1
    for kind in (mr.K_INNER, mr.K_BORDER, mr.K_COAST):
        for ai, a in enumerate(r.arcs):
            if r._kind[ai] == kind and len(a.pts) >= 2:
                col = r._inner[ai] if kind == mr.K_INNER else (mr.COAST if kind == mr.K_COAST else mr.BORDER)
                pg.draw.lines(s, col, False, to_pixels(a.pts, z, ox, oy), lw)
    return s


def main():
    st = GameState.new(CS, PT, GEO.adj, seed=3, geo=GEO)
    cam = Camera(1280, 720)
    r = mr.MapRenderer(PROVS, cam, pg.font.Font())
    r.rebuild(st)
    screen = pg.Surface((1280, 720))
    r._tiles.clear()

    # 1. тайлы = прямая отрисовка; стоимость заливки ограничена
    rng = random.Random(4)
    worst_frame = 0.0
    for zoom in (2.0, 4.0, 8.0):
        cam.zoom = zoom
        for _ in range(3):
            c = rng.choice(PROVS)
            cam.focus(*c.center_world, zoom)
            r._tiles.clear(); r._base = None
            pg_shim.STATS.update(polys=0, cost=0, max_cost=0)
            t0 = time.perf_counter()
            frames = 0
            while True:
                frames += 1
                t1 = time.perf_counter()
                r.draw(screen, st, None, None)
                worst_frame = max(worst_frame, time.perf_counter() - t1)
                if not _missing(r, screen, cam) and frames > 1:
                    break
                assert frames < 200, "тайлы не достраиваются"
                cam.t_zoom = 0.0
            base = pg.Surface((1280, 720))
            assert r._compose_tiles(base), "все тайлы должны быть готовы"        # только карта, без подписей
            ref = reference(r, st, cam, (1280, 720))
            a, b = base.img.convert("RGB"), ref.img
            from PIL import ImageChops
            d = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 3 else 0)   # ±3: полосы градиента океана
            diff = sum(1 for v in d.tobytes() if v)
            frac = diff / (1280 * 720)
            assert frac < 0.002, f"zoom {zoom}: отличается {frac:.4%} пикселей"
            assert pg_shim.STATS["max_cost"] < 2_000_000, pg_shim.STATS
    print(f"1: тайловый кадр совпадает с прямой отрисовкой (≤0.2% пикселей); худший кадр рендера {worst_frame * 1000:.0f} мс "
          f"(Pillow; в SDL заметно быстрее)")

    # 2. бюджет: пока тайлов нет, кадр не строит больше бюджета и рисует запасной кэш
    cam.focus(1800, 450, 6.0)
    r._tiles.clear(); r._base = None
    r.draw(screen, st, None, None)                        # только что менялся масштаб: тайлы откладываются
    assert len(r._tiles) == 0, "во время зума новые тайлы не строятся (показывается кэш мира)"
    cam.t_zoom = 0.0                                      # зум «осел»
    t0 = time.perf_counter()
    r.draw(screen, st, None, None)
    first = time.perf_counter() - t0
    n1 = len(r._tiles)
    assert 1 <= n1 < 24, "за один кадр должна строиться только часть тайлов (бюджет времени)"
    print(f"2: во время зума тайлы не строятся; затем {n1} тайлов за кадр ({first * 1000:.0f} мс), остальные — в следующих")

    # 3. выбор региона через растровый индекс совпадает с точным перебором
    cam.focus(1800, 450, 1.0)
    bad = tot = 0
    for _ in range(600):
        pos = (rng.randrange(1280), rng.randrange(720))
        got = r.province_at(pos)
        wx, wy = cam.screen_to_world(*pos)
        from map.province import world_to_lonlat
        lon, lat = world_to_lonlat(wx, wy)
        exact = next((p.iso for p in r.order if p.contains(lon, lat)), None)
        exact_top = None
        for p in r.order:
            if p.contains(lon, lat):
                exact_top = p.iso
        tot += 1
        bad += 0 if got in (exact_top, exact) else 1
    assert bad / tot < 0.02, f"индекс регионов расходится в {bad}/{tot}"
    print(f"3: province_at совпадает с точным тестом в {100 - 100 * bad / tot:.1f}% точек")

    # 4. смена владельца/оккупации перерисовывает только затронутое
    rid = next(x for x in GEO.adj if x.startswith("FRA"))
    nb = GEO.adj[rid][0]
    cam.focus(*r.by_iso[rid].center_world, 4.0)
    cam.t_zoom = 0.0
    r._tiles.clear(); r._base = None
    for _ in range(60):
        r.draw(screen, st, None, None)
        if not _missing(r, screen, cam):
            break
    n0 = len(r._tiles)
    st.set_ctrl(rid, st.owner[nb] if st.owner[nb] != st.owner[rid] else "DEU")
    rev0 = r._rev
    r.draw(screen, st, None, None)
    assert r._rev == rev0 + 1 and r._occ[rid] is not None, "оккупированный регион должен получить цвет оккупанта"
    assert 0 < len(r._tiles) < n0 or n0 > len(r._tiles) >= 0, "тайлы с затронутым регионом сброшены"
    assert len(r._tiles) < n0, "затронутые тайлы должны быть сброшены"
    print(f"4: смена контроля региона: тайлов было {n0}, осталось {len(r._tiles)} (остальные перестроятся лениво)")

    # 5. оверлеи армий, маршрутов, боёв, столиц
    dip.declare_war(st, "DEU", "POL")
    a = max(am.armies_of(st, "DEU"), key=lambda x: x.strength)
    tgt = next(x for x, o in st.owner.items() if o == "POL")
    ok, msg = am.order_move(st, "DEU", a.id, tgt)
    assert ok, msg
    st.battles = {a.region: 3}
    cam.focus(*r.by_iso[a.region].center_world, 3.0)
    r.eta_of = lambda ar: am.eta_days(st, ar)
    for _ in range(3):
        r.draw(screen, st, a.region, "DEU")
        r.draw_overlays(screen, st, "DEU", 0.03, a.id, tgt)
    assert r.army_at((1, 1)) is None
    found = [r.army_at((int((x0 + x1) / 2), int((y0 + y1) / 2))) for x0, y0, x1, y1, _ in r._army_rects]
    assert a.id in found, "плашка выбранной армии должна находиться по клику"
    cam.focus(1800, 450, 0.7)
    r.draw(screen, st, None, "DEU")
    r.draw_overlays(screen, st, "DEU", 0.03, a.id, None)
    print("5: оверлеи армий, маршрутов, боёв и столиц рисуются; клик по плашке находит армию")

    # 6. при ошибке тайлов рендерер откатывается на кэш мира и не роняет кадр
    r._tiles_ok = True
    orig = r._render_tile
    r._render_tile = lambda *a_, **k_: (_ for _ in ()).throw(RuntimeError("тест"))
    cam.focus(1800, 450, 5.0)
    cam.t_zoom = 0.0
    r._tiles.clear(); r._base = None
    r.draw(screen, st, None, None)
    assert r._tiles_ok is False
    r._render_tile = orig
    print("6: сбой тайлового рендера → безопасный откат на кэш мира")
    print("RENDER OK")


if __name__ == "__main__":
    main()
