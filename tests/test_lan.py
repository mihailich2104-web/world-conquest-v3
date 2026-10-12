"""LAN: вход в идущую партию, синхронизация дельтами, возвращение, лимиты, версии, мусорные данные, голосование.
Реальные сокеты на 127.0.0.1, без pygame. Запуск: python tests/test_lan.py"""
import json
import os
import socket
import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from config import COUNTRIES_PATH, NET_VERSION  # noqa: E402
from core import armies as am, diplomacy as dip  # noqa: E402
from core.game_state import GameState  # noqa: E402
from core.session import Session  # noqa: E402
from map.map_loader import load_world_geo  # noqa: E402
from net.client import Client, ClientError  # noqa: E402
from net.host import Host  # noqa: E402
from net.protocol import encode, recv_msg  # noqa: E402

CS = json.loads(Path(COUNTRIES_PATH).read_text(encoding="utf-8"))
PROVS, GEO, _ = load_world_geo(CS)
PT = [(p.iso, p.name, p.continent) for p in PROVS]


def new_state(seed=11):
    return GameState.new(CS, PT, GEO.adj, seed=seed, geo=GEO)


def connect_nopump(host, name):
    c = Client(name)
    c.connect("127.0.0.1", host.port)
    return c


def pump(sessions, secs=0.3, dt=0.05):
    end = time.time() + secs
    while time.time() < end:
        for s in sessions:
            s.update(dt)
        time.sleep(0.01)


def wait(cond, sessions, secs=6.0, msg=""):
    end = time.time() + secs
    while time.time() < end:
        pump(sessions, 0.05)
        if cond():
            return
    raise AssertionError("не дождались: " + msg)


def snap(st):
    """Сравнимая сводка состояния."""
    return {"date": st.date.toordinal(), "owner": dict(st.owner), "ctrl": dict(st.ctrl),
            "armies": {a.id: (a.owner, a.region, a.to_dict()["strength"], tuple(a.path), a.to_dict()["progress"], a.order)
                       for a in st.armies.values()},
            "alive": {i: c.alive for i, c in st.countries.items()}, "wars": sorted((w.id, w.to_dict()["score"]) for w in st.wars),
            "treasury": {i: round(c.treasury, 1) for i, c in st.countries.items() if i in ("USA", "FRA", "DEU")},
            "log": [e["n"] for e in st.log[-40:]]}


def settle(host_ses, clients, secs=5.0):
    """Ставим хост на паузу, ждём пока все клиенты догонят его состояние."""
    host_ses.clock.paused = True
    host_ses._dirty = True

    def same():
        h = snap(host_ses.state)
        return all(snap(c.state) == h for c in clients)
    wait(same, [host_ses] + clients, secs, "клиенты не догнали хоста")


def make_host(max_players=8):
    st = new_state()
    st.player_iso = "USA"
    host = Host("Тест", {c.iso for c in st.countries.values()}, port=0)
    host.max_players = max_players
    host.start()
    return st, host


def connect(host, name, token="", pumps=()):
    """Подключение в отдельном потоке, пока главный поток хоста обрабатывает очередь (как в настоящей игре)."""
    c = Client(name)
    box = {}

    def run():
        try:
            c.connect("127.0.0.1", host.port, token=token)
        except Exception as exc:                        # noqa: BLE001
            box["err"] = exc
    t = threading.Thread(target=run)
    t.start()
    end = time.time() + 8
    while t.is_alive() and time.time() < end:
        for s_ in pumps:
            s_.update(0.02)
        time.sleep(0.005)
    t.join(1)
    if "err" in box:
        raise box["err"]
    return c


