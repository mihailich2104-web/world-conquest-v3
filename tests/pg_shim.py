"""Минимальная замена pygame на Pillow — чтобы проверять рендерер карты без SDL (CI/песочница без pygame).
Не для игры. Считает «стоимость» pygame.draw.polygon как вершины × высота (так заливает SDL)."""
from __future__ import annotations

import sys
import types

from PIL import Image, ImageDraw, ImageFont

SRCALPHA = 0x10000
STATS = {"polys": 0, "cost": 0, "max_cost": 0, "lines": 0}


class Rect:
    def __init__(self, x=0, y=0, w=0, h=0):
        if isinstance(x, Rect):
            x, y, w, h = x.x, x.y, x.w, x.h
        elif isinstance(x, (tuple, list)):
            x, y, w, h = x
        self.x, self.y, self.w, self.h = int(x), int(y), int(w), int(h)

    width = property(lambda s: s.w)
    height = property(lambda s: s.h)
    right = property(lambda s: s.x + s.w)
    bottom = property(lambda s: s.y + s.h)

    def clamp_ip(self, other):
        self.w, self.h = min(self.w, other.w), min(self.h, other.h)
        self.x = max(other.x, min(self.x, other.x + other.w - self.w))
        self.y = max(other.y, min(self.y, other.y + other.h - self.h))

    def collidepoint(self, p):
        return self.x <= p[0] < self.x + self.w and self.y <= p[1] < self.y + self.h

    def __iter__(self):
        return iter((self.x, self.y, self.w, self.h))


class Surface:
    def __init__(self, size, flags=0):
        self.img = Image.new("RGBA" if flags & SRCALPHA else "RGB", (max(1, int(size[0])), max(1, int(size[1]))))
        self._alpha = 255

    def get_size(self): return self.img.size
    def get_width(self): return self.img.size[0]
    def get_height(self): return self.img.size[1]
    def get_rect(self): return Rect(0, 0, *self.img.size)
    def copy(self):
        s = Surface((1, 1)); s.img = self.img.copy(); return s
    def set_alpha(self, a): self._alpha = a

    def fill(self, color, rect=None):
        c = tuple(color)
        if rect is None:
            self.img.paste(c, (0, 0, *self.img.size))
        else:
            x, y, w, h = rect
            self.img.paste(c, (int(x), int(y), int(x + w), int(y + h)))

    def blit(self, src, dest):
        x, y = int(dest[0]), int(dest[1])
        if src.img.mode == "RGBA":
            self.img.paste(src.img.convert("RGB") if self.img.mode == "RGB" else src.img, (x, y), src.img)
        else:
            self.img.paste(src.img, (x, y))

    def subsurface(self, rect):
        r = Rect(rect)
        s = Surface((r.w, r.h)); s.img = self.img.crop((r.x, r.y, r.x + r.w, r.y + r.h)); return s

    def get_at(self, p):
        v = self.img.getpixel((int(p[0]), int(p[1])))
        return (*v, 255) if len(v) == 3 else v


def _pts(points):
    return [(float(x), float(y)) for x, y in points]


class _Draw:
    @staticmethod
    def polygon(surf, color, points, width=0):
        pts = _pts(points)
        if len(pts) < 3:
            raise ValueError("polygon: меньше 3 точек")
        ys = [p[1] for p in pts]
        cost = len(pts) * (int(max(ys)) - int(min(ys)) + 1)
        STATS["polys"] += 1; STATS["cost"] += cost; STATS["max_cost"] = max(STATS["max_cost"], cost)
        d = ImageDraw.Draw(surf.img)
        if width == 0:
            d.polygon(pts, fill=tuple(color))
        else:
            d.line(pts + [pts[0]], fill=tuple(color), width=width)

    @staticmethod
    def lines(surf, color, closed, points, width=1):
        pts = _pts(points)
        if len(pts) < 2:
            raise ValueError("lines: меньше 2 точек")
        STATS["lines"] += 1
        ImageDraw.Draw(surf.img).line(pts + ([pts[0]] if closed else []), fill=tuple(color), width=width)

    @staticmethod
    def line(surf, color, a, b, width=1):
        ImageDraw.Draw(surf.img).line([tuple(a), tuple(b)], fill=tuple(color), width=width)

    @staticmethod
    def circle(surf, color, center, radius, width=0):
        c = (float(center[0]), float(center[1]))
        r = max(1, float(radius))
        d = ImageDraw.Draw(surf.img)
        d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], outline=tuple(color) if width else None,
                  fill=tuple(color) if not width else None, width=max(1, width))

    @staticmethod
    def rect(surf, color, rect, width=0, **kw):
        x, y, w, h = tuple(rect)
        d = ImageDraw.Draw(surf.img)
        d.rectangle([x, y, x + w - 1, y + h - 1], outline=tuple(color) if width else None,
                    fill=tuple(color) if not width else None, width=max(1, width))


class _Transform:
    @staticmethod
    def scale(surf, size):
        s = Surface((1, 1)); s.img = surf.img.resize((int(size[0]), int(size[1])), Image.NEAREST); return s

    @staticmethod
    def smoothscale(surf, size):
        s = Surface((1, 1)); s.img = surf.img.resize((int(size[0]), int(size[1])), Image.BILINEAR); return s


class Font:
    def __init__(self, *a, **k):
        self.f = ImageFont.load_default()

    def render(self, text, aa, color, *a):
        w = max(1, int(self.f.getlength(text))) + 2
        s = Surface((w, 12), SRCALPHA)
        ImageDraw.Draw(s.img).text((1, 0), text, fill=tuple(color) + (255,), font=self.f)
        return s

    def size(self, text): return (int(self.f.getlength(text)) + 2, 12)


def install():
    m = types.ModuleType("pygame")
    m.Rect, m.Surface, m.SRCALPHA = Rect, Surface, SRCALPHA
    m.draw, m.transform = _Draw, _Transform
    m.font = types.SimpleNamespace(Font=Font)
    sys.modules["pygame"] = m
    return m
