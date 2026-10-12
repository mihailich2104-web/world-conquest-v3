"""Армии на карте: приказы, маршруты, постепенное движение, бои, оккупация и снабжение (без pygame).

Модель. Армия стоит в регионе или идёт по маршруту (список регионов); позиция на переходе — доля progress
пути region → path[0]. Бой начинается, когда в одном регионе оказываются армии воюющих держав; победитель
остаётся, проигравший отступает. Регион переходит под контроль (ctrl) армии, которая простояла в нём нужное
число дней без противника; владелец (owner) меняется только мирным договором. Всё состояние — в GameState
и в сохранении; графика лишь рисует его.
"""
from __future__ import annotations

import heapq
import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterator, Optional

if TYPE_CHECKING:
    from .game_state import GameState

log = logging.getLogger(__name__)

MAX_STACK = 14                # дивизий в одной армии
BASE_SPEED_KM = 95.0          # км/сутки по равнине (игровая абстракция)
SEA_SPEED_KM = 330.0
SEA_LOAD_DAYS = 3.0
MORALE_REST = 2.0
RETREAT_MORALE = 24.0
INF = float("inf")


@dataclass
class Army:
    """Соединение: сила в дивизиях (дробная), положение, маршрут, мораль и снабжение."""
    id: int
    owner: str
    name: str
    strength: float
    region: str
    max_strength: float = 0.0
    path: list[str] = field(default_factory=list)
    progress: float = 0.0          # 0..1 пути region → path[0]
    morale: float = 85.0
    supply: float = 100.0
    order: str = "idle"            # idle | move | retreat
    target: str = ""
    fight: str = ""                # регион, где армия сейчас сражается ("" — не сражается)
    fight_days: int = 0
    lost: float = 0.0
    sea: bool = False              # текущий переход — морской
    status: str = "idle"           # idle | moving | fighting | occupying | retreating | resting | no_supply

    def __post_init__(self) -> None:
        if self.max_strength <= 0:
            self.max_strength = self.strength

    @property
    def units(self) -> int:
        """Дивизий для показа (минимум 1, пока армия существует)."""
        return max(1, int(round(self.strength)))

    def to_dict(self) -> dict[str, Any]:
        """Полная точность: загрузка сохранения должна продолжать партию ровно так же (float → JSON без потерь)."""
        d = dict(self.__dict__)
        d["path"] = list(self.path)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Army":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


# ---------- отношения и доступ ----------
def ctrl_of(state: "GameState", rid: str) -> str:
    """Кто фактически контролирует регион."""
    return state.ctrl.get(rid) or state.owner.get(rid, "")


def hostile(state: "GameState", a: str, b: str) -> bool:
    """Воюют ли державы a и b."""
    c = state.countries.get(a)
    return bool(c and b in c.at_war_with)


def friendly(state: "GameState", a: str, b: str) -> bool:
    """Одна держава или союзники."""
    if a == b:
        return True
    c = state.countries.get(a)
    return bool(c and b in c.allies)


def can_enter(state: "GameState", who: str, rid: str) -> bool:
    """Можно ли армии державы who войти в регион: свой/союзный или вражеский (идёт война)."""
    ctl, own = ctrl_of(state, rid), state.owner.get(rid, "")
    if not ctl:
        return False
    return friendly(state, who, ctl) or hostile(state, who, ctl) or hostile(state, who, own)


def region_name(state: "GameState", rid: str) -> str:
    return state.geo.name.get(rid, rid)


def armies_of(state: "GameState", iso: str) -> list[Army]:
    return [a for a in state.armies.values() if a.owner == iso]


def total_strength(state: "GameState", iso: str) -> float:
    return sum(a.strength for a in state.armies.values() if a.owner == iso)


def capital_of(state: "GameState", iso: str) -> str:
    """Регион-столица державы (с учётом переноса столицы)."""
    c = state.countries.get(iso)
    rid = getattr(c, "capital_region", "") if c else ""
    if rid and state.owner.get(rid) == iso:
        return rid
    owned = [r for r, o in state.owner.items() if o == iso]
    if not owned:
        return rid
    return max(owned, key=lambda r: state.geo.area.get(r, 0.0))


