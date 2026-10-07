"""Генерирует src/data/countries.json: ~200 стран с реалистичными стартовыми данными."""
from __future__ import annotations

import colorsys
import hashlib
import json
import logging
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from config import COUNTRIES_PATH  # noqa: E402

log = logging.getLogger("gen_countries")

# ISO|Название|Континент|Население, млн|ВВП, млрд $|Столица
RAW = """
DZA|Алжир|Africa|45|240|Алжир
AGO|Ангола|Africa|36|85|Луанда
BEN|Бенин|Africa|13|19|Порто-Ново
BWA|Ботсвана|Africa|2.6|20|Габороне
BFA|Буркина-Фасо|Africa|23|20|Уагадугу
BDI|Бурунди|Africa|13|3|Гитега
CPV|Кабо-Верде|Africa|0.6|2.4|Прая
CMR|Камерун|Africa|28|47|Яунде
CAF|ЦАР|Africa|5.5|2.5|Банги
TCD|Чад|Africa|18|13|Нджамена
COM|Коморы|Africa|0.8|1.3|Морони
COG|Конго|Africa|6|15|Браззавиль
COD|ДР Конго|Africa|102|66|Киншаса
CIV|Кот-д'Ивуар|Africa|28|79|Ямусукро
DJI|Джибути|Africa|1.1|3.5|Джибути
EGY|Египет|Africa|112|400|Каир
GNQ|Экваториальная Гвинея|Africa|1.7|12|Малабо
ERI|Эритрея|Africa|3.7|2.5|Асмэра
SWZ|Эсватини|Africa|1.2|4.7|Мбабане
ETH|Эфиопия|Africa|126|160|Аддис-Абеба
GAB|Габон|Africa|2.4|20|Либревиль
GMB|Гамбия|Africa|2.7|2.3|Банжул
GHA|Гана|Africa|34|76|Аккра
GIN|Гвинея|Africa|14|21|Конакри
GNB|Гвинея-Бисау|Africa|2.1|1.8|Бисау
KEN|Кения|Africa|55|108|Найроби
LSO|Лесото|Africa|2.3|2.2|Масеру
LBR|Либерия|Africa|5.3|4|Монровия
LBY|Ливия|Africa|7|45|Триполи
MDG|Мадагаскар|Africa|30|15|Антананариву
MWI|Малави|Africa|20|13|Лилонгве
MLI|Мали|Africa|23|19|Бамако
MRT|Мавритания|Africa|4.9|10|Нуакшот
MUS|Маврикий|Africa|1.3|14|Порт-Луи
MAR|Марокко|Africa|37|134|Рабат
MOZ|Мозамбик|Africa|33|18|Мапуту
NAM|Намибия|Africa|2.6|12|Виндхук
NER|Нигер|Africa|27|15|Ниамей
NGA|Нигерия|Africa|224|363|Абуджа
RWA|Руанда|Africa|14|14|Кигали
STP|Сан-Томе и Принсипи|Africa|0.2|0.5|Сан-Томе
SEN|Сенегал|Africa|18|28|Дакар
SYC|Сейшелы|Africa|0.1|2|Виктория
SLE|Сьерра-Леоне|Africa|8.6|4|Фритаун
SOM|Сомали|Africa|18|11|Могадишо
ZAF|ЮАР|Africa|60|380|Претория
SSD|Южный Судан|Africa|11|5|Джуба
SDN|Судан|Africa|48|35|Хартум
TZA|Танзания|Africa|67|79|Додома
TGO|Того|Africa|9|9|Ломе
TUN|Тунис|Africa|12|48|Тунис
UGA|Уганда|Africa|49|49|Кампала
ZMB|Замбия|Africa|20|28|Лусака
ZWE|Зимбабве|Africa|16|35|Хараре
ESH|Западная Сахара|Africa|0.6|1|Эль-Аюн
AFG|Афганистан|Asia|41|14|Кабул
ARM|Армения|Asia|3|24|Ереван
AZE|Азербайджан|Asia|10|78|Баку
BHR|Бахрейн|Asia|1.5|44|Манама
BGD|Бангладеш|Asia|172|437|Дакка
BTN|Бутан|Asia|0.8|2.5|Тхимпху
BRN|Бруней|Asia|0.45|15|Бандар-Сери-Бегаван
KHM|Камбоджа|Asia|17|31|Пномпень
CHN|Китай|Asia|1410|17800|Пекин
CYP|Кипр|Asia|1.3|31|Никосия
GEO|Грузия|Asia|3.7|30|Тбилиси
IND|Индия|Asia|1428|3550|Нью-Дели
IDN|Индонезия|Asia|277|1370|Джакарта
IRN|Иран|Asia|89|400|Тегеран
IRQ|Ирак|Asia|45|250|Багдад
ISR|Израиль|Asia|9.8|510|Иерусалим
JPN|Япония|Asia|124|4200|Токио
JOR|Иордания|Asia|11|50|Амман
KAZ|Казахстан|Asia|20|260|Астана
KWT|Кувейт|Asia|4.3|160|Эль-Кувейт
KGZ|Киргизия|Asia|7|14|Бишкек
LAO|Лаос|Asia|7.5|15|Вьентьян
LBN|Ливан|Asia|5.5|18|Бейрут
MYS|Малайзия|Asia|34|400|Куала-Лумпур
MDV|Мальдивы|Asia|0.5|6|Мале
MNG|Монголия|Asia|3.4|20|Улан-Батор
MMR|Мьянма|Asia|54|65|Нейпьидо
NPL|Непал|Asia|30|40|Катманду
PRK|КНДР|Asia|26|18|Пхеньян
OMN|Оман|Asia|4.6|105|Маскат
PAK|Пакистан|Asia|240|340|Исламабад
PSE|Палестина|Asia|5.4|18|Рамалла
PHL|Филиппины|Asia|117|440|Манила
QAT|Катар|Asia|2.7|220|Доха
SAU|Саудовская Аравия|Asia|36|1070|Эр-Рияд
SGP|Сингапур|Asia|5.9|500|Сингапур
KOR|Южная Корея|Asia|51|1700|Сеул
LKA|Шри-Ланка|Asia|22|75|Коломбо
SYR|Сирия|Asia|22|10|Дамаск
TWN|Тайвань|Asia|23.5|760|Тайбэй
TJK|Таджикистан|Asia|10|12|Душанбе
THA|Таиланд|Asia|72|515|Бангкок
TLS|Восточный Тимор|Asia|1.4|2|Дили
TUR|Турция|Asia|85|1030|Анкара
TKM|Туркменистан|Asia|6.5|60|Ашхабад
ARE|ОАЭ|Asia|9.5|510|Абу-Даби
UZB|Узбекистан|Asia|36|90|Ташкент
VNM|Вьетнам|Asia|100|430|Ханой
YEM|Йемен|Asia|34|21|Сана
ALB|Албания|Europe|2.8|22|Тирана
AND|Андорра|Europe|0.08|3.3|Андорра-ла-Велья
AUT|Австрия|Europe|9|520|Вена
BLR|Беларусь|Europe|9.2|72|Минск
BEL|Бельгия|Europe|11.7|630|Брюссель
BIH|Босния и Герцеговина|Europe|3.2|26|Сараево
BGR|Болгария|Europe|6.4|100|София
HRV|Хорватия|Europe|3.9|82|Загреб
CZE|Чехия|Europe|10.5|330|Прага
DNK|Дания|Europe|5.9|400|Копенгаген
EST|Эстония|Europe|1.4|41|Таллин
FIN|Финляндия|Europe|5.6|300|Хельсинки
FRA|Франция|Europe|68|3030|Париж
DEU|Германия|Europe|84|4450|Берлин
GRC|Греция|Europe|10.4|240|Афины
HUN|Венгрия|Europe|9.6|210|Будапешт
ISL|Исландия|Europe|0.38|31|Рейкьявик
IRL|Ирландия|Europe|5.2|550|Дублин
ITA|Италия|Europe|59|2250|Рим
KOS|Косово|Europe|1.8|10|Приштина
LVA|Латвия|Europe|1.9|43|Рига
LIE|Лихтенштейн|Europe|0.04|7|Вадуц
LTU|Литва|Europe|2.8|78|Вильнюс
LUX|Люксембург|Europe|0.66|85|Люксембург
MLT|Мальта|Europe|0.54|20|Валлетта
MDA|Молдова|Europe|2.6|15|Кишинёв
MCO|Монако|Europe|0.04|8|Монако
MNE|Черногория|Europe|0.62|7|Подгорица
NLD|Нидерланды|Europe|17.6|1120|Амстердам
MKD|Северная Македония|Europe|1.8|14|Скопье
NOR|Норвегия|Europe|5.5|480|Осло
POL|Польша|Europe|37.7|810|Варшава
PRT|Португалия|Europe|10.3|285|Лиссабон
ROU|Румыния|Europe|19|350|Бухарест
RUS|Россия|Europe|144|2000|Москва
SMR|Сан-Марино|Europe|0.034|2|Сан-Марино
SRB|Сербия|Europe|6.7|75|Белград
SVK|Словакия|Europe|5.4|133|Братислава
SVN|Словения|Europe|2.1|68|Любляна
ESP|Испания|Europe|48|1580|Мадрид
SWE|Швеция|Europe|10.5|590|Стокгольм
CHE|Швейцария|Europe|8.8|905|Берн
UKR|Украина|Europe|37|160|Киев
GBR|Великобритания|Europe|67|3340|Лондон
VAT|Ватикан|Europe|0.001|0.3|Ватикан
GRL|Гренландия|North America|0.056|3.1|Нуук
ATG|Антигуа и Барбуда|North America|0.1|1.9|Сент-Джонс
BHS|Багамы|North America|0.4|14|Нассау
BRB|Барбадос|North America|0.28|5.5|Бриджтаун
BLZ|Белиз|North America|0.4|3|Бельмопан
CAN|Канада|North America|40|2140|Оттава
CRI|Коста-Рика|North America|5.2|86|Сан-Хосе
CUB|Куба|North America|11|107|Гавана
DMA|Доминика|North America|0.07|0.6|Розо
DOM|Доминиканская Республика|North America|11|121|Санто-Доминго
SLV|Сальвадор|North America|6.3|34|Сан-Сальвадор
GRD|Гренада|North America|0.12|1.2|Сент-Джорджес
GTM|Гватемала|North America|18|102|Гватемала
HTI|Гаити|North America|11.7|20|Порт-о-Пренс
HND|Гондурас|North America|10.4|34|Тегусигальпа
JAM|Ямайка|North America|2.8|19|Кингстон
MEX|Мексика|North America|128|1790|Мехико
NIC|Никарагуа|North America|6.9|17|Манагуа
PAN|Панама|North America|4.5|83|Панама
KNA|Сент-Китс и Невис|North America|0.05|1.1|Бастер
LCA|Сент-Люсия|North America|0.18|2.4|Кастри
VCT|Сент-Винсент и Гренадины|North America|0.1|0.9|Кингстаун
TTO|Тринидад и Тобаго|North America|1.5|28|Порт-оф-Спейн
USA|США|North America|335|27360|Вашингтон
PRI|Пуэрто-Рико|North America|3.2|120|Сан-Хуан
ARG|Аргентина|South America|46|640|Буэнос-Айрес
BOL|Боливия|South America|12|45|Сукре
BRA|Бразилия|South America|216|2170|Бразилиа
CHL|Чили|South America|19.6|335|Сантьяго
COL|Колумбия|South America|52|363|Богота
ECU|Эквадор|South America|18|118|Кито
GUY|Гайана|South America|0.8|16|Джорджтаун
PRY|Парагвай|South America|6.8|42|Асунсьон
PER|Перу|South America|34|267|Лима
SUR|Суринам|South America|0.6|3.6|Парамарибо
URY|Уругвай|South America|3.4|77|Монтевидео
VEN|Венесуэла|South America|28|100|Каракас
FLK|Фолклендские острова|South America|0.003|0.3|Стэнли
AUS|Австралия|Oceania|26|1700|Канберра
FJI|Фиджи|Oceania|0.93|5|Сува
KIR|Кирибати|Oceania|0.13|0.2|Тарава
MHL|Маршалловы Острова|Oceania|0.04|0.3|Маджуро
FSM|Микронезия|Oceania|0.11|0.4|Паликир
NRU|Науру|Oceania|0.01|0.14|Ярен
NZL|Новая Зеландия|Oceania|5.2|252|Веллингтон
PLW|Палау|Oceania|0.018|0.25|Нгерулмуд
PNG|Папуа — Новая Гвинея|Oceania|10.3|31|Порт-Морсби
WSM|Самоа|Oceania|0.22|0.9|Апиа
SLB|Соломоновы Острова|Oceania|0.74|1.6|Хониара
TON|Тонга|Oceania|0.1|0.5|Нукуалофа
TUV|Тувалу|Oceania|0.01|0.06|Фунафути
VUT|Вануату|Oceania|0.33|1.0|Порт-Вила
NCL|Новая Каледония|Oceania|0.27|9.4|Нумеа
"""

