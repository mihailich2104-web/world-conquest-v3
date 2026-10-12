"""Глобальное состояние игры и сохранение/загрузка в SQLite (с проверкой целостности).

Единица территории — регион (id вида RUS-03; у стран без деления id совпадает с ISO). owner — юридический
владелец, ctrl — фактический контролёр (только там, где он отличается: оккупация). Сохранение версионируется;
сохранения старого формата (владение по странам) мигрируют на регионы автоматически.
"""
from __future__ import annotations

import colorsys
import hashlib
import json
import logging
import random
import sqlite3
from datetime import date, datetime
from typing import Any, Optional

from config import EVENT_LOG_LIMIT, SAVE_DB, START_DATE, USER_DIR
from .armies import Army
from .country import Country
from .geo import Geo
from .war import War

log = logging.getLogger(__name__)
VICTORY_SHARE = 0.5          # доля регионов мира для победы
SAVE_VERSION = 3
CAPTURE_LIMIT = 600


def default_entry(iso: str, name: str, continent: str) -> dict[str, Any]:
    """Запись по умолчанию для страны, которой нет в countries.json."""
    h = int(hashlib.md5(iso.encode()).hexdigest()[:6], 16)
    r, g, b = colorsys.hsv_to_rgb((h % 360) / 360, 0.5, 0.8)
    return {"iso": iso, "name": name, "continent": continent, "population": 2.0, "gdp": 8.0,
            "manpower": 100.0, "stability": 50.0, "ideology": "neutral",
            "color": [int(r * 255), int(g * 255), int(b * 255)], "capital": ""}


