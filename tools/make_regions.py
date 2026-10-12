"""Делит страны карты на регионы (игровые провинции) и пишет src/data/regions.json.

Регион — игровая единица территории: по регионам двигаются армии, их захватывают и передают при мире.
Разбиение — диаграмма Вороного от зафиксированных центров (src/data/region_seeds.json): так id регионов
(например RUS-07) и их положение остаются стабильными при смене разрешения карты (110m → 50m).
Центры считаются один раз (k-means по точкам внутри страны, нужен numpy) и коммитятся в репозиторий;
дальше геометрия пересоздаётся чистым Python из любого provinces.geojson.

    python tools/make_regions.py            # использует region_seeds.json (создаёт, если его нет)
    python tools/make_regions.py --reseed   # пересчитать центры
Названия регионов условные («Россия: Северо-запад»): это не административное деление.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from config import CAPITALS_PATH, COUNTRIES_PATH, DATA_DIR  # noqa: E402
from map.map_loader import load_provinces  # noqa: E402
from map.province import Province, point_in_ring, ring_area  # noqa: E402

SEEDS_PATH = DATA_DIR / "region_seeds.json"
REGIONS_PATH = DATA_DIR / "regions.json"
AREA_PER_REGION = 36.0       # «экваториальных» кв. градусов на регион
MAX_REGIONS = 24
EPS = 1.234567e-9
Pt = tuple[float, float]


# ---------- отсечение полигона полуплоскостью (корректно для вогнутых, с разбиением на части) ----------
def clip_half(poly: list[Pt], a: float, b: float, c: float) -> list[list[Pt]]:
    """poly ∩ {a·x + b·y ≥ c}. Возвращает список простых полигонов (без повторной первой точки)."""
    n = len(poly)
    d = [a * x + b * y - c for x, y in poly]
    if all(v > 0 for v in d):
        return [poly]
    if all(v <= 0 for v in d):
        return []
    start = next(i for i in range(n) if d[i] <= 0)
    chains: list[list[Pt]] = []
    cur: Optional[list[Pt]] = None
    for k in range(1, n + 1):
        i0, i1 = (start + k - 1) % n, (start + k) % n
        p0, p1, d0, d1 = poly[i0], poly[i1], d[i0], d[i1]
        in0, in1 = d0 > 0, d1 > 0
        if not in0 and in1:
            t = d0 / (d0 - d1)
            cur = [(p0[0] + t * (p1[0] - p0[0]), p0[1] + t * (p1[1] - p0[1])), p1]
        elif in0 and in1:
            assert cur is not None
            cur.append(p1)
        elif in0 and not in1:
            assert cur is not None
            t = d0 / (d0 - d1)
            cur.append((p0[0] + t * (p1[0] - p0[0]), p0[1] + t * (p1[1] - p0[1])))
            chains.append(cur)
            cur = None
    if not chains:
        return []
    dx, dy = -b, a                                        # направление линии отсечения
    ends = []
    for ci, ch in enumerate(chains):
        ends.append((ch[0][0] * dx + ch[0][1] * dy, 2 * ci))          # вход
        ends.append((ch[-1][0] * dx + ch[-1][1] * dy, 2 * ci + 1))    # выход
    ends.sort()
    partner: dict[int, int] = {}
    for k in range(0, len(ends) - 1, 2):
        partner[ends[k][1]] = ends[k + 1][1]
        partner[ends[k + 1][1]] = ends[k][1]
    out: list[list[Pt]] = []
    seen: set[int] = set()
    for c0 in range(len(chains)):
        if c0 in seen:
            continue
        ring: list[Pt] = []
        c = c0
        for _ in range(len(chains) + 1):
            if c in seen:
                break
            seen.add(c)
            ring.extend(chains[c])
            nxt = partner.get(2 * c + 1)
            if nxt is None or nxt % 2 == 1:
                break
            c = nxt // 2
        if len(ring) >= 3:
            out.append(ring)
    return out


def cell_pieces(ring: list[Pt], seeds: list[Pt], i: int) -> list[list[Pt]]:
    """Часть кольца, лежащая в ячейке Вороного центра i."""
    pieces = [ring[:-1] if ring[0] == ring[-1] else list(ring)]
    xi, yi = seeds[i]
    for j, (xj, yj) in enumerate(seeds):
        if j == i or not pieces:
            continue
        lo, hi = (i, j) if i < j else (j, i)                  # каноническая ориентация: общий шов у соседей идентичен
        (xa, ya), (xb, yb) = seeds[lo], seeds[hi]
        a, b = 2 * (xb - xa), 2 * (yb - ya)                   # точки ближе к hi: a·x + b·y ≥ c
        c = xb * xb + yb * yb - xa * xa - ya * ya + EPS
        if i == hi:
            sa, sb, sc = a, b, c
        else:
            sa, sb, sc = -a, -b, -c
        nxt: list[list[Pt]] = []
        for pc in pieces:
            nxt.extend(clip_half(pc, sa, sb, sc))
        pieces = nxt
    return pieces


# ---------- центры регионов ----------
def eq_area(p: Province) -> float:
    return sum(ring_area(r) * math.cos(math.radians(sum(y for _, y in r) / len(r))) for r in p.polygons)


def region_count(p: Province) -> int:
    return max(1, min(MAX_REGIONS, round(eq_area(p) / AREA_PER_REGION)))


def ring_shift(ring: list[Pt], ref_lon: float) -> float:
    """Сдвиг по долготе, чтобы кольцо через антимеридиан оказалось рядом с основной частью страны."""
    mean = sum(x for x, _ in ring) / len(ring)
    return 360.0 if mean < ref_lon - 180 else -360.0 if mean > ref_lon + 180 else 0.0


def shifted(ring: list[Pt], s: float) -> list[Pt]:
    return [(x + s, y) for x, y in ring] if s else ring


def compute_seeds(p: Province, n: int, rng_seed: int) -> list[Pt]:
    """k-means по случайным точкам внутри страны; центр = ближайшая выборочная точка (гарантированно внутри)."""
    import numpy as np
    rng = np.random.default_rng(rng_seed)
    big = max(p.polygons, key=ring_area)
    ref = sum(x for x, _ in big) / len(big)
    samples: list[Pt] = []
    areas = [ring_area(r) for r in p.polygons]
    total = sum(areas) or 1.0
    for r, ar in zip(p.polygons, areas):
        k = max(25, int(6000 * ar / total))
        sh = shifted(r, ring_shift(r, ref))
        xs, ys = [q[0] for q in sh], [q[1] for q in sh]
        px = rng.uniform(min(xs), max(xs), k * 6)
        py = rng.uniform(min(ys), max(ys), k * 6)
        ins = np.zeros(len(px), dtype=bool)
        j = len(sh) - 1
        for i2 in range(len(sh)):
            xi, yi, xj, yj = sh[i2][0], sh[i2][1], sh[j][0], sh[j][1]
            cond = ((yi > py) != (yj > py)) & (px < (xj - xi) * (py - yi) / (yj - yi + 1e-18) + xi)
            ins ^= cond
            j = i2
        pts = np.stack([px[ins], py[ins]], axis=1)[:k]
        if len(pts) == 0:
            pts = np.array([[sum(xs) / len(xs), sum(ys) / len(ys)]])
        samples.extend((float(a), float(b)) for a, b in pts)
    S = np.array(samples)
    kx = math.cos(math.radians(float(S[:, 1].mean())))
    W = S * np.array([kx, 1.0])
    n = min(n, len(W))
    cent = [W[int(rng.integers(len(W)))]]
    for _ in range(1, n):                                        # k-means++
        dist = np.min(np.stack([((W - c) ** 2).sum(1) for c in cent]), axis=0)
        cent.append(W[int(rng.choice(len(W), p=dist / dist.sum()))] if dist.sum() > 0 else W[0])
    C = np.array(cent)
    for _ in range(30):
        lab = np.argmin(((W[:, None, :] - C[None, :, :]) ** 2).sum(2), axis=1)
        for k in range(n):
            if (lab == k).any():
                C[k] = W[lab == k].mean(0)
    lab = np.argmin(((W[:, None, :] - C[None, :, :]) ** 2).sum(2), axis=1)
    seeds: list[Pt] = []
    for k in range(n):
        idx = np.where(lab == k)[0]
        if len(idx) == 0:
            continue
        best = idx[int(np.argmin(((W[idx] - C[k]) ** 2).sum(1)))]
        x = float(S[best][0])
        x = x - 360 if x > 180 else x + 360 if x < -180 else x
        seeds.append((round(x, 3), round(float(S[best][1]), 3)))
    return seeds


# ---------- имена ----------
DIRS = ["Восток", "Северо-восток", "Север", "Северо-запад", "Запад", "Юго-запад", "Юг", "Юго-восток"]


def name_regions(seeds: list[Pt], cname: str) -> list[str]:
    if len(seeds) == 1:
        return [cname]
    cx = sum(s[0] for s in seeds) / len(seeds)
    cy = sum(s[1] for s in seeds) / len(seeds)
    order = sorted(range(len(seeds)), key=lambda k: (seeds[k][0] - cx) ** 2 + (seeds[k][1] - cy) ** 2)
    labels = [""] * len(seeds)
    labels[order[0]] = "Центр"
    for k in order[1:]:
        ang = math.degrees(math.atan2(seeds[k][1] - cy, seeds[k][0] - cx)) % 360
        labels[k] = DIRS[int((ang + 22.5) % 360 // 45)]
    seen: dict[str, int] = {}
    for lab in labels:
        seen[lab] = seen.get(lab, 0) + 1
    cnt: dict[str, int] = {}
    names = []
    for lab in labels:
        if seen[lab] > 1:
            cnt[lab] = cnt.get(lab, 0) + 1
            lab = f"{lab} {'I' * 0}{cnt[lab]}"
        names.append(f"{cname}: {lab}")
    return names


def r4(pt: Pt) -> list[float]:
    return [round(pt[0], 4), round(pt[1], 4)]


def build(reseed: bool = False) -> dict:
    countries = json.loads(COUNTRIES_PATH.read_text(encoding="utf-8"))
    cname = {c["iso"]: c["name"] for c in countries}
    provs = load_provinces(known=set(cname))
    caps = json.loads(CAPITALS_PATH.read_text(encoding="utf-8")) if CAPITALS_PATH.exists() else {}
    seeds_all: dict[str, list[Pt]] = {}
    if SEEDS_PATH.exists() and not reseed:
        seeds_all = {k: [tuple(s) for s in v] for k, v in json.loads(SEEDS_PATH.read_text(encoding="utf-8")).items()}
    changed = False
    regions = []
    for p in sorted(provs, key=lambda q: q.iso):
        if p.iso not in seeds_all:
            n = region_count(p)
            one = [(round(p.centroid[0], 3), round(p.centroid[1], 3))]
            try:
                seeds_all[p.iso] = compute_seeds(p, n, sum(map(ord, p.iso))) if n > 1 else one
            except ImportError:                                  # нет numpy (CI): новая страна — одним регионом
                seeds_all[p.iso] = one
            changed = True
        seeds = seeds_all[p.iso]
        names = name_regions(seeds, cname.get(p.iso, p.name))
        big = max(p.polygons, key=ring_area)
        ref = sum(x for x, _ in big) / len(big)
        pieces: list[list[list[Pt]]] = [[] for _ in seeds]
        if len(seeds) == 1:
            pieces[0] = [list(r) for r in p.polygons]
        else:
            for ring in p.polygons:
                s = ring_shift(ring, ref)
                sh = shifted(ring, s)
                for i in range(len(seeds)):
                    for pc in cell_pieces(sh, seeds, i):
                        pieces[i].append([(x - s, y) for x, y in pc] if s else pc)
        holes_for: list[list[list[Pt]]] = [[] for _ in seeds]
        for h in p.holes:
            if len(seeds) == 1:
                holes_for[0].append(h)
            else:
                for i, prs in enumerate(pieces):
                    if any(point_in_ring(h[0][0], h[0][1], q) for q in prs):
                        holes_for[i].append(h)
                        break
        for i, prs in enumerate(pieces):
            if not prs:
                continue
            regions.append({
                "id": f"{p.iso}-{i + 1:02d}" if len(seeds) > 1 else p.iso,
                "c": p.iso, "name": names[i], "seed": list(seeds[i]),
                "poly": [[r4(q) for q in (pc + [pc[0]] if pc[0] != pc[-1] else pc)] for pc in prs],
                "holes": [[r4(q) for q in h] for h in holes_for[i]],
            })
    if changed or reseed:
        SEEDS_PATH.write_text(json.dumps({k: [list(s) for s in v] for k, v in sorted(seeds_all.items())},
                                         ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    cap_region: dict[str, str] = {}
    for r in regions:
        cap = caps.get(r["c"])
        if cap and r["c"] not in cap_region:
            if any(point_in_ring(cap[0], cap[1], ring) for ring in r["poly"]):
                cap_region[r["c"]] = r["id"]
    for r in regions:
        r["cap"] = cap_region.get(r["c"]) == r["id"]
    for iso in {r["c"] for r in regions} - set(cap_region):          # столица на границе/в море: ближайший центр
        cap = caps.get(iso) or next(r["seed"] for r in regions if r["c"] == iso)
        best = min((r for r in regions if r["c"] == iso),
                   key=lambda r: (r["seed"][0] - cap[0]) ** 2 + (r["seed"][1] - cap[1]) ** 2)
        best["cap"] = True
    return {"version": 1, "area_per_region": AREA_PER_REGION, "regions": regions}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reseed", action="store_true")
    args = ap.parse_args()
    data = build(args.reseed)
    REGIONS_PATH.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    n = len(data["regions"])
    print(f"регионов: {n} → {REGIONS_PATH} ({REGIONS_PATH.stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    main()
