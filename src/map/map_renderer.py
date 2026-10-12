"""Рендер карты: тайловый кэш (плавные перемещение и масштаб), регионы, оккупация, армии и маршруты.

Почему карта больше не лагает при приближении. Раньше при масштабе ≥ 2 каждый кадр перерисовывал полигоны
стран целиком, а pygame заливает полигон за O(высота в пикселях × число вершин) независимо от видимости.
Теперь:
  * мир ≥ VECTOR_ZOOM рисуется 256-пиксельными тайлами; тайл строится из геометрии, отсечённой по его
    прямоугольнику (map.geom: пиксель-в-пиксель тот же результат, но стоимость ограничена видимой частью);
  * тайлы строятся лениво с бюджетом ~7 мс на кадр, пока их нет — показывается масштабированный кэш мира
    1×; во время прокрутки колеса новые тайлы откладываются, поэтому ничего не подвисает;
  * смена владельцев/оккупации перерисовывает только затронутые регионы и тайлы (а не весь мир);
  * наведение мыши — O(1) через растровый индекс регионов, подсветка и контуры отсекаются по видимому окну.
Игровая позиция армий хранится в состоянии игры; здесь только сглаженная визуализация.
"""
from __future__ import annotations

import logging
import math
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Callable, Optional

import pygame

from config import IDEOLOGY_COLORS, SETTINGS
from .borders import K_BORDER, K_COAST, K_INNER, K_NONE, SEAM, Arc, arc_class, build_arcs, territory_edge
from .camera import WORLD_H, WORLD_W, Camera
from .geom import Path, polyline_runs, ring_polygon, to_pixels
from .province import Province, lonlat_to_world, world_to_lonlat

if TYPE_CHECKING:
    from core.game_state import GameState

log = logging.getLogger(__name__)
OCEAN_TOP = (30, 54, 88)
OCEAN_BOTTOM = (17, 32, 58)
OCEAN = (24, 43, 72)
SHALLOW = (42, 76, 116)
GRID = (36, 62, 98)
COAST = (22, 30, 42)
BORDER = (52, 58, 70)
VECTOR_ZOOM = 2.0            # выше этого масштаба карта рисуется тайлами
TILE = 256
ZQ = 64.0                    # сетка масштаба камеры (см. map.camera.ZOOM_GRID)
MAX_TILES = 240
ZOOM_SETTLE = 0.09           # сек. после последнего шага зума, прежде чем строить новые тайлы
ATT = (200, 82, 78)
DEF = (92, 140, 206)
GOLD = (255, 214, 90)
INK = (16, 18, 24)
STAR = [(math.cos(math.pi / 5 * i - math.pi / 2) * (1.0 if i % 2 == 0 else 0.42),
         math.sin(math.pi / 5 * i - math.pi / 2) * (1.0 if i % 2 == 0 else 0.42)) for i in range(10)]


def soften(c: tuple[int, int, int]) -> tuple[int, int, int]:
    """Приглушает яркий цвет страны к нейтральной «картографической» палитре (спокойнее для глаз)."""
    base = (150, 152, 142)
    return tuple(int(v * 0.68 + b * 0.32) for v, b in zip(c, base))  # type: ignore[return-value]


def lighten(c: tuple[int, int, int], k: float = 0.35) -> tuple[int, int, int]:
    """Осветляет цвет на долю k."""
    return tuple(int(v + (255 - v) * k) for v in c)  # type: ignore[return-value]


def mix(a: tuple[int, int, int], b: tuple[int, int, int], k: float) -> tuple[int, int, int]:
    """Смесь цветов: k — доля b."""
    return tuple(int(x + (y - x) * k) for x, y in zip(a, b))  # type: ignore[return-value]


def ink_for(c: tuple[int, int, int]) -> tuple[int, int, int]:
    """Чёрный или белый текст в зависимости от яркости фона."""
    return INK if (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) > 140 else (240, 242, 246)