class GameState:
    """Все данные партии: страны, владение и контроль регионов, армии, войны, дата, журнал."""

    def __init__(self, countries: dict[str, Country], owner: dict[str, str],
                 adjacency: dict[str, list[str]], seed: int = 1, tutorial: bool = False,
                 geo: Optional[Geo] = None) -> None:
        self.countries = countries
        self.owner = owner                  # регион → юридический владелец (ISO державы)
        self.ctrl: dict[str, str] = {}      # регион → контролёр, если он не владелец (оккупация)
        self.adjacency = adjacency
        self.geo: Geo = geo or Geo.simple(adjacency)
        self.seed = seed
        self.tutorial = tutorial
        self.rng = random.Random(seed)
        self.date: date = date(*START_DATE)
        self.player_iso: Optional[str] = None   # страна локального игрока
        self.humans: set[str] = set()           # страны всех людей (в LAN их несколько); ИИ их не трогает
        self.offers: list[dict[str, Any]] = []  # входящие предложения ИИ людям: {id, to, frm, kind, until}
        self.next_offer_id = 1
        self.wars: list[War] = []
        self.next_war_id = 1
        self.truces: dict[str, int] = {}
        self.log: list[dict[str, Any]] = []
        self.log_seq = 0
        self.stats: dict[str, dict[str, Any]] = {"battles_won": {}, "battles_lost": {}, "annexed": {},
                                                 "casualties": {}}
        self.game_over: Optional[str] = None   # None | "victory" | "defeat"
        self.map_dirty = True
        self.map_rev = 0
        self._nb_cache: dict[str, set[str]] = {}
        # армии и боевая обстановка
        self.armies: dict[int, Army] = {}
        self.next_army_id = 1
        self.army_no: dict[str, int] = {}
        self.removed_armies: list[int] = []      # служебное: удалённые армии (для сетевых дельт)
        self.battles: dict[str, int] = {}        # регион → дней боя (сегодняшние бои)
        self.occupying: dict[str, list[Any]] = {}  # регион → [кто занимает, прогресс в сутках]
        self.sup_cache: dict[str, tuple] = {}
        self.captures: list[dict[str, Any]] = []   # история смены контроля/владения регионами
        self.cap_seq = 0
        self.series: dict[str, list[list[float]]] = {}   # помесячная статистика людей
        self.scenario: dict[str, Any] = {"id": "modern", "name": "Современность", "approx": False,
                                         "era": "modern", "start": date(*START_DATE).toordinal()}

    # ---------- создание ----------
    @classmethod
    def new(cls, countries_data: list[dict[str, Any]], provinces: list[tuple[str, str, str]],
            adjacency: dict[str, list[str]], seed: Optional[int] = None, tutorial: bool = False,
            geo: Optional[Geo] = None) -> "GameState":
        """Новая партия: страны создаются для каждой страны карты (по регионам из geo либо по провинциям)."""
        by_iso = {d["iso"]: d for d in countries_data}
        countries: dict[str, Country] = {}
        owner: dict[str, str] = {}
        regional = geo is not None and bool(geo.country_of) and all(r in geo.country_of for r, _, _ in provinces)
        for rid, name, cont in provinces:
            iso = geo.country_of[rid] if regional else rid
            owner[rid] = iso
            if iso not in countries:
                c = Country.from_json(by_iso.get(iso) or default_entry(iso, name, cont))
                c.iso = iso
                c.init_start()
                if geo is not None:
                    c.capital_region = geo.cap_region.get(iso, rid)
                else:
                    c.capital_region = rid
                countries[iso] = c
        st = cls(countries, owner, adjacency, seed if seed is not None else random.randrange(1 << 30), tutorial, geo)
        from . import armies
        armies.init_armies(st)
        log.info("Новая игра: %d стран, %d регионов, seed=%d", len(countries), len(owner), st.seed)
        return st

    # ---------- запросы ----------
    def owned(self, iso: str) -> list[str]:
        """Регионы, юридически принадлежащие стране."""
        return [p for p, o in self.owner.items() if o == iso]

    def controlled(self, iso: str) -> list[str]:
        """Регионы под фактическим контролем страны."""
        return [p for p, o in self.owner.items() if self.ctrl.get(p, o) == iso]

    def neighbors(self, iso: str) -> set[str]:
        """Страны-соседи (по владельцам смежных регионов)."""
        if iso not in self._nb_cache:
            res: set[str] = set()
            for p in self.owned(iso):
                for q in self.adjacency.get(p, ()):
                    o = self.owner.get(q)
                    if o and o != iso and self.countries[o].alive:
                        res.add(o)
            self._nb_cache[iso] = res
        return self._nb_cache[iso]

    def invalidate_cache(self) -> None:
        """Сбрасывает кэши (после смены владельцев)."""
        self._nb_cache.clear()
        self.sup_cache.clear()

    def alive_countries(self) -> list[Country]:
        """Список существующих стран."""
        return [c for c in self.countries.values() if c.alive]

    @staticmethod
    def truce_key(a: str, b: str) -> str:
        """Ключ пары для словаря перемирий."""
        return "|".join(sorted((a, b)))

    def is_human(self, iso: str) -> bool:
        """Управляется ли страна человеком."""
        return iso in self.humans or iso == self.player_iso

    def human_list(self) -> list[str]:
        """Все страны людей (включая локального игрока)."""
        res = set(self.humans)
        if self.player_iso:
            res.add(self.player_iso)
        return sorted(res)

    @property
    def player(self) -> Optional[Country]:
        """Страна игрока."""
        return self.countries.get(self.player_iso) if self.player_iso else None

    # ---------- изменение карты ----------
    def _map_changed(self) -> None:
        self.map_rev += 1
        self.map_dirty = True
        self.invalidate_cache()

    def set_owner(self, rid: str, who: str, kind: str = "cede") -> None:
        """Юридическая передача региона (мир, аннексия); оккупация снимается."""
        prev = self.owner.get(rid)
        if prev == who and rid not in self.ctrl:
            return
        self.owner[rid] = who
        self.ctrl.pop(rid, None)
        self._record_capture(rid, prev or "", who, kind)
        self._map_changed()

    def set_ctrl(self, rid: str, who: str) -> None:
        """Смена фактического контроля (оккупация/освобождение)."""
        own = self.owner.get(rid)
        prev = self.ctrl.get(rid, own)
        if who == own:
            if rid not in self.ctrl:
                return
            del self.ctrl[rid]
            kind = "liberate"
        else:
            if self.ctrl.get(rid) == who:
                return
            self.ctrl[rid] = who
            kind = "occupy"
        self._record_capture(rid, prev or "", who, kind)
        self._map_changed()

    def _record_capture(self, rid: str, frm: str, to: str, kind: str) -> None:
        self.cap_seq += 1
        self.captures.append({"q": self.cap_seq, "d": self.date.toordinal(), "r": rid, "f": frm, "t": to, "k": kind})
        if len(self.captures) > CAPTURE_LIMIT:
            del self.captures[: len(self.captures) - CAPTURE_LIMIT]

    def touch_army(self, a: Army) -> None:
        """Отмечает изменение армии (для интерфейса и сети)."""

    def note_casualties(self, iso: str, n: float) -> None:
        """Учёт потерь державы (в дивизиях)."""
        cas = self.stats.setdefault("casualties", {})
        cas[iso] = round(cas.get(iso, 0.0) + n, 3)

    def log_event(self, text: str, iso: Optional[str] = None, important: bool = False,
                  kind: str = "", isos: Optional[list[str]] = None) -> None:
        """Добавляет запись в журнал событий (kind: war/battle/move/capture/peace/research/econ/net)."""
        self.log_seq += 1
        e: dict[str, Any] = {"n": self.log_seq, "date": self.date.isoformat(), "text": text, "iso": iso,
                             "imp": important}
        if kind:
            e["k"] = kind
        if isos:
            e["isos"] = [i for i in dict.fromkeys(isos) if i]
        self.log.append(e)
        if len(self.log) > EVENT_LOG_LIMIT:
            del self.log[: len(self.log) - EVENT_LOG_LIMIT]

    def check_victory(self) -> None:
        """Победа: человек владеет ≥50% регионов мира (в LAN game_over = victory:ISO)."""
        if self.game_over:
            return
        for iso in self.human_list():
            if len(self.owned(iso)) >= VICTORY_SHARE * len(self.owner):
                multi = len(self.humans) > 1
                self.game_over = f"victory:{iso}" if multi else "victory"
                self.log_event(f"★ {self.countries[iso].name} достигла мирового господства!", iso, True)
                return

    # ---------- сериализация ----------
    def to_dict(self) -> dict[str, Any]:
        """Полный снимок состояния в JSON-совместимом виде."""
        s = self.rng.getstate()
        return {
            "v": SAVE_VERSION, "seed": self.seed, "tutorial": self.tutorial, "date": self.date.toordinal(),
            "player": self.player_iso, "next_war_id": self.next_war_id, "truces": self.truces,
            "humans": sorted(self.humans), "offers": self.offers, "next_offer_id": self.next_offer_id,
            "owner": self.owner, "ctrl": self.ctrl, "stats": self.stats, "game_over": self.game_over,
            "log": self.log, "log_seq": self.log_seq, "rng": [s[0], list(s[1]), s[2]],
            "wars": [w.to_dict() for w in self.wars],
            "countries": [c.to_dict() for c in self.countries.values()],
            "armies": [a.to_dict() for a in self.armies.values()], "next_army_id": self.next_army_id,
            "army_no": self.army_no, "battles": self.battles, "occupying": self.occupying,
            "captures": self.captures, "cap_seq": self.cap_seq, "series": self.series, "scenario": self.scenario,
            "map_rev": self.map_rev,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any], adjacency: dict[str, list[str]], geo: Optional[Geo] = None) -> "GameState":
        """Восстанавливает состояние; граф смежности и география берутся с загруженной карты."""
        countries = {c["iso"]: Country.from_dict(c) for c in d["countries"]}
        owner = dict(d["owner"])
        geo = geo or Geo.simple(adjacency)
        migrated = False
        if adjacency and owner and not set(owner) <= set(adjacency):
            owner, migrated = _migrate_owner(owner, geo, adjacency)
        st = cls(countries, owner, adjacency, d["seed"], d["tutorial"], geo)
        st.date = date.fromordinal(d["date"])
        st.player_iso, st.next_war_id, st.truces = d["player"], d["next_war_id"], d["truces"]
        for k, v in d["stats"].items():
            st.stats[k] = v
        st.game_over, st.log = d["game_over"], d["log"]
        st.log_seq = d.get("log_seq", max((e.get("n", 0) for e in st.log), default=len(st.log)))
        st.wars = [War.from_dict(w) for w in d["wars"]]
        st.humans = set(d.get("humans", []))
        st.offers, st.next_offer_id = d.get("offers", []), d.get("next_offer_id", 1)
        st.ctrl = {k: v for k, v in d.get("ctrl", {}).items() if k in owner}
        st.armies = {a["id"]: Army.from_dict(a) for a in d.get("armies", [])}
        st.next_army_id = d.get("next_army_id", 1)
        st.army_no = d.get("army_no", {})
        st.battles = d.get("battles", {})
        st.occupying = d.get("occupying", {})
        st.captures = d.get("captures", [])
        st.cap_seq = d.get("cap_seq", max((e.get("q", 0) for e in st.captures), default=0))
        st.series = d.get("series", {})
        st.scenario = d.get("scenario") or st.scenario
        st.map_rev = d.get("map_rev", 0)
        for c in st.countries.values():
            if not c.capital_region:
                c.capital_region = geo.cap_region.get(c.iso, "") or next((r for r, o in owner.items() if o == c.iso), "")
        if migrated or (not st.armies and any(c.army > 0 for c in st.countries.values())):
            from . import armies
            armies.init_armies(st)
        r = d["rng"]
        st.rng.setstate((r[0], tuple(r[1]), r[2]))
        return st

    def load_dict(self, d: dict[str, Any]) -> None:
        """Обновляет это же состояние данными снимка (LAN-клиент): локальные поля сохраняются."""
        new = GameState.from_dict(d, self.adjacency, self.geo)
        keep_player, keep_map_dirty = self.player_iso, self.map_dirty
        changed = new.owner != self.owner or new.ctrl != self.ctrl
        self.__dict__.update({k: v for k, v in new.__dict__.items() if k not in ("adjacency", "geo")})
        self.player_iso = keep_player
        self._nb_cache = {}
        self.map_dirty = keep_map_dirty or changed


