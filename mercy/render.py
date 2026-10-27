"""Markdown layout engine.

A Markdown string is parsed with markdown-it into a syntax tree, then laid out
top-to-bottom into pyglet labels, sprites and shapes inside one Batch.

Document coordinates: x grows to the right from the left edge of the text
column, y grows *downward* from the top of the page. Objects are created at
pyglet y = -doc_y, and the browser scrolls the page by translating the view.
"""

import html
import re
from dataclasses import dataclass, field, replace

import pyglet
from pyglet import shapes
from pyglet.graphics import Batch, Group
from pyglet.text.runlist import RunList, _Run
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

from . import gfx
from .themes import Theme, mix, with_alpha

try:
    from pygments.lexers import get_lexer_by_name
    from pygments.util import ClassNotFound
    from pygments import token as T
except ImportError:  # highlighting is optional
    get_lexer_by_name = None

_md = MarkdownIt("commonmark", {"html": True}).enable("table").enable("strikethrough")
# markdown-it refuses file: links by default; Mercy opens local files, so allow
# everything except script-running schemes.
_md.validateLink = lambda url: not url.strip().lower().startswith(("javascript:", "vbscript:"))

HEADING_SCALE = {1: 2.15, 2: 1.6, 3: 1.3, 4: 1.12, 5: 1.0, 6: 0.92}
CALLOUTS = ("NOTE", "TIP", "IMPORTANT", "WARNING", "CAUTION")
URL_RE = re.compile(r"(https?://[^\s<>()\[\]]+[^\s<>()\[\].,;:!?\"'])")
TASK_RE = re.compile(r"^\[([ xX])\]\s+")
SHORTCODE_RE = re.compile(r":([a-z0-9_+-]+):")
# GitHub emoji shortcodes, mapped to symbols that ordinary text fonts carry
SHORTCODES = {"exclamation": "!", "heavy_exclamation_mark": "!", "warning": "⚠", "white_check_mark": "✓",
              "heavy_check_mark": "✓", "x": "✗", "star": "★", "star2": "★", "arrow_right": "→",
              "arrow_left": "←", "point_right": "→", "heart": "♥", "sparkles": "✦", "information_source": "ℹ",
              "question": "?", "bulb": "※", "memo": "✎", "pencil2": "✎", "zap": "ϟ", "tada": "✦",
              "rocket": "↑", "construction": "⚠", "+1": "+1", "thumbsup": "+1"}


@dataclass(frozen=True)
class Style:
    role: str = "body"              # body | heading | mono
    size: float = 16
    bold: bool = False
    italic: bool = False
    color: tuple = (255, 255, 255, 255)
    link: int | None = None
    code: bool = False
    kbd: bool = False
    strike: bool = False


@dataclass
class Piece:
    kind: str                       # word | space | img | break
    text: str = ""
    style: Style | None = None
    width: float = 0
    font: object = None
    lpad: float = 0                 # inline-code pill padding
    rpad: float = 0
    img: dict | None = None


@dataclass
class Link:
    href: str
    rects: list = field(default_factory=list)       # (x, y, w, h) in doc coords
    labels: list = field(default_factory=list)
    lines: list = field(default_factory=list)
    icons: list = field(default_factory=list)       # shapes tinted like the link text
    bgs: list = field(default_factory=list)         # tiles that light up on hover
    color: tuple = (0, 0, 0, 255)
    hover: tuple = (0, 0, 0, 255)


@dataclass
class TextRun:
    """A stretch of text on one visual line, for selection and copying."""
    x: float
    top: float
    h: float
    text: str
    font: object
    sep: str                 # joins this run to the previous one when copied
    _edges: list | None = None

    @property
    def edges(self):
        """x offsets of each character boundary (len(text) + 1 values)."""
        if self._edges is None:
            xs, acc = [0.0], 0.0
            for g in self.font.get_glyphs(self.text):
                acc += g.advance
                xs.append(acc)
            xs += [acc] * (len(self.text) + 1 - len(xs))
            self._edges = xs
        return self._edges

    @property
    def width(self):
        return self.edges[-1]

    def index_at(self, dx):
        e = self.edges
        for i in range(len(self.text)):
            if dx < (e[i] + e[i + 1]) / 2:
                return i
        return len(self.text)


SEP_RANK = {"": 0, " ": 1, "\t": 2, "\n": 3, "\n\n": 4}


DOT_ITEM_RE = re.compile(r"^((?:[ \t]*>)*)([ \t]*)\. +(?=\S)")
QUOTE_RE = re.compile(r"^((?:[ \t]*>)*)([ \t]*)")
FENCE_RE = re.compile(r"^(?:[ \t]*>)*[ \t]*(```|~~~)")


def auto_number(markdown: str) -> str:
    """Turn lines beginning with ". " into numbered list items.

    Each marker becomes "1. " and the renderer numbers the rest of the list.
    That marker is one character wider than ". ", so lines nested under it are
    shifted right by one per enclosing item to stay inside it.
    """
    out = []
    stack = []          # indents of the ". " items we're inside
    fence = None
    prev_blank = False
    level = ""          # the blockquote prefix the current list lives in
    for line in markdown.split("\n"):
        quote, ws = QUOTE_RE.match(line).groups()
        if quote.replace(" ", "") != level:
            stack, level = [], quote.replace(" ", "")
        indent = len(ws.expandtabs(4))
        body = line[len(quote) + len(ws):]
        m = None if fence else DOT_ITEM_RE.match(line)
        if m:
            while stack and stack[-1] >= indent:
                stack.pop()
            out.append(f"{quote}{' ' * (indent + len(stack))}1. {line[m.end():]}")
            stack.append(indent)
            prev_blank = False
            continue
        if stack and body.strip():
            if indent == 0 and prev_blank and not fence:
                stack = []      # an unindented paragraph after a gap ends the list
            else:
                extra = sum(1 for s_ in stack if s_ < indent)
                line = f"{quote}{' ' * (indent + extra)}{body}"
        f = FENCE_RE.match(line)
        if f:
            fence = None if fence == f[1] else (fence or f[1])
        prev_blank = not body.strip()
        out.append(line)
    return "\n".join(out)


def slugify(text: str) -> str:
    s = re.sub(r"[^\w\- ]", "", text.strip().lower())
    return re.sub(r" ", "-", s)


