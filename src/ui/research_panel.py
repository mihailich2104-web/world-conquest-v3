"""Панель исследований: 5 категорий (экономика, армия, промышленность, инфраструктура, дипломатия)."""
from __future__ import annotations

from config import RESEARCH_SLOTS, TECH_BONUS
from core import research as rs
from . import SidePanel

EFFECT = {"economy": "доход", "infantry": "сила армии", "tanks": "сила армии", "air": "авиация", "navy": "флот",
          "industry": "производство и доход", "infra": "рост, резервы, снабжение", "diplomacy": "дипломатия"}


class ResearchPanel(SidePanel):
    """Текущие исследования и кнопки запуска следующей технологии каждой ветки."""
    NAME = "research"

    def build(self) -> None:
        ctx = self.ctx
        c = ctx.state.player if ctx.state else None
        if c is None or not ctx.can_act:
            return
        tree = rs.get_tree()
        cur = [f"{tree.techs[t].name}: {p:.0f}/{tree.techs[t].cost}" for t, p in c.researching]
        pts = rs.research_points(c)
        lines = [f"<b>Исследования</b> (слоты {len(c.researching)}/{RESEARCH_SLOTS}, {pts:.1f} очк./день)"]
        lines += cur or ["Нет активных исследований"]
        self.add_text("<br>".join(lines), len(lines) + 1)
        for cat, branches in rs.CATEGORIES:
            for br in branches:
                t = tree.next_in_branch(c, br)
                n = rs.tech_levels(c, br)
                ru = rs.BRANCH_NAMES[br]
                bonus = TECH_BONUS.get(br, 0.0) * n * 100
                if t is None:
                    self.add_button(f"{ru} {n}/6 — изучено (+{bonus:.0f}%)", lambda: None, enabled=False, h=34)
                else:
                    busy = any(r[0] == t.id for r in c.researching)
                    self.add_button(f"{ru} {n}/6: {t.name} ({t.cost} оч.)" + (" …" if busy else ""),
                                    (lambda tid=t.id: ctx.do({"t": "research", "tech": tid})),
                                    enabled=not busy, h=34)
