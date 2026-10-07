"""Панель страны: краткая сводка выбранного государства; для своей — экономика и управление."""
from __future__ import annotations

from config import COSTS, IDEOLOGY_NAMES
from core import economy, military
from core.diplomacy import get_relation
from . import SidePanel


class CountryPanel(SidePanel):
    """Название, население, экономика, армия, стабильность, отношения; для своей страны — действия."""
    NAME = "country"

    def build(self) -> None:
        ctx, st = self.ctx, self.ctx.state
        iso = ctx.selected or ctx.player_iso
        if st is None or iso is None or iso not in st.countries:
            self.add_text("<b>Страна не выбрана</b><br>Кликните по стране на карте.", 3)
            return
        c, own = st.countries[iso], iso == ctx.player_iso
        owner = st.owner.get(iso, iso)
        head = f"<b><font color='#e6e9ee' size=4>{c.name}</font></b><br>{IDEOLOGY_NAMES.get(c.ideology, c.ideology)}"
        if not c.alive:
            self.add_text(head + f"<br>Оккупирована: {st.countries[owner].name}", 4)
            return
        lines = [head,
                 f"Население: {c.population:,.1f} млн",
                 f"Экономика (ВВП): {c.gdp:,.0f} млрд $",
                 f"Армия: {c.army}  Флот: {c.navy}  Авиация: {c.airforce}  (сила {military.combat_power(c):.0f})",
                 f"Стабильность: {c.stability:.0f}%   Мораль: {c.morale * 100:.0f}%",
                 f"Территорий: {len(st.owned(iso))}   Столица: {c.capital or '—'}"]
        rel_line = None
        if ctx.player_iso and not own and st.player:
            rel = get_relation(st, iso, ctx.player_iso)
            tags = []
            if iso in st.player.allies:
                tags.append("<font color='#8fd18f'>союзник</font>")
            if iso in st.player.pacts:
                tags.append("пакт")
            if iso in st.player.trade:
                tags.append("торговля")
            if iso in st.player.at_war_with:
                tags.append("<font color='#e08080'>война</font>")
            rel_line = f"Отношения с вами: {rel:+d}" + (f"  ({', '.join(tags)})" if tags else "")
            lines.append(rel_line)
        if own:
            r = economy.report(c)
            lines += ["<b>Экономика</b>",
                      f"Казна: {c.treasury:,.0f}   Налог: {c.tax_rate * 100:.0f}%",
                      f"Доход: {r['income']:+.1f} (налоги {r['tax']:.1f}, торговля {r['trade']:.1f})",
                      f"Расходы на армию: -{r['upkeep']:.1f}   Чистый: {r['net']:+.1f}/день",
                      f"Производство: {r['production']:.1f}   Фабрики: {c.factories}  Укрепл.: {c.fort}",
                      f"Сталь {c.resources['steel']:.0f}  Нефть {c.resources['oil']:.0f}  "
                      f"Еда {c.resources['food']:.0f}  Электр. {c.resources['electronics']:.0f}",
                      f"Торговых соглашений: {len(c.trade)}   Люд. резервы: {c.manpower:,.0f} тыс."]
            mods = [m for m in c.modifiers if m[0] in ("income", "industry", "food")]
            if mods:
                lines.append(f"Активных эффектов событий: {len(mods)}")
        self.add_text("<br>".join(lines), len(lines) + 2)

        if ctx.player_iso is None:                       # режим выбора страны
            self.add_button(f"Играть за {c.name}", ctx.start_play, key="btn_play")
        elif own and ctx.can_act:
            f, fo = COSTS["factory"], COSTS["fort"]
            self.add_button(f"Построить фабрику ({f['treasury']:.0f} казны, {f['steel']:.0f} стали)",
                            lambda: ctx.do({"t": "build", "kind": "factory"}), key="btn_factory")
            self.add_button(f"Построить укрепление ({fo['treasury']:.0f} казны)",
                            lambda: ctx.do({"t": "build", "kind": "fort"}))
            self.add_row([("Налог +5%", lambda: ctx.do({"t": "tax", "delta": 0.05})),
                          ("Налог −5%", lambda: ctx.do({"t": "tax", "delta": -0.05}))])
        elif not own:
            self.add_button("Открыть дипломатию", lambda: ctx.open_panel("diplomacy"))