def _migrate_owner(owner: dict[str, str], geo: Geo, adjacency: dict[str, list[str]]) -> tuple[dict[str, str], bool]:
    """Старый формат: владение по странам (ISO → владелец) → владение регионами."""
    new: dict[str, str] = {}
    for rid in adjacency:
        c = geo.country_of.get(rid, rid)
        new[rid] = owner.get(c, c)
    return new, True


# ---------- SQLite ----------
def _db() -> sqlite3.Connection:
    """Открывает БД сохранений (создаёт таблицу при необходимости; запись синхронная — не повреждается при сбое)."""
    USER_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(SAVE_DB, timeout=10)
    con.execute("PRAGMA synchronous=FULL")
    con.execute("CREATE TABLE IF NOT EXISTS saves(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,"
                " saved_at TEXT, player TEXT, game_date TEXT, data TEXT)")
    cols = {r[1] for r in con.execute("PRAGMA table_info(saves)")}
    for col in ("sha", "scenario", "ver"):
        if col not in cols:
            con.execute(f"ALTER TABLE saves ADD COLUMN {col} TEXT")
    return con


def save_game(state: GameState, name: str = "Сохранение") -> int:
    """Сохраняет партию одной транзакцией с контрольной суммой; возвращает id записи. Хранится ≤20 последних."""
    try:
        data = json.dumps(state.to_dict(), ensure_ascii=False)
        sha = hashlib.sha256(data.encode("utf-8")).hexdigest()
        with _db() as con:
            cur = con.execute(
                "INSERT INTO saves(name,saved_at,player,game_date,data,sha,scenario,ver) VALUES(?,?,?,?,?,?,?,?)",
                (name, datetime.now().isoformat(timespec="seconds"), state.player_iso,
                 state.date.isoformat(), data, sha, str(state.scenario.get("name", "")), str(SAVE_VERSION)))
            con.execute("DELETE FROM saves WHERE id NOT IN (SELECT id FROM saves ORDER BY id DESC LIMIT 20)")
            return int(cur.lastrowid or 0)
    except (sqlite3.Error, OSError, ValueError):
        log.exception("Ошибка сохранения")
        return 0


