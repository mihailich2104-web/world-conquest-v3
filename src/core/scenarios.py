"""Исторические сценарии: загрузка, проверка данных и применение к новой партии (без pygame).

Сценарий — JSON в src/data/scenarios. Он не переписывает ядро игры: задаёт дату, владельцев регионов
правилами (страны целиком, регионы по рамке координат центров, отдельные регионы), переименования и стартовые
показатели держав, союзы, войны и эпоху (недоступные ветки технологий). Новые даты добавляются новым файлом.
Границы строятся из регионов современной карты, поэтому для исторических дат они приблизительны — сценарий
явно помечает это (поле approx), и интерфейс показывает пометку.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Optional

from config import DATA_DIR
from . import armies, diplomacy, stats, war as warmod
from .country import Country
from .game_state import GameState, default_entry

log = logging.getLogger(__name__)
SCENARIO_DIR: Path = DATA_DIR / "scenarios"
SCHEMA_VERSION = 1


class ScenarioError(ValueError):
    """Сценарий повреждён или не подходит к карте."""


def _all_isos() -> set[str]:
    try:
        from config import COUNTRIES_PATH
        return {c["iso"] for c in json.loads(Path(COUNTRIES_PATH).read_text(encoding="utf-8"))}
    except (OSError, ValueError, KeyError):
        return set()


def _read(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ScenarioError(f"{path.name}: {exc}") from exc


def list_scenarios(directory: Path = SCENARIO_DIR) -> list[dict[str, Any]]:
    """Краткие описания всех доступных сценариев (битые файлы пропускаются с записью в журнал)."""
    out = []
    for p in sorted(directory.glob("*.json")):
        try:
            d = _read(p)
            out.append({"id": d["id"], "name": d["name"], "date": d["date"], "desc": d.get("desc", ""),
                        "approx": bool(d.get("approx")), "notes": d.get("notes", ""), "era": d.get("era", "modern"),
                        "year": d["date"][0]})
        except (ScenarioError, KeyError, IndexError, TypeError):
            log.exception("Сценарий %s пропущен", p.name)
    return sorted(out, key=lambda x: -x["year"])


def load_scenario(sid: str, directory: Path = SCENARIO_DIR) -> dict[str, Any]:
    """Читает сценарий по id."""
    for p in directory.glob("*.json"):
        d = _read(p)
        if d.get("id") == sid:
            return d
    raise ScenarioError(f"Сценарий «{sid}» не найден")


def validate(data: dict[str, Any], state: GameState, tech_ids: Optional[set[str]] = None) -> list[str]:
    """Список ошибок данных сценария относительно карты (пустой — всё корректно)."""
    err: list[str] = []
    geo = state.geo
    modern = set(geo.country_of.values()) | _all_isos()      # страны без полигонов на карте — допустимы (пустое правило)
    try:
        date(*data["date"])
    except (KeyError, TypeError, ValueError):
        err.append("некорректная дата")
    if data.get("v", SCHEMA_VERSION) > SCHEMA_VERSION:
        err.append("версия формата новее поддерживаемой")
    known = set(state.countries) | {c["id"] for c in data.get("countries", ()) if c.get("new")}
    for i, r in enumerate(data.get("rules", ())):
        if r.get("to") not in known:
            err.append(f"правило {i}: неизвестный владелец {r.get('to')}")
        if r.get("ctrl") and r["ctrl"] not in known:
            err.append(f"правило {i}: неизвестный контролёр {r['ctrl']}")
        for c in r.get("c", ()):
            if c not in modern:
                err.append(f"правило {i}: нет страны {c}")
        for rid in r.get("regions", ()):
            if rid not in state.owner:
                err.append(f"правило {i}: нет региона {rid}")
        b = r.get("box")
        if b is not None and (len(b) != 4 or b[0] > b[2] or b[1] > b[3]):
            err.append(f"правило {i}: некорректная рамка")
        if not (r.get("c") or r.get("regions")):
            err.append(f"правило {i}: не задано, к чему оно относится")
    for pair in list(data.get("alliances", ())) + list(data.get("pacts", ())):
        if len(pair) != 2 or any(x not in known for x in pair):
            err.append(f"некорректная пара {pair}")
    for w in data.get("wars", ()):
        if any(x not in known for x in w.get("attackers", []) + w.get("defenders", [])) or not w.get("attackers") \
                or not w.get("defenders"):
            err.append(f"некорректная война {w}")
    if tech_ids is not None:
        for t in data.get("techs", ()):
            if t not in tech_ids:
                err.append(f"неизвестная технология {t}")
    for c in data.get("countries", ()):
        if not c.get("id") or (not c.get("new") and c["id"] not in state.countries):
            err.append(f"страна {c.get('id')}: нет на карте (укажите new)")
    return err


def apply_scenario(state: GameState, data: dict[str, Any], tech_ids: Optional[set[str]] = None,
                   difficulty: str = "normal", ai_activity: int = 1) -> None:
    """Применяет сценарий к только что созданной партии (до выбора страны игроком)."""
    errs = validate(data, state, tech_ids)
    if errs:
        raise ScenarioError("; ".join(errs[:6]))
    geo = state.geo
    d = date(*data["date"])
    state.date = d
    state.scenario = {"id": data["id"], "name": data["name"], "era": data.get("era", "modern"),
                      "approx": bool(data.get("approx")), "start": d.toordinal(), "difficulty": difficulty,
                      "ai_activity": ai_activity, "v": SCHEMA_VERSION}
    dflt = data.get("default", {})
    entries = {c["id"]: c for c in data.get("countries", ())}
    original = {iso: (c.name, c.capital) for iso, c in state.countries.items()}
    # 1. масштаб показателей всех стран эпохи
    for c in state.countries.values():
        c.population *= dflt.get("pop_mult", 1.0)
        c.gdp *= dflt.get("gdp_mult", 1.0)
        c.manpower *= dflt.get("pop_mult", 1.0)
        c.army = max(1, int(round(c.army * dflt.get("army_mult", 1.0))))
        c.navy = int(c.army * 0.2)
        c.airforce = 0 if "air" in data.get("locked", ()) else int(c.army * dflt.get("air_mult", 0.15))
        c.factories = max(1, int(c.gdp ** 0.4 / 3))
        c.locked = list(data.get("locked", ()))
        if dflt.get("fallback_fmt") and c.iso not in entries:
            c.name = dflt["fallback_fmt"].format(name=c.name)
    for f in data.get("flat", ()):                       # абсолютные значения для групп стран (напр. земли без государств)
        for iso in f["c"]:
            c = state.countries.get(iso)
            if c is None:
                continue
            c.gdp = float(f.get("gdp", c.gdp))
            c.population = float(f.get("pop", c.population))
            c.manpower = c.population * 50
            c.army = int(f.get("army", c.army))
            c.navy = int(c.army * 0.2)
            c.factories = max(1, int(c.gdp ** 0.4 / 3))
    # 2. новые страны и переопределения
    for iso, e in entries.items():
        if e.get("new"):
            ent = default_entry(iso, e.get("name", iso), e.get("continent", ""))
            ent.update({k: e[k] for k in ("population", "gdp", "ideology", "color", "stability") if k in e})
            c = Country.from_json(ent)
            c.iso = iso
            c.army = max(1, int(round(c.army * dflt.get("army_mult", 1.0))))
            c.locked = list(data.get("locked", ()))
            c.alive = False
            state.countries[iso] = c
    # 3. владельцы регионов по правилам
    moved: dict[str, dict[str, int]] = {}
    for r in data.get("rules", ()):
        ids = set(r.get("regions", ()))
        for mc in r.get("c", ()):
            ids.update(geo.regions_of(mc))
        box = r.get("box")
        for rid in sorted(ids):
            if box:
                x, y = geo.center[rid]
                if not (box[0] <= x <= box[2] and box[1] <= y <= box[3]):
                    continue
            src = geo.country_of.get(rid, rid)
            state.owner[rid] = r["to"]
            if r.get("ctrl") and r["ctrl"] != r["to"]:
                state.ctrl[rid] = r["ctrl"]
            else:
                state.ctrl.pop(rid, None)
            if src != r["to"]:
                moved.setdefault(src, {})[r["to"]] = moved.get(src, {}).get(r["to"], 0) + 1
    # 4. перенос экономики вместе с землёй (как при аннексии)
    for src, to_map in moved.items():
        S = state.countries[src]
        total = len(geo.regions_of(src)) or 1
        for to, n in to_map.items():
            W = state.countries[to]
            share = min(1.0, n / total)
            W.gdp += S.gdp * 0.6 * share
            W.population += S.population * share
            W.manpower += S.manpower * 0.5 * share
            W.factories += int(S.factories * share) // 2
            if to not in entries or "army" not in entries[to]:
                W.army += int(S.army * share) // 3
    # 5. явные показатели держав
    for iso, e in entries.items():
        c = state.countries[iso]
        for k in ("name", "ideology", "stability"):
            if k in e:
                setattr(c, k, e[k])
        if "stability" in e:
            c.base_stability = float(e["stability"])
        if "color" in e:
            c.color = tuple(e["color"])  # type: ignore[assignment]
        if "pop" in e:
            c.population, c.manpower = float(e["pop"]), float(e["pop"]) * 50
        if "gdp" in e:
            c.gdp = float(e["gdp"])
        if "army" in e:
            c.army = int(e["army"])
        c.navy = int(e.get("navy", c.army * 0.2))
        c.airforce = 0 if "air" in c.locked else int(e.get("air", c.army * 0.15))
        c.factories = max(1, int(e.get("factories", c.gdp ** 0.4 / 3)))
        c.fort = int(e.get("fort", c.fort))
    # 6. существование, столицы, казна
    have: dict[str, int] = {}
    for o in state.owner.values():
        have[o] = have.get(o, 0) + 1
    for iso, c in state.countries.items():
        c.alive = have.get(iso, 0) > 0
        if not c.alive:
            c.army = c.navy = c.airforce = 0
            continue
        e = entries.get(iso, {})
        owned = [r for r, o in state.owner.items() if o == iso]
        cap = e.get("cap")
        if not cap and e.get("cap_ll"):
            cap = min(owned, key=lambda r: (geo.center[r][0] - e["cap_ll"][0]) ** 2 + (geo.center[r][1] - e["cap_ll"][1]) ** 2)
        if not cap:
            cap = geo.cap_region.get(iso) if state.owner.get(geo.cap_region.get(iso, "")) == iso else ""
        if not cap or state.owner.get(cap) != iso:
            cap = max(owned, key=lambda r: geo.area.get(r, 0.0))
        c.capital_region = cap
        c.capital_pos = tuple(e["cap_ll"]) if e.get("cap_ll") else None
        c.capital = e.get("capital") or (original[iso][1] if iso in original and geo.country_of.get(cap) == iso
                                          else geo.name.get(cap, cap).split(": ")[-1])
        c.init_start()
        if "treasury" in e:
            c.treasury = float(e["treasury"])
    # 7. технологии эпохи
    for c in state.countries.values():
        c.technologies = list(data.get("techs", ()))
        c.researching, c.training, c.construction = [], [], []
    for iso, e in entries.items():
        if "techs" in e:
            state.countries[iso].technologies = sorted(set(state.countries[iso].technologies) | set(e["techs"]))
    # 8. дипломатия и войны
    for a, b in data.get("alliances", ()):
        if state.countries[a].alive and state.countries[b].alive:
            state.countries[a].allies.add(b)
            state.countries[b].allies.add(a)
    for a, b in data.get("pacts", ()):
        if state.countries[a].alive and state.countries[b].alive:
            state.countries[a].pacts.add(b)
            state.countries[b].pacts.add(a)
    for a, b, delta in data.get("relations", ()):
        if a in state.countries and b in state.countries:
            diplomacy.change_relation(state, a, b, int(delta))
    armies.init_armies(state)
    for w in data.get("wars", ()):
        A, D = w["attackers"], w["defenders"]
        if all(state.countries[i].alive for i in A + D):
            war = warmod.War(state.next_war_id, list(A), list(D), A[0], D[0], momentum=float(w.get("momentum", 0)),
                             started=d.toordinal() - int(w.get("ago", 0)))
            state.next_war_id += 1
            state.wars.append(war)
    warmod.rebuild_links(state)
    state.log.clear()
    state.log_seq = 0
    state.captures.clear()
    state.series.clear()
    state.invalidate_cache()
    state.map_dirty = True
    state.map_rev += 1
    stats.econ_update(state)
    state.log_event(f"Начало сценария «{data['name']}»", None, True, kind="war")
    for w in state.wars:
        w.score = warmod.compute_score(state, w)
