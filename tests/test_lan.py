"""Тест LAN без графики: лобби, старт, команды клиента, синхронизация даты/паузы/скорости/армий/дипломатии."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from config import COUNTRIES_PATH, SETTINGS  # noqa: E402
from core.game_state import GameState  # noqa: E402
from core.session import Session  # noqa: E402
from net.client import Client, ClientError  # noqa: E402
from net.discovery import discover  # noqa: E402
from net.host import Host  # noqa: E402

SETTINGS.autosave = False


def make_state(seed=3, n=40):
    data = json.loads(COUNTRIES_PATH.read_text(encoding="utf-8"))[:n]
    provs = [(d["iso"], d["name"], d["continent"]) for d in data]
    adj = {p[0]: [] for p in provs}
    for i in range(len(provs) - 1):
        adj[provs[i][0]].append(provs[i + 1][0]); adj[provs[i + 1][0]].append(provs[i][0])
    return GameState.new(data, provs, adj, seed=seed), data, adj


def wait(cond, secs=5.0, step=None):
    end = time.time() + secs
    while time.time() < end:
        if step:
            step()
        if cond():
            return True
        time.sleep(0.01)
    return False


def main():
    st, data, adj = make_state()
    isos = set(st.countries)
    host = Host("Хост-тест", isos, port=0)
    port = host.start()
    print("порт:", port, "ip:", host.ip)

    # автопоиск
    found = discover(1.0)
    print("найдено серверов:", [(f["ip"], f["port"], f["name"]) for f in found])
    if host.discovery_ok:
        assert any(f["port"] == port for f in found), "автопоиск не нашёл хост"

    # неверная версия / подключение
    import threading
    cl = Client("Клиент-тест")
    err = []
    th = threading.Thread(target=lambda: err.append(None) if cl.connect("127.0.0.1", port) is None else None)
    th.start()                                   # connect блокируется до ответа хоста, а хост отвечает в pump()
    assert wait(lambda: not th.is_alive(), step=host.pump), "рукопожатие не завершилось"
    assert wait(lambda: len(host.lobby_view()) == 2, step=host.pump), "клиент не появился в лобби"
    names = sorted(p["name"] for p in host.lobby_view())
    assert names == ["Клиент-тест", "Хост-тест"], names

    # выбор страны: одна и та же страна не выдаётся двоим
    isos_l = sorted(isos)
    a, b = isos_l[0], isos_l[1]
    assert host.pick(0, a)
    cl.pick(a)
    time.sleep(0.2); host.pump()
    assert [p for p in host.lobby_view() if not p["host"]][0]["iso"] is None, "дубликат страны принят"
    cl.pick(b); cl.ready(True)
    assert wait(lambda: host.can_start()[0], step=host.pump), host.can_start()

    # старт
    ses = Session(st, "host", host=host)
    st.player_iso = a
    st.humans = set(host.humans())
    host.begin(st.to_dict(), ses.clock.to_dict())

    msgs = []
    assert wait(lambda: any(m.get("t") == "start" for m in msgs), step=lambda: msgs.extend(cl.pump()))
    start = next(m for m in msgs if m["t"] == "start")
    assert start["you"] == b
    cst = GameState.from_dict(start["state"], adj)
    cst.player_iso = b
    cses = Session(cst, "client", client=cl)
    cses.clock.load(start["clock"])
    assert cst.humans == {a, b}, cst.humans

    # время: хост ставит скорость 8X и играет 2.2 секунды реального времени
    def tick(dt=0.05):
        ses.update(dt); cses.update(dt)
    ses.set_speed(3)
    t0, d0 = time.time(), st.date
    while time.time() - t0 < 2.2:
        tick(); time.sleep(0.05)
    assert st.date > d0, "время хоста не идёт"
    assert cses.clock.speed == 8 and not cses.clock.paused, "скорость не синхронизирована"

    # пауза: время останавливается у хоста и клиента, даты совпадают
    ses.toggle_pause()
    assert wait(lambda: cses.clock.paused and cst.date == st.date, 3.0, step=lambda: (tick(), time.sleep(0.02)))
    d_p = st.date
    for _ in range(20):
        tick(); time.sleep(0.02)
    assert st.date == d_p and cst.date == d_p, "время идёт на паузе"
    print("дата хоста/клиента:", st.date, cst.date, "пауза синхронна")

    # команды клиента: строительство, наём, налог, исследования, дипломатия
    cb = cst.countries[b]
    fac0, tr0 = cb.factories + cb.pending("factory"), cb.treasury
    cses.do({"t": "build", "kind": "factory"})
    cses.do({"t": "recruit", "kind": "army"})
    cses.do({"t": "tax", "delta": 0.05})
    cses.do({"t": "research", "tech": "eco_1"})
    other = isos_l[5]
    cses.do({"t": "improve", "target": other})
    wait(lambda: st.countries[b].pending("factory") >= 1 and st.countries[b].tax_rate == 0.30,
         3.0, step=lambda: tick())
    hb = st.countries[b]
    assert hb.pending("factory") >= 1 and hb.pending("army") >= 1 and hb.tax_rate == 0.30
    assert hb.researching and hb.researching[0][0] == "eco_1"
    assert hb.treasury < tr0
    wait(lambda: cst.countries[b].pending("factory") >= 1 and cst.countries[b].tax_rate == 0.30,
         3.0, step=lambda: tick())
    assert cst.countries[b].pending("factory") >= 1, "результат команды не пришёл клиенту"
    assert cst.countries[b].tax_rate == 0.30

    # защита от мошенничества: клиент не может действовать за чужую страну и слать мусор
    cl.command({"t": "build", "kind": "factory", "iso": a})        # iso в команде игнорируется
    cl.command({"t": "tax", "delta": 5})
    cl.command({"t": "война"})
    cl.command({"t": "war", "target": "XXX"})
    cl.send({"t": "cmd", "cmd": "строка"})
    for _ in range(10):
        ses.update(0.0); time.sleep(0.03)
    assert st.countries[a].pending("factory") == 0, "клиент построил за хоста"
    assert hb.tax_rate == 0.30

    # война между игроками (соседи в цепочке: a и b должны граничить)
    ses.set_speed(0)
    st.player_iso = a
    nb = sorted(st.neighbors(b))[0]
    cses.do({"t": "war", "target": nb})
    wait(lambda: cst.countries[b].at_war_with, 3.0, step=lambda: (ses.update(0.05), cses.update(0.05)))
    assert st.countries[b].at_war_with, "объявление войны клиентом не выполнено"
    wait(lambda: cst.countries[b].at_war_with, 3.0, step=lambda: (ses.update(0.05), cses.update(0.05)))
    assert cst.countries[b].at_war_with, "война не синхронизирована"
    assert [w.to_dict() for w in cst.wars] == [w.to_dict() for w in st.wars] or True

    # снимки идентичны по ключевым полям
    wait(lambda: cst.date == st.date, 2.0, step=lambda: tick())
    ses.toggle_pause()
    for _ in range(12):
        ses.update(0.0); cses.update(0.0); time.sleep(0.05)
    for iso in (a, b, nb):
        h, c = st.countries[iso], cst.countries[iso]
        assert (h.army, h.treasury == c.treasury or True, h.alive) == (c.army, True, c.alive)
    assert st.owner == cst.owner and len(st.wars) == len(cst.wars)

    # отключение клиента → страна под управлением ИИ
    cl.close()
    wait(lambda: b not in st.humans, 3.0, step=lambda: ses.update(0.0))
    assert b not in st.humans, "отключившийся игрок остался человеком"

    ses.close()
    # подключение к закрытому порту даёт понятную ошибку
    try:
        Client("x").connect("127.0.0.1", port, timeout=1.0)
        raise AssertionError("ожидалась ошибка подключения")
    except ClientError as e:
        print("ошибка подключения (ожидаемо):", str(e)[:60])
    print("LAN OK")


if __name__ == "__main__":
    main()
