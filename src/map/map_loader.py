"""Загрузка карты: GeoJSON (чистый json), настоящие столицы, граф соседей, запасная карта."""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Iterator, Optional

from config import CAPITALS_PATH, NON_PLAYABLE, PROVINCES_PATH
from .province import Province, Ring

log = logging.getLogger(__name__)
CELL = 0.4  # размер ячейки (градусы) для поиска общих границ


def _rings_from_geom(geom: dict[str, Any]) -> tuple[list[Ring], list[Ring]]:
    """(внешние кольца, внутренние кольца-анклавы) из GeoJSON-геометрии."""
    if geom["type"] == "Polygon":
        polys = [geom["coordinates"]]
    elif geom["type"] == "MultiPolygon":
        polys = geom["coordinates"]
    else:
        return [], []
    outer = [[(p[0], p[1]) for p in poly[0]] for poly in polys if poly]
    holes = [[(p[0], p[1]) for p in ring] for poly in polys for ring in poly[1:]]
    return outer, holes


def _polys_from_geojson_geom(geom: dict[str, Any]) -> list[Ring]:
    """Внешние кольца полигонов из GeoJSON-геометрии."""
    return _rings_from_geom(geom)[0]


def _iter_features(path: Path) -> Iterator[tuple[dict[str, Any], list[Ring], list[Ring]]]:
    """Читает признаки GeoJSON напрямую через json (GeoPandas не нужен)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for f in data["features"]:
        outer, holes = _rings_from_geom(f["geometry"])
        yield f["properties"], outer, holes


def load_capitals(path: Path = CAPITALS_PATH) -> dict[str, tuple[float, float]]:
    """Настоящие координаты столиц: ISO3 → (lon, lat)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {k: (float(v[0]), float(v[1])) for k, v in raw.items()}
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        log.warning("capitals.json не найден — столицы будут в центрах стран")
        return {}


def load_provinces(path: Path = PROVINCES_PATH, known: Optional[set[str]] = None) -> list[Province]:
    """Загружает провинции из provinces.geojson (без Антарктики и т.п.).

    known — ISO стран из countries.json. Территория, которой нет в known, но чей суверен (props["sov"])
    есть в known, присоединяется к суверену как заморская часть (так 50m-карта не засоряется микро-территориями).
    """
    entries: list[tuple[str, dict[str, Any], list[Ring], list[Ring]]] = []
    for props, rings, holes in _iter_features(path):
        iso = str(props["iso"])
        if iso in NON_PLAYABLE or not rings:
            continue
        sov = props.get("sov")
        if known and iso not in known and sov and sov in known and sov != iso:
            iso = str(sov)
            props = {**props, "iso": iso}
        entries.append((iso, props, rings, holes))
    provs: dict[str, Province] = {}
    for iso, props, rings, holes in entries:
        if iso in provs:                      # дубликаты ISO / территории суверена — объединяем геометрию
            provs[iso].polygons.extend(rings)
            provs[iso].holes.extend(holes)
        else:
            p = Province(iso, str(props["name"]), str(props.get("continent", "")), list(rings))
            p.holes = list(holes)
            provs[iso] = p
    caps = load_capitals()
    out = []
    for p in provs.values():
        p.finalize()
        p.capital = caps.get(p.iso)
        out.append(p)
    return out


def build_adjacency(provs: list[Province]) -> dict[str, list[str]]:
    """Соседи = провинции, чьи вершины лежат в соседних ячейках сетки (общая граница или узкий пролив)."""
    own: dict[str, set[tuple[int, int]]] = {}
    cells: dict[tuple[int, int], set[str]] = {}
    for p in provs:
        cs = {(int(math.floor(lon / CELL)), int(math.floor(lat / CELL))) for ring in p.polygons for lon, lat in ring}
        own[p.iso] = cs
        for c in cs:
            cells.setdefault(c, set()).add(p.iso)
    adj: dict[str, set[str]] = {p.iso: set() for p in provs}
    for p in provs:
        near: set[tuple[int, int]] = set()
        for cx, cy in own[p.iso]:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    near.add((cx + dx, cy + dy))
        for c in near:
            adj[p.iso] |= cells.get(c, set())
        adj[p.iso].discard(p.iso)
    return {k: sorted(v) for k, v in adj.items()}


def build_fallback(countries: list[dict[str, Any]]) -> list[Province]:
    """Запасная карта-сетка: используется, если provinces.geojson не создан."""
    cols = 16
    rows = math.ceil(len(countries) / cols)
    w, h = 360.0 / cols, 140.0 / rows
    out = []
    for i, c in enumerate(countries):
        x0, y1 = -180 + (i % cols) * w, 80 - (i // cols) * h
        ring: Ring = [(x0, y1), (x0 + w, y1), (x0 + w, y1 - h), (x0, y1 - h), (x0, y1)]
        out.append(Province(c["iso"], c["name"], c.get("continent", ""), [ring]).finalize())
    log.warning("provinces.geojson не найден — использую запасную карту-сетку "
                "(запустите tools/prepare_map.py)")
    return out


def load_world(countries: list[dict[str, Any]]) -> tuple[list[Province], dict[str, list[str]], bool]:
    """Возвращает (провинции, смежность, is_fallback)."""
    try:
        provs = load_provinces(known={c["iso"] for c in countries})
        if not provs:
            raise ValueError("пустая карта")
        fallback = False
    except (OSError, ValueError, KeyError):
        log.exception("Не удалось загрузить карту")
        provs, fallback = build_fallback(countries), True
    return provs, build_adjacency(provs), fallback