# ---------- время в пути ----------
def leg_days(state: "GameState", who: str, a: str, b: str, sea: bool = False) -> float:
    """Суток на переход между центрами регионов a → b."""
    geo = state.geo
    if sea:
        return SEA_LOAD_DAYS + geo.dist_km(a, b) / SEA_SPEED_KM
    sp = (geo.terrain_of(a)[0] + geo.terrain_of(b)[0]) / 2.0
    c = state.countries.get(who)
    infra = 0.0
    if c is not None:
        try:
            from .research import tech_bonus
            infra = tech_bonus(c, "infra")
        except Exception:                                       # noqa: BLE001 — нет дерева технологий (тесты)
            infra = 0.0
    return max(2.0, geo.dist_km(a, b) / (BASE_SPEED_KM * sp * (1.0 + 0.5 * infra)))


def _links(state: "GameState", who: str, rid: str, navy_ok: bool) -> Iterator[tuple[str, bool]]:
    geo = state.geo
    for n in geo.adj.get(rid, ()):
        yield n, False
    if navy_ok and rid in geo.coastal:
        for _, n in geo.sea_links(rid):
            yield n, True


def has_fleet(state: "GameState", who: str) -> bool:
    """Достаточно ли флота для морской переброски."""
    c = state.countries.get(who)
    return bool(c and c.navy >= 2)


def dijkstra(state: "GameState", who: str, start: str, target: Optional[str] = None,
             limit: float = INF) -> tuple[dict[str, float], dict[str, str]]:
    """Кратчайшие времена пути (сутки) от start с учётом доступа; target — ранний выход."""
    navy_ok = has_fleet(state, who)
    best = {start: 0.0}
    prev: dict[str, str] = {}
    heap = [(0.0, start)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > best.get(u, INF):
            continue
        if u == target:
            break
        for v, sea in _links(state, who, u, navy_ok):
            if not can_enter(state, who, v):
                continue
            nd = d + leg_days(state, who, u, v, sea)
            if nd < best.get(v, INF) and nd <= limit:
                best[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))
    return best, prev


def find_path(state: "GameState", a: Army, target: str) -> Optional[tuple[list[str], float]]:
    """Маршрут (без стартового региона) и суток в пути; None — недостижимо."""
    start = a.path[0] if (a.path and a.progress > 0) else a.region
    head = [a.path[0]] if (a.path and a.progress > 0) else []
    if target == start:
        return head, 0.0
    best, prev = dijkstra(state, a.owner, start, target)
    if target not in best:
        return None
    chain = [target]
    while chain[-1] != start:
        chain.append(prev[chain[-1]])
    chain.reverse()
    path = head + chain[1:] if head else chain[1:]
    extra = (1.0 - a.progress) * leg_days(state, a.owner, a.region, a.path[0]) if head else 0.0
    return path, best[target] + extra


def eta_days(state: "GameState", a: Army) -> float:
    """Сколько суток до прибытия в конечную точку маршрута (−1 — нет маршрута)."""
    if not a.path:
        return -1.0
    total = (1.0 - a.progress) * leg_days(state, a.owner, a.region, a.path[0], a.sea) if a.progress > 0 \
        else leg_days(state, a.owner, a.region, a.path[0], _is_sea(state, a.region, a.path[0]))
    for x, y in zip(a.path, a.path[1:]):
        total += leg_days(state, a.owner, x, y, _is_sea(state, x, y))
    return total


def _is_sea(state: "GameState", a: str, b: str) -> bool:
    return b not in state.geo.adj.get(a, ())


# ---------- создание ----------
def new_army(state: "GameState", iso: str, strength: float, region: str, name: str = "") -> Army:
    """Создаёт армию (id выдаёт состояние)."""
    aid = state.next_army_id
    state.next_army_id += 1
    no = state.army_no.get(iso, 0) + 1
    state.army_no[iso] = no
    a = Army(aid, iso, name or f"{no}-я армия", float(strength), region)
    state.armies[aid] = a
    return a


