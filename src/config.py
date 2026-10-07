"""Глобальные константы, пути и пользовательские настройки проекта."""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Корень ресурсов: в PyInstaller — sys._MEIPASS, иначе корень репозитория
if getattr(sys, "frozen", False):
    ROOT: Path = Path(sys._MEIPASS)  # type: ignore[attr-defined]
else:
    ROOT = Path(__file__).resolve().parent.parent

DATA_DIR: Path = ROOT / "src" / "data"
ASSETS_DIR: Path = ROOT / "assets"
FONT_PATH: Path = ASSETS_DIR / "fonts" / "DejaVuSans.ttf"
THEME_PATH: Path = ASSETS_DIR / "theme.json"
PROVINCES_PATH: Path = DATA_DIR / "provinces.geojson"
COUNTRIES_PATH: Path = DATA_DIR / "countries.json"
CAPITALS_PATH: Path = DATA_DIR / "capitals.json"      # ISO3 → [lon, lat] настоящих столиц
TECH_PATH: Path = DATA_DIR / "technologies.json"
USER_DIR: Path = Path.home() / ".world_conquest"
SAVE_DB: Path = USER_DIR / "saves.db"
SETTINGS_PATH: Path = USER_DIR / "settings.json"

WINDOW_SIZE: tuple[int, int] = (1280, 720)
FPS: int = 60
TITLE: str = "World Conquest"
HUD_HEIGHT: int = 48
LEFT_PANEL_W: int = 156
SIDE_PANEL_W: int = 360
TUTORIAL_H: int = 120

# Карта (проекция Миллера: 1 градус долготы = MAP_SCALE пикселей)
MAP_SCALE: float = 10.0
ZOOM_MIN: float = 0.35
ZOOM_MAX: float = 8.0
ZOOM_STEP: float = 1.15
SIMPLIFY_TOLERANCE: float = 0.05
NON_PLAYABLE: frozenset[str] = frozenset({"ATA", "ATF"})

# Геймплей
START_DATE: tuple[int, int, int] = (2025, 1, 1)
AUTOSAVE_EVERY_DAYS: int = 30
AUTO_TURN_SECONDS: float = 0.35
EVENT_LOG_LIMIT: int = 300

# Игровое время: секунд реального времени на один игровой день при скорости 1X
DAY_SECONDS: float = 1.0
SPEEDS: tuple[int, ...] = (1, 2, 4, 8, 16)
MAX_DAYS_PER_FRAME: int = 4

# LAN-мультиплеер
NET_VERSION: int = 1
NET_DEFAULT_PORT: int = 47800
NET_DISCOVERY_PORT: int = 47801
NET_SNAPSHOT_INTERVAL: float = 0.25
NET_MAX_PLAYERS: int = 8

IDEOLOGY_COLORS: dict[str, tuple[int, int, int]] = {
    "democracy": (70, 130, 220),
    "communism": (200, 50, 50),
    "autocracy": (140, 90, 60),
    "neutral": (150, 150, 150),
}
IDEOLOGY_NAMES: dict[str, str] = {
    "democracy": "Демократия", "communism": "Коммунизм",
    "autocracy": "Автократия", "neutral": "Нейтралитет",
}
RESOURCE_NAMES: dict[str, str] = {
    "steel": "Сталь", "oil": "Нефть", "food": "Еда", "electronics": "Электроника",
}

# Стоимость строительства и найма (days — время исполнения в ходах/днях)
COSTS: dict[str, dict[str, float]] = {
    "factory": {"treasury": 60, "steel": 10, "electronics": 2, "days": 14},
    "fort": {"treasury": 40, "steel": 15, "days": 10},
    "army": {"treasury": 30, "steel": 5, "manpower": 10, "days": 3},
    "navy": {"treasury": 60, "steel": 10, "oil": 5, "manpower": 5, "days": 8},
    "air": {"treasury": 40, "steel": 4, "electronics": 3, "manpower": 2, "days": 5},
}
UNIT_NAMES: dict[str, str] = {"army": "дивизия", "navy": "корабль", "air": "эскадрилья"}
BUILD_NAMES: dict[str, str] = {"factory": "фабрика", "fort": "укрепление"}
# Бонусы технологий за уровень по веткам
TECH_BONUS: dict[str, float] = {
    "infantry": 0.08, "tanks": 0.12, "air": 0.10, "navy": 0.10, "industry": 0.08,
    "economy": 0.05, "infra": 0.05, "diplomacy": 0.10,
}
RESEARCH_SLOTS: int = 2


@dataclass
class Settings:
    """Пользовательские настройки (сохраняются в JSON)."""
    fullscreen: bool = False
    ai_aggression: float = 1.0   # 0 — мирный ИИ, 2 — агрессивный
    map_mode: str = "political"  # political | ideology
    autosave: bool = True
    player_name: str = "Игрок"
    last_ip: str = ""
    last_port: int = 47800

    @classmethod
    def load(cls) -> "Settings":
        """Читает настройки с диска; при ошибке возвращает значения по умолчанию."""
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self) -> None:
        """Сохраняет настройки на диск."""
        try:
            USER_DIR.mkdir(parents=True, exist_ok=True)
            SETTINGS_PATH.write_text(json.dumps(asdict(self)), encoding="utf-8")
        except OSError:
            log.exception("Не удалось сохранить настройки")


SETTINGS: Settings = Settings.load()
