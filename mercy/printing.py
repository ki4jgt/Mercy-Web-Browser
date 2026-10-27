"""Printing: lay a document out for paper, render it to page images, wrap those
in a PDF, and hand that to the system's printing service.

pyglet can't talk to printers, so Mercy makes a PDF and uses the standard
tools: CUPS (`lp`) on Linux and macOS, the shell's print verb on Windows.
"""

import dataclasses
import locale
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

import pyglet
from pyglet.gl import (GL_COLOR_BUFFER_BIT, GL_SCISSOR_TEST, glClear, glClearColor, glDisable, glEnable,
                       glScissor, glViewport)
from pyglet.math import Mat4, Vec3

from .render import Page
from .system import IS_MAC, IS_WINDOWS

PAPER = {"A4": (595.28, 841.89), "Letter": (612.0, 792.0)}   # in points
DPI = 200
MARGIN_PT = 54            # three-quarters of an inch
BODY_PT = 11              # body text size on paper
SAVE_AS_PDF = "Save as PDF"


def default_paper() -> str:
    """Letter in North America and a few other places, A4 everywhere else."""
    loc = (locale.getlocale()[0] or os.environ.get("LANG", "")).upper()
    return "Letter" if re.search(r"_(US|CA|MX|PH|CL|CO|VE)\b", loc) else "A4"


def print_theme(theme):
    """Black ink on white paper, keeping the theme's typefaces."""
    return dataclasses.replace(
        theme, name="Print", dark=False, bg_top="#ffffff", bg_bottom="#ffffff", blobs=(),
        surface="#ffffff", surface_alpha=255, text="#1f2328", muted="#59636e", heading="#0d1117",
        accent="#0969da", accent2="#8250df", link="#0550ae", code_bg="#f6f8fa", code_text="#1f2328",
        border="#d0d7de", blob_alpha=0,
        syntax=dict(keyword="#cf222e", string="#0a3069", comment="#6e7781", number="#0550ae",
                    function="#8250df", type="#953800", operator="#24292f", builtin="#0550ae"))


@dataclasses.dataclass
class PrintJob:
    title: str
    url: str
    paper: str
    pages: list            # [(width, height, rgb bytes, top-down)]
    pdf_path: str = ""


def render(window, markdown, theme, images, resolve, title, url, paper) -> PrintJob:
    """Lay the document out at print resolution and draw each page off-screen."""
    k = DPI / 72
    pw, ph = (round(v * k) for v in PAPER[paper])
    m = round(MARGIN_PT * k)
    head = round(28 * k)                       # room for the running header / footer
    S = BODY_PT * DPI / 96                     # pyglet sizes are points at 96 dpi
    content_w = pw - 2 * m
    page = Page(markdown, print_theme(theme), content_w, S, images, resolve=resolve, printing=True)
    slices = page.page_breaks(ph - 2 * m - 2 * head)

    tex = pyglet.image.Texture.create(pw, ph)
    fb = pyglet.image.Framebuffer()
    fb.attach_texture(tex)
    saved_projection, saved_view = window.projection, window.view
    small = 8.5 * DPI / 96
    font = theme.body_font
    out = []
    try:
        fb.bind()
        glViewport(0, 0, pw, ph)
        window.projection = Mat4.orthogonal_projection(0, pw, 0, ph, -255, 255)
        for n, (y0, y1) in enumerate(slices, 1):
            glClearColor(1, 1, 1, 1)
            glClear(GL_COLOR_BUFFER_BIT)
            top = ph - m - head
            glEnable(GL_SCISSOR_TEST)
            glScissor(0, round(top - (y1 - y0)), pw, round(y1 - y0) + 2)
            window.view = Mat4.from_translation(Vec3(m, round(top + y0), 0))
            page.batch.draw()
            glDisable(GL_SCISSOR_TEST)
            window.view = Mat4()
            # running header and footer
            hb = pyglet.graphics.Batch()
            grey, rule = (110, 118, 129, 255), (208, 215, 222, 255)
            labels = [
                pyglet.text.Label(_fit(title, 70), font_name=font, font_size=small, color=grey,
                                  x=m, y=ph - m, anchor_y="top", batch=hb),
                pyglet.text.Label(time.strftime("%Y-%m-%d"), font_name=font, font_size=small, color=grey,
                                  x=pw - m, y=ph - m, anchor_x="right", anchor_y="top", batch=hb),
                pyglet.text.Label(_fit(url, 90), font_name=font, font_size=small, color=grey,
                                  x=m, y=m, anchor_y="bottom", batch=hb),
                pyglet.text.Label(f"{n} / {len(slices)}", font_name=font, font_size=small, color=grey,
                                  x=pw - m, y=m, anchor_x="right", anchor_y="bottom", batch=hb),
            ]
            rules = [pyglet.shapes.Rectangle(m, ph - m - head * 0.55, pw - 2 * m, 2, color=rule, batch=hb),
                     pyglet.shapes.Rectangle(m, m + head * 0.55, pw - 2 * m, 2, color=rule, batch=hb)]
            hb.draw()
            for o in labels + rules:
                o.delete()
            # read back in the texture's own RGBA layout (pyglet's format conversion is a
            # slow regex), top row first, then drop alpha with C-speed slicing
            rgba = tex.get_image_data().get_data("RGBA", -pw * 4)
            rgb = bytearray(pw * ph * 3)
            for c in range(3):
                rgb[c::3] = rgba[c::4]
            out.append((pw, ph, bytes(rgb)))
    finally:
        fb.unbind()
        glClearColor(0, 0, 0, 1)
        window.projection, window.view = saved_projection, saved_view
        glViewport(0, 0, *window.get_framebuffer_size())
        page.delete()
        fb.delete()
        tex.delete()
    return PrintJob(title=title, url=url, paper=paper, pages=out)


