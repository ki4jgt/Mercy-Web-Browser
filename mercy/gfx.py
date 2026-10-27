"""Small drawing helpers built on pyglet.shapes."""

import math

import pyglet
from pyglet import shapes

_fonts: dict[tuple, pyglet.font.base.Font] = {}
_widths: dict[tuple, float] = {}


def font(name: str, size: float, bold: bool = False, italic: bool = False):
    key = (name, round(size, 2), bold, italic)
    f = _fonts.get(key)
    if f is None:
        f = _fonts[key] = pyglet.font.load(name, size, bold=bold, italic=italic)
    return f


def text_width(f, text: str) -> float:
    key = (id(f), text)
    w = _widths.get(key)
    if w is None:
        w = _widths[key] = sum(g.advance for g in f.get_glyphs(text))
    return w


class GradientRect(shapes.Rectangle):
    """A rectangle whose color fades from one edge to the opposite one."""

    def __init__(self, x, y, width, height, start, end, vertical=True, **kwargs):
        self._start = tuple(start) + (255,) * (4 - len(start))
        self._end = tuple(end) + (255,) * (4 - len(end))
        self._vertical = vertical
        super().__init__(x, y, width, height, color=self._start, **kwargs)

    def _vertex_colors(self):
        # Vertex order: (x1,y1) (x2,y1) (x2,y2) (x1,y1) (x2,y2) (x1,y2)
        s, e = self._start, self._end
        if self._vertical:  # start = bottom, end = top
            return s + s + e + s + e + e
        return s + e + e + s + e + s  # start = left, end = right

    def _create_vertex_list(self):
        super()._create_vertex_list()
        self._vertex_list.colors[:] = self._vertex_colors()

    def _update_color(self):
        self._vertex_list.colors[:] = self._vertex_colors()

    def set_colors(self, start, end):
        self._start = tuple(start) + (255,) * (4 - len(start))
        self._end = tuple(end) + (255,) * (4 - len(end))
        self._update_color()


def rrect(x, y, w, h, r, color, batch, group):
    r = max(0.0, min(r, w / 2, h / 2))
    if r < 1:
        return shapes.Rectangle(x, y, w, h, color=color, batch=batch, group=group)
    return shapes.RoundedRectangle(x, y, w, h, r, segments=max(6, int(r)), color=color,
                                   batch=batch, group=group)


def soft_shadow(x, y, w, h, r, batch, group, strength=60, spread=14, offset=6, color=(0, 0, 0)):
    """Fake a blurred drop shadow with stacked translucent rounded rects."""
    out = []
    steps = 7
    for i in range(steps):
        t = (i + 1) / steps
        grow = spread * (1 - t)
        a = int(strength * (t ** 2) / steps * 2.2)
        out.append(rrect(x - grow, y - offset - grow, w + 2 * grow, h + 2 * grow, r + grow,
                         (*color[:3], min(255, a)), batch, group))
    return out


class RadialGradient(shapes.Circle):
    """A disc that fades from `color` at its center to transparent at its rim."""

    def __init__(self, x, y, radius, color, **kwargs):
        self._center = tuple(color[:3]) + (color[3] if len(color) > 3 else 255,)
        super().__init__(x, y, radius, segments=96, color=self._center, **kwargs)

    def _vertex_colors(self):
        rim = self._center[:3] + (0,)
        return (self._center + rim + rim) * self._segments

    def _create_vertex_list(self):
        super()._create_vertex_list()
        self._vertex_list.colors[:] = self._vertex_colors()

    def _update_color(self):
        self._vertex_list.colors[:] = self._vertex_colors()


class Glow:
    """A soft light: a few stacked radial gradients give a smooth, peaked falloff."""

    def __init__(self, x, y, radius, color, alpha, batch, group):
        a = min(255, alpha * 6)
        self.circles = [RadialGradient(x, y, radius * f, (*color[:3], int(a * w)), batch=batch, group=group)
                        for f, w in ((1.0, 0.55), (0.6, 0.3), (0.3, 0.15))]

    def move(self, x, y):
        for c in self.circles:
            c.position = (x, y)

    def delete(self):
        for c in self.circles:
            c.delete()


