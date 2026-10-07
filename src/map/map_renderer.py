"""Рендер карты: кэш мировой поверхности + векторная отрисовка при приближении.

Реализм: проекция Миллера, градиент океана, градусная сетка, «мелководье» у берегов, береговая линия,
границы только между разными державами (захваченные страны сливаются с завоевателем), настоящие столицы,
армии на маршрутах от столицы атакующего к столице защитника.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

import pygame

from config import IDEOLOGY_COLORS, SETTINGS
from core.game_state import GameState
from .borders import Arc, arc_kind, border_point, build_arcs, territory_edge
from .camera import WORLD_H, WORLD_W, Camera
from .province import Province, lonlat_to_world, world_to_lonlat

log = logging.getLogger(__name__)
OCEAN_TOP = (30, 54, 88)
OCEAN_BOTTOM = (17, 32, 58)
OCEAN = (24, 43, 72)
SHALLOW = (42, 76, 116)
GRID = (36, 62, 98)
COAST = (22, 30, 42)
BORDER = (52, 58, 70)
VECTOR_ZOOM = 2.0        # выше этого масштаба рисуем полигоны напрямую (чётче)
ATT = (214, 84, 84)
DEF = (92, 140, 206)
STAR = [(math.cos(math.pi / 5 * i - math.pi / 2) * (1.0 if i % 2 == 0 else 0.42),
         math.sin(math.pi / 5 * i - math.pi / 2) * (1.0 if i % 2 == 0 else 0.42)) for i in range(10)]


def soften(c: tuple[int, int, int]) -> tuple[int, int, int]:
    """Приглушает яркий цвет страны к нейтральной «картографической» палитре (спокойнее для глаз)."""
    base = (150, 152, 142)
    return tuple(int(v * 0.68 + b * 0.32) for v, b in zip(c, base))  # type: ignore[return-value]


def lighten(c: tuple[int, int, int], k: float = 0.35) -> tuple[int, int, int]:
    """Осветляет цвет на долю k."""
    return tuple(int(v + (255 - v) * k) for v in c)  # type: ignore[return-value]


class MapRenderer:
    """Рисует провинции, границы, столицы, армии, подписи; определяет провинцию под курсором."""

    def __init__(self, provinces: list[Province], camera: Camera, font: pygame.font.Font) -> None:
        self.provs = provinces
        self.by_iso = {p.iso: p for p in provinces}
        self.order = sorted(provinces, key=lambda p: -p.area)       # крупные снизу, мелкие (анклавы) сверху
        self.cam = camera
        self.font = font
        self.world = pygame.Surface((int(WORLD_W), int(WORLD_H)))
        self.arcs: list[Arc] = build_arcs(provinces)
        self.arcs_by_iso: dict[str, list[Arc]] = {}
        for a in self.arcs:
            self.arcs_by_iso.setdefault(a.iso, []).append(a)
            if a.nb and not a.nb.startswith("#"):
                self.arcs_by_iso.setdefault(a.nb, []).append(a)
        self._label_cache: dict[tuple[str, int], pygame.Surface] = {}
        self._badge_cache: dict[tuple[str, tuple[int, int, int], bool], pygame.Surface] = {}
        self._label_info: dict[str, tuple[float, float, float]] = {}   # владелец → (x, y, ширина области в мировых px)
        self._tb_cache: dict[tuple[str, str], float] = {}
        self._prog: dict[tuple[int, str], float] = {}                  # анимированный прогресс армий по маршруту
        self.pulse = 0.0
        self.backdrop: Optional[pygame.Surface] = None

    # ---------- цвета ----------
    def color_of(self, iso: str, state: GameState) -> tuple[int, int, int]:
        """Цвет провинции: цвет владельца или цвет его идеологии."""
        owner = state.countries.get(state.owner.get(iso, iso))
        if owner is None:
            return (90, 90, 90)
        if SETTINGS.map_mode == "ideology":
            return soften(IDEOLOGY_COLORS.get(owner.ideology, (150, 150, 150)))
        return soften(owner.color)

    # ---------- кэш мировой поверхности ----------
    def _paint_ocean(self) -> None:
        w, h = self.world.get_size()
        for y in range(h):
            k = y / max(1, h - 1)
            col = tuple(int(OCEAN_TOP[i] + (OCEAN_BOTTOM[i] - OCEAN_TOP[i]) * k) for i in range(3))
            pygame.draw.line(self.world, col, (0, y), (w, y))
        for lon in range(-180, 181, 30):                      # градусная сетка
            x = lonlat_to_world(lon, 0)[0]
            pygame.draw.line(self.world, GRID, (x, 0), (x, h))
        for lat in range(-45, 81, 15):
            y = lonlat_to_world(0, lat)[1]
            pygame.draw.line(self.world, GRID, (0, y), (w, y))

    def _compute_labels(self, state: GameState) -> None:
        """Подписи по державам: одна на державу; ширина — по смежным провинциям (империя подписывается целиком)."""
        groups: dict[str, list[Province]] = {}
        for p in self.provs:
            groups.setdefault(state.owner.get(p.iso, p.iso), []).append(p)
        info: dict[str, tuple[float, float, float]] = {}
        for owner, ps in groups.items():
            big = max(ps, key=lambda p: p.area)
            near = {big.iso, *state.adjacency.get(big.iso, ())}
            grp = [p for p in ps if p.iso in near]
            x0 = min(p.main_wbbox[0] for p in grp)
            x1 = max(p.main_wbbox[2] for p in grp)
            tot = sum(p.area for p in grp) or 1.0
            cx = sum(p.world_centroid[0] * p.area for p in grp) / tot
            cy = sum(p.world_centroid[1] * p.area for p in grp) / tot
            info[owner] = (cx, cy, x1 - x0)
        self._label_info = info

    def rebuild(self, state: GameState) -> None:
        """Перерисовывает кэш мировой поверхности (при смене владельцев/режима)."""
        self._paint_ocean()
        for p in self.order:                                  # мелководье у берегов
            for ring in p.wpolys:
                if len(ring) >= 3:
                    pygame.draw.polygon(self.world, SHALLOW, ring, 5)
        for p in self.order:                                  # суша
            col = self.color_of(p.iso, state)
            for ring in p.wpolys:
                if len(ring) >= 3:
                    pygame.draw.polygon(self.world, col, ring)
        for a in self.arcs:                                   # границы и берега
            kind = arc_kind(a, state.owner)
            if kind and len(a.pts) >= 2:
                pygame.draw.lines(self.world, COAST if kind == "coast" else BORDER, False, a.pts, 1)
        self._compute_labels(state)
        self._tb_cache.clear()
        state.map_dirty = False

    # ---------- рисование ----------
    def draw(self, screen: pygame.Surface, state: GameState, hover: Optional[str],
             selected: Optional[str], extra: tuple[str, ...] = ()) -> None:
        """Рисует карту в окно. extra — дополнительно подсвеченные провинции (туториал)."""
        if state.map_dirty:
            self.rebuild(state)
        cam = self.cam
        screen.fill(OCEAN)
        vx, vy, vw, vh = cam.view_rect()
        if cam.zoom < VECTOR_ZOOM:
            src = pygame.Rect(int(max(vx, 0)), int(max(vy, 0)), 0, 0)
            src.w = int(min(vx + vw, WORLD_W)) - src.x + 1
            src.h = int(min(vy + vh, WORLD_H)) - src.y + 1
            src.clamp_ip(self.world.get_rect())
            if src.w > 0 and src.h > 0:
                sub = self.world.subsurface(src)
                size = (max(1, int(src.w * cam.zoom)), max(1, int(src.h * cam.zoom)))
                screen.blit(pygame.transform.scale(sub, size), cam.world_to_screen(src.x, src.y))
        else:
            self._draw_vector(screen, state)
        for iso in extra:
            self._outline(screen, iso, (255, 215, 0), 3)
        if hover and hover in self.by_iso:
            self._fill_hover(screen, state.owner.get(hover, hover), state)
        if selected and selected in state.countries:
            self._outline_territory(screen, state, state.owner.get(selected, selected))
        self._labels(screen, state)

    def _draw_vector(self, screen: pygame.Surface, state: GameState) -> None:
        z = self.cam.zoom
        for p in self.order:
            if self._visible(p):
                col = self.color_of(p.iso, state)
                for pts in self._screen_rings(p):
                    pygame.draw.polygon(screen, SHALLOW, pts, 5)
                for pts in self._screen_rings(p):
                    pygame.draw.polygon(screen, col, pts)
        width = 2 if z >= 4.0 else 1
        for a in self.arcs:
            if not self._arc_visible(a):
                continue
            kind = arc_kind(a, state.owner)
            if kind:
                pts = self._screen_pts(a.pts, z)
                if len(pts) >= 2:
                    pygame.draw.lines(screen, COAST if kind == "coast" else BORDER, False, pts, width)

    def _screen_pts(self, pts: list[tuple[float, float]], z: float) -> list[tuple[float, float]]:
        ox, oy = self.cam.x, self.cam.y
        step = 2 if len(pts) > 300 and z < 4.0 else 1
        return [((x - ox) * z, (y - oy) * z) for x, y in pts[::step]]

    def _arc_visible(self, a: Arc) -> bool:
        vx, vy, vw, vh = self.cam.view_rect()
        b = a.bbox
        return not (b[2] < vx or b[0] > vx + vw or b[3] < vy or b[1] > vy + vh)

    def _visible(self, p: Province) -> bool:
        """Пересекается ли рамка провинции с видимой областью."""
        vx, vy, vw, vh = self.cam.view_rect()
        b = p.wbbox
        return not (b[2] < vx or b[0] > vx + vw or b[3] < vy or b[1] > vy + vh)

    def _screen_rings(self, p: Province) -> list[list[tuple[float, float]]]:
        """Кольца провинции в экранных координатах (только крупные на экране)."""
        z, ox, oy = self.cam.zoom, self.cam.x, self.cam.y
        out = []
        for ring in p.wpolys:
            if len(ring) < 3:
                continue
            xs = [pt[0] for pt in ring]
            if (max(xs) - min(xs)) * z < 1.0:
                continue
            out.append([((x - ox) * z, (y - oy) * z) for x, y in ring])
        return out

    def _outline(self, screen: pygame.Surface, iso: str, color: tuple[int, int, int], w: int) -> None:
        """Контур одной провинции."""
        p = self.by_iso.get(iso)
        if p and self._visible(p):
            for pts in self._screen_rings(p):
                pygame.draw.polygon(screen, color, pts, w)

    def _outline_territory(self, screen: pygame.Surface, state: GameState, who: str) -> None:
        """Белый контур всей территории державы (с учётом захваченных земель)."""
        z = self.cam.zoom
        for a in self.arcs:
            if self._arc_visible(a) and territory_edge(a, state.owner, who):
                pts = self._screen_pts(a.pts, z)
                if len(pts) >= 2:
                    pygame.draw.lines(screen, (255, 255, 255), False, pts, 2)

    def _fill_hover(self, screen: pygame.Surface, owner: str, state: GameState) -> None:
        """Подсветка наведения: светлая заливка всей территории державы."""
        for p in self.provs:
            if state.owner.get(p.iso, p.iso) == owner and self._visible(p):
                col = lighten(self.color_of(p.iso, state))
                for pts in self._screen_rings(p):
                    pygame.draw.polygon(screen, col, pts)

    def _text(self, text: str, color: tuple[int, int, int], key: str) -> pygame.Surface:
        k = (text + key, 0)
        surf = self._label_cache.get(k)
        if surf is None:
            surf = self.font.render(text, True, color)
            self._label_cache[k] = surf
        return surf

    def _labels(self, screen: pygame.Surface, state: GameState) -> None:
        """Одна подпись на державу, если помещается в её видимую область."""
        z = self.cam.zoom
        if z < 1.0:
            return
        sw, sh = screen.get_size()
        for owner, (cx, cy, width) in self._label_info.items():
            c = state.countries.get(owner)
            if c is None or not c.alive:
                continue
            surf = self._text(c.name, (16, 18, 24), "n")
            if width * z < surf.get_width() + 8:
                continue
            sx, sy = self.cam.world_to_screen(cx, cy)
            if -60 < sx < sw + 60 and -20 < sy < sh + 20:
                screen.blit(surf, (sx - surf.get_width() / 2, sy - surf.get_height() / 2 - 8))

    def draw_marker(self, screen: pygame.Surface, iso: str, dt: float) -> None:
        """Пульсирующий маркер на столице (для туториала/игрока)."""
        p = self.by_iso.get(iso)
        if not p:
            return
        self.pulse += dt * 4
        sx, sy = self.cam.world_to_screen(*p.capital_world)
        r = 8 + 4 * math.sin(self.pulse)
        pygame.draw.circle(screen, (255, 215, 0), (int(sx), int(sy)), int(r), 3)
        pygame.draw.circle(screen, (255, 60, 60), (int(sx), int(sy)), 3)

    # ---------- столицы ----------
    def _draw_capitals(self, screen: pygame.Surface, state: GameState, mine: Optional[str]) -> None:
        """Столицы на настоящих координатах: звезда — действующая, ромб цвета владельца — оккупированная."""
        z = self.cam.zoom
        if z < 1.0:
            return
        sw, sh = screen.get_size()
        r0 = 3.0 + min(z, 3.0)
        for p in self.provs:
            sx, sy = self.cam.world_to_screen(*p.capital_world)
            if not (-10 < sx < sw + 10 and -10 < sy < sh + 10):
                continue
            c = state.countries.get(p.iso)
            if c is None:
                continue
            if c.alive:
                r = r0 * (1.5 if p.iso == mine else 1.0)
                pts = [(sx + dx * r, sy + dy * r) for dx, dy in STAR]
                pygame.draw.polygon(screen, (255, 214, 90) if p.iso == mine else (250, 244, 214), pts)
                pygame.draw.polygon(screen, (20, 22, 28), pts, 1)
            else:
                col = state.countries[state.owner.get(p.iso, p.iso)].color
                r = r0 * 0.8
                pts = [(sx, sy - r), (sx + r, sy), (sx, sy + r), (sx - r, sy)]
                pygame.draw.polygon(screen, col, pts)
                pygame.draw.polygon(screen, (20, 22, 28), pts, 1)
            if z >= 2.4 and c.capital:
                t = self._text(c.capital, (240, 242, 246), "c")
                screen.blit(t, (sx + r0 + 3, sy - t.get_height() / 2))

    # ---------- армии, войны, фон меню ----------
    def _badge(self, text: str, color: tuple[int, int, int], war: bool) -> pygame.Surface:
        """Плашка армии: тёмный фон, рамка заданного цвета."""
        key = (text, color, war)
        surf = self._badge_cache.get(key)
        if surf is None:
            t = self.font.render(text, True, (236, 238, 242))
            surf = pygame.Surface((t.get_width() + 10, t.get_height() + 4), pygame.SRCALPHA)
            surf.fill((18, 21, 26, 225))
            pygame.draw.rect(surf, color, surf.get_rect(), 2)
            surf.blit(t, (5, 2))
            self._badge_cache[key] = surf
        return surf

    def _front_t(self, state: GameState, a: str, d: str, p0: tuple[float, float], p1: tuple[float, float]) -> float:
        """Доля пути p0→p1, на которой лежит общая граница держав a и d (по умолчанию середина)."""
        key = (a, d)
        t = self._tb_cache.get(key)
        if t is None:
            t = 0.5
            bp = border_point(self.arcs, state.owner, a, d, p0, p1)
            if bp is not None:
                dx, dy = p1[0] - p0[0], p1[1] - p0[1]
                ln = dx * dx + dy * dy
                if ln > 1e-9:
                    t = max(0.1, min(0.9, ((bp[0] - p0[0]) * dx + (bp[1] - p0[1]) * dy) / ln))
            self._tb_cache[key] = t
        return t

    def _dashed(self, screen: pygame.Surface, color: tuple[int, int, int], a: tuple[float, float],
                b: tuple[float, float], dash: float = 9.0) -> None:
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length < 1:
            return
        n = int(length // (dash * 2)) + 1
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        for i in range(n):
            s, e = i * dash * 2, min(length, i * dash * 2 + dash)
            pygame.draw.line(screen, color, (a[0] + ux * s, a[1] + uy * s), (a[0] + ux * e, a[1] + uy * e), 1)

    def _army_marker(self, screen: pygame.Surface, x: float, y: float, ux: float, uy: float,
                     color: tuple[int, int, int], text: str, size: float = 1.0) -> None:
        """Армия в походе: стрелка по направлению движения + плашка с численностью."""
        s = 9 * size
        tip = (x + ux * s, y + uy * s)
        left = (x - ux * s * 0.6 - uy * s * 0.7, y - uy * s * 0.6 + ux * s * 0.7)
        right = (x - ux * s * 0.6 + uy * s * 0.7, y - uy * s * 0.6 - ux * s * 0.7)
        pygame.draw.polygon(screen, color, [tip, left, right])
        pygame.draw.polygon(screen, (16, 18, 24), [tip, left, right], 1)
        b = self._badge(text, color, True)
        screen.blit(b, (x - b.get_width() / 2, y + s * 0.9))

    def draw_overlays(self, screen: pygame.Surface, state: GameState, mine: Optional[str], dt: float = 0.0) -> None:
        """Столицы, войны (маршрут армии от столицы атакующего к столице защитника) и гарнизоны."""
        cam = self.cam
        self._draw_capitals(screen, state, mine)
        attackers_away: set[str] = set()
        self.pulse += dt * 3
        live = {w.id for w in state.wars}
        if len(self._prog) > 64:
            self._prog = {k: v for k, v in self._prog.items() if k[0] in live}
        for w in state.wars:
            dp = self.by_iso.get(w.main_defender)
            if dp is None:
                continue
            D = cam.world_to_screen(*dp.capital_world)
            Dw = dp.capital_world
            target = max(0.0, min(1.0, (w.score + 100.0) / 200.0))
            for k, iso in enumerate([i for i in w.attackers if i in self.by_iso][:4]):
                c = state.countries.get(iso)
                if c is None or not c.alive:
                    continue
                attackers_away.add(iso)
                ap = self.by_iso[iso]
                Aw = ap.capital_world
                A = cam.world_to_screen(*Aw)
                tb = self._front_t(state, iso, w.main_defender, Aw, Dw)
                key = (w.id, iso)
                cur = self._prog.get(key, 0.5)
                cur += (target - cur) * min(1.0, dt * 2.5)
                self._prog[key] = cur
                t = tb * (cur / 0.5) if cur < 0.5 else tb + (1.0 - tb) * ((cur - 0.5) / 0.5)
                F = (A[0] + (D[0] - A[0]) * t, A[1] + (D[1] - A[1]) * t)
                length = math.hypot(D[0] - A[0], D[1] - A[1]) or 1.0
                ux, uy = (D[0] - A[0]) / length, (D[1] - A[1]) / length
                main = iso == w.main_attacker
                self._dashed(screen, (150, 70, 70), A, D)
                pygame.draw.line(screen, ATT, A, F, 3 if main else 2)
                if self._on_screen(screen, F):
                    self._army_marker(screen, F[0], F[1], ux, uy, c.color if main else lighten(c.color, 0.2),
                                      str(c.army), 1.0 if main else 0.8)
            if self._on_screen(screen, D):
                if target > 0.8:                                   # столица под угрозой
                    r = 12 + 3 * math.sin(self.pulse)
                    pygame.draw.circle(screen, ATT, (int(D[0]), int(D[1])), int(r), 2)
        z = cam.zoom
        for c in state.countries.values():
            if not c.alive or c.army <= 0 or c.iso in attackers_away and not self._defending(state, c.iso):
                continue
            p = self.by_iso.get(c.iso)
            if p is None:
                continue
            at_war = bool(c.at_war_with)
            if z < 1.3 and not at_war and c.iso != mine:
                continue
            sx, sy = cam.world_to_screen(*p.capital_world)
            if not self._on_screen(screen, (sx, sy)):
                continue
            b = self._badge(str(c.army), DEF if self._defending(state, c.iso) else c.color, False)
            screen.blit(b, (sx - b.get_width() / 2, sy + 8))

    @staticmethod
    def _defending(state: GameState, iso: str) -> bool:
        return any(iso in w.defenders for w in state.wars)

    @staticmethod
    def _on_screen(screen: pygame.Surface, pt: tuple[float, float]) -> bool:
        return -40 < pt[0] < screen.get_width() + 40 and -40 < pt[1] < screen.get_height() + 40

    def make_backdrop(self, size: tuple[int, int]) -> None:
        """Готовит затемнённый фон главного меню из мировой поверхности (один раз)."""
        w, h = size
        bw = int(h * (self.world.get_width() / self.world.get_height()))
        surf = pygame.transform.smoothscale(self.world, (max(bw, w), h))
        shade = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
        shade.fill((8, 10, 14, 150))
        surf.blit(shade, (0, 0))
        self.backdrop = surf

    def draw_backdrop(self, screen: pygame.Surface, t: float) -> None:
        """Рисует фон меню с медленным горизонтальным дрейфом."""
        if self.backdrop is None:
            screen.fill((16, 20, 28))
            return
        bw, w = self.backdrop.get_width(), screen.get_width()
        span = max(0, bw - w)
        off = int((math.sin(t * 0.05) * 0.5 + 0.5) * span)
        screen.blit(self.backdrop, (-off, 0))

    # ---------- выбор ----------
    def province_at(self, pos: tuple[int, int]) -> Optional[str]:
        """ISO провинции под экранной точкой (None — океан)."""
        wx, wy = self.cam.screen_to_world(*pos)
        lon, lat = world_to_lonlat(wx, wy)
        best: Optional[Province] = None
        for p in self.provs:
            if p.contains(lon, lat) and (best is None or p.area < best.area):
                best = p
        return best.iso if best else None