def _fit(text, n):
    return text if len(text) <= n else text[:n - 1] + "…"


def write_pdf(job: PrintJob, path: str):
    """A minimal PDF: one losslessly compressed page image per page, at paper size."""
    pw_pt, ph_pt = PAPER[job.paper]
    objects = []                                   # object bodies, numbered from 1

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    info = add(b"<< /Title " + _pdf_text(job.title) + b" /Producer (Mercy) /CreationDate "
               + _pdf_text(time.strftime("D:%Y%m%d%H%M%S")) + b" >>")
    pages_ref = len(objects) + 2                   # the Pages object is added right after the catalog
    catalog = add(f"<< /Type /Catalog /Pages {pages_ref} 0 R >>".encode())
    add(b"")                                       # placeholder for Pages, filled in below
    kids = []
    for w, h, rgb in job.pages:
        img = zlib.compress(rgb, 6)
        im = add(f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceRGB "
                 f"/BitsPerComponent 8 /Filter /FlateDecode /Length {len(img)} >>\nstream\n".encode()
                 + img + b"\nendstream")
        draw = f"q {pw_pt:.2f} 0 0 {ph_pt:.2f} 0 0 cm /Im0 Do Q".encode()
        content = add(f"<< /Length {len(draw)} >>\nstream\n".encode() + draw + b"\nendstream")
        kids.append(add(f"<< /Type /Page /Parent {pages_ref} 0 R /MediaBox [0 0 {pw_pt:.2f} {ph_pt:.2f}] "
                        f"/Resources << /XObject << /Im0 {im} 0 R >> >> /Contents {content} 0 R >>".encode()))
    objects[pages_ref - 1] = (f"<< /Type /Pages /Count {len(kids)} /Kids ["
                              + " ".join(f"{k} 0 R" for k in kids) + "] >>").encode()
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    with open(path, "wb") as f:
        f.write(out)
    job.pdf_path = path


def _pdf_text(s: str) -> bytes:
    """A PDF text string (UTF-16 with a byte-order mark covers any title)."""
    return b"<" + ("﻿" + s).encode("utf-16-be").hex().encode() + b">"


def temp_pdf(job: PrintJob) -> str:
    folder = Path(tempfile.gettempdir()) / "mercy-print"
    folder.mkdir(exist_ok=True)
    path = folder / (safe_name(job.title) + ".pdf")
    write_pdf(job, str(path))
    return str(path)


def safe_name(title: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", title).strip(" .") or "Document"
    return name[:80]


def save_location(title: str) -> Path:
    """~/Documents (or home) / Title.pdf, numbered if that name is taken."""
    folder = Path.home() / "Documents"
    if not folder.is_dir():
        folder = Path.home()
    base = safe_name(title)
    path = folder / f"{base}.pdf"
    n = 2
    while path.exists():
        path = folder / f"{base} ({n}).pdf"
        n += 1
    return path


# ------------------------------------------------------------------ printers
def printers() -> tuple[list[str], str | None]:
    """The printers the system knows about, and the default one."""
    if IS_WINDOWS:
        return ["Default printer"], "Default printer"
    if not shutil.which("lpstat"):
        return [], None
    try:
        names = subprocess.run(["lpstat", "-e"], capture_output=True, text=True, timeout=5).stdout.split()
        default = subprocess.run(["lpstat", "-d"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return [], None
    m = re.search(r":\s*(\S+)\s*$", default.strip())
    d = m[1] if m and m[1] in names else (names[0] if names else None)
    return names, d


def send_to_printer(pdf_path: str, printer: str, copies: int, paper: str) -> str:
    """Submit the PDF; returns a short confirmation (raises on failure)."""
    if IS_WINDOWS:
        os.startfile(pdf_path, "print")  # the registered PDF app prints to the default printer
        return "Sent to the default printer"
    media = "a4" if paper == "A4" else "letter"
    cmd = ["lp", "-d", printer, "-n", str(max(1, copies)), "-o", f"media={media}", "-o", "fit-to-page",
           "-t", Path(pdf_path).stem, pdf_path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip() or "the printing service refused the job")
    return f"Sent to {printer}"


def open_file(path: str):
    """Open a file in its default app (here: the PDF viewer)."""
    if IS_WINDOWS:
        os.startfile(path)
    elif IS_MAC:
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
