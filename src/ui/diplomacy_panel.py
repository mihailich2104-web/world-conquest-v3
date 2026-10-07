"""Панель дипломатии: предложения ИИ и действия с выбранной страной."""
from __future__ import annotations

from core import diplomacy as dip
from core.war import find_war
from . import SidePanel


class DiplomacyPanel(SidePanel):
    """Улучшение отношений, торговля, пакты, союзы, война и мир; входящие предложения."""
    NAME = "diplomacy"

    def build(self) -> None:
        ctx, st = self.ctx, self.ctx.state
        me, tgt = ctx.player_iso, ctx.selected
        if st is None or me is None or not ctx.can_act:
            return
        mine = [o for o in st.offers if o["to"] == me]
        if mine:
            self.add_text("<b>Входящие предложения</b>", 1)
            for o in mine[:3]:
                name = st.countries[o["frm"]].name
                self.add_text(f"{name}: {dip.OFFER_TEXT[o['kind']]}", 1)
                oid = o["id"]
                self.add_row([("Принять", lambda i=oid: ctx.do({"t": "offer_accept", "id": i})),
                              ("Отклонить", lambda i=oid: ctx.do({"t": "offer_decline", "id": i}))], h=28)
            if len(mine) > 3:
                self.add_text(f"и ещё предложений: {len(mine) - 3}", 1)
        if tgt is None or tgt == me or tgt not in st.countries or not st.countries[tgt].alive:
            self.add_text("<b>Дипломатия</b><br>Выберите на карте другую страну.", 3)
            return
        a, b = st.countries[me], st.countries[tgt]
        tags = []
        if tgt in a.allies: tags.append("союзник")
        if tgt in a.pacts: tags.append("пакт о ненападении")
        if tgt in a.trade: tags.append("торговое соглашение")
        if tgt in a.at_war_with: tags.append("<font color='#e08080'>ВОЙНА</font>")
        left = dip.truce_left(st, me, tgt)
        if left: tags.append(f"перемирие {left} дн.")
        rel = dip.get_relation(st, tgt, me)
        lines = [f"<b>{b.name}</b>", f"Отношения: {rel:+d}", f"Статус: {', '.join(tags) or 'нет соглашений'}",
                 f"Армия {b.army} / ваша {a.army}",
                 f"Общая граница: {'да' if tgt in st.neighbors(me) else 'нет'}"]
        w = find_war(st, me, tgt)
        if w:
            lines.append(f"Счёт войны для вас: {w.score_for(me):+.0f}  (день {w.days})")
        self.add_text("<br>".join(lines), len(lines) + 1)
        at_war = tgt in a.at_war_with

        def cmd(t: str):
            return lambda: ctx.do({"t": t, "target": tgt})
        self.add_button("Улучшить отношения (10 казны)", cmd("improve"), enabled=not at_war)
        self.add_button("Торговое соглашение", cmd("trade"), enabled=not at_war)
        self.add_button("Пакт о ненападении", cmd("pact"), enabled=not at_war)
        if tgt in a.allies:
            self.add_button("Расторгнуть союз", cmd("break_alliance"))
        else:
            self.add_button("Предложить союз", cmd("alliance"), enabled=not at_war)
        if at_war:
            self.add_button("Предложить мир", cmd("peace"), key="btn_peace")
        else:
            self.add_button("Объявить войну", cmd("war"), key="btn_war")