COMMUNIST = {"CHN", "PRK", "CUB", "VNM", "LAO"}
AUTOCRACY = {"RUS", "BLR", "IRN", "SAU", "ARE", "QAT", "KWT", "OMN", "BHR", "SYR", "TKM",
             "UZB", "TJK", "AZE", "ERI", "EGY", "KAZ", "VEN", "NIC", "MMR", "AFG", "TCD",
             "GNQ", "CMR", "BDI", "DJI", "RWA", "UGA", "SDN", "SWZ", "KGZ", "JOR", "BRN"}
NEUTRAL = {"CHE", "AUT", "IRL", "LIE", "MCO", "SMR", "VAT", "AND", "MLT", "CRI"}
OIL_RICH = {"SAU", "RUS", "ARE", "IRQ", "IRN", "KWT", "QAT", "VEN", "NGA", "LBY", "DZA", "KAZ",
            "NOR", "CAN", "USA", "BRA", "MEX", "AGO", "OMN", "GAB", "COG", "AZE"}
STEEL_RICH = {"CHN", "IND", "RUS", "BRA", "AUS", "UKR", "ZAF", "USA", "CAN", "SWE", "KAZ"}
FOOD_RICH = {"USA", "BRA", "ARG", "UKR", "RUS", "IND", "CHN", "FRA", "CAN", "AUS", "THA", "VNM"}
FRAGILE = {"AFG", "SOM", "SSD", "YEM", "SYR", "SDN", "LBY", "HTI", "MLI", "BFA", "CAF", "COD", "MMR"}


