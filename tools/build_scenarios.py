"""Генерирует src/data/scenarios/*.json (сценарии: современность, 1939, 1488).

Данные заданы здесь компактно и приблизительно: границы строятся из регионов современных стран, поэтому
для исторических дат это не точные границы. Новую дату добавляют новым файлом того же формата (см. README).
Запуск: python tools/build_scenarios.py
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src" / "data" / "scenarios"


def E(id, name, color, pop=None, gdp=None, army=None, capital=None, cap_ll=None, ideology="autocracy",
      new=False, navy=None, air=None, techs=None, stability=None):
    d = {"id": id, "name": name, "color": list(color), "ideology": ideology}
    for k, v in (("pop", pop), ("gdp", gdp), ("army", army), ("capital", capital), ("cap_ll", cap_ll),
                 ("navy", navy), ("air", air), ("techs", techs), ("stability", stability)):
        if v is not None:
            d[k] = v
    if new:
        d["new"] = True
        if pop is not None:
            d["population"] = pop
        if gdp is not None:
            d["gdp"] = gdp
    return d


def R(to, c=None, regions=None, box=None, ctrl=None):
    d = {"to": to}
    if c:
        d["c"] = c
    if regions:
        d["regions"] = regions
    if box:
        d["box"] = box
    if ctrl:
        d["ctrl"] = ctrl
    return d


modern = {
    "v": 1, "id": "modern", "name": "Современность — 2025", "date": [2025, 1, 1], "era": "modern", "approx": False,
    "desc": "Мир таким, каким он задан картой: современные государства и их регионы, современные технологии.",
    "notes": "", "rules": [], "countries": [],
}

T1 = ["inf_1", "ind_1", "eco_1", "navy_1", "infra_1", "tank_1", "air_1"]
EUR_COL_GBR = ["IND", "PAK", "BGD", "LKA", "MMR", "MYS", "BRN", "SGP", "SDN", "SSD", "KEN", "UGA", "TZA", "ZMB", "ZWE",
               "MWI", "BWA", "NGA", "GHA", "SLE", "GMB", "PSE", "ISR", "JOR", "CYP", "MLT", "JAM", "TTO", "GUY", "BLZ",
               "FJI", "LSO", "SWZ", "BHR", "KWT", "QAT", "ARE", "OMN", "BHS", "BRB", "ATG", "DMA", "GRD", "KNA", "LCA",
               "VCT", "TON", "SLB", "VUT", "FLK", "MUS", "SYC", "NPL", "BTN", "MDV"]
FRA_COL = ["DZA", "MAR", "TUN", "SEN", "MLI", "MRT", "NER", "TCD", "CIV", "BFA", "GIN", "BEN", "TGO", "GAB", "COG",
           "CAF", "CMR", "MDG", "DJI", "VNM", "LAO", "KHM", "SYR", "LBN", "NCL", "COM"]
w1939 = {
    "v": 1, "id": "1939", "name": "1939 — Накануне Второй мировой", "date": [1939, 1, 1], "era": "ww2", "approx": True,
    "desc": "Январь 1939. Германия поглощает соседей, Япония воюет в Китае, демократии медлят, СССР готовится. "
            "Колониальные империи владеют Африкой и Азией; всё может пойти иначе.",
    "notes": "Границы приблизительны: карта построена на регионах современных стран, поэтому, например, Западная "
             "Украина и Западная Белоруссия показаны в составе СССР, а колонии — целыми странами.",
    "locked": [], "techs": T1,
    "default": {"pop_mult": 0.35, "gdp_mult": 0.1, "army_mult": 0.5, "air_mult": 0.2},
    "countries": [
        E("RUS", "СССР", (184, 56, 56), 170, 70, 80, "Москва", [37.6, 55.75], "communism", navy=14, air=40,
          techs=["inf_2", "tank_2", "ind_2", "air_1"]),
        E("DEU", "Германия", (96, 100, 110), 79, 85, 60, "Берлин", [13.4, 52.5], "autocracy", navy=12, air=55,
          techs=["inf_2", "tank_2", "air_2", "ind_2"]),
        E("GBR", "Британская империя", (196, 120, 132), 48, 80, 22, "Лондон", [-0.1, 51.5], "democracy", navy=40,
          air=35, techs=["navy_2", "air_2", "ind_2", "eco_2"]),
        E("FRA", "Франция", (96, 128, 190), 42, 45, 55, "Париж", [2.35, 48.85], "democracy", navy=22, air=25,
          techs=["inf_2", "tank_1", "ind_2"]),
        E("ITA", "Италия", (92, 148, 96), 44, 32, 40, "Рим", [12.5, 41.9], "autocracy", navy=18, air=25,
          techs=["inf_2", "navy_2"]),
        E("JPN", "Японская империя", (222, 202, 120), 73, 48, 48, "Токио", [139.7, 35.7], "autocracy", navy=34,
          air=30, techs=["navy_2", "air_2", "inf_2"]),
        E("USA", "США", (74, 118, 168), 131, 190, 14, "Вашингтон", [-77.0, 38.9], "democracy", navy=30, air=20,
          techs=["ind_3", "eco_3", "navy_2", "air_2"]),
        E("POL", "Польша", (178, 70, 84), 35, 14, 36, "Варшава", [21.0, 52.2], "autocracy", navy=2, air=10),
        E("CHN", "Китайская Республика", (206, 172, 70), 450, 28, 70, "Чунцин", [106.5, 29.6], "autocracy", navy=2,
          air=4),
        E("CZE", "Чехословакия", (128, 150, 196), 14, 10, 20, "Прага", [14.4, 50.1], "democracy"),
        E("SRB", "Югославия", (126, 112, 164), 15, 9, 24, "Белград", [20.5, 44.8], "autocracy"),
        E("ESP", "Испания (Франко)", (214, 170, 80), 25, 12, 30, "Бургос", [-3.7, 42.35], "autocracy"),
        E("MNC", "Маньчжоу-го", (170, 124, 78), 40, 6, 10, "Синьцзин", [125.3, 43.9], "autocracy", new=True),
    ],
    "rules": [
        R("DEU", c=["AUT"]), R("CZE", c=["SVK"]),
        R("RUS", c=["BLR", "UKR", "KAZ", "UZB", "TKM", "TJK", "KGZ", "GEO", "ARM", "AZE"]),
        R("ROU", c=["MDA"]), R("SRB", c=["BIH", "HRV", "SVN", "MKD", "MNE", "KOS"]),
        R("ITA", c=["LBY", "ETH", "ERI", "SOM"]), R("GBR", c=EUR_COL_GBR), R("FRA", c=FRA_COL),
        R("NLD", c=["IDN", "SUR"]), R("BEL", c=["COD", "RWA", "BDI"]),
        R("PRT", c=["AGO", "MOZ", "GNB", "CPV", "STP", "TLS"]), R("ESP", c=["GNQ", "ESH"]),
        R("USA", c=["PHL", "PRI"]), R("JPN", c=["KOR", "PRK", "TWN", "FSM", "MHL", "PLW"]),
        R("AUS", c=["PNG", "NRU"]), R("NZL", c=["WSM"]),
        R("MNC", regions=["CHN-02", "CHN-05", "CHN-11"]),
        R("CHN", regions=["CHN-09", "CHN-10", "CHN-06", "CHN-14"], ctrl="JPN"),
    ],
    "alliances": [["DEU", "ITA"], ["GBR", "FRA"], ["GBR", "POL"], ["FRA", "POL"], ["GBR", "AUS"], ["GBR", "CAN"],
                  ["GBR", "NZL"], ["GBR", "ZAF"], ["JPN", "MNC"]],
    "pacts": [["DEU", "JPN"], ["ITA", "JPN"]],
    "relations": [["DEU", "POL", -35], ["DEU", "FRA", -25], ["DEU", "GBR", -25], ["RUS", "DEU", -30],
                  ["RUS", "JPN", -30], ["JPN", "USA", -20], ["ITA", "FRA", -20], ["JPN", "GBR", -15],
                  ["JPN", "CHN", -60]],
    "wars": [{"attackers": ["JPN", "MNC"], "defenders": ["CHN"], "momentum": 10, "ago": 540}],
}

FLAT_1488 = ["USA", "CAN", "AUS", "NZL", "BRA", "ARG", "CHL", "COL", "VEN", "URY", "PRY", "GRL", "PNG", "ZAF", "NAM",
             "BWA", "AGO", "MOZ", "MDG", "SAU", "KAZ", "MNG", "CUB", "DOM", "HTI", "JAM", "ISL", "FJI"]
w1488 = {
    "v": 1, "id": "1488", "name": "1488 — Позднее Средневековье", "date": [1488, 1, 1], "era": "medieval",
    "approx": True,
    "desc": "Конец XV века. Османы воюют с мамлюками, Москва собирает русские земли, Испания завершает Реконкисту, "
            "Мин правит Китаем, в Мезоамерике и Андах растут империи ацтеков и инков.",
    "notes": "Достоверных границ нет: карта построена на регионах современных стран. Государства показаны "
             "приблизительно (регионы отнесены к ближайшему историческому государству), земли без данных "
             "помечены «Земли: …». Танки и авиации в эту эпоху нет.",
    "locked": ["tanks", "air"], "techs": [],
    "default": {"pop_mult": 0.06, "gdp_mult": 0.004, "army_mult": 0.25, "air_mult": 0.0, "fallback_fmt": "Земли: {name}"},
    "flat": [{"c": FLAT_1488, "gdp": 3, "pop": 4, "army": 3}],
    "countries": [
        E("CHN", "Империя Мин", (200, 168, 64), 130, 60, 38, "Пекин", [116.4, 39.9]),
        E("MGL", "Могулистан и Турфан", (150, 130, 96), 5, 3, 5, new=True),
        E("TIB", "Тибет", (176, 98, 96), 5, 3, 4, new=True),
        E("JRC", "Чжурчжэни и племена Маньчжурии", (110, 130, 96), 4, 3, 5, new=True),
        E("MNG", "Монгольские ханства", (140, 120, 80), 3, 3, 12),
        E("KOR", "Чосон", (160, 190, 210), 9, 8, 10, "Ханян", [127.0, 37.55]),
        E("JPN", "Япония (Сэнгоку)", (214, 214, 210), 14, 10, 14, "Киото", [135.75, 35.0]),
        E("VNM", "Дайвьет", (120, 170, 110), 4, 6, 9),
        E("THA", "Аютия", (160, 120, 170), 3, 5, 8),
        E("MMR", "Государства Бирмы", (190, 150, 90), 3, 4, 6),
        E("MYS", "Малаккский султанат", (96, 160, 150), 2, 6, 5),
        E("IDN", "Нусантара (султанаты и Маджапахит)", (120, 150, 90), 8, 5, 7),
        E("IND", "Делийский султанат (Лоди)", (170, 110, 80), 25, 18, 20, "Дели", [77.2, 28.6]),
        E("GUJ", "Гуджаратский султанат", (190, 130, 100), 6, 6, 8, new=True),
        E("BGD", "Бенгальский султанат", (110, 150, 120), 7, 6, 8),
        E("BHM", "Бахманидский султанат", (150, 90, 110), 8, 6, 9, new=True),
        E("VJN", "Виджаянагара", (210, 150, 70), 10, 8, 12, new=True),
        E("AFG", "Тимуриды (Герат)", (96, 130, 170), 6, 8, 10, "Герат", [62.2, 34.35]),
        E("IRN", "Ак-Коюнлу (Тебриз)", (130, 90, 140), 7, 8, 12, "Тебриз", [46.3, 38.1]),
        E("TUR", "Османская империя", (84, 140, 100), 9, 22, 26, "Эдирне", [26.55, 41.7]),
        E("EGY", "Мамлюкский султанат", (200, 180, 100), 6, 14, 15, "Каир", [31.2, 30.05]),
        E("SAU", "Бедуинские племена Аравии", (190, 170, 130), 3, 3, 4),
        E("RUS", "Великое княжество Московское", (170, 60, 60), 7, 10, 13, "Москва", [37.6, 55.75]),
        E("KZN", "Казанское ханство", (110, 150, 170), 2, 4, 6, new=True),
        E("GHD", "Большая Орда", (160, 140, 90), 2, 5, 10, new=True),
        E("SIB", "Сибирские ханства и племена", (120, 140, 130), 1, 2, 3, new=True),
        E("POL", "Польша и Литва", (190, 90, 100), 9, 16, 16, "Краков", [19.9, 50.05]),
        E("LVA", "Ливонский орден", (150, 150, 160), 1, 4, 5),
        E("DNK", "Кальмарская уния", (190, 110, 90), 3, 7, 8, "Копенгаген", [12.6, 55.7]),
        E("SWE", "Швеция", (100, 140, 190), 2, 5, 7, "Стокгольм", [18.1, 59.3]),
        E("DEU", "Священная Римская империя", (140, 140, 150), 14, 22, 15, "Вена", [16.4, 48.2]),
        E("CZE", "Королевство Богемия", (120, 160, 190), 3, 6, 6),
        E("HUN", "Королевство Венгрия", (170, 100, 70), 4, 10, 12, "Буда", [19.05, 47.5]),
        E("ROU", "Валахия и Молдавия", (130, 170, 120), 2, 4, 6),
        E("ITA", "Итальянские государства", (110, 170, 110), 9, 20, 12, "Венеция", [12.3, 45.45]),
        E("FRA", "Королевство Франция", (100, 130, 200), 14, 24, 20, "Париж", [2.35, 48.85]),
        E("ESP", "Кастилия и Арагон", (214, 170, 80), 7, 14, 14, "Вальядолид", [-4.7, 41.65]),
        E("PRT", "Португалия", (96, 160, 100), 1, 6, 5),
        E("GBR", "Королевство Англия", (196, 110, 120), 4, 9, 9, "Лондон", [-0.1, 51.5]),
        E("IRL", "Ирландские лордства", (90, 170, 100), 1, 2, 3),
        E("MAR", "Ваттасиды (Марокко)", (190, 90, 80), 3, 4, 6),
        E("DZA", "Государство Зайянидов", (170, 130, 80), 2, 4, 5),
        E("TUN", "Хафсиды", (150, 110, 100), 2, 4, 5),
        E("TUA", "Туареги и племена Сахары", (180, 160, 110), 1, 2, 3, new=True),
        E("SNG", "Империя Сонгай", (200, 140, 60), 5, 6, 10, "Гао", [0.05, 16.3], new=True),
        E("SEN", "Джолоф", (140, 170, 100), 1, 2, 3),
        E("BEN", "Королевство Бенин", (170, 100, 90), 1, 3, 4),
        E("ETH", "Эфиопская империя", (110, 160, 90), 4, 5, 9, "Шоа", [39.0, 9.0]),
        E("SOM", "Султанат Адал и Сомали", (150, 160, 120), 1, 2, 4),
        E("KNG", "Королевство Конго", (150, 120, 90), 2, 3, 5, new=True),
        E("ZWE", "Мономотапа", (170, 130, 110), 1, 3, 4),
        E("TZA", "Города Суахили (Килва)", (110, 150, 160), 1, 3, 3),
        E("AZT", "Ацтекская Тройственная империя", (176, 80, 70), 10, 8, 14, "Теночтитлан", [-99.1, 19.4], new=True),
        E("GTM", "Города-государства майя", (120, 160, 110), 2, 3, 5),
        E("INC", "Тауантинсуйу (Инки)", (200, 170, 70), 10, 8, 14, "Куско", [-72.0, -13.5], new=True),
    ],
    "rules": [
        # Европа
        R("DEU", c=["AUT", "SVN", "LUX", "BEL", "NLD", "LIE"]), R("HUN", c=["SVK", "HRV"]), R("ROU", c=["MDA"]),
        R("POL", c=["LTU", "BLR", "UKR"]), R("LVA", c=["EST"]), R("DNK", c=["NOR", "ISL"]), R("SWE", c=["FIN"]),
        R("ESP", c=["AND"]), R("TUR", c=["BGR", "GRC", "SRB", "MKD", "MNE", "ALB", "KOS", "BIH"]), R("ITA", c=["CYP", "MLT", "SMR", "VAT"]),
        # Россия и степь
        R("SIB", c=["RUS"]), R("RUS", regions=["RUS-13", "RUS-23", "RUS-20", "RUS-19"]),
        R("KZN", regions=["RUS-05", "RUS-09"]), R("GHD", regions=["RUS-11", "KAZ-02", "KAZ-04"]),
        R("KAZ", c=["KGZ"]),
        # Азия
        R("MGL", regions=["CHN-01", "CHN-08", "CHN-15", "CHN-21"]), R("TIB", regions=["CHN-04", "CHN-19", "CHN-20", "CHN-12"]),
        R("JRC", regions=["CHN-02", "CHN-05", "CHN-11"]), R("KOR", c=["PRK"]), R("IDN", c=["TLS"]),
        R("THA", c=["LAO", "KHM"]), R("MYS", c=["SGP", "BRN"]), 
        R("IND", regions=["IND-06", "IND-04", "PAK-01"]), R("GUJ", regions=["IND-02"]),
        R("BGD", regions=["IND-01", "IND-05"]), R("BHM", regions=["IND-03"]), R("VJN", regions=["IND-07"]),
        R("PAK", regions=["PAK-02"]), R("NPL", c=["BTN"]),
        R("AFG", c=["TKM", "UZB", "TJK"]), R("AFG", regions=["IRN-04"]),
        R("IRN", c=["IRQ", "AZE", "ARM"]), R("IRN", regions=["TUR-01"]),
        R("TUR", regions=["TUR-02"]),
        R("EGY", c=["SYR", "LBN", "PSE", "ISR", "JOR"]), R("EGY", regions=["SAU-02"]),
        R("SAU", c=["YEM", "OMN", "ARE", "QAT", "BHR", "KWT"]),
        # Африка
        R("TUN", regions=["LBY-03"]), R("TUA", regions=["DZA-02", "DZA-04", "DZA-05", "MLI-03", "NER-01", "TCD-02"]),
        R("SNG", c=["BFA"]), R("SNG", regions=["MLI-01", "MLI-02", "NER-02", "NER-03"]),
        R("SEN", c=["GMB"]), R("BEN", regions=["NGA-02"]), R("BEN", c=["TGO", "BEN"]),
        R("SOM", regions=["ETH-03"]), R("KNG", regions=["AGO-01", "COD-04"]), R("KNG", c=["COG", "GAB"]),
        R("ZWE", c=["ZWE"]), R("TZA", c=["KEN"]), R("TZA", regions=["MOZ-01"]),
        # Америка
        R("AZT", regions=["MEX-02", "MEX-03"]), R("GTM", c=["BLZ", "HND", "SLV"]),
        R("INC", c=["PER", "ECU", "BOL"]), R("INC", regions=["CHL-02", "ARG-01", "COL-03"]),
    ],
    "alliances": [["ESP", "PRT"], ["POL", "HUN"]],
    "pacts": [["FRA", "ESP"]],
    "relations": [["TUR", "EGY", -60], ["RUS", "KZN", -50], ["RUS", "POL", -30], ["FRA", "DEU", -15],
                  ["ESP", "FRA", -10], ["CHN", "MNG", -40]],
    "wars": [{"attackers": ["TUR"], "defenders": ["EGY"], "momentum": 0, "ago": 900},
             {"attackers": ["RUS"], "defenders": ["KZN"], "momentum": 4, "ago": 200}],
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for d in (modern, w1939, w1488):
        (OUT / f"{d['id']}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        print("записан", d["id"], len(json.dumps(d, ensure_ascii=False)) // 1024, "КБ")


if __name__ == "__main__":
    main()