def init_armies(state: "GameState") -> None:
    """Раскладывает дивизии каждой державы по армиям: столица + приграничные регионы."""
    state.armies.clear()
    state.army_no.clear()
    state.next_army_id = 1
    for iso in sorted(state.countries):
        c = state.countries[iso]
        if not c.alive or c.army <= 0:
            continue
        owned = [r for r, o in state.owner.items() if o == iso]
        if not owned:
            continue
        cap = capital_of(state, iso)
        n = c.army
        k = min(8, max(1, math.ceil(n / 5)))
        border = sorted((r for r in owned if r != cap and any(state.owner.get(x) not in (iso, None)
                                                              for x in state.geo.adj.get(r, ()))),
                        key=lambda r: -state.geo.area.get(r, 0.0))
        spots = [cap] + border
        for i in range(k):
            share = n // k + (1 if i < n % k else 0)
            if share > 0:
                new_army(state, iso, share, spots[i % len(spots)] if spots else cap)


def reconcile(state: "GameState") -> None:
    """Сводит число дивизий в армиях со счётчиком Country.army (найм/потери/события меняют счётчик)."""
    by: dict[str, list[Army]] = {}
    for a in state.armies.values():
        by.setdefault(a.owner, []).append(a)
    for iso, c in state.countries.items():
        arms = by.get(iso, [])
        have = sum(a.strength for a in arms)
        want = float(c.army) if c.alive else 0.0
        if abs(have - want) < 0.5:
            c.army = int(round(have)) if arms else c.army
            continue
        if want > have:                                  # новые дивизии — в столицу
            cap = capital_of(state, iso)
            add = want - have
            home = next((a for a in sorted(arms, key=lambda x: x.id)
                         if a.region == cap and not a.path and not a.fight and a.strength + add <= MAX_STACK), None)
            if home is not None:
                home.strength += add
                home.max_strength = max(home.max_strength, home.strength)
            elif cap:
                while add > 0.5:                                # стеки не больше MAX_STACK дивизий
                    part = min(add, float(MAX_STACK))
                    new_army(state, iso, part, cap)
                    add -= part
        else:                                            # потери снаружи (события, роспуск) — со всех армий пропорционально
            cut = have - want
            for a in arms:
                a.strength = max(0.0, a.strength - cut * a.strength / max(have, 1e-9))
            _drop_dead(state)
        c.army = int(round(sum(a.strength for a in state.armies.values() if a.owner == iso)))


def _drop_dead(state: "GameState") -> None:
    for aid in [i for i, a in state.armies.items() if a.strength < 0.45]:
        del state.armies[aid]


# ---------- приказы (проверяются и для игрока, и для ИИ) ----------
def ops_limit(state: "GameState", iso: str) -> int:
    """Предел одновременных наступательных операций."""
    c = state.countries[iso]
    return 3 + c.army // 6


def _offensive(state: "GameState", who: str, rid: str) -> bool:
    ctl, own = ctrl_of(state, rid), state.owner.get(rid, "")
    return hostile(state, who, ctl) or hostile(state, who, own)


def order_move(state: "GameState", iso: str, army_id: int, target: str) -> tuple[bool, str]:
    """Приказ армии идти в регион target (с проверкой прав, достижимости и предела операций)."""
    a = state.armies.get(army_id)
    if a is None:
        return False, "Армия не найдена"
    if a.owner != iso:
        return False, "Это не ваша армия"
    if target not in state.owner:
        return False, "Неизвестный регион"
    if a.fight:
        return False, "Армия ведёт бой — отдайте приказ об отступлении"
    if target == a.region and not a.path:
        return False, "Армия уже здесь"
    if _offensive(state, iso, target):
        ops = sum(1 for x in armies_of(state, iso) if x.id != a.id and x.path and _offensive(state, iso, x.target))
        if ops >= ops_limit(state, iso):
            return False, f"Предел одновременных операций: {ops_limit(state, iso)}"
    if not can_enter(state, iso, target):
        return False, "Границы закрыты: нужна война или союз"
    res = find_path(state, a, target)
    if res is None:
        return False, "Маршрута нет: путь перекрыт (нужна война, союз или флот ≥ 2)"
    path, days = res
    a.path, a.target, a.order = path, target, "move" if path else "idle"
    a.status = "moving" if path else "idle"
    if a.progress <= 0:
        a.sea = bool(path) and _is_sea(state, a.region, path[0])
    state.touch_army(a)
    return True, f"{a.name}: марш в «{region_name(state, target)}», прибытие через {math.ceil(days)} дн."


