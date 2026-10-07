"""Проверка сценария обучения без pygame (заглушка pygame.Rect)."""
import json, sys, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
fake = types.ModuleType("pygame"); fake.Rect = lambda *a: a; sys.modules["pygame"] = fake
from config import COUNTRIES_PATH
from core.game_state import GameState
from core.turn_manager import TurnManager
from map.map_loader import load_world
from tutorial.tutor_engine import TutorContext, TutorEngine

data = json.loads(COUNTRIES_PATH.read_text(encoding="utf-8"))
provs, adj, fb = load_world(data)
st = GameState.new(data, [(p.iso, p.name, p.continent) for p in provs], adj, seed=3, tutorial=True)
class Cam:  # заглушка камеры
    def focus(self, *a): pass
class Rend:
    by_iso = {p.iso: p for p in provs}
ctx = TutorContext({}, Rend(), Cam()); ctx.state = st
opened = []; ctx.open_panel = opened.append; ctx.select = lambda i: None
eng = TutorEngine(ctx); tm = TurnManager(st)
eng.update(); assert eng.step.id == "select"
st.player_iso = "DEU" if "DEU" in st.countries else next(iter(st.countries))
me = st.player
eng.update(); assert eng.step.id == "capital", eng.step.id
eng.next(); eng.update(); assert eng.step.id == "factory"
assert me.build("factory")[0]; eng.update(); assert eng.step.id == "division"
assert me.recruit("army")[0]; eng.update(); assert eng.step.id == "war"
tgt = ctx.memo["target"]; print("цель:", tgt, "армия цели:", st.countries[tgt].army, "моя:", me.army)
ok, m = me.declare_war(st, tgt); assert ok, m
eng.update(); assert eng.step.id == "battle"
for _ in range(10):
    tm.next_turn(); eng.update()
    if eng.step.id == "finish": break
assert eng.step.id == "finish", (eng.step.id, st.stats)
eng.next(); assert eng.finished
print("Туториал проходится: OK;", st.log[-3:])
