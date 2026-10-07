"""HUD: верхняя строка (дата, пауза, скорости, деньги и показатели) и левая панель действий."""
from __future__ import annotations

import logging
from typing import Any, Optional

import pygame
import pygame_gui
from pygame_gui.elements import UIButton, UILabel, UIPanel

from config import HUD_HEIGHT, LEFT_PANEL_W, SIDE_PANEL_W, SPEEDS
from core import economy
from core.military import combat_power
from . import UIContext, get_font

log = logging.getLogger(__name__)
MONTHS = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
HUD_KEYS = ("btn_country", "btn_diplomacy", "btn_research", "btn_military", "btn_map", "btn_save", "btn_menu",
            "btn_next_turn", "btn_pause")


class HUD:
    """Верх: дата / пауза / скорости / деньги / показатели. Слева: основные действия.
    handle_event возвращает имя действия или None."""

    def __init__(self, ctx: UIContext) -> None:
        self.ctx = ctx
        self._cache: dict = {}
        w, h = ctx.size
        m = ctx.manager
        self.top = UIPanel(pygame.Rect(0, 0, w, HUD_HEIGHT), starting_height=3, manager=m)
        self.left = UIPanel(pygame.Rect(0, HUD_HEIGHT, LEFT_PANEL_W, h - HUD_HEIGHT), starting_height=3, manager=m)
        self.buttons: dict[UIButton, str] = {}
        # --- верхняя строка ---
        self.lbl_date = UILabel(pygame.Rect(6, 8, 136, 32), "", m, self.top)
        self.btn_pause = UIButton(pygame.Rect(146, 6, 62, 36), "Пауза", m, self.top)
        self.buttons[self.btn_pause] = "pause"
        ctx.reg["btn_pause"] = self.btn_pause
        self.speed_btns: list[UIButton] = []
        x = 212
        for i, sp in enumerate(SPEEDS):
            b = UIButton(pygame.Rect(x, 6, 44, 36), f"{sp}X", m, self.top)
            self.buttons[b] = f"speed:{i}"
            self.speed_btns.append(b)
            x += 46
        x += 8
        self.lbl_money = UILabel(pygame.Rect(x, 8, 220, 32), "", m, self.top)
        self.lbl_pop = UILabel(pygame.Rect(x + 222, 8, 150, 32), "", m, self.top)
        self.lbl_army = UILabel(pygame.Rect(x + 374, 8, 150, 32), "", m, self.top)
        self.lbl_stab = UILabel(pygame.Rect(x + 526, 8, 190, 32), "", m, self.top)
        self.lbl_war = UILabel(pygame.Rect(x + 718, 8, max(60, w - x - 722), 32), "", m, self.top)
        # --- левая панель ---
        spec = [("Страна", "panel:country", "btn_country"), ("Дипломатия", "panel:diplomacy", "btn_diplomacy"),
                ("Исследования", "panel:research", "btn_research"), ("Армия", "panel:military", "btn_military"),
                None,
                ("Режим карты", "map_mode", "btn_map"), ("Сохранить", "save", "btn_save"),
                ("+1 день", "next_turn", "btn_next_turn"), None, ("Меню", "menu", "btn_menu")]
        y = 8
        bw = LEFT_PANEL_W - 14
        for item in spec:
            if item is None:
                y += 14
                continue
            text, action, key = item
            b = UIButton(pygame.Rect(4, y, bw, 38), text, m, self.left)
            self.buttons[b] = action
            ctx.reg[key] = b
            y += 44
        self.btn_next = ctx.reg["btn_next_turn"]
        self.btn_save = ctx.reg["btn_save"]

    def _set(self, el: Any, text: str) -> None:
        """Меняет текст элемента только при изменении (перерисовка дорогая)."""
        if self._cache.get(el) != text:
            self._cache[el] = text
            el.set_text(text)

    @property
    def rect(self) -> pygame.Rect:
        """Верхняя строка (для блокировки кликов по карте)."""
        return pygame.Rect(0, 0, self.ctx.size[0], HUD_HEIGHT)

    @property
    def left_rect(self) -> pygame.Rect:
        """Левая панель."""
        return pygame.Rect(0, HUD_HEIGHT, LEFT_PANEL_W, self.ctx.size[1] - HUD_HEIGHT)

    def blocks(self, pos: tuple[int, int]) -> bool:
        """Попадает ли точка в HUD."""
        return self.rect.collidepoint(pos) or self.left_rect.collidepoint(pos)

    def update(self, ses: Any) -> None:
        """Обновляет подписи и состояние кнопок по игре и часам."""
        st = self.ctx.state
        c = st.player if st else None
        clock = ses.clock if ses else None
        if st is None or c is None:
            for el in (self.lbl_date, self.lbl_money, self.lbl_pop, self.lbl_army, self.lbl_stab, self.lbl_war):
                self._set(el, "")
            return
        d = st.date
        self._set(self.lbl_date, f"{d.day} {MONTHS[d.month - 1]} {d.year}")
        net = economy.net_income(c)
        self._set(self.lbl_money, f"Казна {c.treasury:,.0f}  ({net:+.1f}/д)".replace(",", " "))
        self._set(self.lbl_pop, f"Нас. {c.population:,.1f} млн")
        self._set(self.lbl_army, f"Армия {c.army}  (сила {combat_power(c):.0f})")
        self._set(self.lbl_stab, f"Стаб. {c.stability:.0f}%  Мораль {c.morale * 100:.0f}%")
        extra = f"Войн: {len(c.at_war_with)}"
        if ses and ses.multiplayer:
            extra += f"   LAN: {max(1, len(st.humans))}"
        self._set(self.lbl_war, extra)
        if clock:
            self._set(self.btn_pause, "Пуск" if clock.paused else "Пауза")
            for i, b in enumerate(self.speed_btns):
                active = (i == clock.speed_idx) and not clock.paused
                if active != self._cache.get(("sel", b)):
                    self._cache[("sel", b)] = active
                    (b.select if active else b.unselect)()
            client = ses.is_client
            for b in [self.btn_pause, *self.speed_btns, self.btn_next, self.btn_save]:
                want = not client and not (b is self.btn_next and ses.multiplayer)
                if b is self.btn_next:
                    want = want and clock.paused
                if b is self.btn_save:
                    want = ses.mode != "client"
                if self._cache.get(("en", b)) != want:
                    self._cache[("en", b)] = want
                    (b.enable if want else b.disable)()

    def handle_event(self, event: pygame.event.Event) -> Optional[str]:
        """Возвращает действие нажатой кнопки HUD."""
        if event.type == pygame_gui.UI_BUTTON_PRESSED:
            return self.buttons.get(event.ui_element)
        return None

    def kill(self) -> None:
        """Удаляет HUD."""
        for k in HUD_KEYS:
            self.ctx.reg.pop(k, None)
        self.top.kill()
        self.left.kill()

    def draw_log(self, screen: pygame.Surface, bottom: int, side_open: bool) -> None:
        """Последние события в левом нижнем углу карты (чужие игроки в LAN не показываются)."""
        st = self.ctx.state
        if not st or not st.log:
            return
        me = st.player_iso
        humans = st.human_list()
        recent = [e for e in st.log[-12:] if e.get("iso") is None or e.get("iso") == me or e.get("iso") not in humans][-5:]
        if not recent:
            return
        font = get_font(14)
        width = screen.get_width() - LEFT_PANEL_W - (SIDE_PANEL_W if side_open else 0) - 24
        y = bottom - 8 - len(recent) * 20
        bg = pygame.Surface((min(640, width), len(recent) * 20 + 6), pygame.SRCALPHA)
        bg.fill((12, 14, 18, 170))
        x0 = LEFT_PANEL_W + 8
        screen.blit(bg, (x0, y - 3))
        for e in recent:
            col = (240, 214, 140) if e.get("imp") else (214, 218, 224)
            screen.blit(font.render(f"{e['date'][5:]}  {e['text']}", True, col), (x0 + 6, y))
            y += 20
