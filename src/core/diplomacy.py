"""Дипломатия: отношения, торговля, пакты, союзы, объявление войны, мир."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from . import war as warmod
from .military import combat_power
from .research import tech_bonus

if TYPE_CHECKING:
    from .country import Country
    from .game_state import GameState

log = logging.getLogger(__name__)
MAX_ALLIES = 6
MAX_TRADE = 5
OFFER_DAYS = 30
MAX_OFFERS = 6


def _need(a: "Country", base: int) -> int:
    """Порог отношений с учётом технологий дипломатии proposer'а (−3 за 10% бонуса)."""
    return base - int(tech_bonus(a, "diplomacy") * 30)


def base_relation(a: "Country", b: "Country") -> int:
    """Стартовые отношения по идеологии."""
    if a.ideology == b.ideology:
        return 10
    pair = {a.ideology, b.ideology}
    if pair == {"democracy", "communism"}:
        return -20
    if pair == {"democracy", "autocracy"}:
        return -10
    return 0


def get_relation(state: "GameState", a_iso: str, b_iso: str) -> int:
    """Текущие отношения a → b (−100..100)."""
    a, b = state.countries[a_iso], state.countries[b_iso]
    return a.relations.get(b_iso, base_relation(a, b) - (5 if b_iso in state.neighbors(a_iso) else 0))


def change_relation(state: "GameState", a_iso: str, b_iso: str, delta: int) -> None:
    """Симметрично меняет отношения двух стран."""
    for x, y in ((a_iso, b_iso), (b_iso, a_iso)):
        cur = get_relation(state, x, y)
        state.countries[x].relations[y] = max(-100, min(100, cur + delta))


def hostile(a: "Country", b: "Country") -> bool:
    """Идеологически враждебные страны не заключают союзов."""
    return {a.ideology, b.ideology} == {"democracy", "communism"}


def _pair(state: "GameState", a_iso: str, b_iso: str) -> tuple["Country | None", "Country | None", str]:
    """Проверяет, что обе страны существуют и живы."""
    a, b = state.countries.get(a_iso), state.countries.get(b_iso)
    if not a or not b or a_iso == b_iso:
        return None, None, "Некорректная пара стран"
    if not a.alive or not b.alive:
        return None, None, "Страна не существует"
    if b_iso in a.at_war_with:
        return None, None, "Страны находятся в состоянии войны"
    return a, b, ""


def improve_relations(state: "GameState", a_iso: str, b_iso: str) -> tuple[bool, str]:
    """Дипломатическая миссия: +10 к отношениям за 10 ед. казны."""
    a, b, err = _pair(state, a_iso, b_iso)
    if err:
        return False, err
    if a.treasury < 10:
        return False, "Не хватает казны (10)"
    a.treasury -= 10
    change_relation(state, a_iso, b_iso, round(10 * (1 + tech_bonus(a, "diplomacy"))))
    return True, f"Отношения с {b.name}: {get_relation(state, a_iso, b_iso):+d}"


def propose_trade(state: "GameState", a_iso: str, b_iso: str) -> tuple[bool, str]:
    """Торговое соглашение (+5% к доходу обеим сторонам)."""
    a, b, err = _pair(state, a_iso, b_iso)
    if err:
        return False, err
    if b_iso in a.trade:
        return False, "Соглашение уже заключено"
    if len(a.trade) >= MAX_TRADE or len(b.trade) >= MAX_TRADE:
        return False, f"Достигнут лимит торговых партнёров ({MAX_TRADE})"
    if get_relation(state, b_iso, a_iso) < _need(a, 0):
        return False, f"{b.name} отказывает: отношения слишком плохие"
    a.trade.add(b_iso)
    b.trade.add(a_iso)
    change_relation(state, a_iso, b_iso, 5)
    return True, f"Торговое соглашение с {b.name} заключено"


def propose_pact(state: "GameState", a_iso: str, b_iso: str) -> tuple[bool, str]:
    """Пакт о ненападении."""
    a, b, err = _pair(state, a_iso, b_iso)
    if err:
        return False, err
    if b_iso in a.pacts:
        return False, "Пакт уже действует"
    if get_relation(state, b_iso, a_iso) < _need(a, 10):
        return False, f"{b.name} отказывает: нужны отношения не ниже {_need(a, 10):+d}"
    a.pacts.add(b_iso)
    b.pacts.add(a_iso)
    change_relation(state, a_iso, b_iso, 5)
    return True, f"Пакт о ненападении с {b.name} подписан"


