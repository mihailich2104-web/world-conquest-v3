"""Панель армии: состав, атака/защита, мораль, снабжение, найм, войны и мир."""
from __future__ import annotations

from config import COSTS
from core import military
from . import SidePanel


class MilitaryPanel(SidePanel):
    """Состав сил, очередь обучения и текущие войны."""
    NAME = "military"

    def build(self) -> None:
        ctx, st = self.ctx, self.ctx.state
        c = st.player if st else None
        if c is None or not ctx.can_act:
            return
        lines = ["<b>Вооружённые силы</b>",
                 f"Дивизии: {c.army} (+{c.pending('army')})  Флот: {c.navy} (+{c.pending('navy')})  "
                 f"Авиация: {c.airforce} (+{c.pending('air')})",
                 f"Атака: {military.attack_power(c):.1f}   Защита: {military.defense_power(c):.1f}",
                 f"Мораль: {c.morale * 100:.0f}%   Снабжение: {military.supply_factor(c) * 100:.0f}%",
                 f"Людские резервы: {c.manpower:.0f} тыс.   Укрепления: {c.fort}"]
        for w in st.wars:
            if w.side_of(c.iso):
                en = st.countries[w.enemy_main(c.iso)]
                lines.append(f"<font color='#e08080'>Война с {en.name}: линия фронта {w.score_for(c.iso):+.0f}, "
                             f"день {w.days}</font>")
        self.add_text("<br>".join(lines), len(lines) + 2)
        a, n, f = COSTS["army"], COSTS["navy"], COSTS["air"]
        self.add_button(f"Нанять дивизию ({a['treasury']:.0f} казны, {a['steel']:.0f} стали)",
                        lambda: ctx.do({"t": "recruit", "kind": "army"}), key="btn_recruit_army")
        self.add_button(f"Построить корабль ({n['treasury']:.0f} казны)",
                        lambda: ctx.do({"t": "recruit", "kind": "navy"}))
        self.add_button(f"Нанять эскадрилью ({f['treasury']:.0f} казны)",
                        lambda: ctx.do({"t": "recruit", "kind": "air"}))
        for w in [w for w in st.wars if w.side_of(c.iso)][:3]:
            en = w.enemy_main(c.iso)
            self.add_button(f"Предложить мир: {st.countries[en].name}",
                            (lambda e=en: ctx.do({"t": "peace", "target": e})))
