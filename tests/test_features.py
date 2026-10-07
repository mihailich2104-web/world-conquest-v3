"""Проверка новых систем: часы, события, технологии-бонусы, предложения ИИ, сохранения, ИИ."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from config import COUNTRIES_PATH, SETTINGS, SPEEDS  # noqa: E402
from core import diplomacy, economy, research  # noqa: E402
from core.clock import GameClock  # noqa: E402
from core.game_state import GameState  # noqa: E402
from core.session import Session  # noqa: E402
from map.map_loader import load_world  # noqa: E402

SETTINGS.autosave = False


def world(seed=5):
    data = json.loads(COUNTRIES_PATH.read_text(encoding="utf-8"))
    provs, adj, _ = load_world(data)
    return GameState.new(data, [(p.iso, p.name, p.continent) for p in provs], adj, seed=seed), adj


def main():
    # --- часы: скорости и пауза ---
    c = GameClock()
    got = {sp: 0 for sp in SPEEDS}
    for i, sp in enumerate(SPEEDS):
        c = GameClock(speed_idx=i)
        got[sp] = sum(c.update(0.05) for _ in range(200))        # 10 секунд реального времени
    print("дней за 10 с:", got)
    assert got[1] == 10 and got[2] == 20 and got[4] == 40 and got[8] == 80 and got[16] == 160, got
    c = GameClock(paused=True, speed_idx=4)
    assert sum(c.update(1.0) for _ in range(50)) == 0
    assert GameClock(speed_idx=4).update(10.0) == 4              # защита от лагов

    st, adj = world()
    st.player_iso = "DEU"
    st.humans = {"DEU"}
    ses = Session(st)
    assert not ses.clock.paused
    d0 = st.date
    for _ in range(100):
        ses.update(0.05)
    assert (st.date - d0).days == 5, (st.date - d0).days        # 5 с при 1X = 5 дней
    ses.toggle_pause()
    for _ in range(100):
        ses.update(0.05)
    assert (st.date - d0).days == 5, "время идёт на паузе"

    # --- технологии дают реальные бонусы ---
    c = st.countries["DEU"]
    base = economy.daily_income(c)
    for i in range(1, 4):
        c.technologies.append(f"eco_{i}")
    assert economy.daily_income(c) > base * 1.1, "экономические технологии не влияют на доход"
    assert research.tech_bonus(c, "economy") > 0
    assert len({t for t in research.get_tree().techs if t.startswith(("eco_", "infra_", "dipl_"))}) == 18

    # --- события и ИИ за 2 года ---
    st2, _ = world(seed=11)
    st2.player_iso = None
    ses2 = Session(st2)
    import core.events as ev
    fired = []
    orig = ev.fire
    ev.fire = lambda s, c, e: (fired.append(e.id), orig(s, c, e))[1]
    st2.humans = set()
    for _ in range(730):
        ses2.tm.next_turn()
    ev.fire = orig
    print("событий за 2 года у людей:", len(fired), "аннексий:", st2.stats.get("annexed", {}).keys(),
          "войн объявлено:", sum(len(v) for v in st2.stats.get("wars_declared", {}).values()))
    assert len(st2.alive_countries()) > 5

    # --- предложения ИИ человеку ---
    st3, _ = world(seed=2)
    me = "DEU"
    st3.player_iso, st3.humans = me, {me}
    nb = sorted(st3.neighbors(me))[0]
    assert diplomacy.make_offer(st3, nb, me, "trade") and not diplomacy.make_offer(st3, nb, me, "trade")
    oid = st3.offers[0]["id"]
    ok, msg = diplomacy.respond_offer(st3, me, oid, True)
    assert ok and nb in st3.countries[me].trade, msg
    assert not diplomacy.respond_offer(st3, me, oid, True)[0]

    # --- сохранение/загрузка хранит всё ---
    c = st3.countries[me]
    c.modifiers.append(["income", 10, -0.2])
    diplomacy.make_offer(st3, nb, me, "pact")
    for _ in range(40):
        st3.date = st3.date
    d = json.loads(json.dumps(st3.to_dict()))
    back = GameState.from_dict(d, adj)
    assert back.date == st3.date and back.countries[me].modifiers == [["income", 10, -0.2]]
    assert back.offers == st3.offers and back.humans == st3.humans and back.owner == st3.owner
    assert back.countries[me].trade == st3.countries[me].trade
    # старые сохранения без новых полей тоже загружаются
    for k in ("humans", "offers", "next_offer_id"):
        d.pop(k, None)
    for cd in d["countries"].values() if isinstance(d["countries"], dict) else d["countries"]:
        cd.pop("modifiers", None)
    GameState.from_dict(d, adj)
    print("FEATURES OK")


if __name__ == "__main__":
    main()
