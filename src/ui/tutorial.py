"""Оверлей обучения: текст внизу экрана, кнопки и пульсирующая подсветка."""
from __future__ import annotations

import math
import time
from typing import Optional

import pygame
import pygame_gui
from pygame_gui.elements import UIButton

from config import LEFT_PANEL_W, SIDE_PANEL_W, TUTORIAL_H
from tutorial.tutor_engine import TutorEngine
from . import UIContext, get_font, wrap_text


class TutorialOverlay:
    """Рисует подсказку и подсветку; возвращает 'next'/'skip' по кнопкам."""

    def __init__(self, ctx: UIContext, engine: TutorEngine) -> None:
        self.ctx, self.engine = ctx, engine
        w, h = ctx.size
        self.box = pygame.Rect(LEFT_PANEL_W + 8, h - TUTORIAL_H - 8, w - LEFT_PANEL_W - SIDE_PANEL_W - 24, TUTORIAL_H)
        self.btn_next = UIButton(pygame.Rect(self.box.right - 250, self.box.bottom - 40, 110, 32), "Далее",
                                 ctx.manager)
        self.btn_skip = UIButton(pygame.Rect(self.box.right - 130, self.box.bottom - 40, 122, 32),
                                 "Пропустить", ctx.manager)
        self.btn_next.hide()

    def kill(self) -> None:
        """Удаляет кнопки."""
        self.btn_next.kill()
        self.btn_skip.kill()

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Нажатия «Далее» / «Пропустить»."""
        if event.type == pygame_gui.UI_BUTTON_PRESSED:
            if event.ui_element == self.btn_next:
                self.engine.next()
                return True
            if event.ui_element == self.btn_skip:
                self.engine.skip()
                return True
        return False

    def update(self) -> None:
        """Показывает «Далее» только на информационных шагах."""
        if self.engine.manual:
            self.btn_next.show()
        else:
            self.btn_next.hide()

    def draw_box(self, screen: pygame.Surface) -> None:
        """Фон и текст подсказки (рисуется до UI-менеджера)."""
        if self.engine.finished:
            return
        bg = pygame.Surface(self.box.size, pygame.SRCALPHA)
        bg.fill((10, 20, 40, 225))
        screen.blit(bg, self.box.topleft)
        pygame.draw.rect(screen, (255, 215, 0), self.box, 2, border_radius=6)
        font = get_font(16)
        y = self.box.y + 8
        for line in wrap_text(self.engine.text(), font, self.box.w - 24)[:4]:
            screen.blit(font.render(line, True, (240, 240, 240)), (self.box.x + 12, y))
            y += 22

    def draw_highlight(self, screen: pygame.Surface, map_rect: pygame.Rect) -> None:
        """Пульсирующая рамка вокруг цели (рисуется после UI-менеджера)."""
        if self.engine.finished:
            return
        kind, target = self.engine.highlight()
        k = 0.5 + 0.5 * math.sin(time.time() * 5)
        col = (255, int(180 + 60 * k), 0)
        if kind == "rect" and target is not None:
            pygame.draw.rect(screen, col, target.inflate(8 + 6 * k, 8 + 6 * k), 3, border_radius=6)
        elif kind == "map":
            pygame.draw.rect(screen, col, map_rect.inflate(-8, -8), int(2 + 3 * k))