def plain_text(node) -> str:
    if node.type in ("text", "code_inline"):
        return node.content
    if node.type in ("softbreak", "hardbreak"):
        return " "
    return "".join(plain_text(c) for c in node.children)


class Page:
    def __init__(self, markdown: str, theme: Theme, width: float, base_size: float,
                 images: dict, resolve=lambda src: src):
        self.theme = theme
        self.resolve = resolve
        self.width = width
        self.S = base_size
        self.images = images
        self.batch = Batch()
        self.g_card = Group(order=0)     # the page card and its shadow
        self.g_block = Group(order=1)    # code / table / quote panels
        self.g_rows = Group(order=2)     # table stripes and rules
        self.g_bg = Group(order=3)       # inline pills
        self.g_deco = Group(order=4)
        self.g_sel = Group(order=5)      # text selection highlight, under the glyphs
        self.g_text = Group(order=6)
        self.g_top = Group(order=7)
        self.objects = []
        self.links: list[Link] = []
        self.anchors: dict[str, float] = {}
        self.headings: list[dict] = []   # for the header explorer: level, text, slug, y
        self.wanted_images: set[str] = set()
        self.copy_texts: list[str] = []
        self.runs: list[TextRun] = []    # selectable text, in document order
        self.sep_next = ""
        self.prefix_next = ""            # list marker for the next copied line
        self.title = ""
        self.pad_x = round(base_size * 3.2)
        self.pad_y = round(base_size * 3.0)

        self.y = self.pad_y
        self.pending = 0.0
        self.container_top = self.y
        self.marker = None
        self.text_color = theme.c("text")
        self.list_depth = 0

        tree = SyntaxTreeNode(_md.parse(auto_number(markdown)))
        self.children(tree, 0, width)
        self.height = self.y + self.pad_y
        self._card()

    # ------------------------------------------------------------------ utils
    def keep(self, *objs):
        for o in objs:
            if isinstance(o, list):
                self.objects.extend(o)
            else:
                self.objects.append(o)
        return objs[0] if len(objs) == 1 else objs

    def space(self, amount: float):
        self.pending = max(self.pending, amount)

    def begin(self):
        if self.y > self.container_top + 0.5:
            self.y += self.pending
        self.pending = 0.0

    def font_for(self, st: Style):
        t = self.theme
        name = {"body": t.body_font, "heading": t.heading_font, "mono": t.mono_font}[st.role]
        return name, gfx.font(name, st.size, st.bold, st.italic)

    def base_style(self, **kw) -> Style:
        return Style(size=self.S, color=self.text_color, **kw)

    def rect(self, x, doc_top, w, h, color, group=None, radius=0):
        return self.keep(gfx.rrect(x, -(doc_top + h), w, h, radius, color, self.batch, group or self.g_bg))

    def _card(self):
        t = self.theme
        x, w, h = -self.pad_x, self.width + 2 * self.pad_x, self.height
        r = self.S * 1.3
        strength = 110 if t.dark else 28
        self.keep(gfx.soft_shadow(x, -h, w, h, r, self.batch, self.g_card, strength=strength,
                                  spread=self.S * 1.6, offset=self.S * 0.5))
        self.keep(gfx.rrect(x - 1, -h - 1, w + 2, h + 2, r + 1, t.c("border", 180), self.batch, self.g_card))
        self.keep(gfx.rrect(x, -h, w, h, r, t.c("surface", t.surface_alpha), self.batch, self.g_card))
        # a thin luminous edge along the top of the card
        edge_w = w - 2 * r
        a = 70 if t.dark else 120
        self.keep(gfx.GradientRect(x + r, -1.5, edge_w / 2, 1.5, t.c("accent", 0), t.c("accent", a),
                                   vertical=False, batch=self.batch, group=self.g_bg))
        self.keep(gfx.GradientRect(x + r + edge_w / 2, -1.5, edge_w / 2, 1.5, t.c("accent2", a),
                                   t.c("accent2", 0), vertical=False, batch=self.batch, group=self.g_bg))

    # ------------------------------------------------------------- block level
    def children(self, node, x, w):
        for child in node.children:
            self.block(child, x, w)

    def block(self, node, x, w):
        handler = getattr(self, "b_" + node.type, None)
        if handler:
            handler(node, x, w)

    def b_paragraph(self, node, x, w):
        tight = node.hidden
        self.begin()
        items = self.inline(node.children[0], self.base_style()) if node.children else []
        if self.marker and self.marker.get("task") is not None:
            items = self.strip_task(items)
        runs = self.align_runs(items)
        if len(runs) == 1 and runs[0][0] == "left":
            only_images = items and all(p.kind == "img" or (p.kind == "word" and not p.text.strip())
                                        for p in self.pieces(items))
            runs = [("center" if only_images else "left", items)]
        sep = "\n" if self.list_depth else "\n\n"
        for align, run in runs:
            self.flow(run, x, w, align=align, sep=sep)
            sep = "\n"
        self.space(self.S * (0.35 if tight else 1.0))

    def align_runs(self, items):
        """Split items into (align, items) runs by source line.

        A line starting with ":" is right-aligned; one that also ends with ":"
        is centered. Neighbouring plain lines are joined back together so they
        reflow as usual.
        """
        lines, cur = [], []
        for it in items:
            if it[0] in ("soft", "break"):
                lines.append((cur, it))
                cur = []
            else:
                cur.append(it)
        lines.append((cur, None))
        runs = []
        prev_sep = None
        for line, sep in lines:
            align, line = self.line_alignment(line)
            if runs and align == "left" and runs[-1][0] == "left":
                runs[-1][1].append(prev_sep)
                runs[-1][1].extend(line)
            elif line:
                runs.append((align, list(line)))
            prev_sep = sep
        return runs or [("left", [])]

    def line_alignment(self, line):
        if not line or line[0][0] != "text" or not line[0][1].lstrip().startswith(":"):
            return "left", line
        out = list(line)
        k, v, st = out[0]
        out[0] = (k, v.lstrip()[1:].lstrip(), st)
        align = "right"
        k, v, st = out[-1]
        if k == "text" and v.rstrip().endswith(":"):
            out[-1] = (k, v.rstrip()[:-1].rstrip(), st)
            align = "center"
        if not any(it[0] != "text" or it[1].strip() for it in out):
            return "left", line  # just colons: leave the text alone
        return align, out

    def b_heading(self, node, x, w):
        level = int(node.tag[1])
        S = self.S
        self.space(S * (2.0 if level <= 2 else 1.5))
        self.begin()
        text = plain_text(node)
        if not self.title and level == 1:
            self.title = text
        self.add_heading(level, text)
        st = Style(role="heading", size=S * HEADING_SCALE[level], bold=level <= 4,
                   color=self.theme.c("heading") if level < 6 else self.theme.c("muted"))
        for align, run in self.align_runs(self.inline(node.children[0], st)):
            self.flow(run, x, w, align=align, line_height=1.28)
        t = self.theme
        if level == 1:
            self.y += S * 0.45
            bar = S * 0.18
            self.keep(gfx.GradientRect(x, -(self.y + bar), w * 0.35, bar, t.c("accent"), t.c("accent2"),
                                       vertical=False, batch=self.batch, group=self.g_deco))
            self.keep(gfx.GradientRect(x + w * 0.35, -(self.y + bar), w * 0.65, bar, t.c("accent2"),
                                       t.c("accent2", 0), vertical=False, batch=self.batch, group=self.g_deco))
            self.y += bar
            self.space(S * 1.2)
        elif level == 2:
            self.y += S * 0.4
            self.rect(x, self.y, w, 1, t.c("border"), self.g_deco)
            self.y += 1
            self.space(S * 1.0)
        else:
            self.space(S * 0.6)

    def add_heading(self, level, text):
        """Give a heading an anchor and list it for the header explorer."""
        slug = base = slugify(text)
        n = 1
        while slug in self.anchors:
            slug = f"{base}-{n}"
            n += 1
        self.anchors[slug] = self.y
        label = re.sub(r"\s+", " ", text).strip()
        if label.startswith(":"):  # colon-aligned heading: drop the markers
            label = label.strip(":").strip()
        if label:
            self.headings.append(dict(level=level, text=label, slug=slug, y=self.y))
        return slug

    def b_hr(self, node, x, w):
        self.space(self.S * 1.6)
        self.begin()
        t = self.theme
        h = 2
        self.keep(gfx.GradientRect(x, -(self.y + h), w / 2, h, t.c("accent", 0), t.c("accent", 200),
                                   vertical=False, batch=self.batch, group=self.g_deco))
        self.keep(gfx.GradientRect(x + w / 2, -(self.y + h), w / 2, h, t.c("accent2", 200),
                                   t.c("accent2", 0), vertical=False, batch=self.batch, group=self.g_deco))
        d = self.S * 0.32
        self.keep(shapes.Rectangle(x + w / 2, -(self.y + h / 2), d, d, color=t.c("accent2"),
                                   batch=self.batch, group=self.g_text))
        self.objects[-1].anchor_position = (d / 2, d / 2)
        self.objects[-1].rotation = 45
        self.y += h
        self.space(self.S * 1.6)

    def b_bullet_list(self, node, x, w, ordered=False):
        S = self.S
        self.begin()
        indent = S * 1.7
        start = int(node.attrs.get("start", 1)) if ordered else 1
        depth = self.list_depth
        self.list_depth += 1
        if not depth:
            self.queue_sep("\n\n")
        for i, item in enumerate(node.children):
            self.begin()
            self.container_top = self.y
            task = self.task_state(item)
            mark = f"{start + i}." if ordered else "-"
            if task is not None:
                mark += " [x]" if task else " [ ]"
            self.prefix_next = "  " * depth + mark + " "
            self.marker = dict(x=x, indent=indent, ordered=ordered, n=start + i, depth=depth, task=task)
            saved = self.text_color
            if task:
                self.text_color = self.theme.c("muted")
            self.children(item, x + indent, w - indent)
            self.text_color = saved
            self.marker = None
            self.space(S * 0.35)
        self.list_depth -= 1
        self.space(S * (0.35 if depth else 1.0))

    def b_ordered_list(self, node, x, w):
        self.b_bullet_list(node, x, w, ordered=True)

    def task_state(self, item):
        try:
            first = item.children[0].children[0].children[0]
        except IndexError:
            return None
        if first.type == "text":
            m = TASK_RE.match(first.content)
            if m:
                return m[1].lower() == "x"
        return None

    def strip_task(self, items):
        for i, it in enumerate(items):
            if it[0] == "text":
                items[i] = ("text", TASK_RE.sub("", it[1], count=1), it[2])
                if self.marker["task"]:
                    items[i] = ("text", items[i][1], replace(it[2], strike=True))
                break
        return items

    def draw_marker(self, baseline, asc, line_top, line_h):
        m, self.marker = self.marker, None
        t, S = self.theme, self.S
        cx = m["x"] + m["indent"] * 0.45
        mid = baseline - asc * 0.36
        if m["task"] is not None:
            size = S * 1.0
            bx, by = m["x"] + (m["indent"] - size) / 2 - S * 0.1, mid - size / 2
            if m["task"]:
                self.rect(bx, by, size, size, t.c("accent"), self.g_deco, radius=S * 0.25)
                self.keep(gfx.check_mark(bx + size / 2, -(by + size / 2), size * 0.75,
                                         t.c("surface"), self.batch, self.g_text, width=S * 0.13))
            else:
                self.rect(bx, by, size, size, t.c("muted", 200), self.g_deco, radius=S * 0.25)
                self.rect(bx + 1.5, by + 1.5, size - 3, size - 3, t.c("surface"), self.g_text,
                          radius=S * 0.2)
        elif m["ordered"]:
            name, f = self.font_for(Style(size=S * 0.95, bold=True))
            self.keep(pyglet.text.Label(f"{m['n']}.", font_name=name, font_size=S * 0.95, bold=True,
                                        color=t.c("accent"), x=round(m["x"] + m["indent"] - S * 0.5),
                                        y=round(-baseline), anchor_x="right", batch=self.batch,
                                        group=self.g_text))
        elif m["depth"] == 0:
            self.keep(shapes.Circle(cx, -mid, S * 0.17, segments=20, color=t.c("accent"),
                                    batch=self.batch, group=self.g_deco))
        elif m["depth"] == 1:
            self.keep(shapes.Arc(cx, -mid, S * 0.17, segments=24, thickness=1.6, color=t.c("accent2"),
                                 batch=self.batch, group=self.g_deco))
        else:
            d = S * 0.26
            self.rect(cx - d / 2, mid - d / 2, d, d, t.c("muted"), self.g_deco, radius=1.5)

    def b_blockquote(self, node, x, w):
        S, t = self.S, self.theme
        self.space(S * 0.4)
        self.begin()
        top = self.y
        kind = None
        try:
            first = node.children[0].children[0].children[0]
            m = re.match(r"^\[!(\w+)\]\s*$", first.content) if first.type == "text" else None
            if m and m[1].upper() in CALLOUTS:
                kind = m[1].upper()
                inline = node.children[0].children[0]
                inline.children = inline.children[2:] if len(inline.children) > 1 else []
        except (IndexError, AttributeError):
            pass
        color = t.callout(kind) if kind else t.c("accent")
        pad = S * 1.0
        bar = 4
        self.y += pad * 0.85
        if kind:
            name, f = self.font_for(Style(size=S * 0.95, bold=True))
            cx = x + bar + pad + S * 0.45
            cy = self.y + f.ascent * 0.62
            self.keep(shapes.Arc(cx, -cy, S * 0.45, segments=28, thickness=1.6, color=color,
                                 batch=self.batch, group=self.g_text))
            glyph = {"NOTE": "i", "TIP": "✓", "IMPORTANT": "!", "WARNING": "!", "CAUTION": "×"}[kind]
            self.keep(pyglet.text.Label(glyph, font_name=name, font_size=S * 0.62, bold=True, color=color,
                                        x=round(cx), y=round(-cy), anchor_x="center", anchor_y="center",
                                        batch=self.batch, group=self.g_text))
            self.keep(pyglet.text.Label(kind.title(), font_name=name, font_size=S * 0.95, bold=True,
                                        color=color, x=round(cx + S * 0.9), y=round(-(self.y + f.ascent)),
                                        batch=self.batch, group=self.g_text))
            self.y += S * 1.9
        self.container_top = self.y
        saved = self.text_color
        self.text_color = mix(t.c("text"), t.c("muted"), 0.35)
        self.children(node, x + bar + pad, w - bar - pad * 2)
        self.text_color = saved
        self.pending = 0
        self.y += pad * 0.85
        h = self.y - top
        self.rect(x, top, w, h, with_alpha(color, 22 if t.dark else 20), self.g_block, radius=S * 0.55)
        self.rect(x, top, bar, h, color, self.g_deco, radius=2)
        self.space(S * 1.1)

    def b_fence(self, node, x, w):
        S, t = self.S, self.theme
        self.space(S * 0.5)
        self.begin()
        top = self.y
        lang = (node.info or "").split()[0] if node.info else ""
        code = node.content.rstrip("\n").expandtabs(4)
        pad = S * 1.1
        head = S * 2.3
        radius = S * 0.7

        # header: language on the left, a copy button on the right
        hy = top + head / 2
        small = S * 0.75
        name, f = self.font_for(Style(role="body", size=small, bold=True))
        if lang:
            self.keep(pyglet.text.Label(lang.upper(), font_name=name, font_size=small, bold=True,
                                        color=t.c("muted"), x=round(x + pad),
                                        y=round(-(hy + f.ascent * 0.36)), batch=self.batch, group=self.g_text))
        self.copy_texts.append(code)
        link = Link(f"mercy-copy:{len(self.copy_texts) - 1}", color=t.c("muted"), hover=t.c("accent"))
        self.copy_button(link, x + w - pad * 0.55, hy)
        self.links.append(link)

        # body
        size = S * 0.86
        # Wrap by character ourselves (the font is monospaced), so every visual
        # line has known character positions for selection and copying.
        mono = gfx.font(t.mono_font, size)
        cols = max(8, int((w - 2 * pad) // max(1.0, gfx.text_width(mono, "M"))))
        chunks, inserts, pos = [], [], 0          # chunks: (source offset, text, starts a source line)
        for src_line in code.split("\n"):
            for k in range(0, max(1, len(src_line)), cols):
                if k:
                    inserts.append(pos + k)
                chunks.append((pos + k, src_line[k:k + cols], k == 0))
            pos += len(src_line) + 1
        ls = round(size * 1.65)
        doc = pyglet.text.document.FormattedDocument("\n".join(c[1] for c in chunks) or " ")
        doc.set_style(0, len(doc.text), dict(font_name=t.mono_font, font_size=size,
                                             color=t.c("code_text"), line_spacing=ls))
        self.highlight(doc, code, lang, inserts)
        body_top = top + head + pad * 0.35
        layout = pyglet.text.layout.TextLayout(doc, x=round(x + pad), y=round(-body_top), width=w - 2 * pad,
                                               multiline=True, wrap_lines=False, anchor_y="top",
                                               batch=self.batch, group=self.g_text)
        self.keep(layout)
        lead = (ls - (mono.ascent - mono.descent)) / 2
        for i, (_, text, new_line) in enumerate(chunks):
            baseline = body_top + (i + 1) * ls
            self.add_run(x + pad, baseline - mono.ascent - lead, ls, text, mono,
                         "\n\n" if i == 0 else ("\n" if new_line else ""), keep_empty=True)
        self.y = body_top + layout.content_height + pad * 0.9
        h = self.y - top
        if t.dark:
            self.keep(gfx.soft_shadow(x, -(top + h), w, h, radius, self.batch, self.g_card, strength=70,
                                      spread=S * 0.8, offset=S * 0.3))
        else:
            self.keep(gfx.soft_shadow(x, -(top + h), w, h, radius, self.batch, self.g_card, strength=22,
                                      spread=S * 0.7, offset=S * 0.25, color=gfx_tint(t)))
        self.rect(x - 1, top - 1, w + 2, h + 2, t.c("border"), self.g_card, radius=radius + 1)
        self.rect(x, top, w, h, t.c("code_bg"), self.g_block, radius=radius)
        self.rect(x, top + head, w, 1, t.c("border"), self.g_rows)
        self.space(S * 1.2)

    b_code_block = b_fence

    def copy_button(self, link, right, cy):
        """The classic copy glyph: two overlapping rounded squares, in a hover tile."""
        t, S = self.theme, self.S
        tile = S * 1.75
        tx, ty = right - tile, cy - tile / 2
        bg = self.rect(tx, ty, tile, tile, t.c("text", 0), self.g_rows, radius=S * 0.4)
        sq, r, line = S * 0.72, S * 0.18, max(1.5, S * 0.1)
        off = S * 0.28
        cx = tx + tile / 2
        # back square (up and to the right), then the front square on top of it
        for (sx, sy), groups in (((cx - sq / 2 + off / 2, cy - sq / 2 - off / 2), (self.g_bg, self.g_deco)),
                                 ((cx - sq / 2 - off / 2, cy - sq / 2 + off / 2), (self.g_sel, self.g_text))):
            edge = self.rect(sx, sy, sq, sq, link.color, groups[0], radius=r)
            self.rect(sx + line, sy + line, sq - 2 * line, sq - 2 * line, t.c("code_bg"), groups[1],
                      radius=max(0.5, r - line))
            link.icons.append(edge)
        link.bgs.append(bg)
        link.rects.append((tx, ty, tile, tile))

    def highlight(self, doc, code, lang, inserts=()):
        if not (get_lexer_by_name and lang and code):
            return
        try:
            lexer = get_lexer_by_name(lang, stripnl=False, ensurenl=False)
        except ClassNotFound:
            return
        # Build the color/italic run lists directly: calling set_style once per
        # token is quadratic in pyglet, which crawls on large source files.
        t = self.theme
        default = t.c("code_text")
        colors, italics = [], []
        for ttype, value in lexer.get_tokens(code):
            n = len(value)
            if not n:
                continue
            kind = _token_kind(ttype)
            color = t.syntax_color(kind) if kind else default
            ital = kind == "comment"
            for runs, v in ((colors, color), (italics, ital)):
                if runs and runs[-1].value == v:
                    runs[-1].count += n
                else:
                    runs.append(_Run(v, n))
        # Account for the newlines our own wrapping inserted at `inserts` (source
        # offsets): each one lengthens whichever run holds that offset.
        for runs in (colors, italics):
            extra = [0] * len(runs)
            i, start = 0, 0
            for k in inserts:
                while i < len(runs) - 1 and start + runs[i].count <= k:
                    start += runs[i].count
                    i += 1
                extra[i] += 1
            for r, e in zip(runs, extra):
                r.count += e
        total = len(doc.text)
        for name, runs in (("color", colors), ("italic", italics)):
            size = sum(r.count for r in runs)
            if size != total:  # lexer changed the text length; fall back to plain
                return
            rl = RunList(total, None)
            rl.runs = runs
            doc._style_runs[name] = rl

    def b_table(self, node, x, w):
        S, t = self.S, self.theme
        self.space(S * 0.5)
        self.begin()
        rows = []
        for section in node.children:
            for tr in section.children:
                cells = []
                for cell in tr.children:
                    align = "left"
                    m = re.search(r"text-align:(\w+)", cell.attrs.get("style", "") or "")
                    if m:
                        align = m[1]
                    header = cell.type == "th"
                    st = self.base_style(bold=header)
                    if header:
                        st = replace(st, color=t.c("heading"))
                    items = self.inline(cell.children[0], st) if cell.children else []
                    cells.append((items, align))
                rows.append((cells, section.type == "thead"))
        if not rows:
            return
        ncols = max(len(r[0]) for r in rows)
        px, py = S * 0.85, S * 0.55
        nat = [0.0] * ncols
        mins = [0.0] * ncols
        for cells, _ in rows:
            for c, (items, _) in enumerate(cells):
                total, longest = 0.0, 0.0
                for p in self.pieces(items):
                    total += p.width + p.lpad + p.rpad
                    if p.kind != "space":
                        longest = max(longest, p.width + p.lpad + p.rpad)
                nat[c] = max(nat[c], total + 2 * px + 1)
                mins[c] = max(mins[c], longest + 2 * px + 1)
        if sum(nat) <= w:
            widths = nat
        else:
            extra = w - sum(mins)
            flex = sum(n - m for n, m in zip(nat, mins)) or 1
            widths = [m + max(0, extra) * (n - m) / flex for n, m in zip(nat, mins)]
        tw = sum(widths)
        top = self.y
        r = S * 0.55
        row_objs = []
        for ri, (cells, header) in enumerate(rows):
            row_top = self.y
            bottom = row_top
            cx = x
            for c in range(ncols):
                items, align = cells[c] if c < len(cells) else ([], "left")
                self.y = row_top + py
                self.flow(items, cx + px, widths[c] - 2 * px, align=align, line_height=1.5,
                          sep="\t" if c else ("\n" if ri else "\n\n"))
                bottom = max(bottom, self.y)
                cx += widths[c]
            self.y = bottom + py
            row_objs.append((row_top, self.y - row_top, header, ri))
        h = self.y - top
        self.rect(x - 1, top - 1, tw + 2, h + 2, t.c("border"), self.g_card, radius=r + 1)
        self.rect(x, top, tw, h, t.c("code_bg"), self.g_block, radius=r)
        for i, (rt, rh, header, ri) in enumerate(row_objs):
            last = i == len(row_objs) - 1
            if header:
                self.keep(shapes.RoundedRectangle(x, -(rt + rh), tw, rh, (0, r, r, 0), segments=8,
                                                  color=t.c("accent", 30 if t.dark else 26),
                                                  batch=self.batch, group=self.g_rows))
            elif ri % 2 == 0:
                radius = (r, 0, 0, r) if last else 0
                if radius:
                    self.keep(shapes.RoundedRectangle(x, -(rt + rh), tw, rh, radius, segments=8,
                                                      color=t.c("text", 7), batch=self.batch,
                                                      group=self.g_rows))
                else:
                    self.rect(x, rt, tw, rh, t.c("text", 7), self.g_rows)
            if not last:
                self.rect(x, rt + rh - 0.5, tw, 1, t.c("border"), self.g_rows)
        cx = x
        for cw in widths[:-1]:
            cx += cw
            self.rect(cx, top, 1, h, t.c("border", 150), self.g_rows)
        self.space(S * 1.2)

    def b_html_block(self, node, x, w):
        content = re.sub(r"<!--.*?-->", "", node.content, flags=re.S)
        if not content.strip():
            return
        self.begin()
        items = self.html_items(content, self.base_style())
        if not items:
            return
        center = re.search(r"align\s*=\s*[\"']?center", content, re.I) is not None
        heading = re.match(r"\s*<h([1-6])", content, re.I)
        if heading:
            lvl = int(heading[1])
            self.add_heading(lvl, "".join(v for k, v, _ in items if k == "text"))
            st = Style(role="heading", size=self.S * HEADING_SCALE[lvl], bold=True, color=self.theme.c("heading"))
            items = [(k, v, replace(s, role="heading", size=st.size, bold=True, color=st.color))
                     if k == "text" else (k, v, s) for k, v, s in items]
        self.flow(items, x, w, align="center" if center else "left",
                  line_height=1.3 if heading else 1.65)
        self.space(self.S)

    # ---------------------------------------------------------------- inline
    def inline(self, node, style: Style) -> list:
        """Flatten an inline node into ('text'|'img'|'break', value, style) items."""
        out = []
        extra = {}
        for ch in node.children:
            st = replace(style, **extra) if extra else style
            typ = ch.type
            if typ == "text":
                content = SHORTCODE_RE.sub(lambda m: SHORTCODES.get(m[1], m[0]), ch.content)
                parts = URL_RE.split(content) if st.link is None else [content]
                for i, part in enumerate(parts):
                    if i % 2:
                        out.append(("text", part, self.new_link(part, st)))
                    elif part:
                        out.append(("text", part, st))
            elif typ == "softbreak":
                out.append(("soft", None, st))
            elif typ == "hardbreak":
                out.append(("break", None, st))
            elif typ == "strong":
                out += self.inline(ch, replace(st, bold=True))
            elif typ == "em":
                out += self.inline(ch, replace(st, italic=True))
            elif typ == "s":
                out += self.inline(ch, replace(st, strike=True))
            elif typ == "code_inline":
                out.append(("text", ch.content, self.code_style(st)))
            elif typ == "link":
                out += self.inline(ch, self.new_link(ch.attrs.get("href", ""), st))
            elif typ == "image":
                out.append(("img", dict(src=ch.attrs.get("src", ""), alt=plain_text(ch),
                                        width=None, link=st.link), st))
            elif typ == "html_inline":
                tag = ch.content.lower()
                m = re.match(r"<(/?)(\w+)", tag)
                if not m:
                    continue
                closing, name = m[1] == "/", m[2]
                flags = {"b": "bold", "strong": "bold", "i": "italic", "em": "italic", "s": "strike",
                         "del": "strike", "kbd": "kbd", "code": "code"}
                if name == "br":
                    out.append(("break", None, st))
                elif name == "img":
                    out += self.html_items(ch.content, st)
                elif name in flags:
                    key = flags[name]
                    if closing:
                        extra.pop(key, None)
                        if key in ("kbd", "code"):
                            extra.pop("role", None), extra.pop("size", None), extra.pop("color", None)
                    elif key in ("kbd", "code"):
                        cs = self.code_style(style)
                        extra.update(role="mono", size=cs.size, color=cs.color, **{key: True})
                    else:
                        extra[key] = True
        return out

    def html_items(self, content: str, st: Style) -> list:
        out = []
        pos = 0
        tag_re = re.compile(r"<img\b[^>]*>|<br\s*/?>|<a\b[^>]*>|</a>|</?p\b[^>]*>|</h\d>|</div>|<[^>]+>", re.I)
        link_st = None
        for m in tag_re.finditer(content):
            text = html.unescape(re.sub(r"\s+", " ", content[pos:m.start()]))
            if text.strip():
                out.append(("text", text, link_st or st))
            pos = m.end()
            tag = m[0]
            low = tag.lower()
            if low.startswith("<img"):
                src = _attr(tag, "src")
                if src:
                    width = _attr(tag, "width")
                    try:
                        width = float(width.rstrip("px")) if width and not width.endswith("%") else None
                    except ValueError:
                        width = None
                    cur = link_st or st
                    out.append(("img", dict(src=src, alt=_attr(tag, "alt") or "", width=width,
                                            link=cur.link), cur))
                    out.append(("text", " ", cur))
            elif low.startswith("<br") or low.startswith("</p") or low.startswith("</h") or low == "</div>":
                if out:
                    out.append(("break", None, st))
            elif low.startswith("<a"):
                href = _attr(tag, "href")
                link_st = self.new_link(href, st) if href else None
            elif low == "</a>":
                link_st = None
        text = html.unescape(re.sub(r"\s+", " ", content[pos:]))
        if text.strip():
            out.append(("text", text, st))
        while out and out[-1][0] == "break":
            out.pop()
        return out

    def code_style(self, st: Style) -> Style:
        t = self.theme
        color = st.color if st.link is not None else mix(t.c("text"), t.c("accent"), 0.4)
        return replace(st, role="mono", size=st.size * 0.86, code=True, color=color)

    def new_link(self, href: str, st: Style) -> Style:
        t = self.theme
        self.links.append(Link(href, color=t.c("link"), hover=t.c("accent2")))
        return replace(st, link=len(self.links) - 1, color=t.c("link"))

    # ----------------------------------------------------------- line layout
    def pieces(self, items) -> list[Piece]:
        out = []
        S = self.S
        for kind, value, st in items:
            if kind == "soft":
                kind, value = "text", " "
            if kind == "break":
                out.append(Piece("break", style=st))
                continue
            if kind == "img":
                out.append(self.image_piece(value, st))
                continue
            name, f = self.font_for(st)
            tokens = re.findall(r"\s+|\S+", value)
            pill = st.code or st.kbd
            start = len(out)
            for tok in tokens:
                if tok.isspace():
                    out.append(Piece("space", " ", st, gfx.text_width(f, " "), f))
                else:
                    out.append(Piece("word", tok, st, gfx.text_width(f, tok), f))
            if pill and len(out) > start:
                words = [p for p in out[start:] if p.kind == "word"]
                if words:
                    words[0].lpad = S * 0.32
                    words[-1].rpad = S * 0.32
        return out

    def image_piece(self, info, st) -> Piece:
        src = self.resolve(info["src"])
        tex = self.images.get(src)
        maxw = self.width
        if tex is None:
            self.wanted_images.add(src)
            w, h = min(maxw, self.S * 18), self.S * 10
            if info.get("width"):
                w = min(maxw, info["width"])
                h = w * 0.5
        elif tex == "error":
            name, f = self.font_for(self.code_style(st))
            label = info["alt"] or "image"
            return Piece("img", label, st, gfx.text_width(f, label) + self.S * 0.8, f, img=dict(info, broken=True,
                         h=self.S * 1.6))
        else:
            w, h = tex.width, tex.height
            if info.get("width"):
                h, w = h * info["width"] / w, info["width"]
            if w > maxw:
                h, w = h * maxw / w, maxw
        return Piece("img", info["alt"], st, w, img=dict(info, h=h, tex=tex if tex not in (None, "error") else None))

    def flow(self, items, x, width, align="left", line_height=1.65, sep="\n\n"):
        """Lay out inline items as wrapped lines. `sep` is what copied text puts
        between this block and whatever was selected before it."""
        pieces = self.pieces(items)
        lines, cur, cur_w = [], [], 0.0
        line_sep = sep

        def push(after=" "):
            nonlocal cur, cur_w, line_sep
            while cur and cur[-1].kind == "space":
                cur_w -= cur.pop().width
            lines.append((cur, cur_w, line_sep))
            cur, cur_w, line_sep = [], 0.0, after

        for p in pieces:
            if p.kind == "break":
                push("\n")
                continue
            if p.kind == "space":
                if cur and cur[-1].kind != "space":
                    cur.append(p)
                    cur_w += p.width
                continue
            pw = p.width + p.lpad + p.rpad
            if cur and cur_w + pw > width:
                push()
            if p.kind == "word" and pw > width and len(p.text) > 1:
                # a single unbreakable word longer than the line: split it by characters
                for chunk in self.split_word(p, width):
                    if cur and cur_w + chunk.width > width:
                        push("")
                    cur.append(chunk)
                    cur_w += chunk.width
                continue
            cur.append(p)
            cur_w += pw
        if cur or not lines:
            push()
        for line, lw, line_sep in lines:
            self.queue_sep(line_sep)
            self.render_line(line, lw, x, width, align, line_height)

    def split_word(self, p: Piece, width):
        chunks, buf = [], ""
        for ch in p.text:
            if buf and gfx.text_width(p.font, buf + ch) > width:
                chunks.append(Piece("word", buf, p.style, gfx.text_width(p.font, buf), p.font))
                buf = ""
            buf += ch
        if buf:
            chunks.append(Piece("word", buf, p.style, gfx.text_width(p.font, buf), p.font))
        return chunks

    def render_line(self, line, lw, x, width, align, line_height):
        S, t = self.S, self.theme
        asc, desc, box = 0.0, 0.0, S * line_height
        for p in line:
            if p.kind == "img":
                asc = max(asc, p.img["h"] if not p.img.get("broken") else p.font.ascent)
            else:
                asc = max(asc, p.font.ascent)
                desc = min(desc, p.font.descent)
                box = max(box, p.style.size * line_height)
        if not line:
            name, f = self.font_for(self.base_style())
            asc, desc = f.ascent, f.descent
        img_line = any(p.kind == "img" for p in line)
        line_h = max(box, asc - desc + (S * 0.4 if img_line else 0))
        top = self.y
        baseline = top + (line_h - (asc - desc)) / 2 + asc
        if self.marker:
            self.draw_marker(baseline, asc if not img_line else S * 0.75, top, line_h)

        cx = x + {"left": 0, "center": (width - lw) / 2, "right": width - lw}.get(align, 0)
        cx = max(x, cx)
        # group consecutive pieces sharing a style into single labels
        seg, seg_x = [], cx
        segments = []
        for p in line:
            if p.kind == "img":
                if seg:
                    segments.append((seg_x, seg))
                    seg = []
                segments.append((cx, [p]))
                cx += p.width
                seg_x = cx
                continue
            if seg and (p.style != seg[0].style or p.lpad):
                segments.append((seg_x, seg))
                seg, seg_x = [], cx
            if not seg:
                seg_x = cx
            seg.append(p)
            cx += p.width + p.lpad + p.rpad
        if seg:
            segments.append((seg_x, seg))

        for sx, seg in segments:
            p0 = seg[0]
            if p0.kind == "img":
                self.render_image(p0, sx, baseline, top, line_h)
                continue
            st = p0.style
            text = "".join(p.text for p in seg)
            w = sum(p.width + p.lpad + p.rpad for p in seg)
            name = self.font_for(st)[0]
            f = p0.font
            if st.code or st.kbd:
                pad_y = S * 0.18
                ptop = baseline - f.ascent - pad_y
                ph = f.ascent - f.descent + 2 * pad_y
                if st.kbd:
                    self.rect(sx, ptop, w, ph + 2, t.c("border"), self.g_bg, radius=S * 0.25)
                    self.rect(sx + 1, ptop + 1, w - 2, ph - 1, t.c("code_bg"), self.g_deco, radius=S * 0.22)
                else:
                    self.rect(sx, ptop, w, ph, t.c("text", 20 if t.dark else 16), self.g_bg, radius=S * 0.3)
            label = self.keep(pyglet.text.Label(text, font_name=name, font_size=st.size, bold=st.bold,
                                                italic=st.italic, color=st.color, x=round(sx + p0.lpad),
                                                y=round(-baseline), batch=self.batch, group=self.g_text))
            self.add_run(round(sx + p0.lpad), top, line_h, text, f)
            if st.strike:
                self.rect(sx, baseline - f.ascent * 0.32, w, max(1, S * 0.08), with_alpha(st.color, 200),
                          self.g_top)
            if st.link is not None:
                link = self.links[st.link]
                link.labels.append(label)
                link.rects.append((sx, top, w, line_h))
                if not (st.code or st.kbd):
                    ul = self.rect(sx, baseline + max(2, S * 0.14), w, max(1, S * 0.07),
                                   with_alpha(st.color, 90), self.g_deco)
                    link.lines.append(ul)
        self.y = top + line_h

    def render_image(self, p, x, baseline, top, line_h):
        t, S = self.theme, self.S
        info = p.img
        if info.get("broken"):
            h = S * 1.5
            itop = baseline - p.font.ascent - S * 0.3
            self.rect(x + 2, itop, p.width - 4, h, t.c("muted", 40), self.g_bg, radius=S * 0.3)
            self.keep(pyglet.text.Label(p.text, font_name=t.mono_font, font_size=S * 0.75,
                                        color=t.c("muted"), x=round(x + S * 0.4),
                                        y=round(-(itop + h / 2)), anchor_y="center",
                                        batch=self.batch, group=self.g_text))
            if info.get("link") is not None:
                self.links[info["link"]].rects.append((x, itop, p.width, h))
            return
        w, h = p.width, info["h"]
        itop = baseline - h
        tex = info.get("tex")
        if tex is None:
            # still loading: a softly shimmering placeholder
            self.rect(x + 2, itop, w - 4, h, t.c("muted", 30), self.g_bg, radius=S * 0.5)
            self.keep(pyglet.text.Label(info["alt"] or "loading image…", font_name=t.body_font,
                                        font_size=S * 0.8, color=t.c("muted"), x=round(x + w / 2),
                                        y=round(-(itop + h / 2)), anchor_x="center", anchor_y="center",
                                        width=None, batch=self.batch, group=self.g_text))
        else:
            if h > S * 6:
                self.keep(gfx.soft_shadow(x, -(itop + h), w, h, 6, self.batch, self.g_bg,
                                          strength=90 if t.dark else 40, spread=S, offset=S * 0.4))
            sprite = pyglet.sprite.Sprite(tex, x=round(x), y=round(-(itop + h)), batch=self.batch,
                                          group=self.g_deco)
            sprite.scale_x = w / tex.width
            sprite.scale_y = h / tex.height
            self.keep(sprite)
        if info.get("link") is not None:
            self.links[info["link"]].rects.append((x, itop, w, h))

    # -------------------------------------------------------------- selection
    def queue_sep(self, sep):
        if SEP_RANK.get(sep, 0) > SEP_RANK.get(self.sep_next, 0):
            self.sep_next = sep

    def add_run(self, x, top, h, text, font, sep=None, keep_empty=False):
        if sep is not None:
            self.queue_sep(sep)
        if not text and not keep_empty:
            return
        lead = (self.sep_next if self.runs else "") + self.prefix_next
        self.runs.append(TextRun(x, top, h, text, font, lead))
        self.sep_next = self.prefix_next = ""

    def text_at(self, dx, dy, slack=0.0):
        """Is there selectable text under this point?"""
        for r in self.runs:
            if r.top <= dy < r.top + r.h and r.x - slack <= dx <= r.x + r.width + slack:
                return True
        return False

    def caret_at(self, dx, dy):
        """The (run, char) position nearest to a document point."""
        if not self.runs:
            return None
        best, best_d = None, None
        for i, r in enumerate(self.runs):
            if r.top <= dy < r.top + r.h:
                if dx < r.x:
                    cand, d = (i, 0), r.x - dx
                elif dx > r.x + r.width:
                    cand, d = (i, len(r.text)), dx - r.x - r.width
                else:
                    return i, r.index_at(dx - r.x)
                if best_d is None or d < best_d:
                    best, best_d = cand, d
        if best:
            return best
        # between lines: snap to the start of the next text below the point
        below = [(r.top, i) for i, r in enumerate(self.runs) if r.top > dy]
        if below:
            return min(below)[1], 0
        return len(self.runs) - 1, len(self.runs[-1].text)

    def word_at(self, pos):
        i, c = pos
        text = self.runs[i].text
        if not text:
            return pos, pos
        c = min(c, len(text) - 1)
        if text[c].isspace():
            return (i, c), (i, c + 1)
        a = b = c
        while a > 0 and not text[a - 1].isspace():
            a -= 1
        while b < len(text) and not text[b].isspace():
            b += 1
        return (i, a), (i, b)

    def line_at(self, pos):
        """Expand a position to the whole visual line (all runs sharing its row)."""
        i, _ = pos
        r = self.runs[i]
        a = i
        while a > 0 and self.runs[a].sep == "" and abs(self.runs[a - 1].top - r.top) < 1:
            a -= 1
        b = i
        while b + 1 < len(self.runs) and self.runs[b + 1].sep == "" and abs(self.runs[b + 1].top - r.top) < 1:
            b += 1
        return (a, 0), (b, len(self.runs[b].text))

    def all_range(self):
        if not self.runs:
            return None
        return (0, 0), (len(self.runs) - 1, len(self.runs[-1].text))

    def _spans(self, a, b):
        a, b = min(a, b), max(a, b)
        for i in range(a[0], b[0] + 1):
            r = self.runs[i]
            start = a[1] if i == a[0] else 0
            end = b[1] if i == b[0] else len(r.text)
            yield i, r, start, end

    def selection_rects(self, a, b):
        out = []
        for i, r, start, end in self._spans(a, b):
            x0, x1 = r.x + r.edges[start], r.x + r.edges[end]
            # include the gap to the next run on the same line so a selection reads as one band
            if i < max(a, b)[0] and end == len(r.text):
                nxt = self.runs[i + 1]
                if nxt.sep in ("", " ") and abs(nxt.top - r.top) < 1 and nxt.x > x1:
                    x1 = nxt.x
            if x1 > x0:
                out.append((x0, r.top, x1 - x0, r.h))
        return out

    def selected_text(self, a, b):
        parts = []
        for i, r, start, end in self._spans(a, b):
            if parts:
                parts.append(r.sep)
            parts.append(r.text[start:end])
        return "".join(parts)

    # ------------------------------------------------------------------ query
    def link_at(self, dx, dy):
        for i, link in enumerate(self.links):
            for (x, y, w, h) in link.rects:
                if x <= dx <= x + w and y <= dy <= y + h:
                    return i
        return None

    def set_hover(self, index, on):
        if index is None or index >= len(self.links):
            return
        link = self.links[index]
        color = link.hover if on else link.color
        for lab in link.labels:
            if lab.color[:3] in (link.color[:3], link.hover[:3]):
                lab.color = color
        for ln in link.lines:
            ln.color = with_alpha(color, 255 if on else 90)
        for ic in link.icons:
            ic.color = color
        for bg in link.bgs:
            bg.color = with_alpha(self.theme.c("text"), 22 if on else 0)

    def delete(self):
        for o in self.objects:
            try:
                o.delete()
            except Exception:
                pass
        self.objects.clear()


def gfx_tint(theme):
    return mix(theme.c("accent"), (0, 0, 0, 255), 0.6)


def _attr(tag: str, name: str):
    m = re.search(rf"\b{name}\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", tag, re.I)
    if not m:
        return None
    return html.unescape(m[1].strip("\"'"))


def _token_kind(tt):
    if tt in T.Comment:
        return "comment"
    if tt in T.String:
        return "string"
    if tt in T.Number:
        return "number"
    if tt in T.Keyword.Type:
        return "type"
    if tt in T.Keyword:
        return "keyword"
    if tt in T.Name.Function or tt in T.Name.Class:
        return "function"
    if tt in T.Name.Builtin or tt in T.Name.Exception:
        return "builtin"
    if tt in T.Name.Decorator or tt in T.Name.Tag or tt in T.Name.Attribute:
        return "type"
    if tt in T.Operator:
        return "operator"
    return None
