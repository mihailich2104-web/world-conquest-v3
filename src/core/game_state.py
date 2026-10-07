"""Глобальное состояние игры и сохранение/загрузка в SQLite."""
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
from .country import Country
from .war import War

log = logging.getLogger(__name__)
VICTORY_SHARE = 0.5   # доля провинций мира для победы


def default_entry(iso: str, name: str, continent: str) -> dict[str, Any]:
    """Запись по умолчанию для страны, которой нет в countries.json."""
    h = int(hashlib.md5(iso.encode()).hexdigest()[:6], 16)
    r, g, b = colorsys.hsv_to_rgb((h % 360) / 360, 0.5, 0.8)
    return {"iso": iso, "name": name, "continent": continent, "population": 2.0, "gdp": 8.0,
            "manpower": 100.0, "stability": 50.0, "ideology": "neutral",
            "color": [int(r * 255), int(g * 255), int(b * 255)], "capital": ""}


class GameState:
    """Все данные партии: страны, владение провинциями, войны, дата, журнал."""

    def __init__(self, countries: dict[str, Country], owner: dict[str, str],
                 adjacency: dict[str, list[str]], seed: int = 1, tutorial: bool = False) -> None:
        self.countries = countries
        self.owner = owner                  # провинция(iso) → текущий владелец(iso)
        self.adjacency = adjacency
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
        self.stats: dict[str, dict[str, int]] = {"battles_won": {}, "battles_lost": {}, "annexed": {}}
        self.game_over: Optional[str] = None   # None | "victory" | "defeat"
        self.map_dirty = True
        self._nb_cache: dict[str, set[str]] = {}

    # ---------- создание ----------
    @classmethod
    def new(cls, countries_data: list[dict[str, Any]], provinces: list[tuple[str, str, str]],
            adjacency: dict[str, list[str]], seed: Optional[int] = None,
            tutorial: bool = False) -> "GameState":
        """Новая партия: страны создаются для каждой провинции карты."""
        by_iso = {d["iso"]: d for d in countries_data}
        countries: dict[str, Country] = {}
        for iso, name, cont in provinces:
            c = Country.from_json(by_iso.get(iso) or default_entry(iso, name, cont))
            c.iso = iso
            c.init_start()
            countries[iso] = c
        owner = {iso: iso for iso, _, _ in provinces}
        st = cls(countries, owner, adjacency, seed if seed is not None else random.randrange(1 << 30), tutorial)
        log.info("Новая игра: %d стран, seed=%d", len(countries), st.seed)
        return st

    # ---------- запросы ----------
    def owned(self, iso: str) -> list[str]:
        """Провинции, принадлежащие стране."""
        return [p for p, o in self.owner.items() if o == iso]

    def neighbors(self, iso: str) -> set[str]:
        """Страны-соседи (по владельцам смежных провинций)."""
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
        """Сбрасывает кэш соседей (после аннексии)."""
        self._nb_cache.clear()

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

    def log_event(self, text: str, iso: Optional[str] = None, important: bool = False) -> None:
        """Добавляет запись в журнал событий."""
        self.log.append({"date": self.date.isoformat(), "text": text, "iso": iso, "imp": important})
        if len(self.log) > EVENT_LOG_LIMIT:
            del self.log[: len(self.log) - EVENT_LOG_LIMIT]

    def check_victory(self) -> None:
        """Победа: человек контролирует ≥50% провинций мира (в LAN game_over = victory:ISO)."""
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
            "seed": self.seed, "tutorial": self.tutorial, "date": self.date.toordinal(),
            "player": self.player_iso, "next_war_id": self.next_war_id, "truces": self.truces,
            "humans": sorted(self.humans), "offers": self.offers, "next_offer_id": self.next_offer_id,
            "owner": self.owner, "stats": self.stats, "game_over": self.game_over,
            "log": self.log[-60:], "rng": [s[0], list(s[1]), s[2]],
            "wars": [w.to_dict() for w in self.wars],
            "countries": [c.to_dict() for c in self.countries.values()],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any], adjacency: dict[str, list[str]]) -> "GameState":
        """Восстанавливает состояние; граф смежности берётся с загруженной карты."""
        countries = {c["iso"]: Country.from_dict(c) for c in d["countries"]}
        st = cls(countries, d["owner"], adjacency, d["seed"], d["tutorial"])
        st.date = date.fromordinal(d["date"])
        st.player_iso, st.next_war_id, st.truces = d["player"], d["next_war_id"], d["truces"]
        st.stats, st.game_over, st.log = d["stats"], d["game_over"], d["log"]
        st.wars = [War.from_dict(w) for w in d["wars"]]
        st.humans = set(d.get("humans", []))
        st.offers, st.next_offer_id = d.get("offers", []), d.get("next_offer_id", 1)
        r = d["rng"]
        st.rng.setstate((r[0], tuple(r[1]), r[2]))
        return st

    def load_dict(self, d: dict[str, Any]) -> None:
        """Обновляет это же состояние данными снимка (LAN-клиент): локальные поля сохраняются."""
        new = GameState.from_dict(d, self.adjacency)
        keep_player, keep_map_dirty = self.player_iso, self.map_dirty
        owner_changed = new.owner != self.owner
        self.__dict__.update({k: v for k, v in new.__dict__.items() if k not in ("adjacency",)})
        self.player_iso = keep_player
        self._nb_cache = {}
        self.map_dirty = keep_map_dirty or owner_changed


# ---------- SQLite ----------
def _db() -> sqlite3.Connection:
    """Открывает БД сохранений (создаёт таблицу при необходимости)."""
    USER_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(SAVE_DB)
    con.execute("CREATE TABLE IF NOT EXISTS saves(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,"
                " saved_at TEXT, player TEXT, game_date TEXT, data TEXT)")
    return con


def save_game(state: GameState, name: str = "Сохранение") -> int:
    """Сохраняет партию; возвращает id записи. Хранится не более 20 последних."""
    try:
        with _db() as con:
            cur = con.execute(
                "INSERT INTO saves(name,saved_at,player,game_date,data) VALUES(?,?,?,?,?)",
                (name, datetime.now().isoformat(timespec="seconds"), state.player_iso,
                 state.date.isoformat(), json.dumps(state.to_dict(), ensure_ascii=False)))
            con.execute("DELETE FROM saves WHERE id NOT IN (SELECT id FROM saves ORDER BY id DESC LIMIT 20)")
            return int(cur.lastrowid or 0)
    except sqlite3.Error:
        log.exception("Ошибка сохранения")
        return 0


def list_saves(limit: int = 5) -> list[dict[str, Any]]:
    """Последние сохранения (без тела данных)."""
    try:
        with _db() as con:
            rows = con.execute("SELECT id,name,saved_at,player,game_date FROM saves ORDER BY id DESC LIMIT ?",
                               (limit,)).fetchall()
        return [dict(zip(("id", "name", "saved_at", "player", "game_date"), r)) for r in rows]
    except sqlite3.Error:
        log.exception("Ошибка чтения списка сохранений")
        return []


def load_game(save_id: int, adjacency: dict[str, list[str]]) -> Optional[GameState]:
    """Загружает партию по id; None при ошибке."""
    try:
        with _db() as con:
            row = con.execute("SELECT data FROM saves WHERE id=?", (save_id,)).fetchone()
        return GameState.from_dict(json.loads(row[0]), adjacency) if row else None
    except (sqlite3.Error, ValueError, KeyError):
        log.exception("Ошибка загрузки сохранения")
        return None
