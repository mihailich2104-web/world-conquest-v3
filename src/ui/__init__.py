"""Общие утилиты GUI: шрифты, контекст UI, базовый класс боковой панели."""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

import pygame
import pygame_gui
from pygame_gui.elements import UIButton, UIPanel, UITextBox

from config import FONT_PATH, HUD_HEIGHT, SIDE_PANEL_W

log = logging.getLogger(__name__)
_FONTS: dict[int, pygame.font.Font] = {}


def get_font(size: int) -> pygame.font.Font:
    """Шрифт с кириллицей: DejaVuSans из assets, иначе системный."""
    if size not in _FONTS:
        try:
            _FONTS[size] = pygame.font.Font(str(FONT_PATH), size)
        except (OSError, FileNotFoundError):
            log.warning("Шрифт %s не найден — системный", FONT_PATH)
            _FONTS[size] = pygame.font.SysFont("dejavusans,arial,liberationsans", size)
    return _FONTS[size]


def wrap_text(text: str, font: pygame.font.Font, width: int) -> list[str]:
    """Перенос текста по словам под заданную ширину в пикселях."""
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            test = f"{cur} {word}".strip()
            if font.size(test)[0] <= width:
                cur = test
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


class UIContext:
    """Разделяемое состояние UI: менеджер, реестр элементов, текущая игра, колбэки."""

    def __init__(self, manager: pygame_gui.UIManager, size: tuple[int, int]) -> None:
        self.manager = manager
        self.size = size
        self.reg: dict[str, Any] = {}          # имя → элемент (для подсветки в туториале)
        self.state: Any = None                 # GameState
        self.selected: Optional[str] = None    # выбранная на карте страна
        self.open_panel: Callable[[str], None] = lambda name: None
        self.refresh: Callable[[], None] = lambda: None
        self.start_play: Callable[[], None] = lambda: None
        self.do: Callable[[dict], None] = lambda cmd: None     # игровая команда (одиночная/хост/клиент)
        self.session: Any = None                               # core.session.Session

    @property
    def player_iso(self) -> Optional[str]:
        """ISO страны игрока."""
        return self.state.player_iso if self.state else None

    def say(self, ok: bool, msg: str) -> None:
        """Записывает результат действия игрока в журнал."""
        if self.state is not None:
            self.state.log_event(("" if ok else "✖ ") + msg, self.player_iso)

    @property
    def can_act(self) -> bool:
        """Есть ли у локального игрока живая страна."""
        st = self.state
        return bool(st and self.player_iso and st.countries[self.player_iso].alive)


class SidePanel:
    """Базовая правая панель; содержимое пересоздаётся методом build()."""
    NAME = ""
    TITLE = ""

    def __init__(self, ctx: UIContext) -> None:
        self.ctx = ctx
        self.panel: Optional[UIPanel] = None
        self.widgets: list[Any] = []
        self.actions: dict[Any, Callable[[], None]] = {}
        self.keys: list[str] = []
        self.y = 6

    @property
    def rect(self) -> pygame.Rect:
        """Экранный прямоугольник панели."""
        w, h = self.ctx.size
        return pygame.Rect(w - SIDE_PANEL_W, HUD_HEIGHT, SIDE_PANEL_W, h - HUD_HEIGHT)

    @property
    def is_open(self) -> bool:
        """Открыта ли панель."""
        return self.panel is not None

    def open(self) -> None:
        """Открывает панель и строит содержимое."""
        if self.panel is None:
            self.panel = UIPanel(relative_rect=self.rect, starting_height=2, manager=self.ctx.manager)
        self.refresh()

    def close(self) -> None:
        """Закрывает панель."""
        self._reset()
        if self.panel is not None:
            self.panel.kill()
            self.panel = None

    def _reset(self) -> None:
        for wdg in self.widgets:
            wdg.kill()
        self.widgets.clear()
        self.actions.clear()
        for k in self.keys:
            self.ctx.reg.pop(k, None)
        self.keys.clear()
        self.y = 6

    def refresh(self) -> None:
        """Пересоздаёт содержимое (вызывается после любого изменения данных)."""
        if self.panel is None:
            return
        self._reset()
        try:
            self.build()
        except Exception:  # панель не должна ронять игру
            log.exception("Ошибка построения панели %s", self.NAME)

    def build(self) -> None:
        """Переопределяется в наследниках."""
        raise NotImplementedError

    # ---------- конструктор виджетов ----------
    def add_text(self, html: str, lines: int) -> None:
        """Текстовый блок (HTML pygame_gui) высотой в lines строк."""
        assert self.panel is not None
        h = lines * 19 + 14
        box = UITextBox(html_text=html, relative_rect=pygame.Rect(4, self.y, SIDE_PANEL_W - 20, h),
                        manager=self.ctx.manager, container=self.panel)
        self.widgets.append(box)
        self.y += h + 4

    def add_button(self, text: str, cb: Callable[[], None], key: Optional[str] = None,
                   enabled: bool = True, h: int = 32) -> None:
        """Кнопка действия; key — имя для подсветки в туториале."""
        assert self.panel is not None
        b = UIButton(relative_rect=pygame.Rect(4, self.y, SIDE_PANEL_W - 20, h), text=text,
                     manager=self.ctx.manager, container=self.panel)
        if not enabled:
            b.disable()
        self.widgets.append(b)
        self.actions[b] = cb
        if key:
            self.ctx.reg[key] = b
            self.keys.append(key)
        self.y += h + 4

    def add_row(self, items: list[tuple[str, Callable[[], None]]], h: int = 32) -> None:
        """Несколько кнопок в одну строку (равной ширины)."""
        assert self.panel is not None
        total = SIDE_PANEL_W - 20
        gap = 4
        w = (total - gap * (len(items) - 1)) // len(items)
        x = 4
        for text, cb in items:
            b = UIButton(relative_rect=pygame.Rect(x, self.y, w, h), text=text,
                         manager=self.ctx.manager, container=self.panel)
            self.widgets.append(b)
            self.actions[b] = cb
            x += w + gap
        self.y += h + 4

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Обрабатывает нажатия кнопок панели."""
        if event.type == pygame_gui.UI_BUTTON_PRESSED and event.ui_element in self.actions:
            try:
                self.actions[event.ui_element]()
            except Exception:
                log.exception("Ошибка действия в панели %s", self.NAME)
            self.ctx.refresh()
            return True
        return False
