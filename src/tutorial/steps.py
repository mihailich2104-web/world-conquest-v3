"""Сценарий обучения: 6 шагов + финал. Каждый шаг — текст, подсветка, условие завершения."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from core.military import combat_power

log = logging.getLogger(__name__)
Ctx = Any  # TutorContext из tutor_engine


@dataclass
class Step:
    """Шаг обучения."""
    id: str
    text: Callable[[Ctx], str]
    highlight: Optional[str] = None                       # ключ элемента | "map" | "capital" | "target"
    on_enter: Optional[Callable[[Ctx], None]] = None
    done: Optional[Callable[[Ctx], bool]] = None          # None → шаг закрывается кнопкой «Далее»
    fallback: Optional[str] = None                        # ключ, если основной элемент не виден


# ---------- on_enter ----------
def _enter_capital(ctx: Ctx) -> None:
    ctx.focus_country(ctx.player.iso, 3.0)


def _enter_factory(ctx: Ctx) -> None:
    ctx.memo["factories"] = ctx.player.factories + ctx.player.pending("factory")
    ctx.open_panel("country")


def _enter_division(ctx: Ctx) -> None:
    ctx.memo["army"] = ctx.player.army + ctx.player.pending("army")
    ctx.open_panel("military")


def _enter_war(ctx: Ctx) -> None:
    """Выбирает слабого соседа-цель и ослабляет его, чтобы первый бой был выигрышным."""
    st, me = ctx.state, ctx.player
    cands = sorted(st.neighbors(me.iso)) or sorted(
        (c.iso for c in st.alive_countries() if c.iso != me.iso),
        key=lambda i: _dist(ctx, me.iso, i))[:1]
    tgt = min(cands, key=lambda i: combat_power(st.countries[i]))
    t = st.countries[tgt]
    me.army = max(me.army, 3)
    t.army, t.navy, t.airforce, t.fort = max(1, min(t.army, me.army // 3)), 0, 0, 0
    t.allies.clear()
    ctx.memo["target"] = tgt
    ctx.select(tgt)
    ctx.open_panel("diplomacy")


def _dist(ctx: Ctx, a: str, b: str) -> float:
    pa, pb = ctx.renderer.by_iso[a].centroid, ctx.renderer.by_iso[b].centroid
    return (pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2


STEPS: list[Step] = [
    Step("select", lambda c: "Шаг 1/6. Выбери страну: кликни по любой стране на карте (колесо — масштаб, "
                             "ЛКМ+перетаскивание — сдвиг), затем нажми «Играть за …». Советуем страну с соседями.",
         highlight="map", done=lambda c: c.state.player_iso is not None),
    Step("capital", lambda c: f"Шаг 2/6. Это твоя страна — {c.player.name}. Золотой маркер — центр страны, "
                              f"где находится столица ({c.player.capital or '—'}). Посмотри вокруг и жми «Далее».",
         highlight="capital", on_enter=_enter_capital),
    Step("factory", lambda c: "Шаг 3/6. Построй фабрику: в панели «Страна» нажми подсвеченную кнопку. "
                              "Фабрики дают сталь, электронику и очки исследований.",
         highlight="btn_factory", fallback="btn_country", on_enter=_enter_factory,
         done=lambda c: c.player.factories + c.player.pending("factory") > c.memo["factories"]),
    Step("division", lambda c: "Шаг 4/6. Найми дивизию: в панели «Армия» нажми подсвеченную кнопку. "
                               "Обучение занимает несколько дней.",
         highlight="btn_recruit_army", fallback="btn_military", on_enter=_enter_division,
         done=lambda c: c.player.army + c.player.pending("army") > c.memo["army"]),
    Step("war", lambda c: f"Шаг 5/6. Объяви войну соседу: мы выбрали слабую цель — "
                          f"{c.state.countries[c.memo['target']].name} (подсвечена). В «Дипломатии» нажми «Объявить войну».",
         highlight="btn_war", fallback="btn_diplomacy", on_enter=_enter_war,
         done=lambda c: bool(c.player.at_war_with)),
    Step("battle", lambda c: "Шаг 6/6. Выиграй первый бой: запусти время кнопкой «Пуск» (Пробел) или жми «+1 день» слева, пока в журнале "
                             "не появится перевес на твоей стороне. Счёт войны — в панели «Армия».",
         highlight="btn_next_turn",
         done=lambda c: c.state.stats["battles_won"].get(c.player.iso, 0) >= 1),
    Step("finish", lambda c: "Обучение завершено! Расширяй экономику, исследуй технологии, заключай союзы "
                             "и захватывай мир (победа — контроль 50% провинций). Нажми «Далее», запусти время (Пробел) и играй свободно."),
]
