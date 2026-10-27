"""Render Mercy's app icon into mercy/icons/.

The icon echoes the browser's Aurora theme: a midnight squircle lit by soft
cyan and violet glows, holding a tilted page of Markdown with a gradient
heading and a small sparkle.

    python tools/make_icon.py
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "mercy" / "icons"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
N = 1024  # master size; everything below is in master pixels

NAVY_TOP = (11, 16, 40)
NAVY_BOTTOM = (38, 18, 78)
CYAN = (125, 211, 252)
VIOLET = (192, 132, 252)
TEAL = (34, 211, 238)


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(len(a)))


def vertical_gradient(size, top, bottom):
    t = np.linspace(0, 1, size)[:, None, None]
    arr = np.array(top)[None, None, :] * (1 - t) + np.array(bottom)[None, None, :] * t
    arr = np.repeat(arr, size, axis=1)
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def horizontal_gradient(w, h, left, right):
    t = np.linspace(0, 1, w)[None, :, None]
    arr = np.array(left)[None, None, :] * (1 - t) + np.array(right)[None, None, :] * t
    arr = np.repeat(arr, h, axis=0)
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def glow(size, cx, cy, radius, color, strength):
    """A soft radial light, returned as an RGB image to be added on top."""
    yy, xx = np.mgrid[0:size, 0:size]
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / radius
    falloff = np.clip(1 - d, 0, 1) ** 2.2 * strength
    arr = falloff[..., None] * np.array(color)[None, None, :]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def squircle_mask(size, inset, radius):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle((inset, inset, size - inset, size - inset), radius, fill=255)
    return m


def sparkle(draw, cx, cy, r, fill):
    """A four-pointed star whose sides curve inward."""
    pts = []
    for i in range(4):
        a0 = np.pi / 2 + i * np.pi / 2
        pts.append((cx + r * np.cos(a0), cy - r * np.sin(a0)))
        # trace a concave arc between this tip and the next one
        for k in range(1, 12):
            t = k / 12
            a = a0 + t * np.pi / 2
            rr = r * (0.2 + 0.8 * abs(2 * t - 1) ** 2.4)
            pts.append((cx + rr * np.cos(a), cy - rr * np.sin(a)))
    draw.polygon(pts, fill=fill)


def render() -> Image.Image:
    inset, radius = 56, 230

    # background: midnight gradient with drifting aurora lights
    bg = vertical_gradient(N, NAVY_TOP, NAVY_BOTTOM)
    for cx, cy, r, col, s in ((230, 220, 620, CYAN, 0.55), (860, 820, 640, VIOLET, 0.6),
                              (760, 150, 380, TEAL, 0.25)):
        bg = ImageChops.add(bg, glow(N, cx, cy, r, col, s))
    icon = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    icon.paste(bg, (0, 0), squircle_mask(N, inset, radius))

    # a faint glassy rim along the top edge
    rim = Image.new("L", (N, N), 0)
    ImageDraw.Draw(rim).rounded_rectangle((inset, inset, N - inset, N - inset), radius,
                                          outline=255, width=6)
    fade = Image.fromarray((np.linspace(110, 0, N)[:, None] * np.ones((1, N))).astype(np.uint8), "L")
    rim = ImageChops.multiply(rim, fade)
    icon.alpha_composite(Image.merge("RGBA", (*[Image.new("L", (N, N), 255)] * 3, rim)))

    # the page: drawn upright on its own layer, then tilted
    page = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    pw, ph = 470, 580
    px, py = (N - pw) // 2, (N - ph) // 2 + 10
    pd = ImageDraw.Draw(page)
    pd.rounded_rectangle((px, py, px + pw, py + ph), 54, fill=(246, 247, 255, 250))
    # gradient heading bar
    bar_x, bar_y, bar_w, bar_h = px + 64, py + 82, 300, 50
    bar_mask = Image.new("L", (N, N), 0)
    ImageDraw.Draw(bar_mask).rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), 25, fill=255)
    bar = horizontal_gradient(N, N, CYAN, VIOLET)
    page.paste(bar, (0, 0), bar_mask)
    # body text lines
    line_c = (176, 184, 214, 255)
    y = bar_y + 118
    for w in (340, 300, 342, 220):
        pd.rounded_rectangle((px + 64, y, px + 64 + w, y + 26), 13, fill=line_c)
        y += 64
    # a small bullet list at the foot
    for i, w in enumerate((200, 150)):
        cy = y + 22 + i * 58
        pd.ellipse((px + 64, cy - 13, px + 90, cy + 13), fill=lerp(CYAN, VIOLET, i))
        pd.rounded_rectangle((px + 112, cy - 13, px + 112 + w, cy + 13), 13, fill=line_c)
    page = page.rotate(-7, resample=Image.BICUBIC, center=(N / 2, N / 2 + 10))

    # soft shadow beneath the page
    shadow = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    alpha = page.getchannel("A").point(lambda a: a * 0.55)
    shadow.putalpha(alpha)
    shadow = shadow.transform((N, N), Image.AFFINE, (1, 0, -10, 0, 1, -34)).filter(ImageFilter.GaussianBlur(34))
    icon.alpha_composite(shadow)
    icon.alpha_composite(page)

    # sparkle with a halo, top right of the page
    halo = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    hd.ellipse((760 - 90, 250 - 90, 760 + 90, 250 + 90), fill=(200, 225, 255, 120))
    sparkle(hd, 760, 250, 170, (255, 255, 255, 200))
    icon.alpha_composite(halo.filter(ImageFilter.GaussianBlur(30)))
    sp = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    sparkle(ImageDraw.Draw(sp), 760, 250, 132, (255, 255, 255, 255))
    sparkle(ImageDraw.Draw(sp), 214, 748, 46, (214, 236, 255, 230))
    icon.alpha_composite(sp)

    # keep everything inside the squircle
    icon.putalpha(ImageChops.multiply(icon.getchannel("A"), squircle_mask(N, inset - 2, radius + 2)))
    return icon


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    master = render()
    for size in SIZES:
        img = master.resize((size, size), Image.LANCZOS)
        if size <= 32:  # a touch of sharpening keeps tiny sizes crisp
            img = img.filter(ImageFilter.UnsharpMask(radius=0.6, percent=60, threshold=0))
        img.save(OUT / f"mercy-{size}.png")
    print(f"wrote {len(SIZES)} icons to {OUT}")


if __name__ == "__main__":
    main()