def order_stop(state: "GameState", iso: str, army_id: int) -> tuple[bool, str]:
    """Отмена приказа: армия останавливается в ближайшем регионе (посреди перехода остановиться нельзя)."""
    a = state.armies.get(army_id)
    if a is None or a.owner != iso:
        return False, "Это не ваша армия"
    if not a.path:
        return False, "Армия не движется"
    a.path = a.path[:1] if a.progress > 0 else []
    a.target = a.path[0] if a.path else ""
    a.order = "move" if a.path else "idle"
    a.status = "moving" if a.path else "idle"
    state.touch_army(a)
    return True, f"{a.name}: приказ отменён"


def order_retreat(state: "GameState", iso: str, army_id: int) -> tuple[bool, str]:
    """Отход в ближайший дружественный регион (из боя — с потерями морали)."""
    a = state.armies.get(army_id)
    if a is None or a.owner != iso:
        return False, "Это не ваша армия"
    if a.progress > 0:
        return False, "Армия на переходе"
    if not _retreat(state, a):
        return False, "Отступать некуда"
    state.touch_army(a)
    return True, f"{a.name}: отступает в «{region_name(state, a.target)}»"


def split_army(state: "GameState", iso: str, army_id: int, n: int) -> tuple[bool, str]:
    """Выделяет из армии n дивизий в новую армию (только стоящую на месте)."""
    a = state.armies.get(army_id)
    if a is None or a.owner != iso:
        return False, "Это не ваша армия"
    if a.progress > 0 or a.fight:
        return False, "Разделять можно только стоящую армию вне боя"
    if not (1 <= n < a.units):
        return False, "Некорректное число дивизий"
    frac = n / a.strength
    b = new_army(state, iso, n, a.region)
    b.morale, b.supply = a.morale, a.supply
    a.strength -= n
    a.max_strength = max(a.strength, a.max_strength * (1 - frac))
    b.max_strength = float(n)
    state.touch_army(a)
    state.touch_army(b)
    return True, f"Из {a.name} выделена {b.name} ({n} див.)"


def merge_armies(state: "GameState", iso: str, ids: list[int]) -> tuple[bool, str]:
    """Объединяет стоящие в одном регионе армии одной державы."""
    arms = [state.armies.get(i) for i in dict.fromkeys(ids)]
    if len(arms) < 2 or any(x is None for x in arms):
        return False, "Выберите две и более армии"
    arms = [x for x in arms if x is not None]
    if any(x.owner != iso for x in arms):
        return False, "Это не ваши армии"
    if len({x.region for x in arms}) != 1 or any(x.progress > 0 or x.fight for x in arms):
        return False, "Армии должны стоять в одном регионе вне боя"
    total = sum(x.strength for x in arms)
    if total > MAX_STACK:
        return False, f"Не более {MAX_STACK} дивизий в одной армии"
    arms.sort(key=lambda x: x.id)
    head = arms[0]
    head.morale = sum(x.morale * x.strength for x in arms) / total
    head.supply = sum(x.supply * x.strength for x in arms) / total
    head.max_strength = sum(x.max_strength for x in arms)
    head.strength = total
    head.path, head.target, head.order, head.status = [], "", "idle", "idle"
    for x in arms[1:]:
        state.armies.pop(x.id, None)
        state.removed_armies.append(x.id)
    state.touch_army(head)
    return True, f"Армии объединены: {head.name} ({head.units} див.)"


def rename_army(state: "GameState", iso: str, army_id: int, name: str) -> tuple[bool, str]:
    a = state.armies.get(army_id)
    name = (name or "").strip()[:28]
    if a is None or a.owner != iso or not name:
        return False, "Нельзя переименовать"
    a.name = name
    state.touch_army(a)
    return True, "Армия переименована"


