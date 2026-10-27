"""Turning addresses into Markdown text (and image bytes)."""

import io
import os
import re
import threading
import queue
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote
from urllib.request import url2pathname

import requests

from .system import IS_MAC

PAGES_DIR = Path(__file__).parent / "pages"
USER_AGENT = "Mercy/0.1 (Markdown browser; +pyglet)"
MARKDOWN_EXT = {".md", ".markdown", ".mdown", ".mkd", ".mdx"}
CODE_EXT = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".json": "json", ".toml": "toml",
    ".yaml": "yaml", ".yml": "yaml", ".sh": "bash", ".c": "c", ".h": "c", ".cpp": "cpp",
    ".rs": "rust", ".go": "go", ".java": "java", ".rb": "ruby", ".lua": "lua", ".css": "css",
    ".html": "html", ".xml": "xml", ".ini": "ini", ".cfg": "ini", ".txt": "", ".log": "",
    ".csv": "", ".sql": "sql",
}

PASTED = {"text": ""}  # Markdown pasted into the window, shown at mercy://pasted
PROVIDERS = {}         # internal pages generated on demand: name -> () -> Markdown

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT


@dataclass
class Document:
    url: str
    text: str
    title: str = ""
    error: bool = False


def normalize(address: str) -> str:
    """Make whatever was typed into the address bar into a full URL."""
    address = address.strip()
    if not address:
        return "mercy://home"
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", address):
        return github_raw(address)
    expanded = os.path.expanduser(address)
    if looks_like_path(address) or os.path.exists(expanded):
        return Path(expanded).resolve().as_uri()
    if " " in address or "." not in address:
        return "mercy://search?" + address
    return github_raw("https://" + address)


def looks_like_path(text: str) -> bool:
    """Unix paths, home-relative paths, and Windows drive or network paths."""
    return (text.startswith(("/", "~", "./", "../", ".\\", "..\\", "\\\\"))
            or re.match(r"^[A-Za-z]:[\\/]", text) is not None)


def github_raw(url: str) -> str:
    """Point GitHub web URLs at their raw Markdown, since that's what we render."""
    m = re.match(r"^https?://github\.com/([^/]+)/([^/#?]+)/blob/(.+)$", url)
    if m:
        return f"https://raw.githubusercontent.com/{m[1]}/{m[2]}/{m[3]}"
    m = re.match(r"^https?://github\.com/([^/]+)/([^/#?]+)/?$", url)
    if m:
        return f"https://raw.githubusercontent.com/{m[1]}/{m[2]}/HEAD/README.md"
    return url


def resolve(base: str, href: str) -> str:
    href = href.strip()
    if re.match(r"^[A-Za-z]:[\\/]", href):  # a Windows path, not a one-letter URL scheme
        return normalize(href)
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", href):
        return github_raw(href)
    if base.startswith("mercy://"):
        if href.startswith(("~", "/")):
            return normalize(href)
        # Internal pages link to their siblings by bare name.
        return "mercy://" + href.lstrip("./").removesuffix(".md")
    return github_raw(urljoin(base, href))


def split_fragment(url: str) -> tuple[str, str]:
    if "#" in url:
        u, frag = url.split("#", 1)
        return u, unquote(frag)
    return url, ""


def _file_path(url: str) -> Path:
    p = urlparse(url)
    path = p.path
    if p.netloc and p.netloc != "localhost":  # file://server/share/... (a Windows network share)
        path = f"//{p.netloc}{path}"
    return Path(url2pathname(path))  # url2pathname also undoes %-escapes, on every platform


def load(url: str) -> Document:
    url, _ = split_fragment(url)
    try:
        if url.startswith("mercy://"):
            return _internal(url)
        if url.startswith("file:"):
            return _local(url)
        if url.startswith(("http://", "https://")):
            return _remote(url)
        return error_page(url, "Unknown scheme", f"Mercy doesn't know how to open `{url}`.")
    except requests.RequestException as e:
        return error_page(url, "Couldn't reach that page", f"```\n{e}\n```")
    except OSError as e:
        return error_page(url, "Couldn't open that file", f"```\n{e}\n```")


def mac_keys(markdown: str) -> str:
    """On a Mac, built-in pages say Cmd and Option where they'd say Ctrl and Alt
    (Ctrl+Tab stays: macOS switches tabs with Control too)."""
    if not IS_MAC:
        return markdown
    markdown = re.sub(r"<kbd>Ctrl</kbd>(?! \+ <kbd>(?:Tab|Shift</kbd> \+ <kbd>Tab))", "<kbd>Cmd</kbd>", markdown)
    return markdown.replace("<kbd>Alt</kbd>", "<kbd>Option</kbd>")


def _internal(url: str) -> Document:
    doc = _internal_page(url)
    doc.text = mac_keys(doc.text)
    return doc


def _internal_page(url: str) -> Document:
    name = url[len("mercy://"):].strip("/") or "home"
    if name.startswith("search?"):
        q = name[len("search?"):]
        return Document(url, f"# Search\n\nMercy doesn't search the web (yet). You typed:\n\n> {q}\n\n"
                        "Try a full address such as `github.com/owner/repo`, a URL ending in `.md`, "
                        "or a path to a local folder like `~/Documents`.", "Search")
    if name == "pasted":
        return Document(url, PASTED["text"] or "# Nothing pasted yet", "Pasted text")
    if name in PROVIDERS:
        return Document(url, PROVIDERS[name](), name.title())
    path = PAGES_DIR / f"{name}.md"
    if not path.exists():
        return error_page(url, "No such page", f"There's no internal page called `{name}`.")
    return Document(url, path.read_text(encoding="utf-8"))