def propose_alliance(state: "GameState", a_iso: str, b_iso: str) -> tuple[bool, str]:
    """Военный союз."""
    a, b, err = _pair(state, a_iso, b_iso)
    if err:
        return False, err
    if b_iso in a.allies:
        return False, "Вы уже союзники"
    if hostile(a, b):
        return False, f"{b.name} отказывает: идеологические противоречия"
    if len(a.allies) >= MAX_ALLIES or len(b.allies) >= MAX_ALLIES:
        return False, f"Достигнут лимит союзников ({MAX_ALLIES})"
    if get_relation(state, b_iso, a_iso) < _need(a, 40):
        return False, f"{b.name} отказывает: нужны отношения не ниже {_need(a, 40):+d}"
    a.allies.add(b_iso)
    b.allies.add(a_iso)
    change_relation(state, a_iso, b_iso, 10)
    return True, f"Союз с {b.name} заключён"


def break_alliance(state: "GameState", a_iso: str, b_iso: str) -> tuple[bool, str]:
    """Расторжение союза."""
    a, b = state.countries[a_iso], state.countries[b_iso]
    if b_iso not in a.allies:
        return False, "Вы не союзники"
    a.allies.discard(b_iso)
    b.allies.discard(a_iso)
    change_relation(state, a_iso, b_iso, -20)
    return True, f"Союз с {b.name} расторгнут"


def can_reach(state: "GameState", a_iso: str, b_iso: str) -> bool:
    """Можно ли воевать: общая граница, либо флот ≥3 кораблей (десант), либо режим обучения."""
    return state.tutorial or b_iso in state.neighbors(a_iso) or state.countries[a_iso].navy >= 3


def truce_left(state: "GameState", a_iso: str, b_iso: str) -> int:
    """Сколько дней осталось до конца перемирия (0 — нет перемирия)."""
    return max(0, state.truces.get(state.truce_key(a_iso, b_iso), 0) - state.date.toordinal())


def declare_war(state: "GameState", a_iso: str, b_iso: str) -> tuple[bool, str]:
    """Объявляет войну b со стороны a (с проверками условий)."""
    a, b, err = _pair(state, a_iso, b_iso)
    if err:
        return False, err
    if b_iso in a.allies:
        return False, "Нельзя воевать с союзником — расторгните союз"
    left = truce_left(state, a_iso, b_iso)
    if left:
        return False, f"Действует перемирие ещё {left} дн."
    if not can_reach(state, a_iso, b_iso):
        return False, "Нет общей границы (для десанта нужно ≥3 кораблей)"
    if a.army < 1:
        return False, "У вас нет армии"
    if b_iso in a.pacts:
        a.pacts.discard(b_iso)
        b.pacts.discard(a_iso)
        a.stability = max(0.0, a.stability - 5)
        state.log_event(f"{a.name} нарушает пакт о ненападении с {b.name}", a_iso)
    a.trade.discard(b_iso)
    b.trade.discard(a_iso)
    change_relation(state, a_iso, b_iso, -50)
    state.stats.setdefault("wars_declared", {}).setdefault(a_iso, []).append(state.date.toordinal())
    warmod.start_war(state, a_iso, b_iso)
    return True, f"Война объявлена: {b.name}"


def ai_accepts_peace(state: "GameState", war: warmod.War, receiver: str) -> tuple[bool, str]:
    """Решение получателя мирного предложения (ИИ)."""
    s = war.score_for(receiver)
    if war.days < 5:
        return False, "Слишком рано для мира"
    if s >= 40:
        return False, "Противник уверен в победе и отказывается"
    if s <= 15 or war.days >= 45:
        return True, "Мир принят"
    return False, "Противник пока хочет продолжать войну"


