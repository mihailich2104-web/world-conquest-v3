"""Войны: счёт по оккупации, капитуляция, мирные договоры с передачей регионов.

Счёт войны (−100..100, «+» — перевес атакующих) считается по доле ценности территорий, которую одна сторона
отобрала у другой (столица весит много), плюс затухающий импульс выигранных сражений. Победитель при мире
забирает занятые регионы только если перевес достаточный; иначе все регионы возвращаются владельцам.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .game_state import GameState

log = logging.getLogger(__name__)

TRUCE_DAYS = 30
SCORE_WIN = 100.0
ANNEX_SCORE = 20.0           # перевес, начиная с которого победитель оставляет занятые регионы


@dataclass
class War:
    """Война между двумя коалициями. score > 0 — перевес атакующих (−100..100)."""
    id: int
    attackers: list[str]
    defenders: list[str]
    main_attacker: str
    main_defender: str
    score: float = 0.0
    days: int = 0
    momentum: float = 0.0                                   # влияние выигранных сражений (затухает)
    captured: list[str] = field(default_factory=list)       # регионы, занятые за эту войну
    battles: int = 0
    started: int = 0

    def side_of(self, iso: str) -> str:
        """'a' — атакующий, 'd' — защитник, '' — не участвует."""
        return "a" if iso in self.attackers else "d" if iso in self.defenders else ""

    def score_for(self, iso: str) -> float:
        """Счёт войны с точки зрения страны iso."""
        return self.score if iso in self.attackers else -self.score

    def enemy_main(self, iso: str) -> str:
        """Главный противник для страны iso."""
        return self.main_defender if iso in self.attackers else self.main_attacker

    def to_dict(self) -> dict[str, Any]:
        """Сериализация войны."""
        d = dict(self.__dict__)
        d["captured"] = list(self.captured)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "War":
        """Восстановление войны (поля новых версий необязательны)."""
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def rebuild_links(state: "GameState") -> None:
    """Пересчитывает at_war_with у всех стран по списку активных войн."""
    for c in state.countries.values():
        c.at_war_with = set()
    for w in state.wars:
        for a in w.attackers:
            for d in w.defenders:
                if a in state.countries and d in state.countries:
                    state.countries[a].at_war_with.add(d)
                    state.countries[d].at_war_with.add(a)


def find_war(state: "GameState", a: str, b: str) -> "War | None":
    """Ищет войну, где a и b на разных сторонах."""
    for w in state.wars:
        if w.side_of(a) and w.side_of(b) and w.side_of(a) != w.side_of(b):
            return w
    return None


def start_war(state: "GameState", attacker: str, defender: str) -> War:
    """Создаёт войну; союзники сторон вступают автоматически."""
    A, D = state.countries[attacker], state.countries[defender]
    atk, dfn = [attacker], [defender]
    for ally in sorted(A.allies):
        if ally in state.countries and state.countries[ally].alive and ally not in D.allies and ally != defender:
            atk.append(ally)
    for ally in sorted(D.allies):
        if ally in state.countries and state.countries[ally].alive and ally not in atk and ally != attacker:
            dfn.append(ally)
    war = War(state.next_war_id, atk, dfn, attacker, defender, started=state.date.toordinal())
    state.next_war_id += 1
    state.wars.append(war)
    rebuild_links(state)
    state.log_event(f"Война: {A.name} объявляет войну: {D.name}"
                    + (f" (союзники: {len(atk) + len(dfn) - 2})" if len(atk) + len(dfn) > 2 else ""),
                    attacker, important=True, kind="war", isos=atk + dfn)
    return war


def compute_score(state: "GameState", war: War) -> float:
    """Счёт по доле ценности земель, занятых противником, плюс импульс сражений."""
    from .armies import region_value
    own = {"a": 0.0, "d": 0.0}
    lost = {"a": 0.0, "d": 0.0}
    for rid, o in state.owner.items():
        side = war.side_of(o)
        if not side:
            continue
        v = region_value(state, rid)
        own[side] += v
        cs = war.side_of(state.ctrl.get(rid, o))
        if cs and cs != side:
            lost[side] += v
    taken_from_d = lost["d"] / own["d"] if own["d"] else 1.0
    taken_from_a = lost["a"] / own["a"] if own["a"] else 1.0
    return max(-SCORE_WIN, min(SCORE_WIN, 100.0 * (taken_from_d - taken_from_a) + war.momentum))


def occupied_share(state: "GameState", war: War, iso: str) -> float:
    """Доля ценности земель державы iso, занятых противниками по этой войне."""
    from .armies import region_value
    tot = occ = 0.0
    me = war.side_of(iso)
    for rid, o in state.owner.items():
        if o != iso:
            continue
        v = region_value(state, rid)
        tot += v
        cs = war.side_of(state.ctrl.get(rid, o))
        if cs and cs != me:
            occ += v
    return occ / tot if tot else 1.0


def daily_update(state: "GameState", war: War) -> None:
    """Сутки войны: пересчёт счёта, капитуляция полностью занятых держав, окончание при решающем перевесе."""
    war.days += 1
    war.momentum *= 0.97
    A = [i for i in war.attackers if state.countries[i].alive]
    D = [i for i in war.defenders if state.countries[i].alive]
    if not A or not D:
        end_war(state, war, "нет участников")
        return
    war.attackers, war.defenders = A, D
    war.score = compute_score(state, war)
    for iso in list(A) + list(D):
        # капитуляция: почти вся земля занята
        if state.countries[iso].alive and occupied_share(state, war, iso) >= 0.9 and war.days >= 10:
            capitulate(state, war, iso)
            if war not in state.wars:
                return
    if war.score >= SCORE_WIN or war.score <= -SCORE_WIN:
        end_war(state, war, "капитуляция" if war.score > 0 else "наступление провалено")


def capitulate(state: "GameState", war: War, loser: str) -> None:
    """Держава капитулирует: противники по войне забирают занятое, участница выходит из войны."""
    side = war.side_of(loser)
    enemy = "d" if side == "a" else "a"
    taken = 0
    for rid in [r for r, o in state.owner.items() if o == loser]:
        ctl = state.ctrl.get(rid, loser)
        if war.side_of(ctl) == enemy:
            state.set_owner(rid, ctl, "cede")
            taken += 1
    state.log_event(f"Капитуляция: {state.countries[loser].name} сложила оружие, потеряно регионов: {taken}",
                    loser, True, kind="peace", isos=[loser])
    _after_transfer(state)
    if state.countries[loser].alive:
        for lst in (war.attackers, war.defenders):
            if loser in lst:
                lst.remove(loser)
        state.countries[loser].stability = max(0.0, state.countries[loser].stability - 10)
        if not war.attackers or not war.defenders or loser in (war.main_attacker, war.main_defender):
            end_war(state, war, "капитуляция")
        else:
            rebuild_links(state)


def end_war(state: "GameState", war: War, reason: str, force_white: bool = False) -> None:
    """Завершает войну: занятые регионы переходят победителю (счёт ≥ порога) или возвращаются; перемирие."""
    if war not in state.wars:
        return
    winner = ""
    if not force_white:
        if war.score >= ANNEX_SCORE:
            winner = "a"
        elif war.score <= -ANNEX_SCORE:
            winner = "d"
    gained: dict[str, int] = {}
    for rid in [r for r, o in state.owner.items() if r in state.ctrl]:
        own, ctl = state.owner[rid], state.ctrl[rid]
        so, sc = war.side_of(own), war.side_of(ctl)
        if not so or not sc or so == sc:
            continue
        if winner and sc == winner:
            state.set_owner(rid, ctl, "cede")
            gained[ctl] = gained.get(ctl, 0) + 1
        else:
            state.set_ctrl(rid, own)
    state.wars.remove(war)
    until = state.date.toordinal() + TRUCE_DAYS
    for a in war.attackers:
        for d in war.defenders:
            state.truces[state.truce_key(a, d)] = until
    for iso in war.attackers + war.defenders:                 # армии покидают чужие земли мирного времени
        _evict_armies(state, iso)
    rebuild_links(state)
    _after_transfer(state)
    extra = ""
    if gained:
        extra = "; " + ", ".join(f"{state.countries[i].name} +{n} рег." for i, n in gained.items())
    state.log_event(f"Мир: война завершена ({reason}){extra}", war.main_attacker, True, kind="peace",
                    isos=war.attackers + war.defenders)


def _evict_armies(state: "GameState", iso: str) -> None:
    """После мира армии, оказавшиеся на закрытой территории, отходят домой (не телепортируются: идут маршрутом)."""
    from . import armies as am
    for a in am.armies_of(state, iso):
        if am.can_enter(state, iso, a.region):
            continue
        a.fight, a.fight_days = "", 0
        ok = am._retreat(state, a)
        if not ok:
            home = am.capital_of(state, iso)
            if home and am.can_enter(state, iso, home):
                res = am.find_path(state, a, home)
                if res and res[0]:
                    a.path, a.target, a.order = res[0], home, "retreat"


def _after_transfer(state: "GameState") -> None:
    """Страны без регионов исчезают; столицы, потерянные по договору, переносятся."""
    from .armies import capital_of
    have: dict[str, int] = {}
    for o in state.owner.values():
        have[o] = have.get(o, 0) + 1
    for c in list(state.countries.values()):
        if not c.alive:
            continue
        if have.get(c.iso, 0) == 0:
            _kill(state, c.iso)
        elif state.owner.get(c.capital_region) != c.iso:
            new = capital_of(state, c.iso)
            if new:
                c.capital_region = new
                c.capital_pos = None
                c.stability = max(0.0, c.stability - 8.0)
                state.log_event(f"{c.name}: столица перенесена в «{state.geo.name.get(new, new)}»", c.iso, True,
                                kind="econ", isos=[c.iso])
    state.invalidate_cache()
    state.check_victory()


def _kill(state: "GameState", iso: str) -> None:
    """Держава прекращает существование (нет регионов)."""
    L = state.countries[iso]
    L.alive = False
    L.army = L.navy = L.airforce = 0
    for aid in [i for i, a in state.armies.items() if a.owner == iso]:
        del state.armies[aid]
        state.removed_armies.append(aid)
    for c in state.countries.values():
        c.allies.discard(iso)
        c.pacts.discard(iso)
        c.trade.discard(iso)
    for w in list(state.wars):
        for lst in (w.attackers, w.defenders):
            if iso in lst:
                lst.remove(iso)
        if iso in (w.main_attacker, w.main_defender) or not w.attackers or not w.defenders:
            if w in state.wars:
                end_war(state, w, "капитуляция", force_white=False)
    rebuild_links(state)
    state.log_event(f"⚑ {L.name} прекратила существование", iso, True, kind="war", isos=[iso])
    if len(state.humans) > 1:
        state.humans.discard(iso)           # LAN: павший игрок наблюдает, партия продолжается
    elif iso == state.player_iso:
        state.game_over = "defeat"


def annex(state: "GameState", winner_iso: str, loser_iso: str) -> None:
    """Победитель получает все регионы проигравшего и часть его потенциала (полная аннексия)."""
    W, L = state.countries[winner_iso], state.countries[loser_iso]
    if not L.alive or winner_iso == loser_iso:
        return
    for rid in [r for r, o in state.owner.items() if o == loser_iso]:
        state.set_owner(rid, winner_iso, "cede")
    W.gdp += L.gdp * 0.6
    W.population += L.population
    W.manpower += L.manpower * 0.5
    W.factories += L.factories // 2
    W.army += L.army // 3
    state.stats["annexed"][winner_iso] = state.stats["annexed"].get(winner_iso, 0) + 1
    state.log_event(f"⚑ {L.name} полностью присоединена к {W.name} — границы стёрты", winner_iso, True,
                    kind="peace", isos=[winner_iso, loser_iso])
    _after_transfer(state)


def resolve_day(state: "GameState", war: War) -> None:
    """Совместимость: сутки войны (бои теперь ведут армии на карте)."""
    daily_update(state, war)