def _local(url: str) -> Document:
    path = _file_path(url)
    if path.is_dir():
        return Document(url.rstrip("/") + "/", _directory_listing(path), path.name or str(path))
    data = path.read_bytes()
    return Document(url, _as_markdown(path.suffix.lower(), data.decode("utf-8", "replace")), path.name)


def _directory_listing(path: Path) -> str:
    for readme in ("README.md", "readme.md", "index.md", "Readme.md"):
        if (path / readme).is_file():
            # Show the readme, but keep the listing reachable below it.
            body = (path / readme).read_text(encoding="utf-8", errors="replace")
            break
    else:
        body = ""
    entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    entries = [e for e in entries if not e.name.startswith(".")]
    lines = [f"# {path.name or path}", "", f"`{path}`", ""]
    if path.parent != path:
        lines.append(f"- [**..** parent folder](<{path.parent.as_uri().rstrip('/')}/>)")
    for e in entries:
        href = "<" + e.absolute().as_uri() + ("/" if e.is_dir() else "") + ">"
        if e.is_dir():
            lines.append(f"- [**{e.name}/**]({href})")
        elif e.suffix.lower() in MARKDOWN_EXT:
            lines.append(f"- [{e.name}]({href})")
        else:
            lines.append(f"- [{e.name}]({href}) — *{_human_size(e)}*")
    listing = "\n".join(lines)
    return f"{body}\n\n---\n\n{listing}" if body else listing


def _human_size(p: Path) -> str:
    try:
        n = p.stat().st_size
    except OSError:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _as_markdown(suffix: str, text: str) -> str:
    if suffix in MARKDOWN_EXT or suffix == "":
        return text
    if suffix in CODE_EXT:
        fence = "````" if "```" in text else "```"
        return f"{fence}{CODE_EXT[suffix]}\n{text}\n{fence}"
    return text


def _remote(url: str) -> Document:
    r = _session.get(url, timeout=15, headers={
        "Accept": "text/markdown, text/x-markdown;q=0.95, text/plain;q=0.9, */*;q=0.1"})
    r.raise_for_status()
    if r.encoding is None or r.encoding.lower() == "iso-8859-1":
        r.encoding = "utf-8"
    ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
    final = github_raw(r.url)
    suffix = os.path.splitext(urlparse(final).path)[1].lower()
    if ctype == "text/html" and suffix not in MARKDOWN_EXT:
        return error_page(final, "This page is HTML",
                          "Mercy is a browser for **Markdown**, and this server answered with HTML.\n\n"
                          "Many sites publish a Markdown version of their pages — try adding `.md` to the "
                          "address, or point Mercy at a raw file (for GitHub, any `github.com/owner/repo` "
                          "address is translated to its README automatically).\n\n"
                          f"[Open in your default browser](mercy-external:{final})")
    if ctype.startswith("image/"):
        return Document(final, f"![{os.path.basename(final)}]({final})")
    return Document(final, _as_markdown(suffix, r.text))


def error_page(url: str, title: str, body: str) -> Document:
    return Document(url, f"# {title}\n\n{body}\n\n<sub>{url}</sub>\n\n[← Go home](mercy://home)",
                    title, error=True)


def fetch_bytes(url: str) -> bytes:
    if url.startswith("file:"):
        return _file_path(url).read_bytes()
    if url.startswith("mercy://"):
        return (PAGES_DIR / url[len("mercy://"):]).read_bytes()
    r = _session.get(url, timeout=20)
    r.raise_for_status()
    return r.content


class Worker:
    """Runs blocking loads on background threads; results are polled on the UI thread."""

    def __init__(self):
        self.results: queue.Queue = queue.Queue()

    def submit(self, tag, fn, *args):
        def run():
            try:
                self.results.put((tag, fn(*args), None))
            except Exception as e:  # reported back to the UI
                self.results.put((tag, None, e))
        threading.Thread(target=run, daemon=True).start()

    def drain(self):
        while True:
            try:
                yield self.results.get_nowait()
            except queue.Empty:
                return


def decode_image(url: str):
    """Runs on a worker thread: download an image and, if Pillow can, decode it there.

    Returns pyglet ImageData, or (name, bytes) for the UI thread to decode with
    pyglet's own codecs (Windows' WIC decoder only works on the thread that set up
    COM, and on Linux pyglet can also read SVG badges).
    """
    data = fetch_bytes(url)
    name = os.path.basename(urlparse(url).path) or "image.png"
    try:
        from PIL import Image
        import pyglet
        img = Image.open(io.BytesIO(data))
        img.seek(0)
        img = img.convert("RGBA")
        return pyglet.image.ImageData(img.width, img.height, "RGBA", img.tobytes(), pitch=-img.width * 4)
    except Exception:
        return name, data


def finish_image(result):
    """On the UI thread: turn decode_image's result into a texture."""
    import pyglet
    if isinstance(result, tuple):
        name, data = result
        result = pyglet.image.load(name, file=io.BytesIO(data))
    return result.get_texture()
