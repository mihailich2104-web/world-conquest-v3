"""Движок обучения: переключает шаги, проверяет условия, отдаёт цель подсветки."""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

import pygame

from core.game_state import GameState
from .steps import STEPS, Step

log = logging.getLogger(__name__)


class TutorContext:
    """Доступ шагов к игре. Заполняется приложением (App)."""

    def __init__(self, reg: dict[str, Any], renderer: Any, camera: Any) -> None:
        self.reg, self.renderer, self.camera = reg, renderer, camera
        self.state: GameState = None  # type: ignore[assignment]
        self.memo: dict[str, Any] = {}
        self.open_panel: Callable[[str], None] = lambda n: None
        self.select: Callable[[str], None] = lambda i: None

    @property
    def player(self):
        """Страна игрока."""
        return self.state.player

    def focus_country(self, iso: str, zoom: float) -> None:
        """Наводит камеру на страну."""
        p = self.renderer.by_iso.get(iso)
        if p:
            self.camera.focus(*p.world_centroid, zoom)


class TutorEngine:
    """Пошаговый сценарий обучения."""

    def __init__(self, ctx: TutorContext) -> None:
        self.ctx = ctx
        self.index = 0
        self.finished = False
        self._entered = -1

    @property
    def step(self) -> Optional[Step]:
        """Текущий шаг (None, если обучение окончено)."""
        return None if self.finished else STEPS[self.index]

    @property
    def manual(self) -> bool:
        """Нужна ли кнопка «Далее»."""
        s = self.step
        return bool(s and s.done is None)

    def text(self) -> str:
        """Текст текущего шага."""
        s = self.step
        try:
            return s.text(self.ctx) if s else ""
        except Exception:
            log.exception("Ошибка текста шага")
            return "…"

    def update(self) -> None:
        """Вызывается каждый кадр: входит в шаг и проверяет условие завершения."""
        s = self.step
        if s is None or (self.index > 0 and self.ctx.state is None):
            return
        try:
            if self._entered != self.index:
                self._entered = self.index
                if s.on_enter:
                    s.on_enter(self.ctx)
            if s.done and s.done(self.ctx):
                self.next()
                self.update()          # сразу входим в следующий шаг (on_enter)
        except Exception:
            log.exception("Ошибка шага обучения %s", s.id)

    def next(self) -> None:
        """Переход к следующему шагу."""
        if self.index + 1 >= len(STEPS):
            self.finished = True
        else:
            self.index += 1

    def skip(self) -> None:
        """Пропуск обучения."""
        self.finished = True

    def highlight(self) -> tuple[str, Any]:
        """Цель подсветки: ('rect', Rect) | ('map', None) | ('capital', iso) | ('none', None)."""
        s = self.step
        if s is None or s.highlight is None:
            return "none", None
        key = s.highlight
        if key == "map":
            return "map", None
        if key == "capital" and self.ctx.state and self.ctx.state.player_iso:
            return "capital", self.ctx.state.player_iso
        for k in (key, s.fallback):
            el = self.ctx.reg.get(k) if k else None
            if el is not None:
                return "rect", pygame.Rect(el.rect)
        return "none", None

    def extra_countries(self) -> tuple[str, ...]:
        """Страны для подсветки на карте (цель войны)."""
        s = self.step
        if s and s.id == "war" and "target" in self.ctx.memo:
            return (self.ctx.memo["target"],)
        return ()