def _h(iso: str, salt: str = "") -> int:
    """Детерминированный хеш для псевдослучайных, но воспроизводимых значений."""
    return int(hashlib.md5((iso + salt).encode()).hexdigest()[:8], 16)


def make_color(iso: str) -> list[int]:
    """Генерирует устойчивый цвет страны по ISO-коду."""
    hue = (_h(iso) % 360) / 360.0
    sat = 0.45 + (_h(iso, "s") % 30) / 100.0
    val = 0.65 + (_h(iso, "v") % 25) / 100.0
    r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
    return [int(r * 255), int(g * 255), int(b * 255)]


def ideology_for(iso: str, continent: str) -> str:
    """Определяет стартовую идеологию страны."""
    if iso in COMMUNIST:
        return "communism"
    if iso in AUTOCRACY:
        return "autocracy"
    if iso in NEUTRAL:
        return "neutral"
    return "democracy" if continent in {"Europe", "North America", "South America", "Oceania"} \
        or iso in {"JPN", "KOR", "IND", "ISR", "TWN", "PHL", "IDN", "ZAF", "GHA", "BWA", "MUS"} \
        else "neutral"


def build() -> list[dict]:
    """Строит список словарей стран из RAW-таблицы."""
    out: list[dict] = []
    for line in RAW.strip().splitlines():
        iso, name, cont, pop, gdp, cap = line.split("|")
        population, gdp_b = float(pop), float(gdp)
        gpc_k = gdp_b / max(population, 0.001)  # тыс. $ на человека
        stab = 45 + 14 * math.log10(1 + gpc_k) + (_h(iso, "st") % 9 - 4)
        if iso in FRAGILE:
            stab -= 15
        out.append({
            "iso": iso, "name": name, "continent": cont,
            "population": population, "gdp": gdp_b,
            "manpower": round(population * 1000 * 0.05, 1),  # тыс. человек
            "stability": round(max(15.0, min(92.0, stab)), 1),
            "ideology": ideology_for(iso, cont),
            "color": make_color(iso), "capital": cap,
            "oil_mult": 3.0 if iso in OIL_RICH else 0.6 + (_h(iso, "o") % 80) / 100,
            "steel_mult": 2.5 if iso in STEEL_RICH else 0.6 + (_h(iso, "m") % 80) / 100,
            "food_mult": 1.6 if iso in FOOD_RICH else 0.6 + (_h(iso, "f") % 70) / 100,
        })
    return out


def main() -> None:
    """Пишет countries.json."""
    logging.basicConfig(level=logging.INFO)
    data = build()
    COUNTRIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    COUNTRIES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("Сгенерировано %d стран → %s", len(data), COUNTRIES_PATH)


if __name__ == "__main__":
    main()
