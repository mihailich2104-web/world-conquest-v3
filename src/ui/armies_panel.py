"""Панель «Войска»: список армий державы, подробности выбранной армии и приказы (стоп, отступление, разделение)."""
from __future__ import annotations

import math

from core import armies as am
from . import SidePanel

TERR_RU = {"plains": "равнины", "forest": "леса", "mountains": "горы", "desert": "пустыня", "jungle": "джунгли",
           "arctic": "арктика"}


def unit_word(st, n: int = 2) -> str:
    """«дивизий» в современности/1939, «полков» в средневековье."""
    if st.scenario.get("era") == "medieval":
        return "полк" if n % 10 == 1 and n % 100 != 11 else "полка" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "полков"
    return "див."


def short(st, rid: str, n: int = 26) -> str:
    """Короткое название региона."""
    name = st.geo.name.get(rid, rid)
    return name if len(name) <= n else name[: n - 1] + "…"


class ArmiesPanel(SidePanel):
    """Управление армиями: выбор из списка или кликом по плашке на карте, приказы — правой кнопкой по региону."""
    NAME = "armies"

    def build(self) -> None:
        ctx, st = self.ctx, self.ctx.state
        c = st.player if st else None
        if c is None or not ctx.can_act:
            return
        arms = sorted(am.armies_of(st, c.iso), key=lambda a: (-a.strength, a.id))
        sel = st.armies.get(ctx.sel_army) if ctx.sel_army else None
        ops = sum(1 for a in arms if a.path and a.target and am._offensive(st, c.iso, a.target))
        uw = unit_word(st)
        head = [f"<b>Войска</b>: армий {len(arms)}, {uw} всего {c.army}",
                f"Наступательных операций: {ops} из {am.ops_limit(st, c.iso)}"]
        self.add_text("<br>".join(head), 2)
        for a in arms[:6]:
            mark = "> " if sel is not None and a.id == sel.id else ""
            self.add_button(f"{mark}{a.name}: {a.units} {uw} — {short(st, a.region, 18)} — {am.describe_status(a)}",
                            (lambda aid=a.id: ctx.select_army(aid)), h=28)
        if len(arms) > 6:
            self.add_text(f"…и ещё {len(arms) - 6}. Остальные выбирайте плашкой на карте.", 1)
        if sel is None:
            self.add_text("Выберите армию: нажмите на её плашку на карте или строку списка. "
                          "Затем <b>правой кнопкой мыши</b> по региону — отправить армию. "
                          "Пунктир на карте — маршрут, число — срок прибытия в днях.", 5)
            return
        if sel.owner != c.iso:
            oc = st.countries.get(sel.owner)
            self.add_text(f"<b>Чужая армия</b>: {oc.name if oc else sel.owner}, около {sel.units} {uw}, "
                          f"регион «{short(st, sel.region)}».", 3)
            return
        eta = am.eta_days(st, sel)
        route = " → ".join(short(st, r, 14) for r in sel.path[:5]) + (" → …" if len(sel.path) > 5 else "")
        holder = am.ctrl_of(st, sel.region)
        hc = st.countries.get(holder)
        terrain = TERR_RU.get(st.geo.terrain.get(sel.region, ""), "")
        lines = [f"<b>{sel.name}</b> — {sel.units} {uw} (из {max(1, round(sel.max_strength))})",
                 f"Состояние: <b>{am.describe_status(sel)}</b>",
                 f"Мораль {sel.morale:.0f}%   Снабжение {sel.supply:.0f}%",
                 f"Регион: «{short(st, sel.region, 30)}», {terrain}; контроль: {hc.name if hc else '—'}",
                 f"Цель: «{short(st, sel.target, 28)}»" if sel.target else "Цель: нет приказа",
                 f"Прибытие через {math.ceil(eta)} дн." if eta >= 0 else "Прибытие: —"]
        if route:
            lines.append(f"Маршрут: {route}")
        if sel.fight:
            lines.append(f"<font color='#e08080'>Идёт бой в «{short(st, sel.fight, 28)}» ({sel.fight_days} дн.)</font>")
        elif st.occupying.get(sel.region):
            lines.append(f"Оккупация региона: {st.occupying[sel.region][1]:.0f} / {am.occ_days(st, sel.region, holder):.0f} дн.")
        self.add_text("<br>".join(lines), len(lines) + 1)
        idle = not sel.path and sel.progress <= 0 and not sel.fight
        mates = [x.id for x in arms if x.region == sel.region and not x.path and x.progress <= 0 and not x.fight]
        self.add_row([("Стоп", lambda: ctx.do({"t": "army_stop", "id": sel.id})),
                      ("Отступить", lambda: ctx.do({"t": "army_retreat", "id": sel.id}))], h=30)
        self.add_row([("Разделить", lambda: ctx.do({"t": "army_split", "id": sel.id, "n": max(1, sel.units // 2)})),
                      ("Объединить", lambda: ctx.do({"t": "army_merge", "ids": mates}))], h=30)
        self.add_button("Показать на карте", lambda: ctx.center_on(sel.region), h=28)
        if not idle and not sel.path:
            self.add_text("Армия сражается: приказ о движении невозможен до конца боя — используйте «Отступить».", 2)