class _Grid:
    """Равномерная сетка по мировым пикселям: быстро находит объекты, чьи рамки задевают прямоугольник."""

    def __init__(self, cell: int = 128) -> None:
        self.cell = cell
        self.cells: dict[tuple[int, int], list[int]] = {}

    def add(self, idx: int, b: tuple[float, float, float, float]) -> None:
        c = self.cell
        for gx in range(int(b[0] // c), int(b[2] // c) + 1):
            for gy in range(int(b[1] // c), int(b[3] // c) + 1):
                self.cells.setdefault((gx, gy), []).append(idx)

    def query(self, r: tuple[float, float, float, float]) -> set[int]:
        out: set[int] = set()
        c = self.cell
        for gx in range(int(r[0] // c), int(r[2] // c) + 1):
            for gy in range(int(r[1] // c), int(r[3] // c) + 1):
                lst = self.cells.get((gx, gy))
                if lst:
                    out.update(lst)
        return out


class MapRenderer:
    """Рисует регионы, границы, столицы, армии, подписи; определяет регион под курсором."""

    def __init__(self, provinces: list[Province], camera: Camera, font: pygame.font.Font) -> None:
        self.provs = provinces
        self.by_iso = {p.iso: p for p in provinces}
        self.by_country: dict[str, list[str]] = {}
        for p in provinces:
            self.by_country.setdefault(p.country or p.iso, []).append(p.iso)
        self.order = sorted(provinces, key=lambda p: -p.area)       # крупные снизу, мелкие (анклавы) сверху
        self._oidx = {p.iso: i for i, p in enumerate(self.order)}
        self.cam = camera
        self.font = font
        self.world = pygame.Surface((int(WORLD_W), int(WORLD_H)))
        self.arcs: list[Arc] = build_arcs(provinces)
        self.paths: list[Path] = [Path(a.pts) for a in self.arcs]
        self.arcs_by_iso: dict[str, list[int]] = {}
        for i, a in enumerate(self.arcs):
            self.arcs_by_iso.setdefault(a.iso, []).append(i)
            if a.nb and not a.nb.startswith("#"):
                self.arcs_by_iso.setdefault(a.nb, []).append(i)
        self.rings: dict[str, list[Path]] = {p.iso: [Path(r) for r in p.wpolys if len(r) >= 3] for p in provinces}
        self._grid_p, self._grid_a = _Grid(), _Grid()
        for i, p in enumerate(self.order):
            self._grid_p.add(i, p.wbbox)
        for i, a in enumerate(self.arcs):
            self._grid_a.add(i, a.bbox)
        # стили (пересчитываются инкрементально в _sync)
        n = len(self.arcs)
        self._kind: list[int] = [K_NONE] * n
        self._rim: list[Optional[tuple[int, int, int]]] = [None] * n
        self._inner: list[tuple[int, int, int]] = [BORDER] * n
        self._fill: dict[str, tuple[int, int, int]] = {}
        self._occ: dict[str, Optional[tuple[int, int, int]]] = {}
        self._sig: dict[str, tuple] = {}
        self._built = False
        self._seen_rev = -1
        # тайлы
        self._tiles: OrderedDict[tuple[int, int, int], pygame.Surface] = OrderedDict()
        self._tile_provs: dict[tuple[int, int, int], frozenset[str]] = {}
        self._tile_ver = 0
        self._tiles_ok = True
        self.tile_ms = 0.0                                   # последнее время построения тайлов (диагностика)
        self._grid_x = [lonlat_to_world(lon, 0)[0] for lon in range(-180, 181, 30)]
        self._grid_y = [lonlat_to_world(0, lat)[1] for lat in range(-45, 81, 15)]
        # кэши
        self._label_cache: dict[tuple[str, int], pygame.Surface] = {}
        self._badge_cache: dict[tuple, pygame.Surface] = {}
        self._label_info: dict[str, tuple[float, float, float]] = {}
        self._sel_cache: Optional[tuple] = None
        self._idmap: Optional[pygame.Surface] = None
        self._disp: dict[int, list[float]] = {}               # сглаженные позиции армий (мировые px)
        self._army_rects: list[tuple[float, float, float, float, int]] = []
        self.pulse = 0.0
        self._base: Optional[pygame.Surface] = None
        self._base_key: Optional[tuple] = None
        self._last_cam: Optional[tuple] = None
        self._rev = 0
        self.lite = False
        self.backdrop: Optional[pygame.Surface] = None
        self.eta_of: Optional[Callable[[object], float]] = None

    # ---------- цвета ----------
    def color_of(self, iso: str, state: "GameState") -> tuple[int, int, int]:
        """Цвет региона: цвет владельца (или идеологии); оккупированный регион сдвинут к цвету оккупанта."""
        oiso = state.owner.get(iso, iso)
        owner = state.countries.get(oiso)
        if owner is None:
            return (90, 90, 90)
        if SETTINGS.map_mode == "ideology":
            col = soften(IDEOLOGY_COLORS.get(owner.ideology, (150, 150, 150)))
        else:
            col = soften(owner.color)
        ctrl = getattr(state, "ctrl", None)
        if ctrl:
            ciso = ctrl.get(iso, oiso)
            cc = state.countries.get(ciso) if ciso != oiso else None
            if cc is not None:
                col = mix(col, soften(cc.color), 0.38)
        return col

    # ---------- синхронизация со state ----------
    def rebuild(self, state: "GameState") -> None:
        """Полная перерисовка кэша мира (старт, загрузка, смена режима карты)."""
        self._sig.clear()
        self._built = False
        self._sync(state)

    def _sync(self, state: "GameState") -> None:
        """Сверяет владельцев/контролёров со стилем и обновляет только изменившееся."""
        mode = SETTINGS.map_mode
        ctrl_map = getattr(state, "ctrl", None) or {}
        changed: list[str] = []
        for p in self.provs:
            o = state.owner.get(p.iso, p.iso)
            sig = (o, ctrl_map.get(p.iso, o), mode)
            if self._sig.get(p.iso) != sig:
                self._sig[p.iso] = sig
                changed.append(p.iso)
        state.map_dirty = False
        self._seen_rev = getattr(state, "map_rev", 0)
        if not changed and self._built:
            return
        full = (not self._built) or len(changed) > max(12, len(self.provs) // 4)
        if full:
            changed = [p.iso for p in self.provs]
        for iso in changed:
            self._fill[iso] = self.color_of(iso, state)
            o, c, _ = self._sig[iso]
            cc = state.countries.get(c) if c != o else None
            self._occ[iso] = soften(cc.color) if cc is not None else None
        arcs: set[int] = set()
        for iso in changed:
            arcs.update(self.arcs_by_iso.get(iso, ()))
        for ai in arcs:
            self._style_arc(ai, state)
        if full:
            self._paint_world()
            self._tiles.clear()
            self._tile_provs.clear()
        else:
            self._repaint_world(changed, arcs)
            touched = set(changed)
            for ai in arcs:
                touched.add(self.arcs[ai].iso)
                if self.arcs[ai].nb:
                    touched.add(self.arcs[ai].nb)
            for key in [k for k, v in self._tile_provs.items() if v & touched]:
                self._tiles.pop(key, None)
                self._tile_provs.pop(key, None)
        self._tile_ver += 1
        self._compute_labels(state)
        self._built = True
        self._rev += 1
        self._sel_cache = None
        self._base = None

    def _style_arc(self, ai: int, state: "GameState") -> None:
        a = self.arcs[ai]
        k = arc_class(a, state.owner)
        oa = self._occ.get(a.iso)
        ob = self._occ.get(a.nb) if (a.nb and a.nb != SEAM) else None
        rim = None
        if k in (K_COAST, K_BORDER):
            rim = oa or ob
        elif k == K_INNER and oa != ob:
            rim = oa or ob
        self._kind[ai] = k
        self._rim[ai] = rim
        if k == K_INNER:
            f = self._fill.get(a.iso, (90, 90, 90))
            self._inner[ai] = (int(f[0] * 0.78), int(f[1] * 0.78), int(f[2] * 0.78))

    # ---------- мировая поверхность 1× ----------
    def _paint_ocean(self) -> None:
        w, h = self.world.get_size()
        for y in range(h):
            k = y / max(1, h - 1)
            col = tuple(int(OCEAN_TOP[i] + (OCEAN_BOTTOM[i] - OCEAN_TOP[i]) * k) for i in range(3))
            pygame.draw.line(self.world, col, (0, y), (w, y))
        for x in self._grid_x:                                  # градусная сетка
            pygame.draw.line(self.world, GRID, (x, 0), (x, h))
        for y in self._grid_y:
            pygame.draw.line(self.world, GRID, (0, y), (w, y))

    def _draw_arcs_on(self, surf: pygame.Surface, idxs, width: int = 1) -> None:
        """Линии границ/берегов/оккупации выбранных дуг на поверхности мира 1×."""
        for want in (K_INNER, K_BORDER, K_COAST):
            for ai in idxs:
                if self._kind[ai] == want and len(self.arcs[ai].pts) >= 2:
                    col = self._inner[ai] if want == K_INNER else (COAST if want == K_COAST else BORDER)
                    pygame.draw.lines(surf, col, False, self.arcs[ai].pts, width)
        for ai in idxs:
            if self._rim[ai] and len(self.arcs[ai].pts) >= 2:
                pygame.draw.lines(surf, self._rim[ai], False, self.arcs[ai].pts, 2)

    def _paint_world(self) -> None:
        self._paint_ocean()
        if not self.lite:
            for ai, a in enumerate(self.arcs):
                if self._kind[ai] == K_COAST and len(a.pts) >= 2:
                    pygame.draw.lines(self.world, SHALLOW, False, a.pts, 5)
        for p in self.order:
            col = self._fill.get(p.iso, (90, 90, 90))
            for ring in p.wpolys:
                if len(ring) >= 3:
                    pygame.draw.polygon(self.world, col, ring)
        self._draw_arcs_on(self.world, range(len(self.arcs)))

    def _repaint_world(self, changed: list[str], arcs: set[int]) -> None:
        """Перерисовывает на кэше мира только изменившиеся регионы и их границы."""
        for p in sorted((self.by_iso[i] for i in changed), key=lambda q: self._oidx[q.iso]):
            col = self._fill.get(p.iso, (90, 90, 90))
            for ring in p.wpolys:
                if len(ring) >= 3:
                    pygame.draw.polygon(self.world, col, ring)
        self._draw_arcs_on(self.world, sorted(arcs))

    def _compute_labels(self, state: "GameState") -> None:
        """Подписи по державам: одна на державу; ширина — по смежным регионам (империя подписывается целиком)."""
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

    # ---------- тайлы ----------
    def _render_tile(self, zk: int, tx: int, ty: int) -> tuple[pygame.Surface, frozenset[str]]:
        """Строит тайл (zk/ZQ — масштаб) из геометрии, отсечённой по прямоугольнику тайла."""
        z = zk / ZQ
        ox, oy = tx * TILE, ty * TILE
        surf = pygame.Surface((TILE, TILE))
        rect = (ox / z, oy / z, (ox + TILE) / z, (oy + TILE) / z)
        m = 4.0 / z + 1.0                                      # запас на толщину линий
        for j in range(0, TILE, 8):                            # градиент океана
            k = max(0.0, min(1.0, (oy + j) / z / WORLD_H))
            surf.fill(tuple(int(OCEAN_TOP[i] + (OCEAN_BOTTOM[i] - OCEAN_TOP[i]) * k) for i in range(3)),
                      (0, j, TILE, 8))
        for gx in self._grid_x:
            x = int(gx * z + 0.5) - ox
            if 0 <= x < TILE:
                pygame.draw.line(surf, GRID, (x, 0), (x, TILE))
        for gy in self._grid_y:
            y = int(gy * z + 0.5) - oy
            if 0 <= y < TILE:
                pygame.draw.line(surf, GRID, (0, y), (TILE, y))
        arcs = sorted(self._grid_a.query(rect))
        kinds: dict[int, list[int]] = {K_INNER: [], K_BORDER: [], K_COAST: []}
        rims: list[int] = []
        touched: set[str] = set()
        for ai in arcs:
            k = self._kind[ai]
            if k in kinds:
                kinds[k].append(ai)
            if self._rim[ai]:
                rims.append(ai)
        lw = 2 if z >= 4.0 else 1
        if not self.lite:                                      # мелководье у берегов
            hw = int(3 + min(z, 3.0))
            for ai in kinds[K_COAST]:
                for run in polyline_runs(self.paths[ai], rect, m):
                    pts = to_pixels(run, z, ox, oy)
                    if len(pts) >= 2:
                        pygame.draw.lines(surf, SHALLOW, False, pts, hw)
        for oi in sorted(self._grid_p.query(rect)):            # суша: крупные регионы снизу
            p = self.order[oi]
            col = self._fill.get(p.iso, (90, 90, 90))
            for path in self.rings[p.iso]:
                poly = ring_polygon(path, rect, m)
                if poly:
                    pts = to_pixels(poly, z, ox, oy)
                    if len(pts) >= 3:
                        pygame.draw.polygon(surf, col, pts)
                        touched.add(p.iso)
        for k in (K_INNER, K_BORDER, K_COAST):
            for ai in kinds[k]:
                col = self._inner[ai] if k == K_INNER else (COAST if k == K_COAST else BORDER)
                hit = False
                for run in polyline_runs(self.paths[ai], rect, m):
                    pts = to_pixels(run, z, ox, oy)
                    if len(pts) >= 2:
                        pygame.draw.lines(surf, col, False, pts, lw)
                        hit = True
                if hit:
                    touched.add(self.arcs[ai].iso)
                    if self.arcs[ai].nb:
                        touched.add(self.arcs[ai].nb)
        for ai in rims:
            for run in polyline_runs(self.paths[ai], rect, m):
                pts = to_pixels(run, z, ox, oy)
                if len(pts) >= 2:
                    pygame.draw.lines(surf, self._rim[ai], False, pts, lw + 1)  # type: ignore[arg-type]
        return surf, frozenset(touched)

    def _store_tile(self, key: tuple[int, int, int], surf: pygame.Surface, provs: frozenset[str]) -> None:
        self._tiles[key] = surf
        self._tile_provs[key] = provs
        self._tile_ver += 1
        limit = MAX_TILES // 2 if self.lite else MAX_TILES
        while len(self._tiles) > limit:
            old, _ = self._tiles.popitem(last=False)
            self._tile_provs.pop(old, None)

    def _draw_scaled_world(self, screen: pygame.Surface) -> None:
        """Кэш мира 1×, растянутый на экран: основной способ при малом масштабе и запасной при дорисовке тайлов."""
        cam = self.cam
        screen.fill(OCEAN)
        vx, vy, vw, vh = cam.view_rect()
        src = pygame.Rect(int(max(vx, 0)), int(max(vy, 0)), 0, 0)
        src.w = int(min(vx + vw, WORLD_W)) - src.x + 1
        src.h = int(min(vy + vh, WORLD_H)) - src.y + 1
        src.clamp_ip(self.world.get_rect())
        if src.w > 0 and src.h > 0:
            sub = self.world.subsurface(src)
            size = (max(1, int(src.w * cam.zoom)), max(1, int(src.h * cam.zoom)))
            screen.blit(pygame.transform.scale(sub, size), cam.world_to_screen(src.x, src.y))

    def _origin(self) -> tuple[int, int]:
        """Целочисленное смещение экрана в «пикселях масштаба» (общее для тайлов и подсветок)."""
        z = self.cam.zoom
        return int(round(self.cam.x * z)), int(round(self.cam.y * z))

    def _compose_tiles(self, screen: pygame.Surface) -> bool:
        """Рисует вид тайлами; возвращает True, если все нужные тайлы уже готовы."""
        cam = self.cam
        zk = int(round(cam.zoom * ZQ))
        cam.zoom = zk / ZQ
        w, h = screen.get_size()
        sx0, sy0 = self._origin()
        keys = [(zk, tx, ty) for ty in range(sy0 // TILE, (sy0 + h - 1) // TILE + 1)
                for tx in range(sx0 // TILE, (sx0 + w - 1) // TILE + 1)]
        missing = [k for k in keys if k not in self._tiles]
        if missing and time.monotonic() - cam.t_zoom > ZOOM_SETTLE:
            cx, cy = (sx0 + w / 2) / TILE, (sy0 + h / 2) / TILE
            missing.sort(key=lambda k: (k[1] + 0.5 - cx) ** 2 + (k[2] + 0.5 - cy) ** 2)
            budget = 0.004 if self.lite else 0.007
            t0 = time.perf_counter()
            for i, k in enumerate(missing):
                if i and time.perf_counter() - t0 > budget:
                    break
                surf, provs = self._render_tile(*k)
                self._store_tile(k, surf, provs)
            self.tile_ms = (time.perf_counter() - t0) * 1000.0
            missing = [k for k in keys if k not in self._tiles]
        if missing:
            self._draw_scaled_world(screen)
        for k in keys:
            t = self._tiles.get(k)
            if t is not None:
                self._tiles.move_to_end(k)
                screen.blit(t, (k[1] * TILE - sx0, k[2] * TILE - sy0))
        return not missing

    # ---------- рисование ----------
    def draw(self, screen: pygame.Surface, state: "GameState", hover: Optional[str],
             selected: Optional[str], extra: tuple[str, ...] = ()) -> None:
        """Рисует карту в окно. extra — дополнительно подсвеченные регионы/страны (туториал)."""
        if state.map_dirty or getattr(state, "map_rev", 0) != self._seen_rev or not self._built:
            self._sync(state)
        cam = self.cam
        key = (cam.x, cam.y, cam.zoom, screen.get_size(), self._rev, self._tile_ver, self.lite)
        if key == self._base_key and self._base is not None:
            screen.blit(self._base, (0, 0))
        else:
            complete = True
            if cam.zoom >= (4.0 if self.lite else VECTOR_ZOOM) and self._tiles_ok:
                try:
                    complete = self._compose_tiles(screen)
                except Exception:                              # noqa: BLE001 — не роняем кадр: откат к кэшу мира
                    log.exception("Ошибка тайлового рендера — отключён, используется кэш мира")
                    self._tiles_ok = False
                    self._tiles.clear()
                    self._tile_provs.clear()
                    self._draw_scaled_world(screen)
            else:
                self._draw_scaled_world(screen)
            here = (cam.x, cam.y, cam.zoom)
            if complete and here == self._last_cam:
                self._base = screen.copy()
                self._base_key = (cam.x, cam.y, cam.zoom, screen.get_size(), self._rev, self._tile_ver, self.lite)
            else:
                self._base = None
            self._last_cam = here
        self._draw_top(screen, state, hover, selected, extra)

    def _view_rect(self, screen: pygame.Surface) -> tuple[float, float, float, float]:
        vx, vy, vw, vh = self.cam.view_rect()
        return vx, vy, vx + vw, vy + vh

    def _draw_top(self, screen: pygame.Surface, state: "GameState", hover: Optional[str],
                  selected: Optional[str], extra: tuple[str, ...]) -> None:
        for code in extra:
            ids = {r for r, o in state.owner.items() if o == code} or set(self.ids_for(code))
            self._outline_set(screen, ids, (255, 215, 0), 3)
        if hover and hover in self.by_iso:
            self._fill_region(screen, hover, lighten(self._fill.get(hover, (90, 90, 90))))
        if selected and selected in state.countries:
            self._outline_territory(screen, state, selected)
        self._region_names(screen, state)
        self._labels(screen, state)

    def ids_for(self, code: str) -> list[str]:
        """id регионов по коду: сам регион либо все регионы страны с таким ISO."""
        if code in self.by_iso:
            return [code]
        return list(self.by_country.get(code, ()))

    def _fill_region(self, screen: pygame.Surface, iso: str, color: tuple[int, int, int]) -> None:
        """Заливка одного региона (подсветка наведения); геометрия отсекается по окну."""
        z = self.cam.zoom
        ox, oy = self._origin()
        view = self._view_rect(screen)
        for path in self.rings.get(iso, ()):
            poly = ring_polygon(path, view, 2.0)
            if poly:
                pts = to_pixels(poly, z, ox, oy)
                if len(pts) >= 3:
                    pygame.draw.polygon(screen, color, pts)

    def _draw_runs(self, screen: pygame.Surface, ai: int, color: tuple[int, int, int], w: int,
                   view: tuple[float, float, float, float]) -> None:
        z = self.cam.zoom
        ox, oy = self._origin()
        for run in polyline_runs(self.paths[ai], view, 2.0):
            pts = to_pixels(run, z, ox, oy)
            if len(pts) >= 2:
                pygame.draw.lines(screen, color, False, pts, w)

    def _outline_set(self, screen: pygame.Surface, ids: set[str], color: tuple[int, int, int], w: int) -> None:
        """Внешний контур набора регионов."""
        view = self._view_rect(screen)
        seen: set[int] = set()
        for iso in ids:
            for ai in self.arcs_by_iso.get(iso, ()):
                if ai in seen:
                    continue
                seen.add(ai)
                a = self.arcs[ai]
                if a.nb == SEAM:
                    continue
                inside_a = a.iso in ids
                inside_b = (a.nb in ids) if a.nb else False
                if (a.nb is None and inside_a) or (a.nb is not None and inside_a != inside_b):
                    self._draw_runs(screen, ai, color, w, view)

    def _outline_territory(self, screen: pygame.Surface, state: "GameState", who: str) -> None:
        """Белый контур всей территории державы (с учётом захваченных земель); список дуг кэшируется."""
        ck = (who, self._rev)
        if self._sel_cache is None or self._sel_cache[0] != ck:
            owned = [i for i, o in state.owner.items() if o == who]
            idx: set[int] = set()
            for iso in owned:
                idx.update(self.arcs_by_iso.get(iso, ()))
            edge = [ai for ai in sorted(idx) if territory_edge(self.arcs[ai], state.owner, who)]
            self._sel_cache = (ck, edge)
        view = self._view_rect(screen)
        for ai in self._sel_cache[1]:
            b = self.arcs[ai].bbox
            if b[2] < view[0] or b[0] > view[2] or b[3] < view[1] or b[1] > view[3]:
                continue
            self._draw_runs(screen, ai, (255, 255, 255), 2, view)

    def _text(self, text: str, color: tuple[int, int, int], key: str) -> pygame.Surface:
        k = (text + key, 0)
        surf = self._label_cache.get(k)
        if surf is None:
            surf = self.font.render(text, True, color)
            self._label_cache[k] = surf
        return surf

    def _labels(self, screen: pygame.Surface, state: "GameState") -> None:
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

    def _region_names(self, screen: pygame.Surface, state: "GameState") -> None:
        """Названия регионов при сильном приближении — чтобы целиться армиями в понятные цели."""
        z = self.cam.zoom
        if z < 3.4:
            return
        sw, sh = screen.get_size()
        for p in self.provs:
            if p.country == p.iso and len(self.by_country.get(p.country, ())) == 1:
                continue
            sx, sy = self.cam.world_to_screen(*p.center_world)
            if not (0 < sx < sw and 0 < sy < sh):
                continue
            nm = p.name.split(": ")[-1]
            t = self._text(nm, (232, 236, 242), "r")
            if (p.main_wbbox[2] - p.main_wbbox[0]) * z > t.get_width() + 12:
                screen.blit(t, (sx - t.get_width() / 2, sy + 6))

    def draw_marker(self, screen: pygame.Surface, iso: str, dt: float) -> None:
        """Пульсирующий маркер на столице (для туториала/игрока)."""
        p = self.by_iso.get(iso) or self.by_iso.get((self.by_country.get(iso) or [""])[0])
        if not p:
            return
        self.pulse += dt * 4
        sx, sy = self.cam.world_to_screen(*p.capital_world)
        r = 8 + 4 * math.sin(self.pulse)
        pygame.draw.circle(screen, (255, 215, 0), (int(sx), int(sy)), int(r), 3)
        pygame.draw.circle(screen, (255, 60, 60), (int(sx), int(sy)), 3)

    # ---------- столицы ----------
    def capital_world(self, state: "GameState", iso: str) -> Optional[tuple[float, float]]:
        """Мировые координаты столицы державы iso (None — нет данных)."""
        c = state.countries.get(iso)
        if c is None:
            return None
        pos = getattr(c, "capital_pos", None)
        if pos:
            return lonlat_to_world(pos[0], pos[1])
        p = self.by_iso.get(getattr(c, "capital_region", "") or iso)
        return p.capital_world if p is not None else None

    def _draw_capitals(self, screen: pygame.Surface, state: "GameState", mine: Optional[str]) -> None:
        """Столицы: звезда — действующая, ромб цвета оккупанта — захваченная."""
        z = self.cam.zoom
        if z < 1.0:
            return
        sw, sh = screen.get_size()
        r0 = 3.0 + min(z, 3.0)
        ctrl = getattr(state, "ctrl", {}) or {}
        for c in state.countries.values():
            if not c.alive:
                continue
            w = self.capital_world(state, c.iso)
            if w is None:
                continue
            sx, sy = self.cam.world_to_screen(*w)
            if not (-10 < sx < sw + 10 and -10 < sy < sh + 10):
                continue
            rid = getattr(c, "capital_region", "") or c.iso
            holder = ctrl.get(rid, state.owner.get(rid, c.iso))
            if holder == c.iso:
                r = r0 * (1.5 if c.iso == mine else 1.0)
                pts = [(sx + dx * r, sy + dy * r) for dx, dy in STAR]
                pygame.draw.polygon(screen, GOLD if c.iso == mine else (250, 244, 214), pts)
                pygame.draw.polygon(screen, INK, pts, 1)
            else:
                oc = state.countries.get(holder)
                col = oc.color if oc else (160, 160, 160)
                r = r0 * 1.1
                pts = [(sx, sy - r), (sx + r, sy), (sx, sy + r), (sx - r, sy)]
                pygame.draw.polygon(screen, col, pts)
                pygame.draw.polygon(screen, INK, pts, 1)
                pygame.draw.circle(screen, ATT, (int(sx), int(sy)), int(r + 4), 1)
            if z >= 2.4 and c.capital:
                t = self._text(c.capital, (240, 242, 246), "c")
                screen.blit(t, (sx + r0 + 3, sy - t.get_height() / 2))

    # ---------- армии ----------
    def _badge(self, text: str, fill: tuple[int, int, int], border: tuple[int, int, int], bw: int) -> pygame.Surface:
        """Плашка армии: заливка цвета державы, рамка по состоянию, число дивизий."""
        key = (text, fill, border, bw)
        surf = self._badge_cache.get(key)
        if surf is None:
            t = self.font.render(text, True, ink_for(fill))
            surf = pygame.Surface((t.get_width() + 12, t.get_height() + 4), pygame.SRCALPHA)
            surf.fill((*fill, 235))
            pygame.draw.rect(surf, border, surf.get_rect(), bw)
            surf.blit(t, (6, 2))
            if len(self._badge_cache) > 600:
                self._badge_cache.clear()
            self._badge_cache[key] = surf
        return surf

    def army_target_world(self, state: "GameState", a) -> tuple[float, float]:
        """Логическая позиция армии в мировых px: центр региона либо точка на ходу к следующему региону."""
        p0 = self.by_iso.get(a.region)
        if p0 is None:
            return (0.0, 0.0)
        x0, y0 = p0.center_world
        if a.path and a.progress > 0:
            p1 = self.by_iso.get(a.path[0])
            if p1 is not None:
                x1, y1 = p1.center_world
                t = max(0.0, min(1.0, a.progress))
                return x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
        return x0, y0

    def _dashed(self, screen: pygame.Surface, color: tuple[int, int, int], a: tuple[float, float],
                b: tuple[float, float], dash: float = 8.0, width: int = 1) -> None:
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length < 1 or length > 6000:
            return
        n = int(length // (dash * 2)) + 1
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        for i in range(n):
            s, e = i * dash * 2, min(length, i * dash * 2 + dash)
            pygame.draw.line(screen, color, (a[0] + ux * s, a[1] + uy * s), (a[0] + ux * e, a[1] + uy * e), width)

    def _arrow(self, screen: pygame.Surface, x: float, y: float, ux: float, uy: float,
               color: tuple[int, int, int], s: float = 7.0) -> None:
        tip = (x + ux * s, y + uy * s)
        left = (x - ux * s * 0.6 - uy * s * 0.7, y - uy * s * 0.6 + ux * s * 0.7)
        right = (x - ux * s * 0.6 + uy * s * 0.7, y - uy * s * 0.6 - ux * s * 0.7)
        pygame.draw.polygon(screen, color, [tip, left, right])
        pygame.draw.polygon(screen, INK, [tip, left, right], 1)

    @staticmethod
    def _on_screen(screen: pygame.Surface, pt: tuple[float, float], m: float = 40) -> bool:
        return -m < pt[0] < screen.get_width() + m and -m < pt[1] < screen.get_height() + m

    def draw_overlays(self, screen: pygame.Surface, state: "GameState", mine: Optional[str], dt: float = 0.0,
                      sel_army: Optional[int] = None, pick_target: Optional[str] = None) -> None:
        """Столицы, маршруты, бои и армии. sel_army — выбранная армия; pick_target — регион-цель под курсором."""
        self.pulse += dt * 3
        self._draw_capitals(screen, state, mine)
        armies = getattr(state, "armies", None) or {}
        self._army_rects = []
        if armies:
            self._draw_battles(screen, state)
            self._draw_routes(screen, state, armies, mine, sel_army, pick_target)
            self._draw_armies(screen, state, armies, mine, sel_army, dt)

    def _draw_battles(self, screen: pygame.Surface, state: "GameState") -> None:
        for rid in getattr(state, "battles", ()) or ():
            p = self.by_iso.get(rid)
            if p is None:
                continue
            sx, sy = self.cam.world_to_screen(*p.center_world)
            if not self._on_screen(screen, (sx, sy)):
                continue
            r = 13 + 3 * math.sin(self.pulse * 1.6)
            pygame.draw.circle(screen, ATT, (int(sx), int(sy)), int(r), 2)
            d = r * 0.55
            pygame.draw.line(screen, ATT, (sx - d, sy - d), (sx + d, sy + d), 2)
            pygame.draw.line(screen, ATT, (sx - d, sy + d), (sx + d, sy - d), 2)

    def _route_points(self, state: "GameState", a) -> list[tuple[float, float]]:
        """Экранные точки маршрута: текущее положение → центры регионов пути."""
        cam = self.cam
        pts = [cam.world_to_screen(*self._disp.get(a.id, self.army_target_world(state, a)))]
        for rid in a.path:
            p = self.by_iso.get(rid)
            if p is not None:
                pts.append(cam.world_to_screen(*p.center_world))
        return pts

    def _draw_routes(self, screen: pygame.Surface, state: "GameState", armies: dict, mine: Optional[str],
                     sel_army: Optional[int], pick_target: Optional[str]) -> None:
        for a in armies.values():
            own = a.owner == mine
            sel = a.id == sel_army
            if not a.path or not (own or sel):
                continue
            pts = self._route_points(state, a)
            if len(pts) < 2:
                continue
            col = GOLD if sel else mix(self._army_color(state, a), (240, 240, 240), 0.2)
            wd = 2 if sel else 1
            for i in range(len(pts) - 1):
                if self._on_screen(screen, pts[i], 3000) or self._on_screen(screen, pts[i + 1], 3000):
                    self._dashed(screen, col, pts[i], pts[i + 1], 8.0 if sel else 6.0, wd)
            if sel:
                for q in pts[1:-1]:
                    if self._on_screen(screen, q):
                        pygame.draw.circle(screen, col, (int(q[0]), int(q[1])), 3)
                tx, ty = pts[-1]
                if self._on_screen(screen, (tx, ty)):
                    r = 10 + 2 * math.sin(self.pulse * 1.5)
                    pygame.draw.circle(screen, col, (int(tx), int(ty)), int(r), 2)
                    if self.eta_of is not None:
                        try:
                            days = self.eta_of(a)
                        except Exception:                      # noqa: BLE001
                            days = -1
                        if days >= 0:
                            t = self._text(f"{int(math.ceil(days))} дн.", (240, 242, 246), "e")
                            screen.blit(t, (tx + 12, ty - t.get_height() / 2))
        if sel_army in armies and pick_target and pick_target in self.by_iso:
            a = armies[sel_army]
            p = self.by_iso[pick_target]
            tx, ty = self.cam.world_to_screen(*p.center_world)
            sx, sy = self.cam.world_to_screen(*self._disp.get(a.id, self.army_target_world(state, a)))
            self._dashed(screen, (240, 240, 240), (sx, sy), (tx, ty), 5.0, 1)
            if self._on_screen(screen, (tx, ty)):
                pygame.draw.circle(screen, (240, 240, 240), (int(tx), int(ty)), 8, 1)

    def _army_color(self, state: "GameState", a) -> tuple[int, int, int]:
        c = state.countries.get(a.owner)
        return soften(c.color) if c else (150, 150, 150)

    def _draw_armies(self, screen: pygame.Surface, state: "GameState", armies: dict, mine: Optional[str],
                     sel_army: Optional[int], dt: float) -> None:
        z = self.cam.zoom
        cam = self.cam
        if len(self._disp) > len(armies) + 32:
            self._disp = {k: v for k, v in self._disp.items() if k in armies}
        k = min(1.0, dt * 9.0)
        slots: dict[str, list[int]] = {}
        for a in armies.values():
            if not a.path or a.progress <= 0:
                slots.setdefault(a.region, []).append(a.id)
        wars_with_me: set[str] = set()
        if mine and mine in state.countries:
            wars_with_me = set(state.countries[mine].at_war_with)
        for a in armies.values():
            tgt = self.army_target_world(state, a)
            cur = self._disp.get(a.id)
            if cur is None or math.hypot(cur[0] - tgt[0], cur[1] - tgt[1]) > 260:
                cur = [tgt[0], tgt[1]]
                self._disp[a.id] = cur
            else:
                cur[0] += (tgt[0] - cur[0]) * k
                cur[1] += (tgt[1] - cur[1]) * k
            sx, sy = cam.world_to_screen(cur[0], cur[1])
            own = a.owner == mine
            sel = a.id == sel_army
            if not (own or sel or z >= 0.9 or a.owner in wars_with_me):
                continue
            ids = slots.get(a.region)
            if ids and len(ids) > 1 and a.id in ids:
                sx += (ids.index(a.id) - (len(ids) - 1) / 2.0) * 25
            if not self._on_screen(screen, (sx, sy)):
                continue
            col = self._army_color(state, a)
            if z < 0.9 and not sel:
                pygame.draw.rect(screen, col, (int(sx) - 3, int(sy) - 3, 7, 7))
                pygame.draw.rect(screen, INK, (int(sx) - 3, int(sy) - 3, 7, 7), 1)
                continue
            fighting = bool(getattr(a, "fight", ""))
            border = GOLD if sel else (ATT if fighting else ((236, 238, 242) if own else INK))
            badge = self._badge(str(a.units), col, border, 2 if (sel or fighting) else 1)
            bx, by = sx - badge.get_width() / 2, sy - badge.get_height() / 2
            screen.blit(badge, (bx, by))
            self._army_rects.append((bx, by, bx + badge.get_width(), by + badge.get_height(), a.id))
            if a.path and a.path[0] in self.by_iso:
                nsx, _ = cam.world_to_screen(*self.by_iso[a.path[0]].center_world)
                right = nsx >= sx
                self._arrow(screen, bx + badge.get_width() + 6 if right else bx - 6, sy,
                            1 if right else -1, 0, (236, 238, 242), 6)

    def army_at(self, pos: tuple[int, int]) -> Optional[int]:
        """id армии под экранной точкой (маркер сверху — приоритетнее)."""
        x, y = pos
        for x0, y0, x1, y1, aid in reversed(self._army_rects):
            if x0 <= x <= x1 and y0 <= y <= y1:
                return aid
        return None

    # ---------- фон меню ----------
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
    def _build_idmap(self) -> None:
        """Растровый индекс регионов (цвет = номер): наведение и клик — O(1) вместо перебора полигонов."""
        m = pygame.Surface(self.world.get_size())
        m.fill((0, 0, 0))
        for i, p in enumerate(self.order):
            code = i + 1
            col = (code & 255, (code >> 8) & 255, 0)
            for ring in p.wpolys:
                if len(ring) >= 3:
                    pygame.draw.polygon(m, col, ring)
        self._idmap = m

    def province_at(self, pos: tuple[int, int]) -> Optional[str]:
        """id региона под экранной точкой (None — океан)."""
        wx, wy = self.cam.screen_to_world(*pos)
        if not (0 <= wx < WORLD_W and 0 <= wy < WORLD_H):
            return None
        if self._idmap is None:
            self._build_idmap()
        assert self._idmap is not None
        r = 0.7 if self.cam.zoom >= 2.0 else 0.0
        offs = ((0.0, 0.0), (r, 0.0), (-r, 0.0), (0.0, r), (0.0, -r)) if r else ((0.0, 0.0),)
        w, h = int(WORLD_W), int(WORLD_H)
        cand: list[int] = []
        for dx, dy in offs:
            x, y = int(wx + dx), int(wy + dy)
            if 0 <= x < w and 0 <= y < h:
                c = self._idmap.get_at((x, y))
                i = (c[0] | (c[1] << 8)) - 1
                if 0 <= i < len(self.order) and i not in cand:
                    cand.append(i)
        if not cand:
            return None
        lon, lat = world_to_lonlat(wx, wy)
        for i in cand:
            if self.order[i].contains(lon, lat):
                return self.order[i].iso
        return self.order[cand[0]].iso
