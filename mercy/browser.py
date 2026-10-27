"""The Mercy browser window: chrome, scrolling, navigation and theming."""

import itertools
import json
import math
import re
import os
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

import pyglet
from pyglet import shapes
from pyglet.math import Mat4, Vec3
from pyglet.window import key, mouse

from . import fetch, gfx, printing
from .bookmarks import UNSORTED, Bookmarks, short_url
from .music import MusicPlayer, fmt_time, is_audio
from .system import IS_MAC, config_dir, prepare_process, shortcut
from .render import Page
from .themes import THEMES, SANS, hex_rgb, mix, pick_font, theme_by_name, with_alpha

TOOLBAR_H = 60
TABS_H = 40
MUSIC_H = 64      # the floating now-playing bar
CHROME_H = TOOLBAR_H + TABS_H   # tab strip on top, toolbar under it
TOC_W = 272
ICON_DIR = Path(__file__).parent / "icons"
SETTINGS = config_dir() / "settings.json"
# The "accelerator" modifier: Command on a Mac, Ctrl elsewhere (where Command means the
# Windows/Super key, which shouldn't trigger shortcuts). Macs also accept Ctrl.
ACCEL = (key.MOD_CTRL | key.MOD_COMMAND) if IS_MAC else key.MOD_CTRL
# Mac wheels and trackpads report many small, accelerated deltas instead of whole notches.
WHEEL_STEP = 2.2 if IS_MAC else 5.0


def approach(value, target, dt, speed=14.0):
    return target + (value - target) * math.exp(-speed * dt)


def ease(t):
    return 1 - (1 - t) ** 3


# Everything that belongs to one tab. The browser keeps the current tab's copy in
# its own attributes (so the rest of the code needn't know about tabs) and swaps
# them in and out when switching.
TAB_FIELDS = ("history", "hist_i", "doc", "url", "page", "page_key", "load_seq", "loading",
              "load_started", "scroll", "target", "toc_scroll")


class Tab:
    _ids = itertools.count(1)

    def __init__(self):
        self.id = next(Tab._ids)
        self.history, self.hist_i = [], -1
        self.doc, self.url, self.page, self.page_key = None, "", None, None
        self.load_seq, self.loading, self.load_started = 0, False, 0.0
        self.scroll = self.target = 0.0
        self.toc_scroll = 0
        self.arrived = None     # (doc, frag, restore) that finished loading in the background
        self.lazy_url = None    # restored from the last session; loads when first shown
        self.title = ""


def quick_title(doc) -> str:
    m = re.search(r"^#\s+(.+?)\s*#*\s*$", doc.text, re.M)
    if m:
        return re.sub(r"[*_`\[\]]|\(.*?\)", "", m[1]).strip(": ")
    return doc.title