def check_mark(cx, cy, size, color, batch, group, width=2.2):
    return [
        shapes.Line(cx - size * 0.45, cy, cx - size * 0.1, cy - size * 0.38, width, color=color,
                    batch=batch, group=group),
        shapes.Line(cx - size * 0.12, cy - size * 0.38, cx + size * 0.5, cy + size * 0.4, width,
                    color=color, batch=batch, group=group),
    ]


def chevron(cx, cy, size, direction, color, batch, group, width=2.0):
    d = -1 if direction == "left" else 1
    tip = (cx + d * size * 0.3, cy)
    return [
        shapes.Line(cx - d * size * 0.3, cy + size * 0.5, *tip, width, color=color, batch=batch, group=group),
        shapes.Line(cx - d * size * 0.3, cy - size * 0.5, *tip, width, color=color, batch=batch, group=group),
        shapes.Circle(*tip, width / 2, color=color, batch=batch, group=group),
    ]


def reload_icon(cx, cy, size, color, batch, group):
    r = size * 0.55
    start = math.radians(60)
    arc = shapes.Arc(cx, cy, r, segments=32, angle=math.radians(290), start_angle=start,
                     thickness=2.0, color=color, batch=batch, group=group)
    # arrow head at the start of the arc
    hx, hy = cx + r * math.cos(start), cy + r * math.sin(start)
    head = shapes.Triangle(hx + 4.5, hy + 1, hx - 3.5, hy + 4.5, hx - 1, hy - 4.5,
                           color=color, batch=batch, group=group)
    return [arc, head]


def home_icon(cx, cy, size, color, batch, group):
    s = size * 0.5
    w = 2.0
    pts = [(cx - s, cy - 0.1 * s), (cx, cy + s * 0.85), (cx + s, cy - 0.1 * s)]
    out = [shapes.Line(*pts[0], *pts[1], w, color=color, batch=batch, group=group),
           shapes.Line(*pts[1], *pts[2], w, color=color, batch=batch, group=group)]
    b = cy - s * 0.85
    x1, x2, top = cx - s * 0.68, cx + s * 0.68, cy + s * 0.1
    out += [shapes.Line(x1, top, x1, b, w, color=color, batch=batch, group=group),
            shapes.Line(x2, top, x2, b, w, color=color, batch=batch, group=group),
            shapes.Line(x1 - 1, b, x2 + 1, b, w, color=color, batch=batch, group=group),
            shapes.Circle(*pts[1], w / 2, color=color, batch=batch, group=group)]
    return out


def bookmark_icon(cx, cy, size, color, batch, group, width=2.0):
    """A ribbon bookmark: a tall rectangle with a notch cut into its foot."""
    s, top, bot = size * 0.32, cy + size * 0.5, cy - size * 0.5
    pts = [(cx - s, top), (cx - s, bot), (cx, bot + size * 0.3), (cx + s, bot), (cx + s, top)]
    out = [shapes.Line(*pts[i], *pts[i + 1], width, color=color, batch=batch, group=group)
           for i in range(len(pts) - 1)]
    out.append(shapes.Line(cx - s - width / 2, top, cx + s + width / 2, top, width, color=color,
                           batch=batch, group=group))
    out += [shapes.Circle(*p, width / 2, color=color, batch=batch, group=group) for p in pts[1:4]]
    return out


def star(cx, cy, radius, color, batch, group):
    # Spikes start at angle 0; rotating 54° clockwise points one straight up.
    return shapes.Star(cx, cy, radius, radius * 0.45, 5, rotation=54, color=color, batch=batch, group=group)


def outline_icon(cx, cy, size, color, batch, group, width=2.0):
    """A table-of-contents glyph: a heading line over two indented entries."""
    s = size * 0.5
    rows = [(cy + s * 0.62, cx - s, cx + s), (cy, cx - s * 0.35, cx + s), (cy - s * 0.62, cx - s * 0.35, cx + s * 0.6)]
    out = []
    for y, x1, x2 in rows:
        out.append(shapes.Line(x1, y, x2, y, width, color=color, batch=batch, group=group))
        out.append(shapes.Circle(x1, y, width / 2, color=color, batch=batch, group=group))
        out.append(shapes.Circle(x2, y, width / 2, color=color, batch=batch, group=group))
    return out