# ---------- суточный шаг ----------
def tick(state: "GameState") -> None:
    """Один игровой день армий: движение → бои → оккупация → снабжение/мораль → сверка численности."""
    if not state.armies:
        return
    state.battles = {}
    for aid in sorted(state.armies):
        a = state.armies.get(aid)
        if a is not None:
            _move(state, a)
    _battles(state)
    _occupy(state)
    _upkeep(state)
    _drop_dead(state)
    tot: dict[str, float] = {}
    for x in state.armies.values():
        tot[x.owner] = tot.get(x.owner, 0.0) + x.strength
    for iso, c in state.countries.items():                 # счётчик всегда равен сумме армий (уничтоженные не «воскресают»)
        c.army = int(round(tot.get(iso, 0.0)))


def _event(state: "GameState", text: str, isos: list[str], kind: str, important: bool = False) -> None:
    """Запись в журнал для людей, причастных к событию (ИИ-события не засоряют журнал)."""
    hum = [i for i in isos if state.is_human(i)]
    if hum or important and state.is_human(state.player_iso or ""):
        state.log_event(text, hum[0] if hum else isos[0], important, kind=kind, isos=isos)


def _move(state: "GameState", a: Army) -> None:
    if not a.path or a.fight:
        if not a.path and not a.fight and a.status in ("moving", "retreating"):
            a.status = "idle"
        return
    nxt = a.path[0]
    if a.progress <= 0 and not can_enter(state, a.owner, nxt):          # путь перекрыт (мир, смена контроля)
        res = find_path(state, a, a.target) if a.target and a.target != a.region else None
        if res is None or not res[0]:
            a.path, a.order, a.status, a.target = [], "idle", "idle", ""
            _event(state, f"{a.name}: путь перекрыт, армия остановилась в «{region_name(state, a.region)}»",
                   [a.owner], "move")
            return
        a.path = res[0]
        nxt = a.path[0]
    sea = a.sea if a.progress > 0 else _is_sea(state, a.region, nxt)
    a.sea = sea
    days = leg_days(state, a.owner, a.region, nxt, sea)
    k = 1.0
    if a.supply < 40:
        k *= 0.7
    if a.morale < 30:
        k *= 0.85
    a.progress += k / days
    a.status = "retreating" if a.order == "retreat" else "moving"
    if a.progress >= 1.0:
        a.region = nxt
        a.path.pop(0)
        a.progress = 0.0
        a.sea = False
        a.morale = max(0.0, a.morale - 0.6)
        if not a.path:
            was_retreat = a.order == "retreat"
            a.order, a.status, a.target = "idle", "idle", ""
            if not was_retreat:
                _event(state, f"{a.name} прибыла в «{region_name(state, a.region)}»", [a.owner], "move")


def _retreat(state: "GameState", a: Army) -> bool:
    """Назначает отход к ближайшему дружественному региону без вражеских армий."""
    here = a.region
    enemy_at = {x.region for x in state.armies.values() if x.progress <= 0 and hostile(state, a.owner, x.owner)}
    seen = {here}
    frontier = [(here, [])]
    for _ in range(4):
        nxt_front = []
        for rid, path in frontier:
            for n in state.geo.adj.get(rid, ()):
                if n in seen:
                    continue
                seen.add(n)
                if n in enemy_at or not can_enter(state, a.owner, n):
                    continue
                if friendly(state, a.owner, ctrl_of(state, n)):
                    a.path, a.target, a.order, a.status = path + [n], n, "retreat", "retreating"
                    if a.fight:
                        a.morale = max(0.0, a.morale - 8.0)
                        a.strength = max(0.0, a.strength * 0.97)
                    a.fight, a.fight_days, a.progress, a.sea = "", 0, 0.0, False
                    return True
                nxt_front.append((n, path + [n]))
        frontier = nxt_front
    return False


# ---------- бои ----------
def _sides(state: "GameState", group: list[Army]) -> list[list[Army]]:
    """Делит армии региона на коалиции (союзники вместе); возвращает две враждующие стороны либо []."""
    comps: list[list[Army]] = []
    for a in group:
        for comp in comps:
            if any(friendly(state, a.owner, b.owner) for b in comp):
                comp.append(a)
                break
        else:
            comps.append([a])
    if len(comps) < 2:
        return []
    comps.sort(key=lambda c: -sum(x.strength for x in c))
    first = comps[0]
    for other in comps[1:]:
        if any(hostile(state, a.owner, b.owner) for a in first for b in other):
            return [first, other]
    return []


