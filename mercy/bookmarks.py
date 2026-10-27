"""Bookmarks: stored as JSON, shown in a quick panel and at mercy://bookmarks."""

import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from .system import config_dir

CONFIG = config_dir()
UNSORTED = "Unsorted"


def _md_escape(text: str) -> str:
    for ch in "\\[]*_`<>#|":
        text = text.replace(ch, "\\" + ch)
    return text


def short_url(url: str) -> str:
    if url.startswith(("http://", "https://")):
        p = urlparse(url)
        return p.netloc + (p.path if p.path not in ("", "/") else "")
    if url.startswith("file://"):
        return url2pathname(urlparse(url).path)
    return url


class Bookmarks:
    def __init__(self, path: Path = CONFIG / "bookmarks.json"):
        self.path = path
        self.items: list[dict] = []
        try:
            self.items = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.items = []

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.items, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    # ----------------------------------------------------------------- queries
    def find(self, url: str):
        return next((b for b in self.items if b["url"] == url), None)

    def get(self, bid: str):
        return next((b for b in self.items if b["id"] == bid), None)

    def folders(self) -> list[str]:
        seen = []
        for b in self.items:
            f = b.get("folder") or UNSORTED
            if f not in seen:
                seen.append(f)
        return sorted(seen, key=lambda f: (f == UNSORTED, f.lower()))

    def search(self, query: str) -> list[dict]:
        q = query.lower().strip()
        if not q:
            return list(self.items)
        words = q.split()
        return [b for b in self.items
                if all(w in f"{b['title']} {b['url']} {b.get('folder', '')}".lower() for w in words)]

    # --------------------------------------------------------------- changes
    def add(self, url: str, title: str, folder: str = "") -> dict:
        existing = self.find(url)
        if existing:
            return existing
        b = dict(id=uuid.uuid4().hex[:10], url=url, title=title.strip() or short_url(url),
                 folder=folder, added=time.time())
        self.items.append(b)
        self.save()
        return b

    def remove(self, bid: str):
        self.items = [b for b in self.items if b["id"] != bid]
        self.save()

    def rename(self, bid: str, title: str):
        b = self.get(bid)
        if b and title.strip():
            b["title"] = title.strip()
            self.save()

    def set_folder(self, bid: str, folder: str):
        b = self.get(bid)
        if b:
            b["folder"] = "" if folder.strip() == UNSORTED else folder.strip()
            self.save()

    def move(self, bid: str, delta: int):
        """Move a bookmark up or down among the others in its folder."""
        b = self.get(bid)
        if not b:
            return
        same = [i for i, o in enumerate(self.items) if (o.get("folder") or "") == (b.get("folder") or "")]
        pos = same.index(self.items.index(b))
        j = pos + delta
        if 0 <= j < len(same):
            a, c = same[pos], same[j]
            self.items[a], self.items[c] = self.items[c], self.items[a]
            self.save()

    # ------------------------------------------------------------- markdown
    def export_markdown(self) -> str:
        lines = ["# Bookmarks", ""]
        for folder in self.folders():
            lines += [f"## {folder}", ""]
            for b in self.items:
                if (b.get("folder") or UNSORTED) == folder:
                    lines.append(f"- [{_md_escape(b['title'])}](<{b['url']}>)")
            lines.append("")
        return "\n".join(lines)

    def manager_page(self) -> str:
        n = len(self.items)
        out = ["# Bookmarks", ""]
        if not n:
            out += ["You haven't bookmarked anything yet.", "",
                    "> [!TIP]",
                    "> Press <kbd>Ctrl</kbd> + <kbd>D</kbd> or click the star in the address bar to bookmark "
                    "the page you're on. <kbd>Ctrl</kbd> + <kbd>B</kbd> opens your bookmarks from anywhere.",
                    "", "[← Back home](home)"]
            return "\n".join(out)
        folders = self.folders()
        out += [f"{n} bookmark{'s' if n != 1 else ''} in {len(folders)} folder{'s' if len(folders) != 1 else ''}"
                " · [Copy as Markdown](mercy-bm:export)", ""]
        for folder in folders:
            members = [b for b in self.items if (b.get("folder") or UNSORTED) == folder]
            out += [f"## {_md_escape(folder)}", ""]
            for i, b in enumerate(members):
                bid = b["id"]
                actions = [f"[rename](mercy-bm:rename:{bid})", f"[folder](mercy-bm:folder:{bid})"]
                if i:
                    actions.append(f"[↑ up](mercy-bm:up:{bid})")
                if i < len(members) - 1:
                    actions.append(f"[↓ down](mercy-bm:down:{bid})")
                actions.append(f"[delete](mercy-bm:delete:{bid})")
                when = time.localtime(b.get("added", 0))  # (%-d isn't portable to Windows)
                added = f"{time.strftime('%b', when)} {when.tm_mday}, {when.tm_year}"
                out += [f"- **[{_md_escape(b['title'])}](<{b['url']}>)**  ",
                        f"  `{short_url(b['url'])}` · *{added}*  ",
                        "  " + " · ".join(actions), ""]
        out += ["---", "",
                "<kbd>Ctrl</kbd> + <kbd>D</kbd> bookmarks the current page · <kbd>Ctrl</kbd> + <kbd>B</kbd> "
                "opens the quick panel"]
        return "\n".join(out)
