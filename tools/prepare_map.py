"""Готовит src/data/provinces.geojson из Natural Earth «Admin 0 – Countries».

По умолчанию берётся детализация 50m (точные границы и береговая линия), при неудаче — 110m;
если сеть недоступна, остаётся уже лежащий в проекте файл. Работает только на стандартной библиотеке.

    python tools/prepare_map.py              # создать, если файла нет
    python tools/prepare_map.py --force      # скачать заново (в CI)
    python tools/prepare_map.py --detail 110 # выбрать детализацию
"""
from __future__ import annotations

import json
import logging
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from config import PROVINCES_PATH  # noqa: E402

log = logging.getLogger("prepare_map")
BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_{d}m_admin_0_countries.geojson"
MIRROR = "https://cdn.jsdelivr.net/gh/nvkelso/natural-earth-vector@master/geojson/ne_{d}m_admin_0_countries.geojson"
SKIP = {"ATA", "ATF"}                 # Антарктида и Антарктические территории Франции
MIN_ISLAND = 0.004                    # мелкие острова (кв. градусов) отбрасываются, главный остров всегда остаётся
DECIMALS = 3                          # ≈ 100 м; общие границы соседей остаются идентичными


def download(detail: int) -> dict[str, Any]:
    """Скачивает GeoJSON нужной детализации, пробуя зеркала."""
    last: Exception | None = None
    for tpl in (BASE, MIRROR):
        url = tpl.format(d=detail)
        try:
            log.info("Загрузка %s", url)
            req = urllib.request.Request(url, headers={"User-Agent": "world-conquest/1.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:  # сеть/формат: пробуем следующее зеркало
            last = exc
            log.warning("Не удалось: %s", exc)
    raise RuntimeError(f"Natural Earth {detail}m недоступен: {last}")


def _area(ring: list[list[float]]) -> float:
    s = 0.0
    for i in range(len(ring) - 1):
        s += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
    return abs(s) / 2.0


def _round_ring(ring: list[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for p in ring:
        q = [round(p[0], DECIMALS), round(p[1], DECIMALS)]
        if not out or q != out[-1]:
            out.append(q)
    if out and out[0] != out[-1]:
        out.append(out[0])
    return out


def _iso(props: dict[str, Any]) -> str | None:
    p = {k.upper(): v for k, v in props.items()}
    iso = p.get("ISO_A3")
    if iso in (None, "-99", ""):
        iso = p.get("ADM0_A3") or p.get("ISO_A3_EH")
    return None if iso in (None, "-99", "") else str(iso)


def convert(src: dict[str, Any]) -> dict[str, Any]:
    """NE GeoJSON → компактный формат игры: {iso, name, continent, sov} + внешние/внутренние кольца."""
    sovereign_iso: dict[str, str] = {}            # название суверена → его ISO
    parsed = []
    for f in src["features"]:
        props = {k.upper(): v for k, v in f["properties"].items()}
        iso = _iso(props)
        if iso is None or iso in SKIP or not f.get("geometry"):
            continue
        parsed.append((iso, props, f["geometry"]))
        if props.get("ADMIN") and props.get("ADMIN") == props.get("SOVEREIGNT"):
            sovereign_iso[str(props["ADMIN"])] = iso
    feats = []
    for iso, props, geom in parsed:
        polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"] \
            if geom["type"] == "MultiPolygon" else []
        polys = [[_round_ring(r) for r in poly] for poly in polys if poly]
        polys = [pl for pl in polys if len(pl[0]) >= 4]
        if not polys:
            continue
        biggest = max(polys, key=lambda pl: _area(pl[0]))
        polys = [pl for pl in polys if pl is biggest or _area(pl[0]) >= MIN_ISLAND]
        polys = [[pl[0]] + [r for r in pl[1:] if len(r) >= 4] for pl in polys]
        sov = sovereign_iso.get(str(props.get("SOVEREIGNT") or ""), iso)
        feats.append({"type": "Feature",
                      "properties": {"iso": iso, "name": props.get("NAME") or props.get("ADMIN") or iso,
                                     "continent": props.get("CONTINENT"), "sov": sov},
                      "geometry": {"type": "Polygon", "coordinates": polys[0]} if len(polys) == 1
                      else {"type": "MultiPolygon", "coordinates": polys}})
    return {"type": "FeatureCollection", "features": feats}


def main() -> None:
    """Скачивает и сохраняет карту; при неудаче сохраняет существующий файл."""
    logging.basicConfig(level=logging.INFO)
    force = "--force" in sys.argv
    if PROVINCES_PATH.exists() and not force:
        log.info("%s уже есть — пропускаю", PROVINCES_PATH)
        return
    details = [50, 110]
    if "--detail" in sys.argv:
        details = [int(sys.argv[sys.argv.index("--detail") + 1])]
    for d in details:
        try:
            out = convert(download(d))
        except Exception:
            log.exception("Детализация %dm не получена", d)
            continue
        if len(out["features"]) < 100:
            log.error("Подозрительно мало стран (%d) — пропускаю %dm", len(out["features"]), d)
            continue
        PROVINCES_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROVINCES_PATH.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        log.info("Сохранено %d стран (%dm) → %s", len(out["features"]), d, PROVINCES_PATH)
        return
    if PROVINCES_PATH.exists():
        log.warning("Скачать не удалось — оставляю имеющуюся карту %s", PROVINCES_PATH)
        return
    raise SystemExit(1)


if __name__ == "__main__":
    main()
