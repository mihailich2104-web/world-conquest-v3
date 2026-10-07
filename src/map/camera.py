"""Камера: панорамирование, масштаб, преобразование координат."""
from __future__ import annotations

from config import ZOOM_MAX, ZOOM_MIN, ZOOM_STEP
from .province import WORLD_H, WORLD_W


class Camera:
    """Камера над мировой поверхностью; (x, y) — мировая точка в левом верхнем углу вида."""

    def __init__(self, view_w: int, view_h: int) -> None:
        self.vw, self.vh = view_w, view_h
        self.zoom = max(ZOOM_MIN, min(view_w / WORLD_W, view_h / WORLD_H) * 1.0)
        self.zoom = max(self.zoom, 0.5)
        self.x = 0.0
        self.y = 0.0
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

    def zoom_at(self, wheel: int, sx: float, sy: float) -> None:
        """Масштаб колесом вокруг курсора (wheel > 0 — приближение)."""
        wx, wy = self.screen_to_world(sx, sy)
        self.zoom = max(ZOOM_MIN, min(ZOOM_MAX, self.zoom * ZOOM_STEP ** wheel))
        self.x, self.y = wx - sx / self.zoom, wy - sy / self.zoom
        self._clamp()

    def focus(self, wx: float, wy: float, zoom: float | None = None) -> None:
        """Центрирует камеру на мировой точке."""
        if zoom is not None:
            self.zoom = max(ZOOM_MIN, min(ZOOM_MAX, zoom))
        self.x = wx - self.vw / 2 / self.zoom
        self.y = wy - self.vh / 2 / self.zoom
        self._clamp()

    def view_rect(self) -> tuple[float, float, float, float]:
        """Видимая область в мировых пикселях: (x, y, w, h)."""
        return self.x, self.y, self.vw / self.zoom, self.vh / self.zoom

    def _clamp(self) -> None:
        """Не даёт уехать за пределы мира (если мир меньше окна — центрирует)."""
        vw, vh = self.vw / self.zoom, self.vh / self.zoom
        self.x = (WORLD_W - vw) / 2 if vw >= WORLD_W else max(0.0, min(WORLD_W - vw, self.x))
        self.y = (WORLD_H - vh) / 2 if vh >= WORLD_H else max(0.0, min(WORLD_H - vh, self.y))