def eff(state: "GameState", a: Army) -> float:
    """Боевая эффективность одной дивизии армии: мораль, снабжение, технологии, ресурсы."""
    c = state.countries[a.owner]
    try:
        from .research import tech_bonus
        tech = 1.0 + tech_bonus(c, "infantry") + tech_bonus(c, "tanks")
    except Exception:                                               # noqa: BLE001
        tech = 1.0
    try:
        from .military import supply_factor
        res = supply_factor(c)
    except Exception:                                               # noqa: BLE001
        res = 1.0
    return tech * (0.35 + 0.65 * a.morale / 100.0) * (0.55 + 0.45 * a.supply / 100.0) * res * (0.9 + 0.1 * c.morale)


def surrounded(state: "GameState", a: Army) -> bool:
    """Окружена ли армия: все соседние регионы под контролем противника."""
    adj = state.geo.adj.get(a.region, ())
    return bool(adj) and all(hostile(state, a.owner, ctrl_of(state, n)) for n in adj)


def _side_power(state: "GameState", side: list[Army], rid: str, defending: bool) -> float:
    p = 0.0
    for a in side:
        v = a.strength * eff(state, a)
        if surrounded(state, a):
            v *= 0.8
        p += v
    if defending:
        holder = ctrl_of(state, rid)
        hc = state.countries.get(holder)
        fort = 1.2 + 0.1 * hc.fort if hc else 1.2
        p *= state.geo.terrain_of(rid)[1] * fort * (1.1 if rid == capital_of(state, state.owner.get(rid, "")) else 1.0)
    air = sum(state.countries[a.owner].airforce for a in side)
    return p * (1.0 + min(0.1, air * 0.004))


def _battles(state: "GameState") -> None:
    by: dict[str, list[Army]] = {}
    for a in state.armies.values():
        if a.progress <= 0:
            by.setdefault(a.region, []).append(a)
    for rid in sorted(by):
        group = by[rid]
        sides = _sides(state, group) if len({a.owner for a in group}) > 1 else []
        if not sides:
            for a in group:
                if a.fight:
                    a.fight, a.fight_days = "", 0
                    a.status = "idle" if not a.path else a.status
            continue
        holder = ctrl_of(state, rid)
        s0, s1 = sides
        def_first = any(friendly(state, x.owner, holder) for x in s0)
        if not def_first and not any(friendly(state, x.owner, holder) for x in s1):
            def_first = any(x.fight == rid for x in s0)          # без «хозяина» обороняется тот, кто уже воюет здесь
        p0 = _side_power(state, s0, rid, def_first) * state.rng.uniform(0.9, 1.1)
        p1 = _side_power(state, s1, rid, not def_first) * state.rng.uniform(0.9, 1.1)
        if p0 + p1 <= 0:
            continue
        r0 = p0 / (p0 + p1)
        state.battles[rid] = max(a.fight_days for a in group) + 1
        for side, r, won in ((s0, r0, r0 >= 0.5), (s1, 1.0 - r0, r0 < 0.5)):
            tot = sum(x.strength for x in side) or 1.0
            lose = min(0.9, 0.05 * (1.0 - r) * 2.0)
            for x in side:
                dmg = x.strength * lose
                x.strength -= dmg
                x.lost += dmg
                state.note_casualties(x.owner, dmg)
                x.morale = max(0.0, min(100.0, x.morale + (1.5 if won else -4.0) - 1.0))
                if not x.fight:
                    x.fight = rid
                    _event(state, f"Сражение в «{region_name(state, rid)}»: {x.name} вступила в бой",
                           [x.owner] + [y.owner for y in (s1 if side is s0 else s0)][:1], "battle")
                x.fight_days += 1
                x.status = "fighting"
                x.path = [] if x.order == "move" else x.path
                if x.order == "move":
                    x.order, x.target = "idle", ""
        loser = s1 if r0 >= 0.5 else s0
        winner = s0 if r0 >= 0.5 else s1
        done = [x for x in loser if x.strength < 0.45]
        for x in done:
            state.armies.pop(x.id, None)
            state.removed_armies.append(x.id)
        broke = [x for x in loser if x.id in state.armies and (x.morale < RETREAT_MORALE or
                                                               (x.fight_days >= 3 and max(r0, 1 - r0) > 0.72))]
        for x in broke:
            if not _retreat(state, x):
                x.morale = max(0.0, x.morale - 5)
                x.strength *= 0.93
        if done or broke:
            ended = not any(x.fight for x in loser if x.id in state.armies and x.fight == rid)
            if ended or not [x for x in loser if x.id in state.armies and x.fight == rid]:
                _battle_end(state, rid, winner, loser)


