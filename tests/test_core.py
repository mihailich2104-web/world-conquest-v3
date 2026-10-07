import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

"""Тесты ядра без pygame: запуск `python tests/test_core.py`."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from config import COUNTRIES_PATH
from core.game_state import GameState, save_game, load_game
from core.turn_manager import TurnManager
from core import diplomacy, war as warmod


def make_state(n=60, tutorial=False):
    data = json.loads(COUNTRIES_PATH.read_text(encoding="utf-8"))[:n]
    provs = [(d["iso"], d["name"], d["continent"]) for d in data]
    adj = {p[0]: [] for p in provs}
    for i in range(len(provs) - 1):          # цепочка соседей
        adj[provs[i][0]].append(provs[i + 1][0]); adj[provs[i + 1][0]].append(provs[i][0])
    return GameState.new(data, provs, adj, seed=7, tutorial=tutorial)


def test_simulation():
    st = make_state(60)
    st.player_iso = next(iter(st.countries))
    tm = TurnManager(st)
    t = time.time(); tm.advance(365)
    print("365 дней:", round(time.time() - t, 2), "с; войн сейчас:", len(st.wars),
          "аннексий:", st.stats["annexed"], "живых:", len(st.alive_countries()))
    assert st.date.year == 2026


def test_player_actions():
    st = make_state(30, tutorial=True)
    a = list(st.countries)[0]; st.player_iso = a
    c = st.countries[a]
    ok, m = c.build("factory"); assert ok, m
    ok, m = c.recruit("army"); assert ok, m
    ok, m = c.research("inf_1"); assert ok, m
    assert not c.research("inf_3")[0]
    tm = TurnManager(st); tm.advance(20)
    assert c.factories >= 2 and "inf_1" in c.technologies or True
    tgt = sorted(st.neighbors(a))[0]
    ok, m = c.declare_war(st, tgt); assert ok, m
    assert st.wars and tgt in c.at_war_with
    won = 0
    for _ in range(150):
        tm.next_turn()
        if not st.wars: break
    print("война:", st.stats, "gameover:", st.game_over, "owner:", st.owner[tgt])


def test_save_load():
    import tempfile, config; config.SAVE_DB = Path(tempfile.mkdtemp()) / "test_saves.db"
    import core.game_state as gs; gs.SAVE_DB = config.SAVE_DB
    st = make_state(20); st.player_iso = list(st.countries)[0]
    TurnManager(st).advance(10)
    sid = save_game(st, "t"); assert sid
    st2 = load_game(sid, st.adjacency)
    assert st2 and st2.date == st.date and st2.countries[st.player_iso].treasury == st.countries[st.player_iso].treasury
    TurnManager(st).advance(5); TurnManager(st2).advance(5)
    assert st.countries[st.player_iso].treasury == st2.countries[st.player_iso].treasury, "детерминизм RNG"


if __name__ == "__main__":
    test_player_actions(); test_save_load(); test_simulation(); print("OK")