def list_saves(limit: int = 5) -> list[dict[str, Any]]:
    """Последние сохранения (без тела данных)."""
    try:
        with _db() as con:
            rows = con.execute("SELECT id,name,saved_at,player,game_date,scenario FROM saves ORDER BY id DESC LIMIT ?",
                               (limit,)).fetchall()
        return [dict(zip(("id", "name", "saved_at", "player", "game_date", "scenario"), r)) for r in rows]
    except sqlite3.Error:
        log.exception("Ошибка чтения списка сохранений")
        return []


def load_game(save_id: int, adjacency: dict[str, list[str]], geo: Optional[Geo] = None) -> Optional[GameState]:
    """Загружает партию по id; None при ошибке или нарушении целостности."""
    try:
        with _db() as con:
            row = con.execute("SELECT data, sha FROM saves WHERE id=?", (save_id,)).fetchone()
        if not row:
            return None
        if row[1] and hashlib.sha256(row[0].encode("utf-8")).hexdigest() != row[1]:
            log.error("Сохранение %s повреждено: контрольная сумма не совпала", save_id)
            return None
        return GameState.from_dict(json.loads(row[0]), adjacency, geo)
    except (sqlite3.Error, ValueError, KeyError, TypeError):
        log.exception("Ошибка загрузки сохранения")
        return None