def _battle_end(state: "GameState", rid: str, winner: list[Army], loser: list[Army]) -> None:
    wi, li = sorted({x.owner for x in winner}), sorted({x.owner for x in loser})
    for i in wi:
        state.stats["battles_won"][i] = state.stats["battles_won"].get(i, 0) + 1
    for i in li:
        state.stats["battles_lost"][i] = state.stats["battles_lost"].get(i, 0) + 1
    for x in winner:
        x.fight, x.fight_days = "", 0
        x.status = "idle" if not x.path else "moving"
        x.morale = min(100.0, x.morale + 4)
    _event(state, f"Сражение в «{region_name(state, rid)}» окончено: победа {state.countries[wi[0]].name}",
           wi + li, "battle", True)
    for w in state.wars:
        if w.side_of(wi[0]) and w.side_of(li[0]) and w.side_of(wi[0]) != w.side_of(li[0]):
            w.battles += 1
            w.momentum = max(-30.0, min(30.0, w.momentum + (8.0 if w.side_of(wi[0]) == "a" else -8.0)))


# ---------- оккупация ----------
def occ_days(state: "GameState", rid: str, ctl: str) -> float:
    """Суток, которые армия должна простоять в регионе, чтобы взять его под контроль."""
    geo = state.geo
    own = state.owner.get(rid, "")
    c = state.countries.get(own)
    d = 3.0 + min(8.0, geo.area.get(rid, 5.0) / 10.0) + (c.fort * 1.2 if c else 0.0)
    d += {"mountains": 2.5, "jungle": 1.5, "forest": 1.0, "arctic": 1.0}.get(geo.terrain.get(rid, ""), 0.0)
    if rid == capital_of(state, own):
        d += 6.0
    return d


def _occupy(state: "GameState") -> None:
    present: dict[str, list[Army]] = {}
    for a in state.armies.values():
        if a.progress <= 0 and not a.fight and not a.path:
            present.setdefault(a.region, []).append(a)
    for rid in list(state.occupying):
        if rid not in present:
            del state.occupying[rid]
    for rid, arms in present.items():
        holder = ctrl_of(state, rid)
        own = state.owner.get(rid, "")
        if any(hostile(state, a.owner, b.owner) for a in arms for b in arms):
            continue
        cand = [a for a in arms if not friendly(state, a.owner, holder) and
                (hostile(state, a.owner, holder) or hostile(state, a.owner, own))]
        lib = [a for a in arms if holder != own and friendly(state, a.owner, own) and hostile(state, a.owner, holder)]
        pick = lib or cand
        if not pick:
            state.occupying.pop(rid, None)
            continue
        lead = max(pick, key=lambda x: x.strength)
        who = own if lib else lead.owner
        cur = state.occupying.get(rid)
        if cur is None or cur[0] != who:
            cur = state.occupying[rid] = [who, 0.0]
        cur[1] += min(2.5, 1.0 + 0.12 * (sum(x.strength for x in pick) - 1.0))
        for a in pick:
            a.status = "occupying"
        if cur[1] >= occ_days(state, rid, holder):
            state.occupying.pop(rid, None)
            take_control(state, rid, who, lead)


def take_control(state: "GameState", rid: str, who: str, army: Optional[Army] = None) -> None:
    """Регион переходит под контроль who (освобождение, если who — владелец)."""
    prev = ctrl_of(state, rid)
    own = state.owner.get(rid, "")
    state.set_ctrl(rid, who)
    name = region_name(state, rid)
    capital = rid == capital_of(state, own)
    for w in state.wars:
        if w.side_of(who) and w.side_of(own) and w.side_of(who) != w.side_of(own):
            if rid not in w.captured:
                w.captured.append(rid)
    if who == own:
        _event(state, f"Освобождён регион «{name}»", [own, prev], "capture", True)
    else:
        text = f"Регион «{name}» захвачен: {state.countries[who].name} у {state.countries[own].name}"
        if capital:
            text = "Столица! " + text
        _event(state, text, [who, own], "capture", True)
        if capital:
            oc = state.countries[own]
            oc.stability = max(0.0, oc.stability - 12.0)
            for a in armies_of(state, own):
                a.morale = max(0.0, a.morale - 10.0)