class Browser(pyglet.window.Window):
    def __init__(self, start_url: str | None = None):
        prepare_process()
        config = pyglet.gl.Config(double_buffer=True, sample_buffers=1, samples=4)
        try:
            super().__init__(1180, 820, caption="Mercy", resizable=True, config=config)
        except pyglet.window.NoSuchConfigException:
            super().__init__(1180, 820, caption="Mercy", resizable=True)
        self.set_minimum_size(520, 360)
        self.set_app_icon()

        settings = self.load_settings()
        self.theme = theme_by_name(settings.get("theme", THEMES[0].name))
        self.zoom = float(settings.get("zoom", 1.0))
        self.toc_open = bool(settings.get("explorer", False))
        self.settings_paper = settings.get("paper")   # the paper size last chosen for printing
        self.pr = None              # the print panel's state while it's open
        self.print_ui = None
        self.toc_t = 1.0 if self.toc_open else 0.0
        self.toc = None
        self.toc_scroll = 0
        self.toc_current = None
        self.ui_font = pick_font(SANS)

        self.worker = fetch.Worker()
        self.images: dict = {}
        self.pending_images: set = set()
        self.history: list[dict] = []
        self.hist_i = -1
        self.doc: fetch.Document | None = None
        self.url = ""
        self.page: Page | None = None
        self.page_key = None
        self.image_gen = 0          # bumps whenever an image arrives, so stale pages get rebuilt
        self.load_seq = 0
        self.seq = itertools.count(1)
        self.tabs = [Tab()]
        self.tab_i = 0
        self.closed_tabs = []
        self.tab_strip = None
        self.tab_drag = None
        self.strip_wheel = (0.0, 0.0)   # wheel travel over the tab strip, and when it last moved
        self.loading = False
        self.load_started = 0.0
        self.relayout_at = None

        self.scroll = 0.0
        self.target = 0.0
        self.arrow_hold = None      # (direction, time pressed) while Up/Down is held
        self.scroll_activity = 0.0
        self.drag_scrollbar = None
        self.fade = 0.0
        self.hover_link = None
        self.hover = None
        self.mouse = (0, 0)
        self.time = 0.0

        self.editing = False
        self.edit_text = ""
        self.caret = 0
        self.anchor = None          # other end of the address-bar selection
        self.view_start = 0         # first visible character of the address text
        self.caret_blink = 0.0
        self.addr_drag = False

        self.sel = None             # page selection: ((run, char), (run, char))
        self.sel_shapes = []
        self.press = None           # pending left-click on the page
        self.clicks = (0.0, (0, 0), 0)  # time, position, count, for double/triple clicks
        self.ctx = None             # right-click menu
        self.prompt = None          # address bar borrowed to ask for text (e.g. renaming)

        self.bookmarks = Bookmarks()
        fetch.PROVIDERS["bookmarks"] = self.bookmarks.manager_page
        self.bm_open = False
        self.bm_panel = None
        self.bm_filter, self.bm_scroll, self.bm_index = "", 0, 0
        self.star_pop = 0.0

        self.music = MusicPlayer(on_change=self.music_changed)
        self.music_t = 0.0          # slide-in of the player bar
        self.music_ui = None
        self.music_dirty = False
        self.music_drag = None

        self.menu_open = False
        self.menu_t = 0.0
        self.toast_text = ""
        self.toast_t = 0.0

        self.bg_batch = None
        self.chrome = None
        self.menu = None
        self.build_background()
        self.build_chrome()
        self.cursor_hand = self.get_system_mouse_cursor(self.CURSOR_HAND)
        self.cursor_text = self.get_system_mouse_cursor(self.CURSOR_TEXT)
        self.cursor_kind = None
        self.keys = key.KeyStateHandler()
        self.push_handlers(self.keys)

        pyglet.clock.schedule_interval(self.update, 1 / 60)
        saved = settings.get("tabs") if not start_url else None
        if saved:
            self.restore_session(saved, int(settings.get("active", 0)))
        else:
            self.navigate(fetch.normalize(start_url) if start_url else "mercy://home")
            self.build_tabs()

    def set_app_icon(self):
        """Give the window (title bar, taskbar, alt-tab) Mercy's icon at every size we have."""
        icons = []
        for size in (16, 24, 32, 48, 64, 128, 256, 512):
            path = ICON_DIR / f"mercy-{size}.png"
            try:
                icons.append(pyglet.image.load(str(path)))
            except Exception:  # a missing or unreadable size just isn't offered
                pass
        if icons:
            self.set_icon(*icons)

    # ================================================================ settings
    def load_settings(self) -> dict:
        try:
            return json.loads(SETTINGS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save_settings(self):
        try:
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            tabs, active = self.session()
            SETTINGS.write_text(json.dumps({"theme": self.theme.name, "zoom": self.zoom,
                                            "explorer": self.toc_open, "tabs": tabs, "active": active,
                                            "paper": self.settings_paper}),
                                encoding="utf-8")
        except OSError:
            pass

    # ================================================================ geometry
    @property
    def S(self):
        return 16 * self.zoom

    def column_width(self):
        pad = round(self.S * 3.2)
        margin = 28 if self.width > 760 else 10
        room = self.width - (TOC_W if self.toc_open and self.toc_pushes() else 0)
        return max(200, min(self.S * 46, room - 2 * (pad + margin)))

    @property
    def view_top(self):
        """Screen y of document y=0 when not scrolled."""
        return self.height - CHROME_H - 28

    def max_scroll(self):
        if not self.page:
            return 0
        # leave room to scroll the last lines clear of the player bar
        return max(0.0, self.page.height - self.view_top + 36 + self.music_lift())

    def to_doc(self, x, y):
        return x - self.page_offset_x(), self.view_top + round(self.scroll) - y

    # ============================================================== background
    def build_background(self):
        t = self.theme
        if self.bg_batch:
            for o in self.bg_objects:
                o.delete()
        self.bg_batch = pyglet.graphics.Batch()
        g0, g1 = pyglet.graphics.Group(0), pyglet.graphics.Group(1)
        self.bg_rect = gfx.GradientRect(0, 0, self.width, self.height, t.c("bg_bottom"), t.c("bg_top"),
                                        batch=self.bg_batch, group=g0)
        self.bg_objects = [self.bg_rect]
        self.glows = []
        r = max(self.width, self.height) * 0.42
        for color in t.blobs:
            glow = gfx.Glow(0, 0, r, hex_rgb(color), t.blob_alpha, self.bg_batch, g1)
            self.glows.append(glow)
            self.bg_objects += glow.circles
        self.place_glows()

    def place_glows(self):
        w, h, tt = self.width, self.height, self.time * 0.06
        spots = [(0.15, 0.85, 0.0), (0.85, 0.3, 2.1), (0.55, 0.05, 4.2)]
        for glow, (fx, fy, ph) in zip(self.glows, spots):
            x = w * (fx + 0.12 * math.sin(tt + ph))
            y = h * (fy + 0.10 * math.cos(tt * 1.3 + ph))
            glow.move(x, y)

    # ================================================================== chrome
    def build_chrome(self):
        t = self.theme
        if self.chrome:
            for o in self.chrome_objects:
                o.delete()
        b = self.chrome = pyglet.graphics.Batch()
        g = [pyglet.graphics.Group(i) for i in range(5)]
        objs = self.chrome_objects = []

        def keep(*os_):
            for o in os_:
                objs.extend(o if isinstance(o, list) else [o])
            return os_[0]

        w, h, H = self.width, self.height, TOOLBAR_H
        bar_y = h - CHROME_H
        cy = bar_y + H / 2
        chrome = t.chrome
        keep(shapes.Rectangle(0, bar_y, w, H, color=with_alpha(chrome, 228), batch=b, group=g[0]))
        keep(gfx.GradientRect(0, bar_y - 10, w, 10, with_alpha(chrome, 0), with_alpha(chrome, 60),
                              batch=b, group=g[0]))
        keep(shapes.Rectangle(0, bar_y, w, 1, color=t.c("border"), batch=b, group=g[1]))
        self.progress = keep(gfx.GradientRect(0, bar_y - 1, 0, 2, t.c("accent"), t.c("accent2"),
                                              vertical=False, batch=b, group=g[3]))

        # navigation buttons
        icon_c = t.c("text")
        self.buttons = []
        specs = [("contents", gfx.outline_icon, None), ("back", gfx.chevron, "left"), ("forward", gfx.chevron, "right"),
                 ("reload", gfx.reload_icon, None), ("home", gfx.home_icon, None),
                 ("bookmarks", gfx.bookmark_icon, None)]
        for i, (name, fn, arg) in enumerate(specs):
            bx = 30 + i * 42
            bg = keep(shapes.Circle(bx, cy, 17, segments=32, color=with_alpha(icon_c, 0), batch=b, group=g[1]))
            icon = fn(bx, cy, 14, arg, icon_c, b, g[2]) if arg else fn(bx, cy, 14, icon_c, b, g[2])
            keep(icon)
            self.buttons.append(dict(name=name, x=bx, y=cy, r=18, bg=bg, icon=icon, hover=0.0))

        # theme button on the right
        tw = 96 + gfx.text_width(gfx.font(self.ui_font, 11, bold=True), t.name)
        tx = w - tw - 14
        self.theme_btn = dict(x=tx, y=cy - 17, w=tw, h=34, hover=0.0)
        self.theme_btn["bg"] = keep(gfx.rrect(tx, cy - 17, tw, 34, 17, with_alpha(icon_c, 0), b, g[1]))
        for i, col in enumerate((t.bg_top, t.accent, t.accent2)):
            keep(shapes.Circle(tx + 20 + i * 11, cy, 8.5, segments=24, color=t.c("border"), batch=b, group=g[2]))
            keep(shapes.Circle(tx + 20 + i * 11, cy, 7, segments=24, color=hex_rgb(col), batch=b, group=g[2]))
        keep(pyglet.text.Label(t.name, font_name=self.ui_font, font_size=11, bold=True, color=icon_c,
                               x=tx + 60, y=cy, anchor_y="center", batch=b, group=g[2]))
        keep(shapes.Line(tx + tw - 22, cy + 2, tx + tw - 18, cy - 2, 1.6, color=t.c("muted"), batch=b, group=g[2]))
        keep(shapes.Line(tx + tw - 18, cy - 2, tx + tw - 14, cy + 2, 1.6, color=t.c("muted"), batch=b, group=g[2]))

        # address pill
        ax = 30 + len(specs) * 42 - 4
        aw = tx - 14 - ax
        ah = 38
        self.addr = dict(x=ax, y=cy - ah / 2, w=aw, h=ah, hover=0.0)
        self.addr["ring"] = keep(gfx.rrect(ax - 3, cy - ah / 2 - 3, aw + 6, ah + 6, ah / 2 + 3,
                                           t.c("accent", 0), b, g[0]))
        self.addr["border"] = keep(gfx.rrect(ax - 1, cy - ah / 2 - 1, aw + 2, ah + 2, ah / 2 + 1,
                                             t.c("border"), b, g[1]))
        fill = t.c("code_bg") if t.dark else mix(t.c("surface"), (255, 255, 255, 255), 0.5)
        self.addr["fill"] = keep(gfx.rrect(ax, cy - ah / 2, aw, ah, ah / 2, fill, b, g[1]))
        self.addr["dot"] = keep(shapes.Circle(ax + 20, cy, 4.5, segments=16, color=t.c("accent"), batch=b, group=g[2]))
        self.addr["dot_ring"] = keep(shapes.Arc(ax + 20, cy, 8, segments=24, thickness=1.2,
                                                color=t.c("accent", 90), batch=b, group=g[2]))
        self.addr_font = gfx.font(self.ui_font, 12)
        lx = ax + 38
        self.addr["lx"] = lx
        self.addr["avail"] = aw - 38 - 44
        self.addr_font_bold = gfx.font(self.ui_font, 12, bold=True)
        self.addr["sel"] = keep(shapes.Rectangle(lx, cy - 10, 0, 20, color=t.c("accent", 0), batch=b, group=g[2]))
        self.addr["host"] = keep(pyglet.text.Label("", font_name=self.ui_font, font_size=12, color=icon_c,
                                                   x=lx, y=cy, anchor_y="center", batch=b, group=g[3]))
        self.addr["path"] = keep(pyglet.text.Label("", font_name=self.ui_font, font_size=12, color=t.c("muted"),
                                                   x=lx, y=cy, anchor_y="center", batch=b, group=g[3]))
        self.addr["caret"] = keep(shapes.Rectangle(lx, cy - 10, 1.5, 20, color=t.c("accent", 0), batch=b, group=g[4]))
        self.addr["spinner"] = keep(shapes.Arc(ax + aw - 22, cy, 7, segments=24, angle=math.radians(260),
                                               thickness=2, color=t.c("accent", 0), batch=b, group=g[3]))
        # bookmark star: an outline is a star with a smaller fill-colored star on top
        sx = ax + aw - 22
        self.addr["star_xy"] = (sx, cy)
        self.addr["star_bg"] = keep(shapes.Circle(sx, cy, 14, segments=28, color=t.c("text", 0), batch=b, group=g[2]))
        self.addr["star"] = keep(gfx.star(sx, cy, 8.5, t.c("muted"), b, g[3]))
        self.addr["star_in"] = keep(gfx.star(sx, cy - 0.3, 6.2, fill, b, g[4]))
        self.addr["star_fill"] = fill
        self.addr["star_hover"] = 0.0

        # scrollbar, status pill and toast (bottom overlays)
        self.thumb = keep(gfx.rrect(w - 9, 0, 5, 40, 2.5, t.c("muted", 0), b, g[1]))
        self.status_bg = keep(gfx.rrect(10, 10, 100, 26, 9, with_alpha(chrome, 0), b, g[1]))
        self.status_label = keep(pyglet.text.Label("", font_name=self.ui_font, font_size=10.5,
                                                   color=t.c("muted", 0), x=22, y=23, anchor_y="center",
                                                   batch=b, group=g[2]))
        self.toast_bg = keep(gfx.rrect(0, 28, 100, 40, 20, with_alpha(chrome, 0), b, g[3]))
        self.toast_label = keep(pyglet.text.Label("", font_name=self.ui_font, font_size=12, bold=True,
                                                  color=t.c("heading", 0), x=w / 2, y=48, anchor_x="center",
                                                  anchor_y="center", batch=b, group=g[4]))
        self.fade_rect = gfx.GradientRect(0, 0, w, bar_y, with_alpha(t.c("bg_bottom"), 0),
                                          with_alpha(t.c("bg_top"), 0))
        objs.append(self.fade_rect)
        self.refresh_address()
        if self.menu_open:
            self.build_menu()
        if self.bm_open:
            self.build_bm_panel()
        if self.toc_open or self.toc:
            self.build_toc()
        self.build_tabs()
        self.music_dirty = True
        if self.print_ui:
            self.build_print()

    def build_menu(self):
        if self.menu:
            for o in self.menu_objects:
                o.delete()
        t = self.theme
        b = self.menu = pyglet.graphics.Batch()
        g = [pyglet.graphics.Group(i) for i in range(4)]
        objs = self.menu_objects = []
        row_h, mw = 40, 220
        mh = row_h * len(THEMES) + 16
        mx = self.width - mw - 14
        my = self.height - CHROME_H - 6 - mh
        self.menu_rect = (mx, my, mw, mh)
        objs += gfx.soft_shadow(mx, my, mw, mh, 14, b, g[0], strength=120 if t.dark else 60, spread=18, offset=8)
        objs.append(gfx.rrect(mx - 1, my - 1, mw + 2, mh + 2, 15, t.c("border"), b, g[0]))
        objs.append(gfx.rrect(mx, my, mw, mh, 14, with_alpha(t.chrome, 250), b, g[0]))
        self.menu_rows = []
        for i, th in enumerate(THEMES):
            ry = my + mh - 8 - (i + 1) * row_h
            hl = gfx.rrect(mx + 6, ry + 2, mw - 12, row_h - 4, 10, t.c("accent", 0), b, g[1])
            objs.append(hl)
            for j, col in enumerate((th.bg_top, th.surface, th.accent, th.accent2)):
                objs.append(shapes.Circle(mx + 24 + j * 12, ry + row_h / 2, 8, segments=24,
                                          color=t.c("border"), batch=b, group=g[2]))
                objs.append(shapes.Circle(mx + 24 + j * 12, ry + row_h / 2, 6.5, segments=24,
                                          color=hex_rgb(col), batch=b, group=g[2]))
            current = th.name == t.name
            objs.append(pyglet.text.Label(th.name, font_name=self.ui_font, font_size=11.5, bold=current,
                                          color=t.c("heading") if current else t.c("text"),
                                          x=mx + 82, y=ry + row_h / 2, anchor_y="center", batch=b, group=g[2]))
            objs.append(pyglet.text.Label("dark" if th.dark else "light", font_name=self.ui_font, font_size=9,
                                          color=t.c("muted"), x=mx + mw - 20, y=ry + row_h / 2, anchor_x="right",
                                          anchor_y="center", batch=b, group=g[2]))
            if current:
                objs += gfx.check_mark(mx + mw - 64, ry + row_h / 2, 11, t.c("accent"), b, g[3], width=2)
            self.menu_rows.append(dict(rect=(mx + 6, ry, mw - 12, row_h), theme=th, hl=hl, hover=0.0))

    # ============================================================== navigation
    def navigate(self, url: str, push=True, restore=None):
        if url.startswith("mercy-copy:"):
            if self.page:
                idx = int(url.split(":", 1)[1])
                self.set_clipboard_text(self.page.copy_texts[idx])
                self.toast("Copied to clipboard")
            return
        if url.startswith("mercy-bm:"):
            self.bookmark_action(url[len("mercy-bm:"):])
            return
        if url.startswith("mercy-external:"):
            webbrowser.open(url.split(":", 1)[1])
            self.toast("Opened in your default browser")
            return
        if url.startswith("mailto:"):
            webbrowser.open(url)
            return
        if is_audio(url):
            self.play_audio(url)
            return
        base, frag = fetch.split_fragment(url)
        cur_base, _ = fetch.split_fragment(self.url)
        if self.history and 0 <= self.hist_i < len(self.history):
            self.history[self.hist_i]["scroll"] = self.scroll
        if push:
            self.history = self.history[:self.hist_i + 1] + [dict(url=url, scroll=0.0)]
            self.hist_i = len(self.history) - 1
        if base == cur_base and self.page and frag is not None and (frag or restore is not None) \
                and not self.loading:
            self.url = url
            self.refresh_address()
            if restore is not None:
                self.target = restore
            else:
                self.scroll_to_anchor(frag)
            return
        self.url = url
        self.loading = True
        self.load_started = self.time
        self.load_seq = next(self.seq)
        self.refresh_address()
        self.worker.submit(("doc", self.tab.id, self.load_seq, frag, restore), fetch.load, url)
        self.build_tabs()

    def go_back(self):
        if self.hist_i > 0:
            self.history[self.hist_i]["scroll"] = self.scroll
            self.hist_i -= 1
            e = self.history[self.hist_i]
            self.navigate(e["url"], push=False, restore=e["scroll"])

    def go_forward(self):
        if self.hist_i < len(self.history) - 1:
            self.history[self.hist_i]["scroll"] = self.scroll
            self.hist_i += 1
            e = self.history[self.hist_i]
            self.navigate(e["url"], push=False, restore=e["scroll"])

    def reload(self):
        if self.url:
            self.images = {k: v for k, v in self.images.items() if v != "error"}
            self.url, url = "", self.url
            self.navigate(url, push=False, restore=self.scroll)

    def follow(self, href: str):
        if href.startswith("#"):
            base, _ = fetch.split_fragment(self.url)
            self.navigate(base + href)
            return
        self.navigate(fetch.resolve(fetch.split_fragment(self.url)[0], href))

    def scroll_to_anchor(self, frag):
        if self.page and frag:
            y = self.page.anchors.get(frag) or self.page.anchors.get(frag.lower())
            if y is not None:
                self.target = min(self.max_scroll(), max(0, y - self.S * 1.2))

    def on_document(self, doc: fetch.Document, frag, restore):
        self.loading = False
        self.doc = doc
        self.url = doc.url + (f"#{frag}" if frag else "")
        if self.history:
            self.history[self.hist_i]["url"] = self.url
        self.relayout()
        self.scroll = self.target = 0.0
        if restore is not None:
            self.scroll = self.target = min(restore, self.max_scroll())
        elif frag:
            self.scroll_to_anchor(frag)
            self.scroll = self.target
        self.fade = 1.0
        self.refresh_address()
        title = self.page.title or doc.title or os.path.basename(urlparse(doc.url).path) or doc.url
        self.set_caption(f"{title} — Mercy")
        self.tab.title = title
        self.build_tabs()

    def relayout(self):
        if not self.doc:
            return
        if self.page:
            self.page.delete()
        base = fetch.split_fragment(self.url)[0]
        self.sel, self.sel_shapes, self.press = None, [], None
        self.page = Page(self.doc.text, self.theme, self.column_width(), self.S, self.images,
                         resolve=lambda src: fetch.resolve(base, src))
        self.page_key = self.layout_key()
        for src in self.page.wanted_images:
            if src not in self.pending_images:
                self.pending_images.add(src)
                self.worker.submit(("img", src), fetch.decode_image, src)
        self.scroll = min(self.scroll, self.max_scroll())
        self.target = min(self.target, self.max_scroll())
        self.hover_link = None
        self.toc_current = None
        if self.toc_open or self.toc:
            self.build_toc()

    # =================================================================== theme
    def set_theme(self, theme):
        self.theme = theme
        self.build_background()
        self.build_chrome()
        self.relayout()
        self.toast(f"{theme.name} theme")
        self.save_settings()

    def cycle_theme(self, step=1):
        i = THEMES.index(self.theme)
        self.set_theme(THEMES[(i + step) % len(THEMES)])

    def set_zoom(self, zoom):
        zoom = max(0.6, min(2.2, round(zoom, 2)))
        if zoom != self.zoom:
            ratio = zoom / self.zoom
            self.zoom = zoom
            self.relayout()
            self.scroll = self.target = min(self.scroll * ratio, self.max_scroll())
            self.toast(f"Zoom {round(zoom * 100)}%")
            self.save_settings()

    def toast(self, text):
        self.toast_text = text
        self.toast_t = 1.8

    # =============================================================== addresses
    def refresh_address(self):
        if not self.chrome:
            return
        a, t = self.addr, self.theme
        f = self.addr_font
        if self.editing:
            text = self.edit_text
            ex = self.edit_x()
            avail = a["avail"] - (ex - a["lx"])
            if self.prompt:
                a["path"].text = self.prompt["label"] + " ›"
                a["path"].x = a["lx"]
                a["path"].color = t.c("accent")
                a["path"].bold = True
            start = min(self.view_start, self.caret)
            while start < self.caret and gfx.text_width(f, text[start:self.caret]) > avail - 8:
                start += 1
            while start > 0 and gfx.text_width(f, text[start - 1:]) <= avail:
                start -= 1
            end = len(text)
            while end > self.caret and gfx.text_width(f, text[start:end]) > avail:
                end -= 1
            self.view_start = start
            shown = text[start:end]
            a["host"].text = shown
            a["host"].x = ex
            a["host"].color = t.c("heading")
            if not self.prompt:
                a["path"].text = ""
            a["caret"].x = ex + gfx.text_width(f, text[start:self.caret])
            sel = self.edit_selection()
            if sel:
                s0, s1 = max(sel[0], start), min(sel[1], end)
                a["sel"].x = ex + gfx.text_width(f, text[start:s0])
                a["sel"].width = max(0, gfx.text_width(f, text[s0:s1]))
                a["sel"].color = t.c("accent", 80)
            else:
                a["sel"].width = 0
            return
        a["sel"].width = 0
        url = self.url
        if url.startswith(("https://", "http://")):
            p = urlparse(url)
            host, rest = p.netloc, url.split(p.netloc, 1)[1]
            a["dot"].color = t.c("accent")
        elif url.startswith("file:"):
            host, rest = "file", "  " + str(fetch._file_path(url))
            a["dot"].color = t.c("accent2")
        else:
            host, rest = ("mercy", url[len("mercy"):]) if url.startswith("mercy:") else ("", url)
            a["dot"].color = t.c("accent2")
        a["dot_ring"].color = with_alpha(a["dot"].color, 90)
        if gfx.text_width(f, host) > a["avail"]:  # very long host: shorten it and drop the path
            while host and gfx.text_width(f, host + "…") > a["avail"]:
                host = host[:-1]
            host, rest = host + "…", ""
        hw = gfx.text_width(f, host)
        room = a["avail"] - hw
        if gfx.text_width(f, rest) > room:
            while rest and gfx.text_width(f, rest + "…") > room:
                rest = rest[:-1]
            rest += "…"
        a["host"].text = host
        a["host"].x = a["lx"]
        a["host"].color = t.c("text")
        a["path"].bold = False
        a["path"].color = t.c("muted")
        a["path"].text = rest
        a["path"].x = a["lx"] + hw

    def start_editing(self, select=True):
        self.editing = True
        self.edit_text = self.url
        self.caret = len(self.edit_text)
        self.anchor = 0 if select else None
        self.view_start = 0
        self.caret_blink = 0
        self.refresh_address()

    def stop_editing(self):
        self.editing = False
        self.prompt = None
        self.anchor = None
        self.addr["caret"].color = self.theme.c("accent", 0)
        self.refresh_address()

    def edit_selection(self):
        if self.anchor is None or self.anchor == self.caret:
            return None
        return min(self.anchor, self.caret), max(self.anchor, self.caret)

    def edit_delete_selection(self):
        sel = self.edit_selection()
        if sel:
            self.edit_text = self.edit_text[:sel[0]] + self.edit_text[sel[1]:]
            self.caret = sel[0]
        self.anchor = None
        return sel is not None

    def edit_insert(self, s):
        self.edit_delete_selection()
        self.edit_text = self.edit_text[:self.caret] + s + self.edit_text[self.caret:]
        self.caret += len(s)
        self.caret_blink = 0
        self.refresh_address()

    def edit_copy(self, cut=False):
        sel = self.edit_selection()
        if sel:
            self.set_clipboard_text(self.edit_text[sel[0]:sel[1]])
            if cut:
                self.edit_delete_selection()
                self.refresh_address()

    def edit_paste(self):
        text = (self.get_clipboard_text() or "").strip().replace("\r", "").replace("\n", " ")
        if text:
            self.edit_insert(text)

    def addr_index_at(self, x):
        """Caret position in the address text nearest to screen x."""
        f, text = self.addr_font, self.edit_text
        dx = x - self.edit_x()
        prev = 0.0
        for i in range(self.view_start, len(text)):
            w = gfx.text_width(f, text[self.view_start:i + 1])
            if dx < (prev + w) / 2:
                return i
            prev = w
        return len(text)

    def word_bounds(self, text, i):
        """Characters that count as one word in an address: stop at URL punctuation."""
        stops = set(" /.:?&=#-_~@")
        a = b = i
        while a > 0 and text[a - 1] not in stops:
            a -= 1
        while b < len(text) and text[b] not in stops:
            b += 1
        return a, b

    # ========================================================= page selection
    def clear_selection(self):
        self.sel = None
        self.draw_selection()

    def set_selection(self, a, b):
        self.sel = (a, b) if a and b and a != b else None
        self.draw_selection()

    def draw_selection(self):
        for s_ in self.sel_shapes:
            s_.delete()
        self.sel_shapes = []
        if not (self.sel and self.page):
            return
        color = self.theme.c("accent", 85 if self.theme.dark else 70)
        for x, top, w, h in self.page.selection_rects(*self.sel):
            self.sel_shapes.append(shapes.Rectangle(x, -(top + h), w, h, color=color,
                                                    batch=self.page.batch, group=self.page.g_sel))

    def selected_text(self):
        return self.page.selected_text(*self.sel) if (self.sel and self.page) else ""

    def copy_selection(self):
        text = self.selected_text()
        if text:
            self.set_clipboard_text(text)
            self.toast("Copied to clipboard")

    def select_all_page(self):
        if self.page and self.page.runs:
            self.set_selection(*self.page.all_range())

    def paste_and_go(self, text=None):
        """Open pasted text: an address is visited, anything else is shown as Markdown."""
        text = (self.get_clipboard_text() if text is None else text) or ""
        text = text.strip()
        if not text:
            self.toast("The clipboard is empty")
            return
        if self.editing:
            self.stop_editing()     # otherwise the bar keeps showing the text being edited
        if "\n" not in text and (" " not in text or "://" in text):
            self.navigate(fetch.normalize(text))
        else:
            fetch.PASTED["text"] = text
            self.navigate("mercy://pasted")

    # ================================================================== events
    def on_resize(self, width, height):
        super().on_resize(width, height)
        if not self.chrome:
            return
        self.build_background()
        self.build_chrome()
        if self.page and abs(self.page.width - self.column_width()) > 0.5:
            self.relayout_at = self.time + 0.12

    def on_draw(self):
        self.clear()
        self.bg_batch.draw()
        if self.page:
            offset_x = self.page_offset_x()
            slide = round(self.fade ** 2 * 18)
            self.view = Mat4.from_translation(Vec3(offset_x, self.view_top + round(self.scroll) - slide, 0))
            self.page.batch.draw()
            self.view = Mat4()
        if self.fade > 0.01:
            self.fade_rect.draw()
        if self.toc and self.toc_t > 0.005:
            self.view = Mat4.from_translation(Vec3(self.toc_x(), 0, 0))
            self.toc["batch"].draw()
            self.view = Mat4()
        self.chrome.draw()
        if self.tab_strip:
            self.tab_strip["batch"].draw()
        if self.music_ui and self.music_t > 0.005:
            mx, my, _, _ = self.music_geometry()
            self.view = Mat4.from_translation(Vec3(mx, my, 0))
            self.music_ui["batch"].draw()
            self.view = Mat4()
        if self.menu_open and self.menu:
            self.menu.draw()
        if self.bm_panel:
            self.bm_panel["batch"].draw()
        if self.ctx:
            self.ctx["batch"].draw()
        if self.print_ui:
            self.print_ui["batch"].draw()

    def update(self, dt):
        self.time += dt
        t = self.theme
        for tag, result, err in self.worker.drain():
            if tag[0] == "doc":
                _, tab_id, seq, frag, restore = tag
                tab = next((tb for tb in self.tabs if tb.id == tab_id), None)
                if tab is None:
                    continue
                url = self.url if tab is self.tab else tab.url
                doc = result if err is None else fetch.error_page(url, "Something went wrong", f"```\n{err}\n```")
                if tab is self.tab:
                    if seq == self.load_seq:
                        self.on_document(doc, frag, restore)
                elif seq == tab.load_seq:
                    # finished in the background: keep it until the tab is shown
                    tab.arrived, tab.loading = (doc, frag, restore), False
                    tab.url = doc.url + (f"#{frag}" if frag else "")
                    if tab.history:
                        tab.history[tab.hist_i]["url"] = tab.url
                    tab.title = quick_title(doc)
                    self.build_tabs()
            elif tag[0] == "print":
                self.toast(result if err is None else f"Printing failed: {str(err)[:80]}")
            elif tag[0] == "img":
                self.pending_images.discard(tag[1])
                try:
                    self.images[tag[1]] = "error" if err or result is None else fetch.finish_image(result)
                except Exception:
                    self.images[tag[1]] = "error"
                self.image_gen += 1
                self.relayout_at = min(self.relayout_at or 1e9, self.time + 0.15)
        if self.relayout_at and self.time >= self.relayout_at:
            self.relayout_at = None
            self.relayout()
            self.update_hover()

        # smooth scrolling; arrow keys glide while held, speeding up the longer they're down
        if self.arrow_hold:
            direction, since = self.arrow_hold
            held = self.keys[key.DOWN if direction > 0 else key.UP]
            if held and not (self.editing or self.bm_open or self.ctx):
                ramp = min(1.0, (self.time - since) / 0.7)
                self.target += direction * self.S * (28 + 72 * ramp * ramp) * dt
            else:
                self.arrow_hold = None
        self.target = max(0.0, min(self.target, self.max_scroll()))
        before = self.scroll
        self.scroll = approach(self.scroll, self.target, dt, 16)
        if abs(self.scroll - self.target) < 0.3:
            self.scroll = self.target
        if abs(self.scroll - before) > 0.1:
            self.scroll_activity = 1.2
            self.update_hover()
        self.scroll_activity = max(0.0, self.scroll_activity - dt)
        self.fade = max(0.0, self.fade - dt * 3.5)

        self.place_glows()
        w, h = self.width, self.height
        bar_y = h - CHROME_H

        # buttons
        enabled = dict(back=self.hist_i > 0, forward=self.hist_i < len(self.history) - 1,
                       reload=True, home=True, bookmarks=True, contents=True)
        for btn in self.buttons:
            on = (self.hover == ("btn", btn["name"]) and enabled[btn["name"]]) or \
                (btn["name"] == "bookmarks" and self.bm_open) or (btn["name"] == "contents" and self.toc_open)
            btn["hover"] = approach(btn["hover"], 1.0 if on else 0.0, dt)
            btn["bg"].color = t.c("text", int(28 * btn["hover"]))
            col = t.c("text") if enabled[btn["name"]] else t.c("muted", 90)
            for s in btn["icon"]:
                s.color = col

        tb = self.theme_btn
        tb["hover"] = approach(tb["hover"], 1.0 if self.hover == ("theme",) or self.menu_open else 0.0, dt)
        tb["bg"].color = t.c("text", int(24 * tb["hover"]))

        a = self.addr
        focus = 1.0 if self.editing else 0.0
        a["hover"] = approach(a["hover"], max(focus, 0.4 if self.hover == ("addr",) else 0.0), dt)
        a["ring"].color = t.c("accent", int(70 * a["hover"]))
        a["border"].color = mix(t.c("border"), t.c("accent"), a["hover"])
        if self.editing:
            self.caret_blink += dt
            on = (self.caret_blink % 1.0) < 0.6
            a["caret"].color = t.c("accent", 255 if on else 0)
        spin = 1.0 if self.loading else 0.0
        a["spinner"].color = t.c("accent", int(255 * spin))
        marked = self.bookmarks.find(self.page_url()) is not None
        show_star = not self.loading and self.is_bookmarkable()
        a["star_hover"] = approach(a["star_hover"], 1.0 if self.hover == ("star",) else 0.0, dt, 20)
        a["star_bg"].color = t.c("text", int(26 * a["star_hover"]) if show_star else 0)
        self.star_pop = max(0.0, self.star_pop - dt * 2.5)
        pop = 1 + 0.45 * math.sin(math.pi * self.star_pop) if self.star_pop else 1
        a["star"].outer_radius, a["star"].inner_radius = 8.5 * pop, 3.8 * pop
        star_c = t.c("accent2") if marked else mix(t.c("muted"), t.c("text"), a["star_hover"])
        a["star"].color = with_alpha(star_c, 255 if show_star else 0)
        a["star_in"].color = with_alpha(a["star_fill"], 255 if (show_star and not marked) else 0)
        a["spinner"].rotation = (self.time * 400) % 360

        # progress line: indeterminate shimmer while loading, scroll position otherwise
        if self.loading:
            phase = ((self.time - self.load_started) * 0.8) % 1.0
            seg = w * 0.3
            self.progress.x = -seg + phase * (w + seg)
            self.progress.width = seg
        else:
            frac = self.scroll / self.max_scroll() if self.max_scroll() > 0 else 0
            self.progress.x = 0
            self.progress.width = w * frac

        # scrollbar
        ms = self.max_scroll()
        if self.page and ms > 0:
            track_top, track_bot = bar_y - 8, 8
            track = track_top - track_bot
            view_h = self.view_top
            th = max(36, track * view_h / (view_h + ms))
            ty = track_top - th - (track - th) * (self.scroll / ms)
            near = self.mouse[0] > w - 24 and self.mouse[1] < bar_y
            vis = 1.0 if (self.drag_scrollbar is not None or near) else min(1.0, self.scroll_activity)
            self.thumb.x, self.thumb.y, self.thumb.height = w - 9, ty, th
            self.thumb.width = 7 if (near or self.drag_scrollbar is not None) else 5
            self.thumb.x = w - 4 - self.thumb.width
            self.thumb.color = t.c("muted", int(170 * vis))
            self.thumb_rect = (w - 16, ty, 16, th, track, ms)
        else:
            self.thumb.color = t.c("muted", 0)
            self.thumb_rect = None

        # status pill
        status = ""
        if self.hover_link is not None and self.page:
            href = self.page.links[self.hover_link].href
            if href.startswith("mercy-copy:"):
                status = "Copy code to clipboard"
            elif href.startswith("mercy-external:"):
                status = href.split(":", 1)[1]
            else:
                status = href if href.startswith("#") else fetch.resolve(fetch.split_fragment(self.url)[0], href)
        elif self.loading and self.time - self.load_started > 0.25:
            status = "Loading " + self.url
        if status:
            if len(status) > 120:
                status = status[:117] + "…"
            self.status_label.text = status
            self.status_bg.width = self.status_label.content_width + 24
        left = TOC_W * ease(self.toc_t)
        self.status_bg.x, self.status_label.x = 10 + left, 22 + left
        lift = self.music_lift()
        self.status_bg.y, self.status_label.y = 10 + lift, 23 + lift
        alpha = 1.0 if status else 0.0
        self.status_alpha = approach(getattr(self, "status_alpha", 0.0), alpha, dt, 18)
        self.status_bg.color = with_alpha(t.chrome, int(235 * self.status_alpha))
        self.status_label.color = t.c("text", int(255 * self.status_alpha))

        # toast
        self.toast_t = max(0.0, self.toast_t - dt)
        ta = min(1.0, self.toast_t * 3)
        if self.toast_text:
            self.toast_label.text = self.toast_text
            tw = self.toast_label.content_width + 44
            self.toast_bg.x, self.toast_bg.width = (w - tw) / 2, tw
            self.toast_bg.y = 28 + self.music_lift() - (1 - ta) * 10
            self.toast_label.y = self.toast_bg.y + 20
        self.toast_bg.color = with_alpha(mix(t.chrome, t.c("accent"), 0.12), int(240 * ta))
        self.toast_label.color = t.c("heading", int(255 * ta))

        # page fade-in
        if self.fade > 0.01:
            a = int(255 * self.fade ** 1.5)
            self.fade_rect.set_colors(t.c("bg_bottom", a), t.c("bg_top", a))

        # dragging a selection past the top or bottom edge scrolls the page
        if self.press and self.press.get("selecting"):
            mx, my = self.mouse
            edge_top, edge_bot = h - CHROME_H - 24, 24
            if my > edge_top:
                self.target -= (my - edge_top) * dt * 18
            elif my < edge_bot:
                self.target += (edge_bot - my) * dt * 18
            if my > edge_top or my < edge_bot:
                self.drag_select(mx, my)

        if self.bm_panel:
            self.update_bm_panel(dt)
        self.update_tabs(dt)
        self.update_music(dt)
        self.update_toc(dt)

        self.update_print(dt)
        if self.ctx:
            for row in self.ctx["rows"]:
                x, y, rw, rh = row["rect"]
                over = row["enabled"] and x <= self.mouse[0] <= x + rw and y <= self.mouse[1] <= y + rh
                row["hover"] = approach(row["hover"], 1.0 if over else 0.0, dt, 24)
                row["hl"].color = t.c("accent", int(45 * row["hover"]))

        # theme menu
        if self.menu_open and self.menu:
            for row in self.menu_rows:
                x, y, rw, rh = row["rect"]
                over = x <= self.mouse[0] <= x + rw and y <= self.mouse[1] <= y + rh
                row["hover"] = approach(row["hover"], 1.0 if over else 0.0, dt, 20)
                row["hl"].color = t.c("accent", int(40 * row["hover"]))

    # =============================================================== bookmarks
    def page_url(self):
        return fetch.split_fragment(self.url)[0]

    def is_bookmarkable(self):
        return bool(self.url) and not self.loading and self.page_url() not in ("mercy://pasted", "mercy://bookmarks")

    def page_title(self):
        title = (self.page.title if self.page else "") or (self.doc.title if self.doc else "")
        return title or short_url(self.page_url())

    def toggle_bookmark(self):
        if not self.is_bookmarkable():
            return
        b = self.bookmarks.find(self.page_url())
        if b:
            self.bookmarks.remove(b["id"])
            self.toast("Bookmark removed")
        else:
            self.bookmarks.add(self.page_url(), self.page_title())
            self.star_pop = 1.0
            self.toast(f"Bookmarked  ·  {shortcut('Ctrl+B')} to see all")
        self.bookmarks_changed()

    def bookmarks_changed(self):
        if self.bm_open:
            self.build_bm_panel()
        if self.page_url() == "mercy://bookmarks" and not self.loading:
            self.reload()

    def bookmark_action(self, cmd):
        """Handle mercy-bm: links from the manager page."""
        op, _, bid = cmd.partition(":")
        bm = self.bookmarks
        if op == "export":
            self.copy_text(bm.export_markdown(), "Bookmarks copied as Markdown")
            return
        b = bm.get(bid)
        if not b:
            return
        if op == "delete":
            bm.remove(bid)
            self.toast(f"Deleted “{b['title']}”")
        elif op in ("up", "down"):
            bm.move(bid, -1 if op == "up" else 1)
        elif op == "rename":
            self.start_prompt("Rename", b["title"], lambda text: (bm.rename(bid, text), self.bookmarks_changed()))
            return
        elif op == "folder":
            others = [f for f in bm.folders() if f != (b.get("folder") or UNSORTED)]
            if others:
                self.toast("Folders: " + ", ".join(others[:6]))
            self.start_prompt("Folder", b.get("folder", ""),
                              lambda text: (bm.set_folder(bid, text), self.bookmarks_changed()))
            return
        self.bookmarks_changed()

    def start_prompt(self, label, initial, done):
        """Borrow the address bar to ask for a line of text."""
        self.close_bm_panel()
        self.start_editing()
        self.prompt = dict(label=label, done=done)
        self.edit_text = initial
        self.caret, self.anchor, self.view_start = len(initial), 0, 0
        self.refresh_address()

    def edit_x(self):
        a = self.addr
        if self.prompt:
            return a["lx"] + gfx.text_width(self.addr_font_bold, self.prompt["label"] + " ›") + 10
        return a["lx"]

    # ------------------------------------------------------------ quick panel
    def toggle_bm_panel(self):
        if self.bm_open:
            self.close_bm_panel()
        else:
            self.menu_open = False
            self.close_context_menu()
            if self.editing:
                self.stop_editing()
            self.bm_open = True
            self.bm_filter, self.bm_scroll, self.bm_index = "", 0, 0
            self.build_bm_panel()

    def close_bm_panel(self):
        self.bm_open = False
        if self.bm_panel:
            for o in self.bm_panel["objs"]:
                o.delete()
            self.bm_panel = None

    def bm_matches(self):
        return self.bookmarks.search(self.bm_filter)

    def build_bm_panel(self):
        if self.bm_panel:
            for o in self.bm_panel["objs"]:
                o.delete()
        t, f = self.theme, self.ui_font
        b = pyglet.graphics.Batch()
        g = [pyglet.graphics.Group(i) for i in range(5)]
        objs = []
        matches = self.bm_matches()
        self.bm_index = max(0, min(self.bm_index, len(matches) - 1))
        visible = 8
        self.bm_scroll = max(0, min(self.bm_scroll, len(matches) - visible))
        self.bm_scroll = min(self.bm_scroll, self.bm_index)
        if self.bm_index >= self.bm_scroll + visible:
            self.bm_scroll = self.bm_index - visible + 1

        row_h, head_h, filt_h, foot_h, pw = 50, 46, 44, 46, 420
        rows_shown = max(1, min(visible, len(matches)))
        ph = head_h + filt_h + rows_shown * row_h + foot_h + 10
        btn = next(bt for bt in self.buttons if bt["name"] == "bookmarks")
        px = max(10, min(btn["x"] - 30, self.width - pw - 10))
        py = self.height - CHROME_H - 6 - ph
        objs += gfx.soft_shadow(px, py, pw, ph, 14, b, g[0], strength=130 if t.dark else 60, spread=20, offset=9)
        objs.append(gfx.rrect(px - 1, py - 1, pw + 2, ph + 2, 15, t.c("border"), b, g[0]))
        objs.append(gfx.rrect(px, py, pw, ph, 14, with_alpha(t.chrome, 252), b, g[0]))

        def label(text, x, y, size=11, color=None, bold=False, anchor_x="left"):
            lab = pyglet.text.Label(text, font_name=f, font_size=size, bold=bold, color=color or t.c("text"),
                                    x=x, y=y, anchor_x=anchor_x, anchor_y="center", batch=b, group=g[3])
            objs.append(lab)
            return lab

        def fit(text, size, width, bold=False):
            font = gfx.font(f, size, bold=bold)
            if gfx.text_width(font, text) <= width:
                return text
            while text and gfx.text_width(font, text + "…") > width:
                text = text[:-1]
            return text + "…"

        top = py + ph
        # header
        hy = top - head_h / 2 - 2
        objs += gfx.bookmark_icon(px + 24, hy, 14, t.c("accent"), b, g[2], width=1.8)
        label("Bookmarks", px + 40, hy, 12.5, t.c("heading"), bold=True)
        n = len(self.bookmarks.items)
        label(f"{n}", px + 40 + gfx.text_width(gfx.font(f, 12.5, bold=True), "Bookmarks") + 10, hy, 10,
              t.c("muted"))
        manage = label("Manage  ›", px + pw - 18, hy, 10.5, t.c("accent"), bold=True, anchor_x="right")
        mw = manage.content_width
        hits = [("manage", None, (px + pw - 18 - mw - 8, hy - 14, mw + 16, 28))]

        # filter field
        fy = top - head_h - filt_h + 6
        fh = filt_h - 12
        objs.append(gfx.rrect(px + 12, fy, pw - 24, fh, fh / 2, t.c("border"), b, g[1]))
        fill = t.c("code_bg") if t.dark else mix(t.c("surface"), (255, 255, 255, 255), 0.5)
        objs.append(gfx.rrect(px + 13, fy + 1, pw - 26, fh - 2, fh / 2 - 1, fill, b, g[1]))
        # magnifier
        objs.append(shapes.Arc(px + 32, fy + fh / 2 + 1.5, 5.5, segments=20, thickness=1.6,
                               color=t.c("muted"), batch=b, group=g[2]))
        objs.append(shapes.Line(px + 36, fy + fh / 2 - 2.5, px + 40, fy + fh / 2 - 6.5, 1.8,
                                color=t.c("muted"), batch=b, group=g[2]))
        if self.bm_filter:
            ft = label(fit(self.bm_filter, 11, pw - 90), px + 50, fy + fh / 2, 11, t.c("heading"))
            caret_x = px + 50 + ft.content_width + 1
        else:
            label("Type to filter…", px + 50, fy + fh / 2, 11, t.c("muted", 170))
            caret_x = px + 50
        caret = shapes.Rectangle(caret_x, fy + fh / 2 - 8, 1.5, 16, color=t.c("accent"), batch=b, group=g[4])
        objs.append(caret)

        # rows
        rows = []
        y = fy - 4
        if not matches:
            msg = "No matches" if self.bm_filter else "No bookmarks yet — press Ctrl+D on any page"
            label(msg, px + pw / 2, y - row_h / 2, 10.5, t.c("muted"), anchor_x="center")
            y -= row_h
        for i in range(self.bm_scroll, min(len(matches), self.bm_scroll + visible)):
            bk = matches[i]
            y -= row_h
            ry = y
            hl = gfx.rrect(px + 8, ry + 3, pw - 16, row_h - 6, 10, t.c("accent", 0), b, g[1])
            objs.append(hl)
            # letter tile
            host = short_url(bk["url"]).lstrip("/")
            letter = (bk["title"][:1] or host[:1] or "?").upper()
            tile_c = t.c("accent") if i % 2 == 0 else t.c("accent2")
            objs.append(gfx.rrect(px + 18, ry + row_h / 2 - 14, 28, 28, 8, with_alpha(tile_c, 45), b, g[2]))
            label(letter, px + 32, ry + row_h / 2, 11.5, tile_c, bold=True, anchor_x="center")
            label(fit(bk["title"], 11.5, pw - 120), px + 58, ry + row_h / 2 + 8, 11.5, t.c("text"))
            sub = host
            if bk.get("folder"):
                sub = f"{bk['folder']}  ·  {host}"
            label(fit(sub, 9.5, pw - 120), px + 58, ry + row_h / 2 - 9, 9.5, t.c("muted"))
            x_lab = label("×", px + pw - 30, ry + row_h / 2 + 1, 15, t.c("muted", 0), anchor_x="center")
            rows.append(dict(id=bk["id"], url=bk["url"], index=i, hl=hl, x_lab=x_lab, hover=0.0,
                             rect=(px + 8, ry + 3, pw - 16, row_h - 6),
                             del_rect=(px + pw - 46, ry + 8, 32, row_h - 16)))
        if len(matches) > visible:
            track = rows_shown * row_h
            th = max(24, track * visible / len(matches))
            span = len(matches) - visible
            ty = fy - 4 - th - (track - th) * self.bm_scroll / span
            objs.append(gfx.rrect(px + pw - 7, ty, 3, th, 1.5, t.c("muted", 120), b, g[2]))

        # footer: bookmark / unbookmark this page
        objs.append(shapes.Rectangle(px + 12, py + foot_h + 4, pw - 24, 1, color=t.c("border"), batch=b, group=g[1]))
        marked = self.bookmarks.find(self.page_url()) is not None
        fy2 = py + 4 + foot_h / 2
        foot_hl = gfx.rrect(px + 8, py + 8, pw - 16, foot_h - 8, 10, t.c("accent", 0), b, g[1])
        objs.append(foot_hl)
        objs.append(gfx.star(px + 26, fy2, 7.5, t.c("accent2") if marked else t.c("muted"), b, g[2]))
        can = self.is_bookmarkable()
        text = "Remove this page" if marked else "Bookmark this page"
        label(text, px + 42, fy2, 11, t.c("text") if can else t.c("muted", 120))
        label(shortcut("Ctrl+D"), px + pw - 22, fy2, 9.5, t.c("muted"), anchor_x="right")
        if can:
            hits.append(("toggle", None, (px + 8, py + 8, pw - 16, foot_h - 8)))
        self.bm_panel = dict(batch=b, objs=objs, rows=rows, hits=hits, rect=(px, py, pw, ph),
                             caret=caret, foot_hl=foot_hl, visible=visible, count=len(matches))

    def bm_hit(self, x, y):
        p = self.bm_panel
        px, py, pw, ph = p["rect"]
        if not (px <= x <= px + pw and py <= y <= py + ph):
            return None
        for row in p["rows"]:
            rx, ry, rw, rh = row["del_rect"]
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return ("bm", "delete", row)
            rx, ry, rw, rh = row["rect"]
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return ("bm", "open", row)
        for kind, payload, (rx, ry, rw, rh) in p["hits"]:
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return ("bm", kind, payload)
        return ("bm", None, None)

    def bm_click(self, kind, row):
        if kind == "open":
            self.close_bm_panel()
            self.navigate(row["url"])
        elif kind == "delete":
            self.bookmarks.remove(row["id"])
            self.bookmarks_changed()
        elif kind == "manage":
            self.close_bm_panel()
            self.navigate("mercy://bookmarks")
        elif kind == "toggle":
            self.toggle_bookmark()

    def bm_key(self, symbol):
        """Keyboard handling while the quick panel is open. Returns True if consumed."""
        matches = self.bm_matches()
        if symbol == key.ESCAPE:
            self.close_bm_panel()
        elif symbol == key.BACKSPACE:
            self.bm_filter = self.bm_filter[:-1]
            self.bm_index = self.bm_scroll = 0
            self.build_bm_panel()
        elif symbol == key.DOWN:
            self.bm_index = min(len(matches) - 1, self.bm_index + 1)
            self.build_bm_panel()
        elif symbol == key.UP:
            self.bm_index = max(0, self.bm_index - 1)
            self.build_bm_panel()
        elif symbol in (key.ENTER, key.NUM_ENTER):
            if matches:
                self.close_bm_panel()
                self.navigate(matches[self.bm_index]["url"])
        else:
            return False
        return True

    def update_bm_panel(self, dt):
        p, t = self.bm_panel, self.theme
        hit = self.bm_hit(*self.mouse)
        for row in p["rows"]:
            over = hit is not None and hit[2] is row
            keyed = row["index"] == self.bm_index and (hit is None or hit[1] is None)
            row["hover"] = approach(row["hover"], 1.0 if (over or keyed) else 0.0, dt, 22)
            row["hl"].color = t.c("accent", int(38 * row["hover"]))
            on_x = over and hit[1] == "delete"
            row["x_lab"].color = (hex_rgb("#f85149") if on_x else t.c("muted", int(220 * row["hover"])))
        on_foot = hit is not None and hit[1] == "toggle"
        p["foot_hl"].color = t.c("accent", 38 if on_foot else 0)
        p["caret"].color = t.c("accent", 255 if (self.time % 1.0) < 0.6 else 0)

    # ========================================================= header explorer
    def toc_pushes(self):
        """Wide windows make room for the explorer; narrow ones let it float over the page."""
        return self.width - TOC_W >= 640

    def content_left(self):
        return TOC_W * ease(self.toc_t) if self.toc_pushes() else 0.0

    def page_offset_x(self):
        left = self.content_left()
        return round(left + (self.width - left - self.page.width) / 2)

    def toggle_toc(self):
        self.toc_open = not self.toc_open
        if self.toc_open:
            self.build_toc()
        if self.page and abs(self.page.width - self.column_width()) > 0.5:
            self.relayout()
        self.save_settings()

    def toc_headings(self):
        return self.page.headings if self.page else []

    def toc_geometry(self):
        bar_y = self.height - CHROME_H
        list_top = bar_y - 62
        row_h = round(30 * min(1.25, max(0.9, self.zoom)))
        visible = max(1, int((list_top - 14) // row_h))
        return bar_y, list_top, row_h, visible

    def build_toc(self):
        if self.toc:
            for o in self.toc["objs"]:
                o.delete()
        t, f = self.theme, self.ui_font
        b = pyglet.graphics.Batch()
        g = [pyglet.graphics.Group(i) for i in range(4)]
        objs = []
        bar_y, list_top, row_h, visible = self.toc_geometry()
        heads = self.toc_headings()
        self.toc_scroll = max(0, min(self.toc_scroll, len(heads) - visible))

        objs.append(shapes.Rectangle(0, 0, TOC_W, bar_y, color=with_alpha(t.chrome, 255), batch=b, group=g[0]))
        objs.append(shapes.Rectangle(TOC_W - 1, 0, 1, bar_y, color=t.c("border"), batch=b, group=g[1]))
        objs.append(gfx.GradientRect(TOC_W, 0, 14, bar_y, (0, 0, 0, 40 if t.dark else 14), (0, 0, 0, 0),
                                     vertical=False, batch=b, group=g[0]))
        hy = bar_y - 30
        objs += gfx.outline_icon(26, hy, 14, t.c("accent"), b, g[2], width=1.8)
        title = pyglet.text.Label("Contents", font_name=f, font_size=12.5, bold=True, color=t.c("heading"),
                                  x=44, y=hy, anchor_y="center", batch=b, group=g[2])
        objs.append(title)
        if heads:
            objs.append(pyglet.text.Label(str(len(heads)), font_name=f, font_size=10, color=t.c("muted"),
                                          x=44 + title.content_width + 9, y=hy, anchor_y="center",
                                          batch=b, group=g[2]))
        objs.append(shapes.Rectangle(16, bar_y - 52, TOC_W - 32, 1, color=t.c("border"), batch=b, group=g[1]))

        rows = []
        if not heads:
            objs.append(pyglet.text.Label("No headings on this page", font_name=f, font_size=10.5,
                                          color=t.c("muted"), x=TOC_W / 2, y=list_top - row_h / 2,
                                          anchor_x="center", anchor_y="center", batch=b, group=g[2]))
        top_level = min((h["level"] for h in heads), default=1)
        for i in range(self.toc_scroll, min(len(heads), self.toc_scroll + visible)):
            hd = heads[i]
            depth = min(4, hd["level"] - top_level)
            ry = list_top - (i - self.toc_scroll + 1) * row_h
            hl = gfx.rrect(10, ry + 2, TOC_W - 20, row_h - 4, 8, t.c("accent", 0), b, g[1])
            bar = gfx.rrect(10, ry + 6, 3, row_h - 12, 1.5, t.c("accent", 0), b, g[2])
            size = 11 if depth == 0 else 10.5
            bold = depth == 0
            x = 24 + depth * 14
            text = hd["text"]
            font = gfx.font(f, size, bold=bold)
            room = TOC_W - x - 18
            if gfx.text_width(font, text) > room:
                while text and gfx.text_width(font, text + "…") > room:
                    text = text[:-1]
                text = text.rstrip() + "…"
            base = t.c("text") if depth == 0 else mix(t.c("text"), t.c("muted"), min(1.0, 0.35 * depth + 0.2))
            lab = pyglet.text.Label(text, font_name=f, font_size=size, bold=bold, color=base, x=x,
                                    y=ry + row_h / 2, anchor_y="center", batch=b, group=g[3])
            objs += [hl, bar, lab]
            if depth:  # a faint guide line for nested entries
                objs.append(shapes.Rectangle(x - 9, ry + 7, 1, row_h - 14, color=t.c("border"), batch=b, group=g[1]))
            rows.append(dict(index=i, slug=hd["slug"], rect=(10, ry + 2, TOC_W - 20, row_h - 4), hl=hl, bar=bar,
                             label=lab, base=base, hover=0.0, glow=0.0))
        if len(heads) > visible:
            track = visible * row_h
            th = max(24, track * visible / len(heads))
            ty = list_top - th - (track - th) * self.toc_scroll / (len(heads) - visible)
            objs.append(gfx.rrect(TOC_W - 7, ty, 3, th, 1.5, t.c("muted", 110), b, g[2]))
        self.toc = dict(batch=b, objs=objs, rows=rows, visible=visible)

    def current_heading(self):
        """Scroll-spy: the heading whose section is at the top of the view."""
        heads = self.toc_headings()
        if not heads:
            return None
        line = self.scroll + self.S * 2.5
        if self.max_scroll() and self.scroll >= self.max_scroll() - 1:
            line = self.scroll + self.view_top * 0.6  # at the very end, short last sections still count
        cur = None
        for i, h in enumerate(heads):
            if h["y"] <= line:
                cur = i
        return cur

    def toc_hit(self, x, y):
        if self.toc_t < 0.5 or not self.toc:
            return None
        x -= self.toc_x()
        if not (0 <= x <= TOC_W and y < self.height - CHROME_H):
            return None
        for row in self.toc["rows"]:
            rx, ry, rw, rh = row["rect"]
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return ("toc", row)
        return ("toc", None)

    def toc_x(self):
        return round(-TOC_W * (1 - ease(self.toc_t)))

    def update_toc(self, dt):
        self.toc_t = approach(self.toc_t, 1.0 if self.toc_open else 0.0, dt, 12)
        if abs(self.toc_t - (1.0 if self.toc_open else 0.0)) < 0.002:
            self.toc_t = 1.0 if self.toc_open else 0.0
        if not self.toc or self.toc_t <= 0:
            return
        t = self.theme
        cur = self.current_heading()
        if cur != self.toc_current:
            self.toc_current = cur
            vis = self.toc["visible"]
            if cur is not None and not (self.toc_scroll <= cur < self.toc_scroll + vis):
                self.toc_scroll = max(0, cur - vis // 3)
                self.build_toc()
        hit = self.toc_hit(*self.mouse)
        for row in self.toc["rows"]:
            over = hit is not None and hit[1] is row
            on = row["index"] == self.toc_current
            row["hover"] = approach(row["hover"], 1.0 if over else 0.0, dt, 22)
            row["glow"] = approach(row["glow"], 1.0 if on else 0.0, dt, 14)
            row["hl"].color = t.c("accent", int(16 * row["glow"] + 28 * row["hover"]))
            row["bar"].color = t.c("accent", int(255 * row["glow"]))
            row["label"].color = mix(row["base"], t.c("accent"), max(row["glow"], 0.5 * row["hover"]))

    # ==================================================================== tabs
    def layout_key(self):
        """Pages built under a different theme, zoom, width or image set need rebuilding."""
        return (self.theme.name, self.zoom, self.column_width(), self.image_gen)

    @property
    def tab(self):
        return self.tabs[self.tab_i]

    def stash(self):
        """Copy the live page state into the current tab object."""
        for f in TAB_FIELDS:
            setattr(self.tab, f, getattr(self, f))

    def unstash(self):
        for f in TAB_FIELDS:
            setattr(self, f, getattr(self.tab, f))

    def tab_title(self, tab):
        if tab is self.tab and self.page:
            title = self.page.title
        else:
            title = tab.title
        if not title and tab.doc:
            title = tab.doc.title or os.path.basename(urlparse(tab.doc.url).path)
        url = self.url if tab is self.tab else (tab.url or tab.lazy_url or "")
        return title or (short_url(fetch.split_fragment(url)[0]) if url else "New Tab")

    def leave_page(self):
        """Tidy up page-bound UI before the current tab is swapped out."""
        if self.editing:
            self.stop_editing()
        self.clear_selection()
        self.press = None
        self.close_context_menu()
        self.close_bm_panel()
        self.menu_open = False
        if self.page:
            self.page.set_hover(self.hover_link, False)
        self.hover_link = None

    def show_tab(self, i):
        """Show tab i, whose state has already been unstashed or is about to be."""
        self.tab_i = i
        self.unstash()
        tab = self.tab
        if tab.arrived:
            doc, frag, restore = tab.arrived
            tab.arrived = None
            self.on_document(doc, frag, restore)
        elif tab.lazy_url:
            url, tab.lazy_url = tab.lazy_url, None
            self.navigate(url)
        elif self.doc and (self.page is None or self.page_key != self.layout_key()):
            self.relayout()
        self.toc_current = None
        if self.toc_open or self.toc:
            self.build_toc()
        self.fade = max(self.fade, 0.45)
        self.refresh_address()
        self.set_caption(f"{self.tab_title(tab)} — Mercy")
        self.build_tabs()
        self.update_hover()

    def switch_tab(self, i):
        i = max(0, min(i, len(self.tabs) - 1))
        if i == self.tab_i:
            return
        self.leave_page()
        self.stash()
        self.show_tab(i)

    def new_tab(self, url="mercy://home", background=False, focus_address=False, index=None):
        tab = Tab()
        index = self.tab_i + 1 if index is None else index
        if not background:
            self.leave_page()
        self.stash()
        self.tabs.insert(index, tab)
        if index <= self.tab_i:
            self.tab_i += 1
        if background:
            # load it without showing it: borrow the live state for a moment
            current = self.tab_i
            self.tab_i = index
            self.unstash()
            self.navigate(url)
            self.stash()
            self.tab_i = current
            self.unstash()
            self.refresh_address()
            self.toast("Opened in a new tab")
        else:
            self.tab_i = index
            self.unstash()
            self.navigate(url)
            if focus_address:
                self.start_editing(select=False)
                self.edit_text, self.caret = "", 0
                self.refresh_address()
            self.set_caption("New Tab — Mercy")
        self.build_tabs()
        self.save_settings()
        return tab

    def close_tab(self, i=None):
        i = self.tab_i if i is None else i
        if i == self.tab_i:
            self.leave_page()
            self.stash()
        tab = self.tabs[i]
        if tab.history or tab.lazy_url:
            self.closed_tabs.append(dict(history=[dict(e) for e in tab.history], hist_i=tab.hist_i,
                                         url=tab.lazy_url, index=i))
            self.closed_tabs = self.closed_tabs[-25:]
        if tab.page:
            tab.page.delete()
        del self.tabs[i]
        if not self.tabs:  # closing the last tab leaves a fresh start page rather than quitting
            self.tabs.append(Tab())
            self.tab_i = 0
            self.unstash()
            self.navigate("mercy://home")
            self.build_tabs()
        elif i == self.tab_i:
            self.show_tab(min(i, len(self.tabs) - 1))
        else:
            if i < self.tab_i:
                self.tab_i -= 1
            self.build_tabs()
        self.save_settings()

    def close_other_tabs(self, keep):
        for i in reversed(range(len(self.tabs))):
            if i != keep:
                if i < keep:
                    keep -= 1
                self.close_tab(i)

    def open_with_history(self, index, history, hist_i, scroll=0.0):
        """Open a tab that already has a back/forward history (reopened or duplicated)."""
        self.leave_page()
        self.stash()
        self.tabs.insert(index, Tab())
        self.tab_i = index
        self.unstash()
        self.history, self.hist_i = [dict(e) for e in history], hist_i
        e = self.history[self.hist_i]
        self.navigate(e["url"], push=False, restore=scroll or e.get("scroll", 0.0))
        self.build_tabs()
        self.save_settings()

    def reopen_closed_tab(self):
        if not self.closed_tabs:
            self.toast("No recently closed tabs")
            return
        info = self.closed_tabs.pop()
        index = min(info["index"], len(self.tabs))
        if info["url"]:
            self.new_tab(info["url"], index=index)
        else:
            self.open_with_history(index, info["history"], info["hist_i"])

    def duplicate_tab(self, i):
        if i == self.tab_i:
            self.stash()
        src = self.tabs[i]
        if src.history:
            self.open_with_history(i + 1, src.history, src.hist_i, src.scroll)
        else:
            self.new_tab(src.lazy_url or "mercy://home", index=i + 1)

    def open_in_new_tab(self, href):
        if href.startswith(("mercy-copy:", "mercy-bm:", "mercy-external:", "mailto:")):
            self.follow(href)
            return
        url = self.resolve_href(href)
        if is_audio(url):
            self.play_audio(url)
            return
        self.new_tab(url, background=True)

    def quit(self):
        self.save_settings()
        self.close()

    def on_close(self):
        self.save_settings()
        super().on_close()

    def session(self):
        """The open tabs, for saving: (url, title) pairs and the active index."""
        out, active = [], 0
        for i, tab in enumerate(self.tabs):
            url = self.url if i == self.tab_i else (tab.url or tab.lazy_url)
            if not url or url.startswith("mercy://pasted"):
                continue
            if i == self.tab_i:
                active = len(out)
            out.append(dict(url=url, title=self.tab_title(tab)))
        return out, active

    def restore_session(self, saved, active):
        self.tabs = []
        for entry in saved:
            tab = Tab()
            tab.lazy_url, tab.title = entry.get("url"), entry.get("title", "")
            self.tabs.append(tab)
        self.tab_i = max(0, min(active, len(self.tabs) - 1))
        self.show_tab(self.tab_i)

    # --------------------------------------------------------------- tab strip
    def tab_geometry(self):
        n = len(self.tabs)
        avail = self.width - 16 - 44
        tw = max(44, min(224, avail / max(1, n)))
        return 8, tw

    def build_tabs(self):
        if not self.chrome:
            return
        if self.tab_strip:
            for o in self.tab_strip["objs"]:
                o.delete()
        t, f = self.theme, self.ui_font
        b = pyglet.graphics.Batch()
        g = [pyglet.graphics.Group(i) for i in range(7)]
        objs = []
        w, h = self.width, self.height
        y0 = h - TABS_H
        th = TABS_H - 7
        cy = y0 + th / 2
        strip = mix(t.chrome, (0, 0, 0, 255), 0.42 if t.dark else 0.07)
        active_c = with_alpha(t.chrome, 255)
        objs.append(shapes.Rectangle(0, y0, w, TABS_H, color=strip, batch=b, group=g[0]))
        x0, tw = self.tab_geometry()
        r = 9
        rows = []
        for i, tab in enumerate(self.tabs):
            x = x0 + i * tw
            active = i == self.tab_i
            hover_bg = gfx.rrect(x + 3, y0 + 4, tw - 6, th - 6, 8, t.c("text", 0), b, g[1])
            objs.append(hover_bg)
            if active:
                objs.append(shapes.RoundedRectangle(x, y0, tw, th, (0, r, r, 0), segments=10, color=active_c,
                                                    batch=b, group=g[2]))
                # the flared feet where the tab melts into the toolbar
                for fx, cx in ((x - r, x - r), (x + tw, x + tw + r)):
                    objs.append(shapes.Rectangle(fx, y0, r, r, color=active_c, batch=b, group=g[2]))
                    objs.append(shapes.Circle(cx, y0 + r, r, segments=24, color=strip, batch=b, group=g[3]))
                objs.append(gfx.GradientRect(x + r, y0 + th - 2, (tw - 2 * r) / 2, 2, t.c("accent", 0),
                                             t.c("accent"), vertical=False, batch=b, group=g[4]))
                objs.append(gfx.GradientRect(x + tw / 2, y0 + th - 2, (tw - 2 * r) / 2, 2, t.c("accent2"),
                                             t.c("accent2", 0), vertical=False, batch=b, group=g[4]))
            elif i + 1 != self.tab_i and i + 1 < len(self.tabs):
                objs.append(shapes.Rectangle(x + tw - 0.5, cy - th * 0.25, 1, th * 0.5, color=t.c("border"),
                                             batch=b, group=g[1]))
            # icon: a dot that turns into a spinner while loading
            ix = x + 16
            url = self.url if active else (tab.url or tab.lazy_url or "")
            dot_c = t.c("accent") if url.startswith(("http://", "https://")) else t.c("accent2")
            dot = shapes.Circle(ix, cy, 3.6, segments=16, color=dot_c, batch=b, group=g[4])
            ring = shapes.Arc(ix, cy, 6.5, segments=20, thickness=1.1, color=with_alpha(dot_c, 90), batch=b, group=g[4])
            spin = shapes.Arc(ix, cy, 6, segments=20, angle=math.radians(260), thickness=1.8,
                              color=t.c("accent", 0), batch=b, group=g[4])
            objs += [dot, ring, spin]
            show_close = tw >= 84 or active
            room = tw - 30 - (26 if show_close else 8)
            title = self.tab_title(tab)
            font = gfx.font(f, 10.5, bold=active)
            if room < 14:
                title = ""
            elif gfx.text_width(font, title) > room:
                while title and gfx.text_width(font, title + "…") > room:
                    title = title[:-1]
                title = title.rstrip() + "…"
            lab = pyglet.text.Label(title, font_name=f, font_size=10.5, bold=active,
                                    color=t.c("heading") if active else t.c("muted"), x=x + 28, y=cy,
                                    anchor_y="center", batch=b, group=g[5])
            objs.append(lab)
            close_bg = shapes.Circle(x + tw - 17, cy, 9, segments=20, color=t.c("text", 0), batch=b, group=g[4])
            c = 3.4
            close_lines = [shapes.Line(x + tw - 17 - c, cy - c, x + tw - 17 + c, cy + c, 1.5, color=t.c("muted", 0),
                                       batch=b, group=g[6]),
                           shapes.Line(x + tw - 17 - c, cy + c, x + tw - 17 + c, cy - c, 1.5, color=t.c("muted", 0),
                                       batch=b, group=g[6])]
            objs += [close_bg] + close_lines
            rows.append(dict(index=i, rect=(x, y0, tw, th), close=(x + tw - 27, cy - 10, 20, 20) if show_close else None,
                             hover_bg=hover_bg, dot=dot, ring=ring, spin=spin, close_bg=close_bg,
                             close_lines=close_lines, hover=0.0, chover=0.0, active=active))
        # a hairline along the strip's foot, broken where the active tab joins the toolbar
        ax = x0 + self.tab_i * tw
        for lx, lw in ((0, ax - r), (ax + tw + r, w - (ax + tw + r))):
            if lw > 0:
                objs.append(shapes.Rectangle(lx, y0, lw, 1, color=t.c("border"), batch=b, group=g[1]))
        # new-tab button
        px = x0 + len(self.tabs) * tw + 20
        plus_bg = shapes.Circle(px, cy, 13, segments=24, color=t.c("text", 0), batch=b, group=g[1])
        objs.append(plus_bg)
        objs.append(shapes.Line(px - 5.5, cy, px + 5.5, cy, 1.8, color=t.c("muted"), batch=b, group=g[4]))
        objs.append(shapes.Line(px, cy - 5.5, px, cy + 5.5, 1.8, color=t.c("muted"), batch=b, group=g[4]))
        self.tab_strip = dict(batch=b, objs=objs, rows=rows, plus=(px, cy), plus_bg=plus_bg, plus_hover=0.0)

    def tab_hit(self, x, y):
        if not self.tab_strip or y < self.height - TABS_H:
            return None
        for row in self.tab_strip["rows"]:
            if row["close"]:
                cx, cy_, cw, ch = row["close"]
                if cx <= x <= cx + cw and cy_ <= y <= cy_ + ch:
                    return ("tab", row["index"], "close")
            rx, ry, rw, rh = row["rect"]
            if rx <= x <= rx + rw:
                return ("tab", row["index"], "body")
        px, py = self.tab_strip["plus"]
        if (x - px) ** 2 + (y - py) ** 2 <= 15 ** 2:
            return ("newtab",)
        return ("tabstrip",)

    def update_tabs(self, dt):
        ts, t = self.tab_strip, self.theme
        if not ts:
            return
        hit = self.hover if self.hover and self.hover[0] in ("tab", "newtab") else None
        for row in ts["rows"]:
            i = row["index"]
            tab = self.tabs[i] if i < len(self.tabs) else None
            if tab is None:
                continue
            over = hit is not None and hit[0] == "tab" and hit[1] == i
            on_close = over and hit[2] == "close"
            row["hover"] = approach(row["hover"], 1.0 if over else 0.0, dt, 20)
            row["chover"] = approach(row["chover"], 1.0 if on_close else 0.0, dt, 24)
            if not row["active"]:
                row["hover_bg"].color = t.c("text", int(16 * row["hover"]))
            loading = (self.loading if i == self.tab_i else tab.loading and not tab.arrived)
            row["spin"].color = t.c("accent", 255 if loading else 0)
            row["spin"].rotation = (self.time * 400) % 360
            row["dot"].visible = row["ring"].visible = not loading
            vis = 1.0 if row["active"] else row["hover"]
            if row["close"] is None:
                vis = 0.0
            row["close_bg"].color = t.c("text", int(26 * row["chover"]))
            col = mix(t.c("muted"), t.c("heading"), row["chover"])
            for ln in row["close_lines"]:
                ln.color = with_alpha(col, int(255 * vis))
        on_plus = hit == ("newtab",)
        ts["plus_hover"] = approach(ts["plus_hover"], 1.0 if on_plus else 0.0, dt, 20)
        ts["plus_bg"].color = t.c("text", int(22 * ts["plus_hover"]))

    def press_tab(self, h, button, x):
        if h[0] == "newtab":
            if button == mouse.LEFT:
                self.new_tab(focus_address=True)
            return
        if h[0] == "tabstrip":
            if button == mouse.LEFT and self.count_clicks(x, self.height) >= 2:
                self.new_tab(focus_address=True)
            elif button == mouse.MIDDLE:
                self.new_tab(focus_address=True)
            return
        _, i, part = h
        if button == mouse.MIDDLE or (button == mouse.LEFT and part == "close"):
            self.close_tab(i)
        elif button == mouse.LEFT:
            self.switch_tab(i)
            self.tab_drag = self.tab_i

    def drag_tab(self, x):
        x0, tw = self.tab_geometry()
        j = max(0, min(len(self.tabs) - 1, int((x - x0) // tw)))
        if j != self.tab_drag:
            tab = self.tabs.pop(self.tab_drag)
            self.tabs.insert(j, tab)
            self.tab_i = self.tab_drag = j
            self.build_tabs()

    def tab_menu_items(self, i):
        return [("New Tab", lambda: self.new_tab(focus_address=True), True, "Ctrl+T"),
                ("Duplicate Tab", lambda: self.duplicate_tab(i), True, ""),
                None,
                ("Close Tab", lambda: self.close_tab(i), True, "Ctrl+W"),
                ("Close Other Tabs", lambda: self.close_other_tabs(i), len(self.tabs) > 1, ""),
                ("Reopen Closed Tab", self.reopen_closed_tab, bool(self.closed_tabs), "Ctrl+Shift+T")]

    # ============================================================ music player
    def music_changed(self):
        self.music_dirty = True
        if self.music.error:
            self.toast(self.music.error)

    def play_audio(self, url):
        """Play an audio link, queueing every other audio link on the page around it."""
        links = []
        if self.page:
            for link in self.page.links:
                u = self.resolve_href(link.href)
                if is_audio(u) and u not in links:
                    links.append(u)
        if url not in links:
            links = [url]
        self.music.play_queue(links, links.index(url))
        if len(links) > 1:
            self.toast(f"Playing {links.index(url) + 1} of {len(links)}")

    def music_geometry(self):
        left = self.content_left()
        w = min(660, self.width - left - 32)
        x = round(left + (self.width - left - w) / 2)
        y = round(14 - (MUSIC_H + 40) * (1 - ease(self.music_t)))
        return x, y, w, MUSIC_H

    def music_lift(self):
        """How far bottom overlays (status, toast) move up to clear the player."""
        return (MUSIC_H + 18) * ease(self.music_t)

    def build_music(self):
        self.music_dirty = False
        if self.music_ui:
            for o in self.music_ui["objs"]:
                o.delete()
            self.music_ui = None
        mp, t, f = self.music, self.theme, self.ui_font
        if not mp.active:
            return
        b = pyglet.graphics.Batch()
        g = [pyglet.graphics.Group(i) for i in range(6)]
        objs = []
        _, _, w, h = self.music_geometry()
        objs += gfx.soft_shadow(0, 0, w, h, 18, b, g[0], strength=130 if t.dark else 55, spread=18, offset=7)
        objs.append(gfx.rrect(-1, -1, w + 2, h + 2, 19, t.c("border"), b, g[0]))
        objs.append(gfx.rrect(0, 0, w, h, 18, with_alpha(t.chrome, 250), b, g[0]))

        # artwork tile with a little animated equaliser
        objs.append(gfx.rrect(12, 14, 40, 40, 11, t.c("accent", 55), b, g[1]))
        objs.append(gfx.GradientRect(14, 16, 36, 36, t.c("accent", 40), t.c("accent2", 70), batch=b, group=g[1]))
        bars = [shapes.Rectangle(19 + i * 6.4, 22, 4, 6, color=t.c("heading", 230), batch=b, group=g[2]) for i in range(5)]
        spin = shapes.Arc(32, 34, 9, segments=24, angle=math.radians(260), thickness=2,
                          color=t.c("heading", 0), batch=b, group=g[2])
        objs += bars + [spin]

        # title and details
        cx = w - 200
        room = cx - 64 - 64
        title = mp.title or "Untitled"
        bold = gfx.font(f, 11.5, bold=True)
        while title and gfx.text_width(bold, title + "…") > room and gfx.text_width(bold, title) > room:
            title = title[:-1]
        if title != (mp.title or "Untitled"):
            title = title.rstrip() + "…"
        objs.append(pyglet.text.Label(title, font_name=f, font_size=11.5, bold=True, color=t.c("heading"),
                                      x=64, y=42, anchor_y="center", batch=b, group=g[3]))
        sub = pyglet.text.Label("", font_name=f, font_size=9.5, color=t.c("muted"), x=64, y=25,
                                anchor_y="center", batch=b, group=g[3])
        objs.append(sub)

        # transport controls
        def round_button(x, r, color):
            c = shapes.Circle(x, 34, r, segments=32, color=color, batch=b, group=g[1])
            objs.append(c)
            return c
        prev_bg, next_bg = round_button(cx - 42, 16, t.c("text", 0)), round_button(cx + 42, 16, t.c("text", 0))
        play_bg = round_button(cx, 19, t.c("accent"))
        ic = t.c("text")
        for x, d in ((cx - 42, -1), (cx + 42, 1)):  # skip icons: a triangle against a bar
            objs.append(shapes.Triangle(x - 4 * d, 40, x - 4 * d, 28, x + 4 * d, 34, color=ic, batch=b, group=g[2]))
            objs.append(shapes.Rectangle(x + 4 * d - (1 if d > 0 else 1), 28, 2.2, 12, color=ic, batch=b, group=g[2]))
        on = t.c("surface") if t.dark else (255, 255, 255, 255)
        if mp.playing or mp.loading:
            objs.append(shapes.Rectangle(cx - 5.5, 27, 4, 14, color=on, batch=b, group=g[2]))
            objs.append(shapes.Rectangle(cx + 1.5, 27, 4, 14, color=on, batch=b, group=g[2]))
        else:
            objs.append(shapes.Triangle(cx - 4, 42, cx - 4, 26, cx + 8, 34, color=on, batch=b, group=g[2]))

        # volume: a little speaker, its sound waves, and a slider
        vx = w - 124
        objs.append(shapes.Rectangle(vx - 6, 31, 4, 6, color=ic, batch=b, group=g[2]))
        objs.append(shapes.Triangle(vx - 3, 34, vx + 3, 40, vx + 3, 28, color=ic, batch=b, group=g[2]))
        objs.append(shapes.Rectangle(vx - 3, 31, 6, 6, color=ic, batch=b, group=g[2]))
        waves = [shapes.Arc(vx + 4, 34, r, segments=12, angle=math.radians(90), start_angle=math.radians(-45),
                            thickness=1.5, color=ic, batch=b, group=g[2]) for r in (5, 9)]
        objs += waves
        sx0, sx1 = w - 104, w - 48
        objs.append(gfx.rrect(sx0, 32.5, sx1 - sx0, 3, 1.5, t.c("muted", 90), b, g[1]))
        vol_fill = gfx.rrect(sx0, 32.5, max(3, (sx1 - sx0) * mp.volume), 3, 1.5, t.c("accent"), b, g[2])
        vol_knob = shapes.Circle(sx0 + (sx1 - sx0) * mp.volume, 34, 5, segments=16, color=t.c("heading"),
                                 batch=b, group=g[3])
        objs += [vol_fill, vol_knob]

        # close
        close_bg = round_button(w - 22, 12, t.c("text", 0))
        c = 3.6
        objs += [shapes.Line(w - 22 - c, 34 - c, w - 22 + c, 34 + c, 1.5, color=t.c("muted"), batch=b, group=g[2]),
                 shapes.Line(w - 22 - c, 34 + c, w - 22 + c, 34 - c, 1.5, color=t.c("muted"), batch=b, group=g[2])]

        # progress along the bottom edge
        px0, px1 = 16, w - 16
        objs.append(gfx.rrect(px0, 6, px1 - px0, 3, 1.5, t.c("muted", 70), b, g[1]))
        prog = gfx.GradientRect(px0, 6, 1, 3, t.c("accent"), t.c("accent2"), vertical=False, batch=b, group=g[2])
        knob = shapes.Circle(px0, 7.5, 5, segments=16, color=t.c("heading", 0), batch=b, group=g[3])
        objs += [prog, knob]

        self.music_ui = dict(
            batch=b, objs=objs, w=w, sub=sub, bars=bars, spin=spin, prog=prog, knob=knob, track=(px0, px1),
            vol=(sx0, sx1), vol_fill=vol_fill, vol_knob=vol_knob, waves=waves,
            buttons={"prev": (cx - 42, 16, prev_bg), "next": (cx + 42, 16, next_bg), "play": (cx, 19, play_bg),
                     "close": (w - 22, 12, close_bg)},
            hover={}, playing=mp.playing, sub_text=None)

    def music_hit(self, x, y):
        if not self.music_ui or self.music_t < 0.5:
            return None
        mx, my, w, h = self.music_geometry()
        x, y = x - mx, y - my
        if not (-2 <= x <= w + 2 and -2 <= y <= h + 2):
            return None
        if y <= 15:
            return ("music", "seek")
        for name, (bx, r, _) in self.music_ui["buttons"].items():
            if (x - bx) ** 2 + (y - 34) ** 2 <= (r + 3) ** 2:
                return ("music", name)
        sx0, sx1 = self.music_ui["vol"]
        if sx0 - 8 <= x <= sx1 + 8 and 22 <= y <= 46:
            return ("music", "volume")
        return ("music", None)

    def music_press(self, part, x):
        mp = self.music
        if part == "play":
            mp.toggle()
        elif part == "prev":
            mp.previous()
        elif part == "next":
            mp.next()
        elif part == "close":
            mp.stop()
        elif part in ("seek", "volume"):
            self.music_drag = part
            self.music_drag_to(x)

    def music_drag_to(self, x):
        mx = self.music_geometry()[0]
        ui = self.music_ui
        if not ui:
            return
        if self.music_drag == "seek":
            a, b_ = ui["track"]
            if self.music.duration:
                self.music.seek((x - mx - a) / (b_ - a) * self.music.duration)
        elif self.music_drag == "volume":
            a, b_ = ui["vol"]
            self.music.set_volume((x - mx - a) / (b_ - a))
            self.music_dirty = True

    def update_music(self, dt):
        mp, t = self.music, self.theme
        mp.poll()
        self.music_t = approach(self.music_t, 1.0 if mp.active else 0.0, dt, 11)
        if self.music_dirty or (self.music_ui and self.music_ui["playing"] != mp.playing) \
                or (mp.active and not self.music_ui):
            self.build_music()
        ui = self.music_ui
        if not ui:
            return
        # details line: artist, time, place in the queue
        if mp.loading:
            text = "Loading…"
        elif mp.error:
            text = mp.error
        else:
            parts = [mp.artist] if mp.artist else []
            parts.append(f"{fmt_time(mp.position)} / {fmt_time(mp.duration)}" if mp.duration
                         else fmt_time(mp.position))
            if len(mp.queue) > 1:
                parts.append(f"{mp.index + 1} of {len(mp.queue)}")
            text = "  ·  ".join(parts)
        if text != ui["sub_text"]:
            ui["sub_text"] = text
            font, room = gfx.font(self.ui_font, 9.5), ui["w"] - 200 - 64 - 56
            while gfx.text_width(font, text) > room and len(text) > 1:
                text = text[:-2].rstrip() + "…"
            ui["sub"].text = text
        # equaliser bars bounce while playing (decorative), rest while paused
        for i, bar in enumerate(ui["bars"]):
            if mp.playing:
                level = 0.5 + 0.5 * math.sin(self.time * (5.1 + i * 1.7) + i * 1.3) * math.sin(self.time * 2.3 + i)
                bar.height = 4 + 20 * abs(level)
            else:
                bar.height = approach(bar.height, 4, dt, 10)
            bar.color = t.c("heading", 0 if mp.loading else 230)
        ui["spin"].color = t.c("heading", 255 if mp.loading else 0)
        ui["spin"].rotation = (self.time * 400) % 360
        # progress
        a, b_ = ui["track"]
        frac = (mp.position / mp.duration) if mp.duration else 0.0
        frac = max(0.0, min(1.0, frac))
        ui["prog"].width = max(1.0, (b_ - a) * frac)
        ui["knob"].x = a + (b_ - a) * frac
        over_seek = self.hover == ("music", "seek") or self.music_drag == "seek"
        ui["knob"].color = t.c("heading", 255 if over_seek and mp.duration else 0)
        # hover glow on buttons
        for name, (_, _, bg) in ui["buttons"].items():
            v = approach(ui["hover"].get(name, 0.0), 1.0 if self.hover == ("music", name) else 0.0, dt, 22)
            ui["hover"][name] = v
            if name == "play":
                bg.color = mix(t.c("accent"), t.c("accent2"), 0.45 * v)
            else:
                bg.color = t.c("text", int(24 * v))
        for i, wv in enumerate(ui["waves"]):
            wv.visible = mp.volume > (0.02 if i == 0 else 0.5)

    # ================================================================ printing
    def open_print(self):
        if not (self.page and self.doc) or self.loading:
            self.toast("Nothing to print yet")
            return
        self.leave_page()
        names, default = printing.printers()
        dests = names + [printing.SAVE_AS_PDF]
        self.pr = dict(dests=dests, dest=dests.index(default) if default in dests else len(dests) - 1,
                       paper=self.settings_paper or printing.default_paper(), copies=1, job=None,
                       preview=0, thumbs={}, busy=True, t=0.0, hover={})
        self.build_print()
        pyglet.clock.schedule_once(lambda dt: self.prepare_print(), 0.05)  # let the panel appear first

    def prepare_print(self):
        pr = self.pr
        if not pr:
            return
        for tex in pr["thumbs"].values():
            tex.delete()
        pr["thumbs"] = {}
        base = fetch.split_fragment(self.url)[0]
        title = self.tab_title(self.tab)
        try:
            pr["job"] = printing.render(self, self.doc.text, self.theme, self.images,
                                        lambda src: fetch.resolve(base, src), title, base, pr["paper"])
        except Exception as e:
            self.close_print()
            self.toast(f"Couldn't prepare the pages ({type(e).__name__})")
            return
        pr["busy"], pr["preview"] = False, 0
        self.build_print()

    def close_print(self):
        if not self.pr:
            return
        for tex in self.pr["thumbs"].values():
            tex.delete()
        if self.print_ui:
            for o in self.print_ui["objs"]:
                o.delete()
        self.pr = self.print_ui = None
        self.update_hover()

    def print_geometry(self):
        w = min(800, self.width - 40)
        h = min(540, self.height - 60)
        return round((self.width - w) / 2), round((self.height - h) / 2), w, h

    def print_thumb(self, n):
        pr = self.pr
        if n not in pr["thumbs"]:
            w, h, rgb = pr["job"].pages[n]
            pr["thumbs"][n] = pyglet.image.ImageData(w, h, "RGB", rgb, pitch=-w * 3).get_texture()
        return pr["thumbs"][n]

    def build_print(self):
        if self.print_ui:
            for o in self.print_ui["objs"]:
                o.delete()
        pr, t, f = self.pr, self.theme, self.ui_font
        b = pyglet.graphics.Batch()
        g = [pyglet.graphics.Group(i) for i in range(6)]
        objs, hits = [], {}
        W, H = self.width, self.height
        px, py, w, h = self.print_geometry()
        backdrop = shapes.Rectangle(0, 0, W, H, color=(0, 0, 0, 0), batch=b, group=g[0])
        objs.append(backdrop)
        objs += gfx.soft_shadow(px, py, w, h, 18, b, g[1], strength=150, spread=26, offset=10)
        objs.append(gfx.rrect(px - 1, py - 1, w + 2, h + 2, 19, t.c("border"), b, g[1]))
        objs.append(gfx.rrect(px, py, w, h, 18, with_alpha(t.chrome, 255), b, g[1]))

        def label(text, x, y, size=11, color=None, bold=False, anchor_x="left"):
            lab = pyglet.text.Label(text, font_name=f, font_size=size, bold=bold, color=color or t.c("text"),
                                    x=x, y=y, anchor_x=anchor_x, anchor_y="center", batch=b, group=g[4])
            objs.append(lab)
            return lab

        def pill(name, x, y, pw, ph, color, radius=None):
            r = gfx.rrect(x, y, pw, ph, ph / 2 if radius is None else radius, color, b, g[2])
            objs.append(r)
            hits[name] = (x, y, pw, ph, r)
            return r

        # preview: the current page on a little desk
        bx, by, bw, bh = px + 24, py + 64, 300, h - 88
        objs.append(gfx.rrect(bx, by, bw, bh, 12, t.c("code_bg"), b, g[2]))
        job = pr["job"]
        if pr["busy"] or not job:
            objs.append(shapes.Arc(bx + bw / 2, by + bh / 2 + 8, 14, segments=28, angle=math.radians(260),
                                   thickness=2.5, color=t.c("accent"), batch=b, group=g[3]))
            label("Preparing pages…", bx + bw / 2, by + bh / 2 - 26, 10.5, t.c("muted"), anchor_x="center")
            pr["spinner"] = objs[-2]
        else:
            tex = self.print_thumb(pr["preview"])
            scale = min((bw - 36) / tex.width, (bh - 36) / tex.height)
            tw, th = tex.width * scale, tex.height * scale
            tx, ty = bx + (bw - tw) / 2, by + (bh - th) / 2 + 6
            objs += gfx.soft_shadow(tx, ty, tw, th, 2, b, g[3], strength=90, spread=10, offset=4)
            sprite = pyglet.sprite.Sprite(tex, x=round(tx), y=round(ty), batch=b, group=g[4])
            sprite.scale = scale
            objs.append(sprite)
            n = len(job.pages)
            label(f"Page {pr['preview'] + 1} of {n}", bx + bw / 2, py + 40, 10.5, t.c("muted"), anchor_x="center")
            for name, cx, d in (("prev_page", bx + 40, "left"), ("next_page", bx + bw - 40, "right")):
                enabled = (pr["preview"] > 0) if d == "left" else (pr["preview"] < n - 1)
                pill(name, cx - 16, py + 24, 32, 32, t.c("text", 0))
                objs += gfx.chevron(cx, py + 40, 12, d, t.c("text") if enabled else t.c("muted", 80), b, g[4],
                                    width=1.8)
                if not enabled:
                    del hits[name]

        # options
        # readable text on the accent colour: dark on light accents, white on dark ones
        r_, g_, b_ = t.c("accent")[:3]
        on_accent = (20, 20, 24, 255) if 0.2126 * r_ + 0.7152 * g_ + 0.0722 * b_ > 150 else (255, 255, 255, 255)
        rx, top = px + 352, py + h - 44
        label("Print", rx, top, 17, t.c("heading"), bold=True)
        title = self.tab_title(self.tab)
        label(title if len(title) < 48 else title[:47] + "…", rx, top - 26, 10.5, t.c("muted"))
        vx, vw = rx + 112, px + w - 24 - (rx + 112)
        dest = pr["dests"][pr["dest"]]
        saving = dest == printing.SAVE_AS_PDF

        y = top - 92
        label("Destination", rx, y, 10.5, t.c("muted"))
        pill("dest", vx, y - 17, vw, 34, t.c("text", 14))
        label(dest if len(dest) < 30 else dest[:29] + "…", vx + 16, y, 11)
        if len(pr["dests"]) > 1:
            objs.append(shapes.Line(vx + vw - 26, y + 2, vx + vw - 21, y - 3, 1.6, color=t.c("muted"), batch=b, group=g[4]))
            objs.append(shapes.Line(vx + vw - 21, y - 3, vx + vw - 16, y + 2, 1.6, color=t.c("muted"), batch=b, group=g[4]))

        y -= 54
        label("Paper", rx, y, 10.5, t.c("muted"))
        for i, size in enumerate(("A4", "Letter")):
            on = pr["paper"] == size
            pill(f"paper:{size}", vx + i * 104, y - 17, 98, 34, t.c("accent") if on else t.c("text", 14))
            label(size, vx + i * 104 + 49, y, 11, on_accent if on else t.c("text"), bold=on,
                  anchor_x="center")

        y -= 54
        label("Copies", rx, y, 10.5, t.c("muted") if not saving else t.c("muted", 110))
        if not saving:
            pill("copies-", vx, y - 17, 34, 34, t.c("text", 14))
            label("−", vx + 17, y + 1, 14, anchor_x="center")
            label(str(pr["copies"]), vx + 60, y, 12, t.c("heading"), bold=True, anchor_x="center")
            pill("copies+", vx + 86, y - 17, 34, 34, t.c("text", 14))
            label("+", vx + 103, y + 1, 14, anchor_x="center")
        else:
            label("—", vx + 17, y, 11, t.c("muted", 110))

        y -= 52
        if job:
            pages = len(job.pages)
            info = f"{pages} page{'s' if pages != 1 else ''} · {pr['paper']} · {printing.DPI} dpi · black on white"
        else:
            info = "Laying out the pages for paper…"
        label(info, rx, y, 10, t.c("muted"))
        if len(pr["dests"]) == 1:
            label("No printers are set up, so you can save a PDF instead.", rx, y - 22, 10, t.c("muted"))

        # buttons
        bw2, gap, by2 = 118, 10, py + 22
        x = px + w - 24 - bw2
        primary = "Save PDF" if saving else "Print"
        pill("go", x, by2, bw2, 38, t.c("accent"), radius=12)
        label(primary, x + bw2 / 2, by2 + 19, 11.5, on_accent, bold=True, anchor_x="center")
        x -= 112 + gap
        pill("open", x, by2, 112, 38, t.c("text", 14), radius=12)
        label("Open PDF", x + 56, by2 + 19, 11, anchor_x="center")
        x -= 92 + gap
        pill("cancel", x, by2, 92, 38, t.c("text", 0), radius=12)
        label("Cancel", x + 46, by2 + 19, 11, t.c("muted"), anchor_x="center")
        if pr["busy"]:
            for k in ("go", "open"):
                hits.pop(k, None)
        self.print_ui = dict(batch=b, objs=objs, hits=hits, backdrop=backdrop, panel=(px, py, w, h))

    def print_hit(self, x, y):
        if not self.print_ui:
            return None
        for name, (hx, hy, hw, hh, _) in self.print_ui["hits"].items():
            if hx <= x <= hx + hw and hy <= y <= hy + hh:
                return ("print", name)
        px, py, w, h = self.print_ui["panel"]
        return ("print", None if px <= x <= px + w and py <= y <= py + h else "backdrop")

    def print_click(self, part):
        pr = self.pr
        if part in ("cancel", "backdrop"):
            self.close_print()
        elif part == "dest":
            pr["dest"] = (pr["dest"] + 1) % len(pr["dests"])
            self.build_print()
        elif part and part.startswith("paper:"):
            paper = part.split(":")[1]
            if paper != pr["paper"]:
                pr["paper"], pr["busy"] = paper, True
                self.settings_paper = paper
                self.save_settings()
                self.build_print()
                pyglet.clock.schedule_once(lambda dt: self.prepare_print(), 0.05)
        elif part in ("copies-", "copies+"):
            pr["copies"] = max(1, min(99, pr["copies"] + (1 if part == "copies+" else -1)))
            self.build_print()
        elif part in ("prev_page", "next_page"):
            self.flip_preview(-1 if part == "prev_page" else 1)
        elif part == "open":
            path = printing.temp_pdf(pr["job"])
            self.close_print()
            printing.open_file(path)
            self.toast("Opened the PDF")
        elif part == "go":
            self.print_go()

    def flip_preview(self, step):
        pr = self.pr
        if pr and pr["job"]:
            pr["preview"] = max(0, min(len(pr["job"].pages) - 1, pr["preview"] + step))
            self.build_print()

    def print_go(self):
        pr = self.pr
        job, dest = pr["job"], pr["dests"][pr["dest"]]
        if dest == printing.SAVE_AS_PDF:
            path = printing.save_location(job.title)
            try:
                printing.write_pdf(job, str(path))
            except OSError as e:
                self.toast(f"Couldn't save the PDF ({e.strerror or e})")
                return
            self.close_print()
            home = str(Path.home())
            shown = "~" + str(path)[len(home):] if str(path).startswith(home) else str(path)
            self.toast(f"Saved {shown}")
            return
        path = printing.temp_pdf(job)
        copies, paper = pr["copies"], pr["paper"]
        self.close_print()
        self.toast(f"Printing {len(job.pages)} page{'s' if len(job.pages) != 1 else ''}…")
        self.worker.submit(("print",), printing.send_to_printer, path, dest, copies, paper)

    def update_print(self, dt):
        if not self.print_ui:
            return
        pr, t = self.pr, self.theme
        pr["t"] = approach(pr["t"], 1.0, dt, 14)
        self.print_ui["backdrop"].color = (0, 0, 0, int((120 if t.dark else 90) * pr["t"]))
        if pr["busy"] and pr.get("spinner"):
            pr["spinner"].rotation = (self.time * 400) % 360
        hit = self.print_hit(*self.mouse)
        for name, (_, _, _, _, shape) in self.print_ui["hits"].items():
            v = approach(pr["hover"].get(name, 0.0), 1.0 if hit == ("print", name) else 0.0, dt, 22)
            pr["hover"][name] = v
            if name == "go":
                shape.color = mix(t.c("accent"), t.c("accent2"), 0.35 * v)
            elif name.startswith("paper:") and pr["paper"] == name.split(":")[1]:
                continue
            else:
                shape.color = t.c("text", int(14 + 20 * v))

    # ------------------------------------------------------------ context menu
    def open_context_menu(self, x, y, h):
        """Right-click menu; its items depend on what was clicked."""
        clip = bool((self.get_clipboard_text() or "").strip())
        items = []
        if h == ("addr",):
            if not self.editing:
                self.start_editing()
            sel = self.edit_selection() is not None
            items = [("Cut", lambda: self.edit_copy(cut=True), sel, "Ctrl+X"),
                     ("Copy", self.edit_copy, sel, "Ctrl+C"),
                     ("Paste", self.edit_paste, clip, "Ctrl+V"),
                     ("Paste & Go", lambda: self.paste_and_go(), clip, "Ctrl+Shift+V"),
                     None,
                     ("Select All", self.edit_select_all, bool(self.edit_text), "Ctrl+A")]
        elif h is None or h[0] in ("link",):
            if self.editing:
                self.stop_editing()
            if h and h[0] == "link":
                href = self.page.links[h[1]].href
                target = self.resolve_href(href)
                if not href.startswith(("mercy-copy:", "mercy-external:")):
                    items += [("Open Link", lambda: self.follow(href), True, ""),
                              ("Open Link in New Tab", lambda: self.open_in_new_tab(href), True, "Ctrl+Click"),
                              ("Copy Link Address", lambda: self.copy_text(target, "Link address copied"),
                               True, ""), None]
            items += [("Copy", self.copy_selection, self.sel is not None, "Ctrl+C"),
                      ("Select All", self.select_all_page, bool(self.page and self.page.runs), "Ctrl+A"),
                      None,
                      ("Paste & Go", lambda: self.paste_and_go(), clip, "Ctrl+V"),
                      ("Copy Page Address", lambda: self.copy_text(self.url, "Page address copied"),
                       bool(self.url), ""),
                      None,
                      ("Print…", self.open_print, bool(self.page and not self.loading), "Ctrl+P")]
        else:
            return
        self.build_context_menu(x, y, items)

    def build_context_menu(self, x, y, items):
        self.close_context_menu()
        t = self.theme
        b = pyglet.graphics.Batch()
        g = [pyglet.graphics.Group(i) for i in range(4)]
        objs = []
        row_h, sep_h, mw = 32, 9, 236
        mh = 12 + sum(sep_h if it is None else row_h for it in items)
        mx = min(x, self.width - mw - 8)
        my = max(8, y - mh) if y - mh > 8 else min(y, self.height - mh - 8)
        objs += gfx.soft_shadow(mx, my, mw, mh, 12, b, g[0], strength=120 if t.dark else 60, spread=16, offset=7)
        objs.append(gfx.rrect(mx - 1, my - 1, mw + 2, mh + 2, 13, t.c("border"), b, g[0]))
        objs.append(gfx.rrect(mx, my, mw, mh, 12, with_alpha(t.chrome, 250), b, g[0]))
        rows = []
        cy = my + mh - 6
        for it in items:
            if it is None:
                cy -= sep_h
                objs.append(shapes.Rectangle(mx + 12, cy + sep_h / 2, mw - 24, 1, color=t.c("border"),
                                             batch=b, group=g[1]))
                continue
            label, action, enabled, keys = it
            cy -= row_h
            hl = gfx.rrect(mx + 6, cy + 2, mw - 12, row_h - 4, 8, t.c("accent", 0), b, g[1])
            objs.append(hl)
            objs.append(pyglet.text.Label(label, font_name=self.ui_font, font_size=11,
                                          color=t.c("text") if enabled else t.c("muted", 120),
                                          x=mx + 18, y=cy + row_h / 2, anchor_y="center", batch=b, group=g[2]))
            if keys:
                objs.append(pyglet.text.Label(shortcut(keys), font_name=self.ui_font, font_size=9.5,
                                              color=t.c("muted", 200 if enabled else 90), x=mx + mw - 18,
                                              y=cy + row_h / 2, anchor_x="right", anchor_y="center",
                                              batch=b, group=g[2]))
            rows.append(dict(rect=(mx + 6, cy, mw - 12, row_h), action=action, enabled=enabled,
                             hl=hl, hover=0.0))
        self.ctx = dict(batch=b, objs=objs, rows=rows, rect=(mx, my, mw, mh))

    def close_context_menu(self):
        if self.ctx:
            for o in self.ctx["objs"]:
                o.delete()
            self.ctx = None

    def copy_text(self, text, message):
        if text:
            self.set_clipboard_text(text)
            self.toast(message)

    def resolve_href(self, href):
        if href.startswith("#"):
            return fetch.split_fragment(self.url)[0] + href
        if href.startswith("mercy-external:"):
            return href.split(":", 1)[1]
        return fetch.resolve(fetch.split_fragment(self.url)[0], href)

    def edit_select_all(self):
        self.anchor, self.caret = 0, len(self.edit_text)
        self.refresh_address()

    # ------------------------------------------------------------------- mouse
    def hit(self, x, y):
        if self.print_ui:  # the print panel is modal
            return self.print_hit(x, y)
        if self.ctx:
            mx, my, mw, mh = self.ctx["rect"]
            if mx <= x <= mx + mw and my <= y <= my + mh:
                for row in self.ctx["rows"]:
                    rx, ry, rw, rh = row["rect"]
                    if rx <= x <= rx + rw and ry <= y <= ry + rh:
                        return ("ctx", row)
                return ("ctx", None)
        if self.bm_panel:
            hit = self.bm_hit(x, y)
            if hit:
                return hit
        if self.menu_open and self.menu:
            mx, my, mw, mh = self.menu_rect
            if mx <= x <= mx + mw and my <= y <= my + mh:
                for row in self.menu_rows:
                    rx, ry, rw, rh = row["rect"]
                    if rx <= x <= rx + rw and ry <= y <= ry + rh:
                        return ("menu", row["theme"])
                return ("menu", None)
        hit = self.music_hit(x, y) or self.tab_hit(x, y) or self.toc_hit(x, y)
        if hit:
            return hit
        if y >= self.height - CHROME_H:
            for btn in self.buttons:
                if (x - btn["x"]) ** 2 + (y - btn["y"]) ** 2 <= btn["r"] ** 2:
                    return ("btn", btn["name"])
            tb = self.theme_btn
            if tb["x"] <= x <= tb["x"] + tb["w"] and tb["y"] <= y <= tb["y"] + tb["h"]:
                return ("theme",)
            a = self.addr
            sx, sy = a["star_xy"]
            if (x - sx) ** 2 + (y - sy) ** 2 <= 15 ** 2 and self.is_bookmarkable():
                return ("star",)
            if a["x"] <= x <= a["x"] + a["w"] and a["y"] <= y <= a["y"] + a["h"]:
                return ("addr",)
            return ("bar",)
        if self.thumb_rect_hit(x, y):
            return ("thumb",)
        if self.page:
            idx = self.page.link_at(*self.to_doc(x, y))
            if idx is not None:
                return ("link", idx)
        return None

    def thumb_rect_hit(self, x, y):
        r = getattr(self, "thumb_rect", None)
        return r is not None and x >= r[0] and r[1] - 4 <= y <= r[1] + r[3] + 4

    def update_hover(self):
        self.hover = h = self.hit(*self.mouse)
        new_link = h[1] if h and h[0] == "link" else None
        if new_link != self.hover_link and self.page:
            self.page.set_hover(self.hover_link, False)
            self.page.set_hover(new_link, True)
            self.hover_link = new_link
        kind = None
        if self.press and self.press.get("selecting"):
            kind = "text"
        elif h and h[0] == "print":
            kind = "hand" if h[1] not in (None, "backdrop") else None
        elif h and (h[0] in ("link", "btn", "theme", "menu", "thumb", "star", "tab", "newtab")
                    or (h[0] in ("bm", "toc", "music") and h[1])) \
                and h != ("menu", None):
            kind = "hand"
        elif h and h[0] == "ctx":
            kind = None
        elif h == ("addr",):
            kind = "text"
        elif h is None and self.page and self.page.text_at(*self.to_doc(*self.mouse)):
            kind = "text"
        if kind != self.cursor_kind:
            self.cursor_kind = kind
            self.set_mouse_cursor({"hand": self.cursor_hand, "text": self.cursor_text}.get(kind))

    def on_mouse_motion(self, x, y, dx, dy):
        self.mouse = (x, y)
        self.update_hover()

    def on_mouse_leave(self, x, y):
        self.mouse = (-1, -1)
        self.update_hover()

    def count_clicks(self, x, y):
        t0, (px, py), n = self.clicks
        n = n + 1 if self.time - t0 < 0.4 and abs(x - px) < 5 and abs(y - py) < 5 else 1
        self.clicks = (self.time, (x, y), n)
        return n

    def on_mouse_press(self, x, y, button, modifiers):
        self.mouse = (x, y)
        if IS_MAC and button == mouse.LEFT and modifiers & key.MOD_CTRL:
            button = mouse.RIGHT  # Control-click is the Mac's right-click
        if self.print_ui:
            h = self.print_hit(x, y)
            if button == mouse.LEFT and h[1]:
                self.print_click(h[1])
            return
        if button == getattr(mouse, "MOUSE4", 8):
            return self.go_back()
        if button == getattr(mouse, "MOUSE5", 16):
            return self.go_forward()
        h = self.hit(x, y)
        if h and h[0] == "ctx":
            if button == mouse.LEFT and h[1] and h[1]["enabled"]:
                action = h[1]["action"]
                self.close_context_menu()
                action()
            return
        self.close_context_menu()
        if h and h[0] == "bm":
            if button == mouse.LEFT and h[1]:
                self.bm_click(h[1], h[2])
            return
        if self.bm_open and h != ("btn", "bookmarks"):
            self.close_bm_panel()
        if h and h[0] == "music":
            if button == mouse.LEFT and h[1]:
                if self.editing:
                    self.stop_editing()
                self.music_press(h[1], x)
            return
        if h and h[0] in ("tab", "newtab", "tabstrip"):
            self.menu_open = False
            if self.editing:
                self.stop_editing()
            if button == mouse.RIGHT and h[0] == "tab":
                self.build_context_menu(x, y, self.tab_menu_items(h[1]))
            else:
                self.press_tab(h, button, x)
            return
        if button == mouse.MIDDLE and h and h[0] == "link":
            self.open_in_new_tab(self.page.links[h[1]].href)
            return
        if h and h[0] == "toc":
            if button == mouse.LEFT and h[1]:
                if self.editing:
                    self.stop_editing()
                self.follow("#" + h[1]["slug"])
                if not self.toc_pushes():  # floating over the page: get out of the way
                    self.toggle_toc()
            return
        if button == mouse.RIGHT:
            self.menu_open = False
            self.open_context_menu(x, y, h)
            return
        if button != mouse.LEFT:
            return
        if self.menu_open and (not h or h[0] != "menu") and h != ("theme",):
            self.menu_open = False
        if self.editing and h != ("addr",):
            self.stop_editing()
        clicks = self.count_clicks(x, y)
        if h is None or (h[0] == "link"):
            self.press_page(x, y, h, clicks, modifiers & key.MOD_SHIFT)
        elif h[0] == "btn":
            {"back": self.go_back, "forward": self.go_forward, "reload": self.reload,
             "home": lambda: self.navigate("mercy://home"), "bookmarks": self.toggle_bm_panel,
             "contents": self.toggle_toc}[h[1]]()
        elif h[0] == "star":
            self.toggle_bookmark()
        elif h[0] == "theme":
            self.menu_open = not self.menu_open
            if self.menu_open:
                self.build_menu()
        elif h[0] == "menu" and h[1] is not None:
            self.menu_open = False
            if h[1] is not self.theme:
                self.set_theme(h[1])
        elif h[0] == "addr":
            self.press_address(x, clicks, modifiers & key.MOD_SHIFT)
        elif h[0] == "thumb":
            self.drag_scrollbar = (y, self.scroll)
        self.update_hover()

    def press_address(self, x, clicks, shift):
        if not self.editing:
            self.start_editing()        # first click selects the whole address
            return
        i = self.addr_index_at(x)
        if clicks == 2:
            self.anchor, self.caret = self.word_bounds(self.edit_text, i)
        elif clicks >= 3:
            self.edit_select_all()
        else:
            if not shift:
                self.anchor = i
            elif self.anchor is None:
                self.anchor = self.caret
            self.caret = i
            self.addr_drag = True
        self.caret_blink = 0
        self.refresh_address()

    def press_page(self, x, y, h, clicks, shift):
        if not self.page:
            return
        pos = self.page.caret_at(*self.to_doc(x, y))
        link = h[1] if h and h[0] == "link" else None
        if pos and clicks == 2 and link is None:
            self.set_selection(*self.page.word_at(pos))
            self.press = dict(x=x, y=y, link=None, selecting=False, anchor=None)
            return
        if pos and clicks >= 3 and link is None:
            self.set_selection(*self.page.line_at(pos))
            self.press = dict(x=x, y=y, link=None, selecting=False, anchor=None)
            return
        if shift and self.sel and pos:
            self.set_selection(self.sel[0], pos)
            self.press = dict(x=x, y=y, link=None, selecting=True, anchor=self.sel[0] if self.sel else pos)
            return
        self.clear_selection()
        self.press = dict(x=x, y=y, link=link, selecting=False, anchor=pos)

    def drag_select(self, x, y):
        p = self.press
        if not (p and p["anchor"] and self.page):
            return
        pos = self.page.caret_at(*self.to_doc(x, y))
        if pos:
            self.set_selection(p["anchor"], pos)

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        self.mouse = (x, y)
        if self.drag_scrollbar is not None and self.thumb_rect:
            y0, s0 = self.drag_scrollbar
            _, _, _, th, track, ms = self.thumb_rect
            span = max(1, track - th)
            self.target = self.scroll = max(0, min(ms, s0 + (y0 - y) * ms / span))
            return
        if self.music_drag:
            self.music_drag_to(x)
            return
        if self.tab_drag is not None:
            self.drag_tab(x)
            return
        if self.addr_drag and self.editing:
            self.caret = self.addr_index_at(x)
            self.refresh_address()
            return
        p = self.press
        if p and p["anchor"]:
            if not p["selecting"] and abs(x - p["x"]) + abs(y - p["y"]) > 4:
                p["selecting"] = True
                p["link"] = None
            if p["selecting"]:
                self.drag_select(x, y)
                self.update_hover()

    def on_mouse_release(self, x, y, button, modifiers):
        self.drag_scrollbar = None
        self.addr_drag = False
        self.tab_drag = None
        self.music_drag = None
        p, self.press = self.press, None
        if button == mouse.LEFT and p and p["link"] is not None and not p["selecting"] and self.page:
            href = self.page.links[p["link"]].href
            if modifiers & ACCEL:
                self.open_in_new_tab(href)
            else:
                self.follow(href)
        self.update_hover()

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        if self.print_ui:  # scroll flips through the preview
            self.flip_preview(-1 if scroll_y > 0 else 1)
            return
        if self.keys_ctrl:
            self.set_zoom(self.zoom + 0.1 * (1 if scroll_y > 0 else -1))
            return
        self.close_context_menu()
        if self.music_hit(x, y):  # wheel over the player sets the volume
            self.music.set_volume(self.music.volume + 0.05 * (1 if scroll_y > 0 else -1))
            self.music_dirty = True
            return
        if y >= self.height - TABS_H:  # wheel over the tab strip flips through tabs
            if self.time - self.strip_wheel[1] > 0.35:
                self.strip_wheel = (0.0, self.time)
            total = self.strip_wheel[0] + scroll_y
            if abs(total) >= 1.0:  # one notch, or a deliberate trackpad swipe
                self.switch_tab(self.tab_i + (-1 if total > 0 else 1))
                total = 0.0
            self.strip_wheel = (total, self.time)
            return
        if self.toc_hit(x, y):
            self.toc_scroll = max(0, self.toc_scroll + (-3 if scroll_y > 0 else 3))
            self.build_toc()
            self.update_hover()
            return
        if self.bm_panel and self.bm_hit(x, y):
            step = -1 if scroll_y > 0 else 1
            self.bm_scroll = max(0, self.bm_scroll + step)
            self.bm_index = max(self.bm_scroll, min(self.bm_index, self.bm_scroll + self.bm_panel["visible"] - 1))
            self.build_bm_panel()
            return
        self.target -= scroll_y * self.S * WHEEL_STEP

    @property
    def keys_ctrl(self):
        k = self.keys
        return k[key.LCTRL] or k[key.RCTRL] or (IS_MAC and (k[key.LCOMMAND] or k[key.RCOMMAND]))

    @property
    def keys_shift(self):
        return self.keys[key.LSHIFT] or self.keys[key.RSHIFT]

    # --------------------------------------------------------------- keyboard
    def on_key_press(self, symbol, modifiers):
        ctrl = modifiers & ACCEL
        alt = modifiers & key.MOD_ALT
        shift = modifiers & key.MOD_SHIFT
        if self.print_ui:
            if symbol == key.ESCAPE:
                self.close_print()
            elif symbol in (key.ENTER, key.NUM_ENTER) and not self.pr["busy"]:
                self.print_go()
            elif symbol in (key.LEFT, key.RIGHT, key.PAGEUP, key.PAGEDOWN):
                self.flip_preview(-1 if symbol in (key.LEFT, key.PAGEUP) else 1)
            return pyglet.event.EVENT_HANDLED
        if self.ctx and symbol == key.ESCAPE:
            self.close_context_menu()
            return pyglet.event.EVENT_HANDLED
        self.close_context_menu()
        if ctrl and not shift and symbol == key.P:
            self.open_print()
            return pyglet.event.EVENT_HANDLED
        if ctrl and symbol == key.T:
            self.reopen_closed_tab() if shift else self.new_tab(focus_address=True)
            return pyglet.event.EVENT_HANDLED
        if ctrl and symbol == key.W:
            self.close_tab()
            return pyglet.event.EVENT_HANDLED
        if ctrl and shift and symbol in (key.BRACKETLEFT, key.BRACKETRIGHT):  # Cmd+Shift+[ ] on a Mac
            self.switch_tab((self.tab_i + (-1 if symbol == key.BRACKETLEFT else 1)) % len(self.tabs))
            return pyglet.event.EVENT_HANDLED
        if ctrl and symbol in (key.TAB, key.PAGEDOWN, key.PAGEUP):
            back = symbol == key.PAGEUP or (symbol == key.TAB and shift)
            self.switch_tab((self.tab_i + (-1 if back else 1)) % len(self.tabs))
            return pyglet.event.EVENT_HANDLED
        if ctrl and not shift and key._1 <= symbol <= key._9:
            n = symbol - key._1
            self.switch_tab(len(self.tabs) - 1 if n == 8 else n)
            return pyglet.event.EVENT_HANDLED
        if ctrl and shift and symbol in (key.P, key.PERIOD, key.COMMA):
            if not self.music.active:
                self.toast("Nothing to play yet: click a link to a music file")
            elif symbol == key.P:
                self.music.toggle()
            else:
                self.music.next() if symbol == key.PERIOD else self.music.previous()
            return pyglet.event.EVENT_HANDLED
        if ctrl and shift and symbol == key.S:
            self.cycle_theme()
            return pyglet.event.EVENT_HANDLED
        if ctrl and symbol == key.E:
            self.toggle_toc()
            return pyglet.event.EVENT_HANDLED
        if ctrl and symbol == key.B:
            self.toggle_bm_panel()
            return pyglet.event.EVENT_HANDLED
        if self.bm_open and not ctrl:
            self.bm_key(symbol)  # the panel owns the keyboard while it's open
            return pyglet.event.EVENT_HANDLED
        if ctrl and symbol == key.D and not self.prompt:
            if self.editing:
                self.stop_editing()
            self.toggle_bookmark()
            return pyglet.event.EVENT_HANDLED
        if self.editing:
            if symbol in (key.ENTER, key.NUM_ENTER):
                text, prompt = self.edit_text, self.prompt
                self.stop_editing()
                if prompt:
                    prompt["done"](text)
                else:
                    self.navigate(fetch.normalize(text))
            elif symbol == key.ESCAPE:
                self.stop_editing()
            elif ctrl and symbol == key.A:
                self.edit_select_all()
            elif ctrl and shift and symbol == key.V:
                self.stop_editing()
                self.paste_and_go()
            elif ctrl and symbol == key.V:
                self.edit_paste()
            elif ctrl and symbol == key.C:
                self.edit_copy()
            elif ctrl and symbol == key.X:
                self.edit_copy(cut=True)
            return pyglet.event.EVENT_HANDLED

        if symbol == key.ESCAPE:
            self.menu_open = False
            self.clear_selection()
            return pyglet.event.EVENT_HANDLED
        view = self.view_top - 40
        if ctrl and symbol in (key.C, key.INSERT):
            self.copy_selection()
        elif ctrl and symbol == key.A:
            self.select_all_page()
        elif ctrl and symbol == key.V or (shift and symbol == key.INSERT):
            self.paste_and_go()
        elif (ctrl and symbol == key.L) or symbol == key.F6:
            self.start_editing()
        elif ctrl and symbol == key.Q:
            self.quit()
        elif (alt and symbol == key.LEFT) or symbol == key.BACKSPACE or (ctrl and symbol == key.BRACKETLEFT) \
                or (IS_MAC and ctrl and symbol == key.LEFT):
            self.go_back()
        elif (alt and symbol == key.RIGHT) or (ctrl and symbol == key.BRACKETRIGHT) \
                or (IS_MAC and ctrl and symbol == key.RIGHT):
            self.go_forward()
        elif alt and symbol == key.HOME:
            self.navigate("mercy://home")
        elif symbol == key.F5 or (ctrl and symbol == key.R):
            self.reload()
        elif ctrl and symbol in (key.EQUAL, key.PLUS, key.NUM_ADD):
            self.set_zoom(self.zoom + 0.1)
        elif ctrl and symbol in (key.MINUS, key.NUM_SUBTRACT):
            self.set_zoom(self.zoom - 0.1)
        elif ctrl and symbol in (key._0, key.NUM_0):
            self.set_zoom(1.0)
        elif symbol in (key.DOWN, key.UP):
            # a small nudge so a quick tap still moves; holding is handled in update()
            self.arrow_hold = (1 if symbol == key.DOWN else -1, self.time)
            self.target += self.arrow_hold[0] * self.S * 1.5
        elif symbol in (key.PAGEDOWN,) or (symbol == key.SPACE and not shift):
            self.target += view
        elif symbol == key.PAGEUP or (symbol == key.SPACE and shift):
            self.target -= view
        elif symbol == key.HOME:
            self.target = 0
        elif symbol == key.END:
            self.target = self.max_scroll()
        return pyglet.event.EVENT_HANDLED

    def on_text(self, text):
        if self.bm_open and text.isprintable() and not self.keys_ctrl:
            self.bm_filter += text
            self.bm_index = self.bm_scroll = 0
            self.build_bm_panel()
        elif self.editing and text.isprintable():
            self.edit_insert(text)

    def on_text_motion(self, motion):
        if self.editing:
            self.edit_motion(motion, select=False)

    def on_text_motion_select(self, motion):
        if self.editing:
            self.edit_motion(motion, select=True)

    def edit_motion(self, motion, select):
        t, c = self.edit_text, self.caret
        sel = self.edit_selection()
        if motion == key.MOTION_BACKSPACE:
            if not self.edit_delete_selection() and c > 0:
                self.edit_text = t[:c - 1] + t[c:]
                self.caret = c - 1
        elif motion == key.MOTION_DELETE:
            if not self.edit_delete_selection():
                self.edit_text = t[:c] + t[c + 1:]
        else:
            if motion == key.MOTION_LEFT:
                new = sel[0] if (sel and not select) else max(0, c - 1)
            elif motion == key.MOTION_RIGHT:
                new = sel[1] if (sel and not select) else min(len(t), c + 1)
            elif motion == key.MOTION_PREVIOUS_WORD:
                new = self.word_bounds(t, max(0, c - 1))[0] if c else 0
            elif motion == key.MOTION_NEXT_WORD:
                new = self.word_bounds(t, min(len(t), c + 1))[1] if c < len(t) else c
            elif motion in (key.MOTION_BEGINNING_OF_LINE, key.MOTION_BEGINNING_OF_FILE, key.MOTION_UP):
                new = 0
            elif motion in (key.MOTION_END_OF_LINE, key.MOTION_END_OF_FILE, key.MOTION_DOWN):
                new = len(t)
            else:
                return
            if select:
                if self.anchor is None:
                    self.anchor = c
            else:
                self.anchor = None
            self.caret = new
        self.caret_blink = 0
        self.refresh_address()
