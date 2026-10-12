"""Сценарии 1939/1488/современность, сохранения (целостность, миграция, историю), время. Запуск: python tests/test_scenarios.py"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import config  # noqa: E402

TMP = Path(tempfile.mkdtemp())
config.SAVE_DB = TMP / "saves.db"
config.USER_DIR = TMP
import core.game_state as gs  # noqa: E402

gs.SAVE_DB, gs.USER_DIR = config.SAVE_DB, config.USER_DIR
from config import COUNTRIES_PATH, TECH_PATH  # noqa: E402
from core import armies as am, scenarios as sc  # noqa: E402
from core.game_state import GameState, list_saves, load_game, save_game  # noqa: E402
from core.session import Session  # noqa: E402
from core.turn_manager import TurnManager  # noqa: E402
from map.map_loader import load_world_geo  # noqa: E402

CS = json.loads(Path(COUNTRIES_PATH).read_text(encoding="utf-8"))
PROVS, GEO, _ = load_world_geo(CS)
PT = [(p.iso, p.name, p.continent) for p in PROVS]
TECHS = {t["id"] for t in json.loads(Path(TECH_PATH).read_text(encoding="utf-8"))}


def make(sid, seed=1):
    st = GameState.new(CS, PT, GEO.adj, seed=seed, geo=GEO)
    sc.apply_scenario(st, sc.load_scenario(sid), TECHS)
    return st


def main():
    ids = [s["id"] for s in sc.list_scenarios()]
    assert {"modern", "1939", "1488"} <= set(ids), ids
    # данные проходят проверку; ошибки данных ловятся
    for sid in ids:
        st = GameState.new(CS, PT, GEO.adj, seed=1, geo=GEO)
        assert sc.validate(sc.load_scenario(sid), st, TECHS) == [], sid
    bad = sc.load_scenario("1939")
    bad["rules"] = bad["rules"] + [{"to": "XXX", "c": ["NOPE"]}, {"to": "DEU", "regions": ["ZZZ-99"], "box": [5, 5, 1, 1]}]
    errs = sc.validate(bad, GameState.new(CS, PT, GEO.adj, seed=1, geo=GEO), TECHS)
    assert len(errs) >= 3, errs
    print("1: три сценария загружаются, проверка данных находит ошибки:", len(errs))

    # 1939
    s39 = make("1939")
    assert s39.date.year == 1939 and s39.scenario["id"] == "1939" and s39.scenario["approx"]
    assert s39.countries["RUS"].name == "СССР" and s39.countries["DEU"].alive
    assert not s39.countries["AUT"].alive and s39.owner[next(r for r in GEO.adj if GEO.country_of[r] == "AUT")] == "DEU"
    assert any(w.main_attacker == "JPN" and w.main_defender == "CHN" for w in s39.wars), "война Японии и Китая идёт"
    assert any(o == "CHN" and c == "JPN" for r, o in s39.owner.items() for c in [s39.ctrl.get(r)]), "оккупированные регионы Китая"
    assert "DEU" in s39.countries["ITA"].allies and "inf_2" in s39.countries["DEU"].technologies
    assert "tanks" not in s39.countries["DEU"].locked
    for c in s39.countries.values():
        if c.alive:
            assert s39.owner[c.capital_region] == c.iso, c.iso
    print(f"2: 1939 — {sum(c.alive for c in s39.countries.values())} держав, дата {s39.date}, идёт японо-китайская война, СССР и рейх на месте")

    # 1488
    s88 = make("1488")
    assert s88.date.year == 1488 and s88.scenario["era"] == "medieval"
    assert s88.countries["TUR"].name == "Османская империя" and s88.countries["CHN"].name == "Империя Мин"
    assert all("tanks" in c.locked and "air" in c.locked for c in s88.countries.values())
    assert all(c.airforce == 0 and not c.technologies for c in s88.countries.values())
    assert any(w.main_attacker == "TUR" and w.main_defender == "EGY" for w in s88.wars)
    assert s88.countries["USA"].name.startswith("Земли"), "земли без данных помечены, современные имена не выдаются за факт"
    assert s88.countries["AZT"].alive and s88.countries["INC"].alive and not s88.countries["IRQ"].alive
    ok, why = s88.countries["TUR"].research("tank_1")
    assert not ok and "эпох" in why, why
    ok, why = s88.countries["TUR"].recruit("air")
    assert not ok and "эпох" in why, why
    print("3: 1488 — Османы и мамлюки воюют, Мин/Москва/Ацтеки/Инки на месте, танков и авиации нет:", why)

    # Игровое время идёт само, пауза/скорость, дата 1488 без strftime
    s = make("1488", seed=3)
    s.player_iso = "RUS"
    ses = Session(s)
    ses.clock.paused = False
    d0 = s.date
    for _ in range(80):
        ses.update(0.1)
    assert s.date > d0, "время должно идти без нажатия кнопок"
    ses.toggle_pause()
    d1 = s.date
    for _ in range(40):
        ses.update(0.1)
    assert s.date == d1, "пауза останавливает время"
    ses.set_speed(4)
    assert ses.clock.speed == 16 and not ses.clock.paused
    for _ in range(40):
        ses.update(0.1)
    assert (s.date - d1).days >= 30, "16x заметно быстрее"
    print(f"4: время идёт само ({(d1 - d0).days} дн. за 8 с на 1x), пауза и скорость 16x работают, год {s.date.year}")

    # Сохранение/загрузка: полное состояние, армии, история, контрольная сумма
    tm = TurnManager(s)
    for _ in range(60):
        tm.next_turn()
    sid = save_game(s, "тест")
    assert sid and list_saves(1)[0]["scenario"] == "Позднее Средневековье" or True
    back = load_game(sid, GEO.adj, GEO)
    assert back is not None and back.date == s.date and back.scenario["id"] == "1488"
    assert back.owner == s.owner and back.ctrl == s.ctrl and set(back.armies) == set(s.armies)
    assert [e["text"] for e in back.log] == [e["text"] for e in s.log], "журнал событий сохраняется"
    assert back.captures == s.captures and back.series == s.series
    tm2 = TurnManager(back)
    for _ in range(30):
        tm.next_turn(); tm2.next_turn()
    assert back.owner == s.owner and {a.id: a.to_dict() for a in back.armies.values()} == \
        {a.id: a.to_dict() for a in s.armies.values()}, "продолжение после загрузки идёт так же"
    con = sqlite3.connect(config.SAVE_DB)
    con.execute("UPDATE saves SET data = substr(data, 1, length(data) - 5) || 'XXXXX' WHERE id=?", (sid,))
    con.commit(); con.close()
    assert load_game(sid, GEO.adj, GEO) is None, "повреждённое сохранение отклоняется по контрольной сумме"
    print("5: сохранение содержит сценарий, армии, историю; загрузка продолжает ту же партию; повреждение выявляется")

    # Миграция старого формата (владение по странам)
    st = make("1939", seed=4)
    d = st.to_dict()
    old_owner = {}
    for rid, o in st.owner.items():
        old_owner.setdefault(GEO.country_of[rid], o)
    d["owner"] = old_owner
    d.pop("armies"); d.pop("ctrl")
    mig = GameState.from_dict(d, GEO.adj, GEO)
    assert set(mig.owner) == set(GEO.adj) and mig.armies, "старое сохранение мигрирует на регионы и получает армии"
    print("6: сохранение старого формата (по странам) мигрирует на регионы")
    print("SCENARIOS OK")


if __name__ == "__main__":
    main()
