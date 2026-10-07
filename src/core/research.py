"""Дерево технологий: 5 веток × 6 уровней и механика исследований."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from config import RESEARCH_SLOTS, TECH_BONUS, TECH_PATH

if TYPE_CHECKING:
    from .country import Country

log = logging.getLogger(__name__)

BRANCH_NAMES = {"economy": "Экономика", "infantry": "Армия: пехота", "tanks": "Армия: танки",
                "air": "Армия: авиация", "navy": "Армия: флот", "industry": "Промышленность",
                "infra": "Инфраструктура", "diplomacy": "Дипломатия"}
# Пять категорий дерева технологий → входящие ветки
CATEGORIES = (("Экономика", ("economy",)), ("Армия", ("infantry", "tanks", "air", "navy")),
              ("Промышленность", ("industry",)), ("Инфраструктура", ("infra",)),
              ("Дипломатия", ("diplomacy",)))


@dataclass(frozen=True)
class Tech:
    """Описание одной технологии."""
    id: str
    name: str
    branch: str
    level: int
    cost: int
    requires: tuple[str, ...]
    desc: str = ""


class TechTree:
    """Хранилище технологий, загруженных из technologies.json."""

    def __init__(self, techs: dict[str, Tech]) -> None:
        self.techs = techs

    @classmethod
    def load(cls) -> "TechTree":
        """Загружает дерево из файла данных."""
        try:
            raw = json.loads(TECH_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.exception("Не удалось загрузить technologies.json")
            raise
        return cls({t["id"]: Tech(t["id"], t["name"], t["branch"], t["level"], t["cost"],
                                  tuple(t["requires"]), t.get("desc", "")) for t in raw})

    def branch(self, branch: str) -> list[Tech]:
        """Технологии ветки по возрастанию уровня."""
        return sorted((t for t in self.techs.values() if t.branch == branch), key=lambda t: t.level)

    def next_in_branch(self, c: "Country", branch: str) -> Optional[Tech]:
        """Следующая неизученная технология ветки (или None, если ветка завершена)."""
        for t in self.branch(branch):
            if t.id not in c.technologies:
                return t
        return None


_TREE: Optional[TechTree] = None


def get_tree() -> TechTree:
    """Ленивая загрузка единственного экземпляра дерева."""
    global _TREE
    if _TREE is None:
        _TREE = TechTree.load()
    return _TREE


def tech_levels(c: "Country", branch: str) -> int:
    """Число изученных технологий в ветке."""
    tree = get_tree()
    return sum(1 for t in c.technologies if t in tree.techs and tree.techs[t].branch == branch)


def tech_bonus(c: "Country", branch: str) -> float:
    """Суммарный бонус ветки (0.0 .. ~0.7)."""
    return TECH_BONUS.get(branch, 0.0) * tech_levels(c, branch)


def tech_mult(c: "Country", branch: str) -> float:
    """Множитель технологий ветки (1.0 — без технологий)."""
    return 1.0 + tech_bonus(c, branch)


def research_points(c: "Country") -> float:
    """Очки исследований в день на один слот."""
    return 1.0 + 0.1 * c.factories + 0.04 * c.gdp ** 0.5


def start_research(c: "Country", tech_id: str) -> tuple[bool, str]:
    """Запускает исследование, если есть слот и выполнены требования."""
    tree = get_tree()
    t = tree.techs.get(tech_id)
    if t is None:
        return False, "Неизвестная технология"
    if tech_id in c.technologies:
        return False, "Уже изучено"
    if any(r[0] == tech_id for r in c.researching):
        return False, "Уже изучается"
    if len(c.researching) >= RESEARCH_SLOTS:
        return False, "Нет свободных слотов исследований"
    if any(r not in c.technologies for r in t.requires):
        return False, "Не выполнены требования"
    c.researching.append([tech_id, 0.0])
    return True, f"Начато исследование: {t.name}"


def research_tick(c: "Country") -> list[str]:
    """Один день исследований; возвращает сообщения о завершённых технологиях."""
    tree, msgs = get_tree(), []
    pts = research_points(c)
    for slot in list(c.researching):
        slot[1] += pts
        t = tree.techs[slot[0]]
        if slot[1] >= t.cost:
            c.researching.remove(slot)
            c.technologies.append(t.id)
            msgs.append(f"{c.name}: изучена технология «{t.name}»")
    return msgs
