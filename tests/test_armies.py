"""Армии и захват территорий на реальной карте регионов (без pygame). Запуск: python tests/test_armies.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from config import COUNTRIES_PATH  # noqa: E402
from core import armies as am, ai_war, commands, diplomacy as dip, stats, war as warmod  # noqa: E402
from core.game_state import GameState  # noqa: E402
from core.turn_manager import TurnManager  # noqa: E402
from map.map_loader import load_world_geo  # noqa: E402

CS = json.loads(Path(COUNTRIES_PATH).read_text(encoding="utf-8"))
PROVS, GEO, _ = load_world_geo(CS)
PT = [(p.iso, p.name, p.continent) for p in PROVS]


def fresh(seed=1):
    st = GameState.new(CS, PT, GEO.adj, seed=seed, geo=GEO)
    st.player_iso = "DEU"
    return st, TurnManager(st)


def freeze_ai(st):
    """Чтобы ИИ не вмешивался в проверку: все страны, кроме указанных, не воюют сами."""
    for c in st.countries.values():
        c.relations = {}


def main():
    # 1. Движение постепенное и по соседним регионам; ETA убывает; армия не телепортируется
    st, tm = fresh()
    dip.declare_war(st, "DEU", "POL")
    a = max(am.armies_of(st, "DEU"), key=lambda x: x.strength)
    tgt = next(r for r, o in st.owner.items() if o == "POL")
    ok, msg = am.order_move(st, "DEU", a.id, tgt)
    assert ok, msg
    eta0 = am.eta_days(st, a)
    trail, prev = [a.region], a.region
    for day in range(1, 60):
        am.tick(st)
        st.date = st.date.fromordinal(st.date.toordinal() + 1)
        if a.region != prev:
            assert a.region in GEO.adj[prev] or a.sea, f"телепортация {prev} → {a.region}"
            trail.append(a.region)
            prev = a.region
        assert 0.0 <= a.progress < 1.0
        if not a.path:
            break
    assert day > 1 and len(trail) >= 2, "армия должна идти несколько суток"
    assert am.eta_days(st, a) <= eta0
    print(f"1: марш {a.name}: {len(trail) - 1} переходов за {day} дн., без телепортации (ETA была {eta0:.0f} дн.)")

    # 2. Права: чужая армия, неизвестный регион, закрытые границы, предел операций
    st, tm = fresh()
    foreign = next(x for x in st.armies.values() if x.owner == "FRA")
    assert am.order_move(st, "DEU", foreign.id, foreign.region) == (False, "Это не ваша армия")
    mine = next(x for x in st.armies.values() if x.owner == "DEU")
    assert not am.order_move(st, "DEU", mine.id, "NOPE")[0]
    far_polish = next(r for r, o in st.owner.items() if o == "POL")
    ok, why = am.order_move(st, "DEU", mine.id, far_polish)
    assert not ok and "закрыты" in why, why
    assert commands.execute(st, "DEU", {"t": "army_move", "id": foreign.id, "target": foreign.region})[0] is False
    assert commands.execute(st, "DEU", {"t": "army_move", "id": True, "target": "POL"})[0] is False
    print("2: чужие армии, неизвестные регионы и закрытые границы отклоняются")

    # 3. Захват: контроль меняется, владелец — нет; граница регионов обновляется; мир передаёт регион
    st, tm = fresh(2)
    freeze_ai(st)
    st.countries["LUX"].army = 1
    st.countries["DEU"].army = 30
    am.reconcile(st)
    dip.declare_war(st, "DEU", "LUX")
    lux_regions = [r for r, o in st.owner.items() if o == "LUX"]
    for x in am.armies_of(st, "DEU"):
        am.order_move(st, "DEU", x.id, lux_regions[0])
    for _ in range(120):
        tm.next_turn()
        if st.ctrl.get(lux_regions[0]) == "DEU" or not st.countries["LUX"].alive:
            break
    w = warmod.find_war(st, "DEU", "LUX")
    assert (st.ctrl.get(lux_regions[0]) == "DEU" and st.owner[lux_regions[0]] == "LUX") or not st.countries["LUX"].alive
    assert any(c["k"] in ("occupy", "cede") for c in st.captures), "история захватов должна вестись"
    print("3: регион занят армией (контроль ≠ владение), история захватов ведётся; счёт войны:",
          round(w.score) if w else "война окончена")

    # 4. Крупную страну нельзя взять мгновенно: после нескольких месяцев войны у России почти все регионы на месте
    st, tm = fresh(3)
    st.player_iso = None
    st.countries["CHN"].army = 60
    am.reconcile(st)
    assert "RUS" in st.neighbors("CHN")
    dip.declare_war(st, "CHN", "RUS")
    for _ in range(200):
        tm.next_turn()
    rus = [r for r, o in st.owner.items() if GEO.country_of[r] == "RUS" and o == "RUS"]
    taken = [r for r in rus if st.ctrl.get(r) == "CHN"]
    assert st.countries["RUS"].alive and len(rus) >= 16, f"Россия не должна падать за 200 дней: {len(rus)} регионов"
    print(f"4: после 200 дней войны Китая с Россией у России {len(rus)} из 24 регионов (занято {len(taken)}) — "
          f"крупную страну одним боем не взять")

    # 5. Потеря столицы не убивает страну, но бьёт по устойчивости и экономике
    st, tm = fresh(4)
    c = st.countries["POL"]
    stab0 = c.stability
    dip.declare_war(st, "DEU", "POL")
    am.take_control(st, c.capital_region, "DEU")
    stats.econ_update(st)
    assert c.alive and c.stability < stab0 and c.econ_mult < 1.0
    print(f"5: столица Польши занята: страна жива, устойчивость {stab0:.0f}→{c.stability:.0f}, экономика ×{c.econ_mult:.2f}")

    # 6. Разделение и объединение армий
    st, tm = fresh(5)
    a = max(am.armies_of(st, "DEU"), key=lambda x: x.strength)
    n0 = a.units
    ok, _ = am.split_army(st, "DEU", a.id, max(1, n0 // 2))
    assert ok
    same = [x.id for x in am.armies_of(st, "DEU") if x.region == a.region and not x.path]
    ok, msg = am.merge_armies(st, "DEU", same[:2])
    assert ok, msg
    assert abs(sum(x.strength for x in am.armies_of(st, "DEU")) - st.countries["DEU"].army) < 1
    assert not am.split_army(st, "FRA", a.id, 1)[0] and not am.merge_armies(st, "FRA", same[:2])[0]
    print("6: разделение/объединение сохраняют силу; чужие армии трогать нельзя")

    # 7. Снабжение: армия вдали от своих земель теряет снабжение и боеспособность
    st, tm = fresh(6)
    dip.declare_war(st, "DEU", "POL")
    a = max(am.armies_of(st, "DEU"), key=lambda x: x.strength)
    a.region = next(r for r, o in st.owner.items() if o == "POL")
    a.supply = 100.0
    s0 = a.strength
    for _ in range(25):
        am._upkeep(st)
    assert a.supply < 70, a.supply
    print(f"7: вне снабжения армия: снабжение 100→{a.supply:.0f}%, мораль {a.morale:.0f}%")

    # 8. Бой: сильная сторона побеждает слабую, проигравшая отступает/уничтожается; число дивизий сходится со счётчиком
    st, tm = fresh(7)
    freeze_ai(st)
    dip.declare_war(st, "DEU", "LUX")
    lux = st.countries["LUX"]
    reg = next(r for r, o in st.owner.items() if o == "LUX")
    big = am.new_army(st, "DEU", 10, reg)
    for _ in range(40):
        am.tick(st)
        if not any(x.owner == "LUX" for x in st.armies.values()):
            break
    assert not any(x.owner == "LUX" and x.region == reg for x in st.armies.values()) and big.id in st.armies
    assert st.stats["battles_won"].get("DEU", 0) >= 1 or not any(x.owner == "LUX" for x in st.armies.values())
    am.reconcile(st)
    for iso, c in st.countries.items():
        tot = sum(a_.strength for a_ in st.armies.values() if a_.owner == iso)
        assert abs(tot - c.army) < 1.01, (iso, tot, c.army)
    print("8: бой выигрывает сильная сторона; счётчики дивизий сходятся с армиями")

    # 9. Детерминизм: одинаковое зерно — одинаковая история
    def run(seed):
        s, t = fresh(seed)
        dip.declare_war(s, "DEU", "POL")
        for _ in range(90):
            t.next_turn()
        return json.dumps({"o": s.owner, "c": s.ctrl, "a": [x.to_dict() for x in s.armies.values()]}, sort_keys=True)
    assert run(9) == run(9)
    print("9: симуляция детерминирована")

    # 10. ИИ не бросает все армии в одну цель: цель «насыщается» силами, шансы оцениваются заранее
    st, tm = fresh(8)
    st.player_iso = None
    freeze_ai(st)
    st.countries["DEU"].army = 90
    am.reconcile(st)
    dip.declare_war(st, "DEU", "POL")
    dip.declare_war(st, "DEU", "FRA")
    budget = [30]
    ai_war.plan(st, st.countries["DEU"], budget)
    sent = [x for x in am.armies_of(st, "DEU") if x.target]
    claimed = {}
    for x in sent:
        claimed[x.target] = claimed.get(x.target, 0.0) + x.strength * am.eff(st, x)
    assert sent, "ИИ с большой армией на войне должен действовать"
    for t, power in claimed.items():
        ep = ai_war.enemy_power(st, t, "DEU")
        assert power <= ep * 1.6 + am.MAX_STACK, f"бессмысленное наращивание на {t}: {power:.0f} против {ep:.0f}"
    idle = [x for x in am.armies_of(st, "DEU") if not x.target]
    assert idle, "часть сил остаётся в резерве/обороне, а не бросается в одну атаку"
    print(f"10: ИИ направил {len(sent)} армий на цели {sorted(claimed)}, резерв {len(idle)}; перенасыщения целей нет")

    # 11. Мир: победитель с перевесом оставляет занятое, иначе всё возвращается
    st, tm = fresh(10)
    freeze_ai(st)
    dip.declare_war(st, "DEU", "POL")
    w = warmod.find_war(st, "DEU", "POL")
    rid = next(r for r, o in st.owner.items() if o == "POL")
    am.take_control(st, rid, "DEU")
    w.days, w.momentum = 70, 25
    w.score = warmod.compute_score(st, w)
    pv = dip.peace_preview(st, "DEU", "POL")
    ok, msg = dip.propose_peace(st, "DEU", "POL", "annex") if w.score >= 20 else dip.propose_peace(st, "DEU", "POL")
    assert warmod.find_war(st, "DEU", "POL") is None, msg
    assert (st.owner[rid] == "DEU") == (pv["kind"] == "annex"), (pv, st.owner[rid])
    print(f"11: мирный договор ({pv['kind']}): регион {'передан' if st.owner[rid] == 'DEU' else 'возвращён'}; {msg}")

    print("ARMIES OK")


if __name__ == "__main__":
    main()