# ---------- снабжение, мораль, восстановление ----------
def _supply_dist(state: "GameState", who: str) -> dict[str, int]:
    """Расстояние (в регионах) до ближайшей дружественной территории; кэш на сутки."""
    key = (state.map_rev, state.date.toordinal() // 3)
    hit = state.sup_cache.get(who)
    if hit and hit[0] == key:
        return hit[1]
    dist: dict[str, int] = {}
    frontier = [r for r in state.owner if friendly(state, who, ctrl_of(state, r))]
    for r in frontier:
        dist[r] = 0
    while frontier:
        nxt = []
        for u in frontier:
            for v in state.geo.adj.get(u, ()):
                if v not in dist:
                    dist[v] = dist[u] + 1
                    nxt.append(v)
        frontier = nxt
    state.sup_cache[who] = (key, dist)
    return dist


def supply_target(state: "GameState", a: Army) -> float:
    """К какому уровню снабжения стремится армия на её позиции."""
    terr = state.geo.terrain_of(a.region)[2]
    holder = ctrl_of(state, a.region)
    if friendly(state, a.owner, holder):
        base = 100.0 - (30.0 if surrounded(state, a) else 0.0)
    else:
        hops = _supply_dist(state, a.owner).get(a.region, 6)
        base = 100.0 - 17.0 * hops - (18.0 if hostile(state, a.owner, holder) else 0.0)
    return max(0.0, base - terr * 3.0)


def _upkeep(state: "GameState") -> None:
    for a in state.armies.values():
        c = state.countries[a.owner]
        tgt = supply_target(state, a)
        a.supply += max(-8.0, min(12.0, tgt - a.supply))
        home = friendly(state, a.owner, ctrl_of(state, a.region))
        if a.supply < 40.0:
            a.morale = max(0.0, a.morale - 1.2)
            a.strength = max(0.0, a.strength * (1.0 - 0.004 * (40.0 - a.supply) / 40.0))
            if a.status not in ("fighting", "retreating"):
                a.status = "no_supply"
        elif not a.fight and a.progress <= 0:
            if home:
                a.morale = min(100.0, a.morale + MORALE_REST)
                if a.supply >= 80.0 and a.strength < a.max_strength and c.manpower > 5:
                    add = min(0.12, a.max_strength - a.strength)
                    a.strength += add
                    c.manpower = max(0.0, c.manpower - add * 8.0)
                    if a.status == "idle" and a.strength < a.max_strength:
                        a.status = "resting"
            elif a.supply > 70:
                a.morale = min(100.0, a.morale + 0.4)
        if a.status == "no_supply" and a.supply >= 40.0:
            a.status = "idle" if not a.path else "moving"
        if c.treasury <= 0 and c.army > 0:
            a.morale = max(0.0, a.morale - 0.3)                         # армия без жалованья
        if a.status == "resting" and a.strength >= a.max_strength - 0.01:
            a.status = "idle"


def describe_status(a: Army) -> str:
    """Подпись состояния для интерфейса."""
    return {"idle": "Стоит", "moving": "В походе", "fighting": "В бою", "occupying": "Оккупирует",
            "retreating": "Отступает", "resting": "Восстанавливается", "no_supply": "Нет снабжения"}.get(a.status, "Стоит")


def war_power(state: "GameState", iso: str) -> float:
    """Оценка полевой силы державы (для ИИ и дипломатии)."""
    return sum(a.strength * eff(state, a) for a in state.armies.values() if a.owner == iso)


def region_value(state: "GameState", rid: str) -> float:
    """Стратегическая ценность региона: площадь + столица."""
    own = state.owner.get(rid, "")
    return 1.0 + min(3.0, state.geo.area.get(rid, 5.0) / 40.0) + (5.0 if rid == capital_of(state, own) else 0.0)
