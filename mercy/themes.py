"""Color styles for Mercy.

Every theme is a flat palette plus three font stacks. Colors are RGB(A)
tuples in the 0-255 range, which is what pyglet expects.
"""

from dataclasses import dataclass, field

import pyglet

# Each stack runs from nicest to most common. The Linux, macOS and Windows favourites
# are mixed in, and every list ends with a font all three systems ship.
SANS = ["Inter", "SF Pro Text", "Segoe UI", "Helvetica Neue", "Lato", "Noto Sans", "Cantarell",
        "DejaVu Sans", "Arial"]
DISPLAY = ["Inter Display", "Inter", "SF Pro Display", "Segoe UI", "Avenir Next", "Helvetica Neue", "Lato",
           "Noto Sans", "DejaVu Sans", "Arial"]
SERIF = ["Charis SIL", "Gentium Book Plus", "Iowan Old Style", "Charter", "Georgia", "Noto Serif",
         "DejaVu Serif", "Times New Roman"]
SERIF_DISPLAY = ["EB Garamond", "Playfair Display", "Hoefler Text", "Palatino", "Palatino Linotype",
                 "Georgia", "Charis SIL", "DejaVu Serif", "Times New Roman"]
MONO = ["JetBrains Mono", "Fira Code", "Cascadia Code", "Cascadia Mono", "Source Code Pro", "SF Mono",
        "Menlo", "Noto Sans Mono", "Go Mono", "DejaVu Sans Mono", "Consolas", "Monaco", "Courier New"]

_font_cache: dict[tuple, str] = {}


def pick_font(candidates: list[str]) -> str:
    key = tuple(candidates)
    if key not in _font_cache:
        _font_cache[key] = next((f for f in candidates if pyglet.font.have_font(f)), candidates[-1])
    return _font_cache[key]


def hex_rgb(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), alpha


def with_alpha(color, alpha: int) -> tuple[int, int, int, int]:
    return color[0], color[1], color[2], alpha


def mix(a, b, t: float) -> tuple[int, int, int, int]:
    a = tuple(a) + (255,) * (4 - len(a))
    b = tuple(b) + (255,) * (4 - len(b))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(4))


@dataclass
class Theme:
    name: str
    dark: bool
    bg_top: str
    bg_bottom: str
    blobs: tuple[str, ...]          # soft glowing lights drifting behind the page
    surface: str                    # the reading card
    surface_alpha: int
    text: str
    muted: str
    heading: str
    accent: str
    accent2: str
    link: str
    code_bg: str
    code_text: str
    border: str
    syntax: dict = field(default_factory=dict)
    body_fonts: list = field(default_factory=lambda: SANS)
    heading_fonts: list = field(default_factory=lambda: DISPLAY)
    mono_fonts: list = field(default_factory=lambda: MONO)
    blob_alpha: int = 10

    # -- derived colors -------------------------------------------------
    def c(self, name: str, alpha: int = 255):
        return hex_rgb(getattr(self, name), alpha)

    @property
    def body_font(self):
        return pick_font(self.body_fonts)

    @property
    def heading_font(self):
        return pick_font(self.heading_fonts)

    @property
    def mono_font(self):
        return pick_font(self.mono_fonts)

    @property
    def chrome(self):
        return mix(self.c("bg_top"), self.c("surface"), 0.5)

    def callout(self, kind: str):
        palette = {
            "NOTE": "#58a6ff" if self.dark else "#0969da",
            "TIP": "#3fb950" if self.dark else "#1a7f37",
            "IMPORTANT": "#a371f7" if self.dark else "#8250df",
            "WARNING": "#d29922" if self.dark else "#9a6700",
            "CAUTION": "#f85149" if self.dark else "#cf222e",
        }
        return hex_rgb(palette.get(kind, self.accent))

    def syntax_color(self, kind: str):
        return hex_rgb(self.syntax.get(kind, self.code_text))