def propose_peace(state: "GameState", a_iso: str, b_iso: str) -> tuple[bool, str]:
    """Предлагает мир: завершает войну между a и b (или выводит одного участника)."""
    w = warmod.find_war(state, a_iso, b_iso)
    if w is None:
        return False, "Вы не воюете с этой страной"
    ok, why = ai_accepts_peace(state, w, b_iso)
    if not ok:
        return False, why
    if a_iso in (w.main_attacker, w.main_defender) and b_iso in (w.main_attacker, w.main_defender):
        gain = state.countries[b_iso].treasury * 0.3 if w.score_for(a_iso) >= 50 else 0.0
        state.countries[b_iso].treasury -= gain
        state.countries[a_iso].treasury += gain
        warmod.end_war(state, w, "мирный договор")
        return True, "Мирный договор подписан" + (f"; контрибуция {gain:.0f}" if gain else "")
    # участник второго плана просто выходит из войны
    for lst in (w.attackers, w.defenders):
        if a_iso in lst and len(lst) > 1:
            lst.remove(a_iso)
    warmod.rebuild_links(state)
    return True, "Вы вышли из войны"


# ---------- предложения ИИ людям ----------
OFFER_TEXT = {"trade": "торговое соглашение", "pact": "пакт о ненападении", "alliance": "военный союз",
              "peace": "мирный договор"}


def make_offer(state: "GameState", frm: str, to: str, kind: str) -> bool:
    """ИИ предлагает человеку соглашение; предложение живёт OFFER_DAYS дней."""
    if kind not in OFFER_TEXT or any(o["frm"] == frm and o["to"] == to and o["kind"] == kind for o in state.offers):
        return False
    if sum(1 for o in state.offers if o["to"] == to) >= MAX_OFFERS:
        return False
    state.offers.append({"id": state.next_offer_id, "to": to, "frm": frm, "kind": kind,
                         "until": state.date.toordinal() + OFFER_DAYS})
    state.next_offer_id += 1
    state.log_event(f"{state.countries[frm].name} предлагает: {OFFER_TEXT[kind]}", to, True)
    return True


def expire_offers(state: "GameState") -> None:
    """Удаляет просроченные и потерявшие смысл предложения."""
    now = state.date.toordinal()
    state.offers = [o for o in state.offers if o["until"] >= now and state.countries[o["frm"]].alive
                    and state.countries[o["to"]].alive]


def respond_offer(state: "GameState", iso: str, offer_id: int, accept: bool) -> tuple[bool, str]:
    """Человек принимает/отклоняет предложение ИИ."""
    off = next((o for o in state.offers if o["id"] == offer_id and o["to"] == iso), None)
    if off is None:
        return False, "Предложение больше не действует"
    state.offers.remove(off)
    frm, to, kind = off["frm"], off["to"], off["kind"]
    if not accept:
        change_relation(state, frm, to, -2)
        return True, "Предложение отклонено"
    A, B = state.countries[frm], state.countries[to]
    if kind == "peace":
        w = warmod.find_war(state, frm, to)
        if w is None:
            return False, "Войны уже нет"
        warmod.end_war(state, w, "мирный договор")
        return True, f"Мир с {A.name} заключён"
    if kind == "trade" and len(A.trade) < MAX_TRADE and len(B.trade) < MAX_TRADE and to not in A.trade:
        A.trade.add(to); B.trade.add(frm); change_relation(state, frm, to, 5)
        return True, f"Торговое соглашение с {A.name} заключено"
    if kind == "pact":
        A.pacts.add(to); B.pacts.add(frm); change_relation(state, frm, to, 5)
        return True, f"Пакт о ненападении с {A.name} подписан"
    if kind == "alliance" and len(A.allies) < MAX_ALLIES and len(B.allies) < MAX_ALLIES and not hostile(A, B):
        A.allies.add(to); B.allies.add(frm); change_relation(state, frm, to, 10)
        return True, f"Союз с {A.name} заключён"
    return False, "Условия соглашения больше не выполняются"


def aggressor_level(state: "GameState", iso: str, days: int = 120) -> int:
    """Сколько войн объявила страна за последние days дней (для реакции ИИ)."""
    now = state.date.toordinal()
    return sum(1 for d in state.stats.get("wars_declared", {}).get(iso, []) if now - d <= days)