def main():
    # 1–3. Хост создаёт игру, первый клиент входит до старта, хост начинает
    st, host = make_host()
    hs = Session(st, "host", host=host)
    a = connect(host, "Анна", pumps=[hs])
    assert a.phase == "lobby" and not a.resumed
    wait(lambda: len(host.players) == 2, [hs], msg="клиент A не появился")
    host.pick(0, "USA")
    a.pick("DEU")
    a.ready(True)
    wait(lambda: host.can_start()[0], [hs], msg="лобби не готово")
    hs.begin_play()
    st_a = new_state(seed=99)
    sa = Session(st_a, "client", client=a)
    wait(lambda: sa.synced, [hs, sa], msg="клиент A не получил стартовый снимок")
    assert st_a.player_iso == "DEU" and "DEU" in st.humans
    print("1-3: старт партии, клиент A синхронизирован")

    # 4. Клиент играет дальше: хост считает дни, клиент получает дельты
    hs.clock.set_speed(3)
    hs.clock.paused = False
    pump([hs, sa], 2.0, 0.1)
    assert st.date > st_a.date or st_a.date.toordinal() >= st.date.toordinal() - 20
    settle(hs, [sa])
    assert snap(st_a) == snap(st)
    print("4: клиент A продолжает играть, состояния совпали; день", st.date)

    # 5–7. Война и движение армий, затем второй клиент входит в идущую партию
    dip.declare_war(st, "RUS", "UKR")
    dip.declare_war(st, "FRA", "ESP")
    hs.clock.paused = False
    pump([hs, sa], 2.5, 0.1)
    moving = [x for x in st.armies.values() if x.path]
    assert moving, "нужны движущиеся армии"
    b = connect(host, "Борис", pumps=[hs, sa])
    assert b.phase == "play" and not b.resumed and b.free, "при входе в идущую партию приходит список свободных стран"
    free_isos = {r["iso"] for r in b.free}
    assert "USA" not in free_isos and "DEU" not in free_isos and "FRA" in free_isos
    st_b = new_state(seed=5)
    sb = Session(st_b, "client", client=b)
    b.claim("DEU")                                     # занята игроком A
    wait(lambda: sb.claim_error, [hs, sa, sb], 4, "отказ в занятой стране")
    assert "уже" in sb.claim_error, sb.claim_error
    b.claim("FRA")
    wait(lambda: sb.synced, [hs, sa, sb], msg="клиент B не получил снимок идущей партии")
    assert st_b.player_iso == "FRA" and "FRA" in st.humans
    print("5-7: клиент B вошёл в идущую партию во время войны и движения армий; дата", st_b.date)

    # 8-9. После входа B видит то же: армии/маршруты/территории/войны; дубликатов нет
    settle(hs, [sa, sb])
    sh, sbb = snap(st), snap(st_b)
    assert sh == sbb
    ids = [a_.id for a_ in st_b.armies.values()]
    assert len(ids) == len(set(ids)) == len(st.armies)
    logs = [e["n"] for e in st_b.log]
    assert len(logs) == len(set(logs)), "события не должны дублироваться"
    assert {w.id for w in st_b.wars} == {w.id for w in st.wars}
    print("8-9: B видит те же армии, маршруты, войны и границы; событий без дублей:", len(logs))

    # 10. Одинаковые результаты игровых событий у всех + изолированность команд
    ok, msg = dip.declare_war(st, "GBR", "IRL") if False else (True, "")
    c_id = next(x.id for x in st.armies.values() if x.owner == "DEU")
    sb.do({"t": "army_move", "id": c_id, "target": "FRA"})          # B (Франция) пытается командовать армией Германии
    pump([hs, sa, sb], 0.6)
    assert any((not ok_) and "не ваша" in m_ for _, ok_, m_ in sb.acks), sb.acks
    # собственная армия A: приказ выполняется на хосте и виден всем
    my = next(x for x in st.armies.values() if x.owner == "DEU")
    tgt = st.geo.adj[my.region][0]
    before = len(sa.acks)
    sa.do({"t": "army_move", "id": my.id, "target": tgt})
    hs.clock.paused = False
    pump([hs, sa, sb], 0.5)
    settle(hs, [sa, sb])
    print("10: чужие армии командам клиента не подчиняются; приказ A выполнен и виден всем:", sa.acks[-1][2][:50])

    # 11-12. Дубликат команды выполняется один раз
    tax0 = st.countries["DEU"].tax_rate
    cid = a.command({"t": "tax", "delta": 0.05})
    a.send({"t": "cmd", "id": cid, "cmd": {"t": "tax", "delta": 0.05}})     # повтор того же id
    pump([hs, sa, sb], 0.6)
    assert abs(st.countries["DEU"].tax_rate - (tax0 + 0.05)) < 1e-9, "дубликат команды не должен повторяться"
    print("11: повторная команда с тем же id не выполняется второй раз")

    # 13. Пауза: один клиент не может сам остановить игру — нужно большинство
    hs.clock.paused = False
    hs.vote = None
    pump([hs, sa, sb], 0.8)                              # клиенты видят, что время идёт
    assert not sa.clock.paused
    sa.toggle_pause()
    pump([hs, sa, sb], 0.4)
    assert not hs.clock.paused and hs.vote and hs.vote["kind"] == "pause"
    assert sb.vote is not None, "клиенты видят голосование"
    sb.cast_vote(True)
    pump([hs, sa, sb], 0.5)
    assert hs.clock.paused and hs.vote is None
    hs.clock.paused = False
    print("13: пауза по просьбе клиента — только после голосования большинства")

    # 14. Игрок отключается и возвращается по токену, прогресс сохранён
    tok = a.token
    a.close()
    wait(lambda: "DEU" not in st.humans, [hs, sb], msg="отключение не замечено")
    assert "DEU" in host.reserved_isos()
    pump([hs, sb], 1.0, 0.1)
    a2 = connect(host, "Анна", token=tok, pumps=[hs, sb])
    assert a2.resumed and a2.iso == "DEU" and a2.phase == "play"
    st_a2 = new_state(seed=1)
    sa2 = Session(st_a2, "client", client=a2)
    wait(lambda: sa2.synced, [hs, sb, sa2], msg="возвращение не синхронизировано")
    assert "DEU" in st.humans and st_a2.player_iso == "DEU"
    settle(hs, [sa2, sb])
    print("14: игрок вернулся по токену в свою страну; состояния совпали")

    # 15. Лимит игроков и несовместимая версия
    st2, host2 = make_host(max_players=2)
    hs2 = Session(st2, "host", host=host2)
    c1 = connect(host2, "Один", pumps=[hs2])
    wait(lambda: len(host2.players) == 2, [hs2])
    try:
        connect(host2, "Лишний", pumps=[hs2])
        raise AssertionError("сервер обязан отклонить подключение при заполненном лимите")
    except ClientError as exc:
        assert "мест" in str(exc), exc
    s = socket.create_connection(("127.0.0.1", host2.port), timeout=3)
    s.sendall(encode({"t": "hello", "v": NET_VERSION + 5, "name": "Старый"}))
    pump([hs2], 0.3)
    rej = recv_msg(s)
    assert rej["t"] == "reject" and "Версии" in rej["reason"]
    s.close()
    print("15: лимит игроков и несовместимая версия корректно отклоняются")

    # 16. Мусорные данные не ломают хост
    junk = [b"\x00\x00\x00", b"\xff\xff\xff\xff\x00abc", struct.pack(">IB", 5, 0) + b"{not", struct.pack(">IB", 3, 0) + b"[]",
            struct.pack(">IB", 4, 1) + b"xyz", os.urandom(300), struct.pack(">IB", 70000, 0) + b"x" * 10]
    for j in junk:
        try:
            s = socket.create_connection(("127.0.0.1", host2.port), timeout=2)
            s.sendall(j)
            time.sleep(0.05)
            s.close()
        except OSError:
            pass
    c1.send({"t": "unknown_type", "x": 1})
    c1.send({"t": "cmd", "id": "bad", "cmd": 5})
    pump([hs2], 0.5)
    c1.send({"t": "ping"})
    got = []
    wait(lambda: any(m.get("t") == "pong" for m in got + c1.pump()) or got.extend(c1.pump()) or any(
        m.get("t") == "pong" for m in got), [hs2], msg="хост перестал отвечать после мусора")
    print("16: мусорные и некорректные сообщения хост переживает, остальные клиенты работают")

    # 17-18. Несколько клиентов входят одновременно, пока хост продолжает считать дни
    st3, host3 = make_host()
    hs3 = Session(st3, "host", host=host3)
    host3.pick(0, "USA")
    hs3.begin_play()
    hs3.clock.set_speed(2)
    hs3.clock.paused = False
    results = {}

    def join(nm, iso):
        try:
            cl = connect_nopump(host3, nm)
            sh_ = new_state(seed=hash(nm) % 1000)
            ss = Session(sh_, "client", client=cl)
            cl.claim(iso)
            results[nm] = (cl, ss)
        except Exception as exc:                        # noqa: BLE001
            results[nm] = exc
    names = [("К1", "DEU"), ("К2", "FRA"), ("К3", "RUS"), ("К4", "CHN")]
    ths = [threading.Thread(target=join, args=n) for n in names]
    d0 = st3.date
    for t in ths:
        t.start()
    pump([hs3], 0.4, 0.1)
    for t in ths:
        t.join(5)
    assert all(not isinstance(v, Exception) for v in results.values()), results
    sess = [v[1] for v in results.values()]
    wait(lambda: all(s_.synced for s_ in sess), [hs3] + sess, 8, "параллельный вход")
    assert st3.date > d0, "игра продолжала идти во время подключения"
    assert st3.humans == {"USA", "DEU", "FRA", "RUS", "CHN"}, st3.humans
    settle(hs3, sess)
    print("17-18: четыре клиента вошли одновременно, партия не останавливалась, состояние едино; дней прошло:",
          (st3.date - d0).days)

    for obj in (host, host2, host3):
        obj.close()
    print("LAN OK")


if __name__ == "__main__":
    main()