THEMES = [
    Theme(
        name="Aurora", dark=True,
        bg_top="#070b1a", bg_bottom="#161036",
        blobs=("#38bdf8", "#a855f7", "#22d3ee"),
        surface="#11162b", surface_alpha=215,
        text="#d6dcf0", muted="#8790b0", heading="#f4f6ff",
        accent="#7dd3fc", accent2="#c084fc", link="#7dd3fc",
        code_bg="#0b0f20", code_text="#d6dcf0", border="#262d4d",
        syntax=dict(keyword="#c792ea", string="#c3e88d", comment="#5f6b94", number="#f78c6c",
                    function="#82aaff", type="#ffcb6b", operator="#89ddff", builtin="#f07178"),
        blob_alpha=12,
    ),
    Theme(
        # A plain, neutral dark mode: graphite greys with no colour cast, for those
        # who want dark without a mood.
        name="Dark", dark=True,
        bg_top="#0b0b0d", bg_bottom="#141417",
        blobs=("#3a3d45", "#2c2f36", "#45484f"),
        surface="#18181b", surface_alpha=240,
        text="#dcdce0", muted="#8e8e96", heading="#f5f5f7",
        accent="#8ab4f8", accent2="#a8a8b3", link="#8ab4f8",
        code_bg="#111113", code_text="#d4d4d8", border="#2a2a2f",
        syntax=dict(keyword="#569cd6", string="#ce9178", comment="#6a9955", number="#b5cea8",
                    function="#dcdcaa", type="#4ec9b0", operator="#d4d4d4", builtin="#9cdcfe"),
        blob_alpha=7,
    ),
    Theme(
        name="Paper", dark=False,
        bg_top="#f3f1ec", bg_bottom="#e7e3da",
        blobs=("#f9d8b4", "#c7dcf7", "#e9d5f7"),
        surface="#fffefb", surface_alpha=250,
        text="#2b2b2b", muted="#76726a", heading="#141414",
        accent="#b4532a", accent2="#d4a373", link="#a8431d",
        code_bg="#f5f2ea", code_text="#2b2b2b", border="#e2ddd1",
        syntax=dict(keyword="#a626a4", string="#50a14f", comment="#a0a1a7", number="#986801",
                    function="#4078f2", type="#c18401", operator="#0184bc", builtin="#e45649"),
        body_fonts=SERIF, heading_fonts=SERIF_DISPLAY, blob_alpha=16,
    ),
    Theme(
        name="Sepia", dark=False,
        bg_top="#efe3c8", bg_bottom="#e2d1ad",
        blobs=("#f2c48d", "#e8b07a", "#f7dcb0"),
        surface="#f8f0dc", surface_alpha=245,
        text="#4a3b2a", muted="#8a7558", heading="#2f2416",
        accent="#9c4a1a", accent2="#c08a3e", link="#8b3a0e",
        code_bg="#efe2c4", code_text="#4a3b2a", border="#dfcda6",
        syntax=dict(keyword="#8f3f71", string="#79740e", comment="#a89984", number="#8f3f71",
                    function="#076678", type="#b57614", operator="#427b58", builtin="#af3a03"),
        body_fonts=SERIF, heading_fonts=SERIF_DISPLAY, blob_alpha=16,
    ),
    Theme(
        name="Glacier", dark=True,
        bg_top="#1f2430", bg_bottom="#2b3242",
        blobs=("#88c0d0", "#5e81ac", "#b48ead"),
        surface="#2a303c", surface_alpha=225,
        text="#d8dee9", muted="#8a93a6", heading="#eceff4",
        accent="#88c0d0", accent2="#81a1c1", link="#8fbcbb",
        code_bg="#242933", code_text="#d8dee9", border="#3b4252",
        syntax=dict(keyword="#81a1c1", string="#a3be8c", comment="#616e88", number="#b48ead",
                    function="#88c0d0", type="#8fbcbb", operator="#81a1c1", builtin="#d08770"),
        blob_alpha=9,
    ),
    Theme(
        name="Dusk", dark=True,
        bg_top="#16141f", bg_bottom="#241d33",
        blobs=("#eb6f92", "#c4a7e7", "#f6c177"),
        surface="#1f1d2e", surface_alpha=220,
        text="#e0def4", muted="#908caa", heading="#f2e9e1",
        accent="#ebbcba", accent2="#c4a7e7", link="#ebbcba",
        code_bg="#191724", code_text="#e0def4", border="#2f2b44",
        syntax=dict(keyword="#31748f", string="#f6c177", comment="#6e6a86", number="#ebbcba",
                    function="#9ccfd8", type="#c4a7e7", operator="#908caa", builtin="#eb6f92"),
        blob_alpha=11,
    ),
    Theme(
        name="Forest", dark=True,
        bg_top="#0a1510", bg_bottom="#11251b",
        blobs=("#34d399", "#a3e635", "#2dd4bf"),
        surface="#10201a", surface_alpha=220,
        text="#d3e4da", muted="#7f9a8b", heading="#effaf3",
        accent="#6ee7b7", accent2="#fbbf24", link="#6ee7b7",
        code_bg="#0b1813", code_text="#d3e4da", border="#1f3a2d",
        syntax=dict(keyword="#fbbf24", string="#a3e635", comment="#5b7a6a", number="#fb923c",
                    function="#6ee7b7", type="#5eead4", operator="#bef264", builtin="#f87171"),
        blob_alpha=9,
    ),
    Theme(
        name="Synthwave", dark=True,
        bg_top="#12062a", bg_bottom="#2a0b3d",
        blobs=("#ff2fb4", "#4deeea", "#7b2ff7"),
        surface="#1b0b33", surface_alpha=210,
        text="#efe1ff", muted="#a48bc4", heading="#ffffff",
        accent="#ff6ad5", accent2="#4deeea", link="#4deeea",
        code_bg="#120625", code_text="#efe1ff", border="#3b1c5e",
        syntax=dict(keyword="#ff6ad5", string="#fede5d", comment="#7d68a3", number="#f97e72",
                    function="#36f9f6", type="#fe4450", operator="#ff8b39", builtin="#72f1b8"),
        blob_alpha=13,
    ),
]


def theme_by_name(name: str) -> Theme:
    return next((t for t in THEMES if t.name.lower() == name.lower()), THEMES[0])
