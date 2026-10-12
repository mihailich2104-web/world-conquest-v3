"""Камера: панорамирование, масштаб, преобразование координат.

Масштаб квантуется до 1/64: тайловый кэш карты ключуется точным значением масштаба, и тайлы, нарисованные
для этого масштаба, годятся без пересчёта. Камера помнит моменты последнего движения/зума — рендерер по ним
понимает, что пользователь двигает карту, и откладывает дорогую дорисовку тайлов.
"""
from __future__ import annotations

import time

from config import ZOOM_MAX, ZOOM_MIN, ZOOM_STEP
from .province import WORLD_H, WORLD_W

ZOOM_GRID = 64.0


def snap_zoom(z: float) -> float:
    """Ближайший масштаб на сетке 1/64 в допустимых пределах."""
    return max(ZOOM_MIN, min(ZOOM_MAX, round(z * ZOOM_GRID) / ZOOM_GRID))


class Camera:
    """Камера над мировой поверхностью; (x, y) — мировая точка в левом верхнем углу вида."""

    def __init__(self, view_w: int, view_h: int) -> None:
        self.vw, self.vh = view_w, view_h
        self.zoom = snap_zoom(max(ZOOM_MIN, min(view_w / WORLD_W, view_h / WORLD_H) * 1.0))
        self.zoom = max(self.zoom, 0.5)
        self.x = 0.0
        self.y = 0.0
        self.t_move = 0.0          # time.monotonic() последнего сдвига
        self.t_zoom = 0.0          # time.monotonic() последнего изменения масштаба
        self._clamp()

    def resize(self, w: int, h: int) -> None:
        """Обновляет размер окна."""
        self.vw, self.vh = w, h
        self._clamp()

    def world_to_screen(self, wx: float, wy: float) -> tuple[float, float]:
        """Мировые пиксели → экран."""
        return (wx - self.x) * self.zoom, (wy - self.y) * self.zoom

    def screen_to_world(self, sx: float, sy: float) -> tuple[float, float]:
        """Экран → мировые пиксели."""
        return sx / self.zoom + self.x, sy / self.zoom + self.y

    def pan(self, dx: float, dy: float) -> None:
        """Сдвиг на dx, dy экранных пикселей (перетаскивание ЛКМ)."""
        self.x -= dx / self.zoom
        self.y -= dy / self.zoom
        self._clamp()
        self.t_move = time.monotonic()

    def zoom_at(self, wheel: int, sx: float, sy: float) -> None:
        """Масштаб колесом вокруг курсора (wheel > 0 — приближение)."""
        wx, wy = self.screen_to_world(sx, sy)
        new = snap_zoom(self.zoom * ZOOM_STEP ** wheel)
        if new == self.zoom:                                   # на пределе или шаг меньше сетки — двигаем на сетку
            new = snap_zoom(self.zoom + (1 if wheel > 0 else -1) / ZOOM_GRID)
        self.zoom = new
        self.x, self.y = wx - sx / self.zoom, wy - sy / self.zoom
        self._clamp()
        self.t_zoom = self.t_move = time.monotonic()

    def focus(self, wx: float, wy: float, zoom: float | None = None) -> None:
        """Центрирует камеру на мировой точке."""
        if zoom is not None:
            self.zoom = snap_zoom(zoom)
            self.t_zoom = time.monotonic()
        self.x = wx - self.vw / 2 / self.zoom
        self.y = wy - self.vh / 2 / self.zoom
        self._clamp()
        self.t_move = time.monotonic()

    def view_rect(self) -> tuple[float, float, float, float]:
        """Видимая область в мировых пикселях: (x, y, w, h)."""
        return self.x, self.y, self.vw / self.zoom, self.vh / self.zoom

    def _clamp(self) -> None:
        """Не даёт уехать за пределы мира (если мир меньше окна — центрирует)."""
        vw, vh = self.vw / self.zoom, self.vh / self.zoom
        self.x = (WORLD_W - vw) / 2 if vw >= WORLD_W else max(0.0, min(WORLD_W - vw, self.x))
        self.y = (WORLD_H - vh) / 2 if vh >= WORLD_H else max(0.0, min(WORLD_H - vh, self.y))
