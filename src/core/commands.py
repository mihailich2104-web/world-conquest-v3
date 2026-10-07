"""Единый слой команд игрока. Одиночная игра и хост выполняют их напрямую; LAN-клиент отправляет
их хосту, который проверяет и выполняет (хост авторитетен). Аргументы всегда валидируются."""
from __future__ import annotations

import logging
from typing import Any, Callable

from config import BUILD_NAMES, COSTS
from . import diplomacy as dip

log = logging.getLogger(__name__)
Result = tuple[bool, str]


def _tgt(state, iso: str, args: dict) -> str | None:
    t = args.get("target")
    if not isinstance(t, str) or t not in state.countries or t == iso or not state.countries[t].alive:
        return None
    return t


def _build(state, iso, a) -> Result:
    kind = a.get("kind")
    return state.countries[iso].build(kind) if kind in BUILD_NAMES else (False, "Неизвестный тип строительства")


def _recruit(state, iso, a) -> Result:
    kind = a.get("kind")
    return state.countries[iso].recruit(kind) if kind in ("army", "navy", "air") else (False, "Неизвестный тип юнита")


def _research(state, iso, a) -> Result:
    tid = a.get("tech")
    return state.countries[iso].research(tid) if isinstance(tid, str) else (False, "Неизвестная технология")


def _tax(state, iso, a) -> Result:
    c = state.countries[iso]
    delta = a.get("delta")
    if delta not in (-0.05, 0.05):
        return False, "Некорректное изменение налога"
    c.tax_rate = round(max(0.05, min(0.6, c.tax_rate + delta)), 2)
    return True, f"Налог: {c.tax_rate * 100:.0f}%"


def _dipl(fn: Callable) -> Callable:
    def run(state, iso, a) -> Result:
        t = _tgt(state, iso, a)
        return fn(state, iso, t) if t else (False, "Некорректная цель")
    return run


def _war(state, iso, a) -> Result:
    t = _tgt(state, iso, a)
    return state.countries[iso].declare_war(state, t) if t else (False, "Некорректная цель")


def _peace(state, iso, a) -> Result:
    t = _tgt(state, iso, a)
    return state.countries[iso].propose_peace(state, t) if t else (False, "Некорректная цель")


def _offer(accept: bool) -> Callable:
    def run(state, iso, a) -> Result:
        oid = a.get("id")
        return dip.respond_offer(state, iso, oid, accept) if isinstance(oid, int) else (False, "Некорректное предложение")
    return run


HANDLERS: dict[str, Callable[[Any, str, dict], Result]] = {
    "build": _build, "recruit": _recruit, "research": _research, "tax": _tax,
    "improve": _dipl(dip.improve_relations), "trade": _dipl(dip.propose_trade),
    "pact": _dipl(dip.propose_pact), "alliance": _dipl(dip.propose_alliance),
    "break_alliance": _dipl(dip.break_alliance), "war": _war, "peace": _peace,
    "offer_accept": _offer(True), "offer_decline": _offer(False),
}


def execute(state, iso: str, cmd: dict) -> Result:
    """Выполняет команду от имени страны iso. Возвращает (успех, сообщение)."""
    try:
        if not isinstance(cmd, dict) or cmd.get("t") not in HANDLERS:
            return False, "Неизвестная команда"
        c = state.countries.get(iso)
        if c is None or not c.alive:
            return False, "Ваша страна не существует"
        if state.game_over and not str(state.game_over).startswith("victory:"):
            return False, "Игра окончена"
        ok, msg = HANDLERS[cmd["t"]](state, iso, cmd)
        if ok:
            state.map_dirty = True
        return ok, msg
    except Exception as exc:  # неверные данные клиента не должны ронять хост
        log.exception("Ошибка выполнения команды %s", cmd)
        return False, f"Ошибка команды: {exc.__class__.__name__}"
