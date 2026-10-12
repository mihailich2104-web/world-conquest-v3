"""Панель «Прогресс»: статистика кампании за месяц/год/всё время, состояние войн и журнал событий."""
from __future__ import annotations

import logging

import pygame

from config import SIDE_PANEL_W
from core import stats as st_mod
from core.diplomacy import peace_preview
from . import SidePanel

log = logging.getLogger(__name__)
PERIODS = ((30, "Месяц"), (365, "Год"), (0, "Кампания"))
FILTERS = (("all", "Все", None), ("war", "Война", {"war", "battle", "peace"}),
           ("map", "Карта", {"capture", "move"}), ("eco", "Экономика", {"econ", "research", "warn"}))


class ProgressPanel(SidePanel):
    """Как развивается государство: территория, армия, экономика, технологии, война и хронология."""
    NAME = "progress"

    def __init__(self, ctx) -> None:                       # noqa: ANN001
        super().__init__(ctx)
        self.period = 365
        self.flt = "all"

    def _chart(self, rows: list[list[float]], idx: int, label: str) -> None:
        """Мини-график показателя по срезам (линия на тёмном фоне)."""
        w, h = SIDE_PANEL_W - 28, 54
        surf = pygame.Surface((w, h))
        surf.fill((24, 28, 34))
        vals = [r[idx] for r in rows]
        if len(vals) >= 2:
            lo, hi = min(vals), max(vals)
            span = (hi - lo) or 1.0
            pts = [(4 + (w - 8) * i / (len(vals) - 1), h - 6 - (h - 12) * (v - lo) / span) for i, v in enumerate(vals)]
            pygame.draw.lines(surf, (150, 190, 226), False, pts, 2)
        try:
            from pygame_gui.elements import UIImage
            img = UIImage(pygame.Rect(4, self.y, w, h), surf, self.ctx.manager, container=self.panel)
            self.widgets.append(img)
            self.y += h + 2
            self.add_text(f"<font size=2 color='#9aa4b2'>{label}: {vals[0]:.0f} → {vals[-1]:.0f}</font>" if vals else label, 1)
        except Exception:                                   # noqa: BLE001 — график необязателен
            log.exception("График не создан")

    def build(self) -> None:
        ctx, st = self.ctx, self.ctx.state
        c = st.player if st else None
        if c is None or not ctx.can_act:
            return
        self.add_row([((f"[{n}]" if d == self.period else n), (lambda d=d: self._set_period(d))) for d, n in PERIODS],
                     h=26)
        s = st_mod.summary(st, c.iso, self.period)
        if s:
            ch = lambda v: f"{v:+.0f}" if v else "±0"      # noqa: E731
            lines = [f"<b>Прогресс: {c.name}</b>",
                     f"Территория: {s['owned']:.0f} рег. ({ch(s['d_owned'])}); под контролем {s['controlled']:.0f}",
                     f"Армия: {s['army']:.0f} ({ch(s['d_army'])}); потери за период {s['d_cas']:.1f}",
                     f"Сражения: побед {s['d_won']:.0f}, поражений {s['d_lost']:.0f} (всего {s['won']:.0f}/{s['lost']:.0f})",
                     f"Экономика: ВВП {s['gdp']:.0f} ({ch(s['d_gdp'])}); казна {s['treasury']:.0f}",
                     f"Технологий: {s['techs']:.0f} ({ch(s['d_techs'])})"]
        else:
            lines = [f"<b>Прогресс: {c.name}</b>", "Срезы появятся с первого числа следующего месяца."]
        self.add_text("<br>".join(lines), len(lines) + 1)
        rows = st_mod.period_rows(st, c.iso, self.period)
        if len(rows) >= 2:
            self._chart(rows, 1, "Регионов во владении")
        wars = [w for w in st.wars if w.side_of(c.iso)]
        wl = [f"<b>Войны: {len(wars)}</b>"] if wars else ["Войн нет."]
        for w in wars[:2]:
            en = st.countries[w.enemy_main(c.iso)]
            mine = sum(1 for r in w.captured if st.ctrl.get(r) == c.iso)
            lost = sum(1 for r, o in st.owner.items() if o == c.iso and w.side_of(st.ctrl.get(r, o)) not in ("", w.side_of(c.iso)))
            pv = peace_preview(st, c.iso, en.iso)
            term = {"annex": f"мир сейчас: +{pv.get('gain', 0)} рег. нам", "cede": f"мир сейчас: −{pv.get('lose', 0)} рег.",
                    "white": "мир сейчас: границы без изменений"}.get(pv.get("kind", ""), "")
            wl.append(f"{en.name}: счёт {w.score_for(c.iso):+.0f}, день {w.days}, сражений {w.battles}<br>"
                      f"занято нами {mine}, потеряно {lost}; {term}")
        self.add_text("<br>".join(wl), len(wl) + len(wars) + 1)
        self.add_row([(n if k != self.flt else f"[{n}]", (lambda k=k: self._set_flt(k))) for k, n, _ in FILTERS], h=26)
        kinds = next(v for k, _, v in FILTERS if k == self.flt)
        me = c.iso
        rows_e = [e for e in st.log if (kinds is None or e.get("k") in kinds)
                  and (e.get("iso") in (None, me) or me in e.get("isos", ()) or e.get("iso") not in st.human_list())]
        shown = rows_e[-9:]
        html = "<br>".join(f"<font size=2 color='{'#f0d68c' if e.get('imp') else '#cfd4da'}'>{e['date']}  {e['text']}</font>"
                           for e in reversed(shown)) or "Событий нет."
        self.add_text(html, min(12, len(shown) * 2 + 1))

    def _set_period(self, d: int) -> None:
        self.period = d

    def _set_flt(self, k: str) -> None:
        self.flt = k
