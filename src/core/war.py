"""Войны: формула боя, оккупация (аннексия), мирные договоры."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .military import combat_power, lose_units

if TYPE_CHECKING:
    from .game_state import GameState

log = logging.getLogger(__name__)

TRUCE_DAYS = 30
DAILY_LOSS = 0.02
SCORE_WIN = 100.0


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
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "War":
        """Восстановление войны."""
        return cls(**d)


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
    war = War(state.next_war_id, atk, dfn, attacker, defender)
    state.next_war_id += 1
    state.wars.append(war)
    rebuild_links(state)
    state.log_event(f"Война: {A.name} объявляет войну: {D.name}"
                    + (f" (союзники: {len(atk) + len(dfn) - 2})" if len(atk) + len(dfn) > 2 else ""),
                    attacker, important=True)
    return war


def end_war(state: "GameState", war: War, reason: str) -> None:
    """Завершает войну и ставит перемирие между бывшими противниками."""
    if war in state.wars:
        state.wars.remove(war)
    until = state.date.toordinal() + TRUCE_DAYS
    for a in war.attackers:
        for d in war.defenders:
            state.truces[state.truce_key(a, d)] = until
    rebuild_links(state)
    state.log_event(f"Мир: война завершена ({reason})", war.main_attacker, important=True)


def annex(state: "GameState", winner_iso: str, loser_iso: str) -> None:
    """Победитель захватывает все провинции проигравшего и часть его потенциала."""
    W, L = state.countries[winner_iso], state.countries[loser_iso]
    if not L.alive or winner_iso == loser_iso:
        return
    for prov, owner in state.owner.items():
        if owner == loser_iso:
            state.owner[prov] = winner_iso
    W.gdp += L.gdp * 0.6
    W.population += L.population
    W.manpower += L.manpower * 0.5
    W.factories += L.factories // 2
    W.army += L.army // 3
    L.alive = False
    L.army = L.navy = L.airforce = 0
    for c in state.countries.values():
        c.allies.discard(loser_iso)
        c.pacts.discard(loser_iso)
        c.trade.discard(loser_iso)
    for w in list(state.wars):               # выбывший покидает все войны
        for lst in (w.attackers, w.defenders):
            if loser_iso in lst:
                lst.remove(loser_iso)
        if loser_iso in (w.main_attacker, w.main_defender) or not w.attackers or not w.defenders:
            end_war(state, w, "капитуляция")
    rebuild_links(state)
    state.stats["annexed"][winner_iso] = state.stats["annexed"].get(winner_iso, 0) + 1
    state.invalidate_cache()
    state.map_dirty = True
    state.log_event(f"⚑ {L.name} полностью присоединена к {W.name} — границы стёрты", winner_iso, important=True)
    if len(state.humans) > 1:
        state.humans.discard(loser_iso)        # LAN: павший игрок наблюдает, партия продолжается
    elif loser_iso == state.player_iso:
        state.game_over = "defeat"
    state.check_victory()


def resolve_day(state: "GameState", war: War) -> None:
    """Один день боёв: power_atk = Σ(юниты·tech·мораль), power_def — то же × укрепления."""
    rng = state.rng
    A = [state.countries[i] for i in war.attackers if state.countries[i].alive]
    D = [state.countries[i] for i in war.defenders if state.countries[i].alive]
    war.days += 1
    if not A or not D:
        end_war(state, war, "нет участников")
        return
    pa = sum(combat_power(c) for c in A) * rng.uniform(0.9, 1.1)
    pd = sum(combat_power(c, defending=True) for c in D) * rng.uniform(0.9, 1.1)
    if pa + pd <= 0:
        return
    ratio = pa / (pa + pd)
    war.score = max(-SCORE_WIN, min(SCORE_WIN, war.score + (ratio - 0.5) * 20))
    atk_win = ratio > 0.5
    for side, won, loss in ((A, atk_win, DAILY_LOSS * (1 - ratio)), (D, not atk_win, DAILY_LOSS * ratio)):
        for c in side:
            lose_units(c, loss, rng)
            c.morale = max(0.4, min(1.5, c.morale + (0.01 if won else -0.02)))
            c.stability = max(0.0, c.stability - 0.03)
            if c.army > 0:
                key = "battles_won" if won else "battles_lost"
                state.stats[key][c.iso] = state.stats[key].get(c.iso, 0) + 1
    MA, MD = state.countries[war.main_attacker], state.countries[war.main_defender]
    if MD.army + MD.navy + MD.airforce == 0:
        war.score = min(SCORE_WIN, war.score + 2)
    if MA.army + MA.navy + MA.airforce == 0:
        war.score = max(-SCORE_WIN, war.score - 2)
    if state.player_iso and war.side_of(state.player_iso):
        who = "атакующих" if atk_win else "защитников"
        state.log_event(f"Бой {MA.name} — {MD.name}: перевес у {who} (счёт {war.score:+.0f})",
                        state.player_iso)
    if war.score >= SCORE_WIN:
        annex(state, war.main_attacker, war.main_defender)
        if war in state.wars:
            end_war(state, war, "капитуляция")
    elif war.score <= -SCORE_WIN:
        MA.stability = max(0.0, MA.stability - 10)
        end_war(state, war, "наступление провалено")
