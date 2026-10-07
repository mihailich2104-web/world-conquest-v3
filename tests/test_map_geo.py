"""Карта без графики: проекция, столицы, границы по общим рёбрам (слияние при захвате), анклавы, плотные данные."""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from config import COUNTRIES_PATH, PROVINCES_PATH  # noqa: E402
from core import war  # noqa: E402
from core.game_state import GameState  # noqa: E402
from map.borders import arc_kind, build_arcs, border_point, territory_edge  # noqa: E402
from map.map_loader import build_adjacency, load_provinces, load_world  # noqa: E402
from map.province import WORLD_H, WORLD_W, lonlat_to_world, world_to_lonlat  # noqa: E402


def ring(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


def feat(iso, name, geom, sov=None):
    return {"type": "Feature", "properties": {"iso": iso, "name": name, "continent": "X", "sov": sov or iso},
            "geometry": geom}


def main():
    # --- проекция ---
    for lon, lat in [(0, 0), (37.6, 55.7), (-175, 70), (179, -50), (13.4, 52.5)]:
        x, y = lonlat_to_world(lon, lat)
        lo, la = world_to_lonlat(x, y)
        assert abs(lo - lon) < 1e-6 and abs(la - lat) < 1e-6
        assert 0 <= x <= WORLD_W and -1 <= y <= WORLD_H + 1
    # Миллер: Москва севернее Берлина, масштаб растёт к полюсам
    assert lonlat_to_world(0, 60)[1] < lonlat_to_world(0, 50)[1]

    # --- настоящие столицы и слияние границ при захвате (реальные данные) ---
    data = json.loads(COUNTRIES_PATH.read_text(encoding="utf-8"))
    provs, adj, fb = load_world(data)
    by = {p.iso: p for p in provs}
    assert not fb
    caps = {p.iso: p.capital for p in provs}
    assert all(caps.values()), [i for i, c in caps.items() if not c]
    assert abs(caps["DEU"][0] - 13.41) < 0.1 and abs(caps["DEU"][1] - 52.52) < 0.1      # Берлин
    assert abs(caps["JPN"][0] - 139.69) < 0.1 and abs(caps["RUS"][1] - 55.76) < 0.1      # Токио, Москва
    assert abs(caps["BRA"][1] + 15.79) < 0.1                                             # Бразилиа, не центр страны
    arcs = build_arcs(provs)
    owner = {p.iso: p.iso for p in provs}
    pair = lambda a, b: [x for x in arcs if {x.iso, x.nb} == {a, b}]            # noqa: E731
    shown = lambda a, b: sum(1 for x in pair(a, b) if arc_kind(x, owner) == "border")   # noqa: E731
    assert shown("DEU", "POL") >= 1 and shown("DEU", "FRA") >= 1
    coast0 = sum(1 for x in arcs if arc_kind(x, owner) == "coast")
    # DEU захватывает POL: граница DEU–POL исчезает, граница POL–соседи становится границей DEU
    st = GameState.new(data, [(p.iso, p.name, p.continent) for p in provs], adj, seed=1)
    war.annex(st, "DEU", "POL")
    assert st.owner["POL"] == "DEU" and not st.countries["POL"].alive
    assert shown("DEU", "POL") >= 1                                              # (старый owner ещё без обновления)
    owner = dict(st.owner)
    assert shown("DEU", "POL") == 0, "граница внутри одной державы должна стираться"
    assert sum(1 for x in pair("POL", "CZE") if arc_kind(x, owner) == "border") >= 1      # новая граница DEU–CZE
    assert sum(1 for x in arcs if arc_kind(x, owner) == "coast") == coast0                 # берег не стирается
    # подсветка территории: у DEU внешний контур включает бывшую границу POL–CZE, но не DEU–POL
    assert any(territory_edge(x, owner, "DEU") for x in pair("POL", "CZE"))
    assert not any(territory_edge(x, owner, "DEU") for x in pair("DEU", "POL"))
    bp = border_point(arcs, {p.iso: p.iso for p in provs}, "DEU", "FRA", by["DEU"].capital_world, by["FRA"].capital_world)
    assert bp is not None and by["DEU"].wbbox[0] - 50 < bp[0] < by["FRA"].wbbox[2] + 400

    # --- анклавы (дыры) и территории суверена: синтетические данные ---
    geo = {"type": "FeatureCollection", "features": [
        feat("AAA", "A", {"type": "Polygon", "coordinates": [ring(0, 0, 10, 10), ring(4, 4, 6, 6)[::-1]]}),
        feat("BBB", "B", {"type": "Polygon", "coordinates": [ring(4, 4, 6, 6)]}),
        feat("TTT", "T (территория A)", {"type": "Polygon", "coordinates": [ring(20, 0, 22, 2)]}, sov="AAA"),
        feat("CCC", "C", {"type": "Polygon", "coordinates": [ring(10, 0, 14, 10)]}),
    ]}
    with tempfile.TemporaryDirectory() as td:
        pth = Path(td) / "p.geojson"
        pth.write_text(json.dumps(geo), encoding="utf-8")
        ps = load_provinces(pth, known={"AAA", "BBB", "CCC"})
    pi = {p.iso: p for p in ps}
    assert set(pi) == {"AAA", "BBB", "CCC"}, set(pi)                              # территория T влилась в суверена A
    assert len(pi["AAA"].polygons) == 2 and len(pi["AAA"].holes) == 1
    arcs2 = build_arcs(ps)
    own = {"AAA": "AAA", "BBB": "BBB", "CCC": "CCC"}
    pr = lambda a, b: [x for x in arcs2 if {x.iso, x.nb} == {a, b}]                 # noqa: E731
    assert pr("AAA", "BBB") and pr("AAA", "CCC")                                     # анклав B сопоставлен с дырой A
    assert sum(1 for x in pr("AAA", "BBB") if arc_kind(x, own) == "border") >= 1
    own["BBB"] = "AAA"
    assert sum(1 for x in pr("AAA", "BBB") if arc_kind(x, own) == "border") == 0     # анклав слился с A
    assert not [x for x in arcs2 if x.nb == "#seam"]

    # --- плотные данные (как 50m): время загрузки, смежности и границ ---
    src = json.loads(PROVINCES_PATH.read_text(encoding="utf-8"))

    def densify(coords, k=8):
        out = []
        for i in range(len(coords) - 1):
            (x0, y0), (x1, y1) = coords[i][:2], coords[i + 1][:2]
            out += [[x0 + (x1 - x0) * j / k, y0 + (y1 - y0) * j / k] for j in range(k)]
        return out + [coords[-1][:2]]

    for f in src["features"]:
        g = f["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        newp = [[densify(r) for r in poly] for poly in polys]
        f["geometry"] = {"type": "Polygon", "coordinates": newp[0]} if g["type"] == "Polygon" else \
            {"type": "MultiPolygon", "coordinates": newp}
    with tempfile.TemporaryDirectory() as td:
        pth = Path(td) / "dense.geojson"
        pth.write_text(json.dumps(src), encoding="utf-8")
        t0 = time.time()
        dp = load_provinces(pth, known={c["iso"] for c in data})
        t1 = time.time()
        dadj = build_adjacency(dp)
        t2 = time.time()
        darcs = build_arcs(dp)
        t3 = time.time()
    nv = sum(len(r) for p in dp for r in p.polygons)
    print(f"плотная карта: {nv} вершин; загрузка {t1 - t0:.2f} c, соседи {t2 - t1:.2f} c, границы {t3 - t2:.2f} c, "
          f"дуг {len(darcs)}")
    assert (t3 - t0) < 25, "загрузка плотной карты слишком медленная"
    assert any("FRA" in v for v in dadj["DEU"])
    print("MAP GEO OK")


if __name__ == "__main__":
    main()
