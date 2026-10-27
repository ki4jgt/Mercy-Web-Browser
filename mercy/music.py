"""The integrated music player: a play queue on top of pyglet.media.

Playback is global to the browser, so music keeps going while you switch tabs
or browse. Local files play directly; web files are downloaded on a worker
thread into a temporary folder first (pyglet's decoders want a real file).
"""

import atexit
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import pyglet

from . import fetch

AUDIO_EXT = fetch.AUDIO_EXT


def is_audio(url: str) -> bool:
    path = urlparse(fetch.split_fragment(url)[0]).path
    return os.path.splitext(unquote(path))[1].lower() in AUDIO_EXT


def guess_tags(url: str) -> tuple[str, str]:
    """Title and artist from a file name like '03 - Artist - Title.mp3'."""
    name = os.path.splitext(os.path.basename(unquote(urlparse(url).path)))[0]
    name = re.sub(r"^\s*\d{1,3}\s*[-._)]?\s*", "", name).replace("_", " ").strip() or name
    if name.islower():  # "glacier drift" reads better as "Glacier Drift"
        name = name.title()
    if " - " in name:
        artist, title = name.split(" - ", 1)
        return title.strip(), artist.strip()
    return name, ""


def read_tags(path: str, url: str) -> tuple[str, str]:
    """Real tags when mutagen is installed (optional), otherwise a guess from the file name."""
    title, artist = guess_tags(url)
    try:
        import mutagen
        f = mutagen.File(path, easy=True)
        if f and f.tags:
            title = (f.tags.get("title") or [title])[0] or title
            artist = (f.tags.get("artist") or [artist])[0] or artist
    except Exception:
        pass
    return title, artist


class MusicPlayer:
    def __init__(self, on_change):
        self.on_change = on_change          # called whenever the UI should redraw
        self.worker = fetch.Worker()
        self.queue: list[str] = []
        self.index = -1
        self.player: pyglet.media.Player | None = None
        self.source = None
        self.title = self.artist = ""
        self.duration = 0.0
        self.loading = False
        self.error = ""
        self.volume = 0.8
        self.seq = 0
        self._tmp = None
        self._file = None

    # ---------------------------------------------------------------- state
    @property
    def active(self) -> bool:
        return bool(self.queue)

    @property
    def playing(self) -> bool:
        return bool(self.player and self.player.playing)

    @property
    def position(self) -> float:
        return self.player.time if self.player else 0.0

    @property
    def current_url(self):
        return self.queue[self.index] if 0 <= self.index < len(self.queue) else None

    # -------------------------------------------------------------- control
    def play_queue(self, urls: list[str], start: int = 0):
        self.queue = list(urls)
        self.load(max(0, min(start, len(self.queue) - 1)))

    def load(self, i: int):
        self._drop_player()
        self.index = i
        url = self.queue[i]
        self.title, self.artist = guess_tags(url)
        self.duration, self.error = 0.0, ""
        self.loading = True
        self.seq += 1
        if url.startswith("file:"):
            self._start(str(fetch._file_path(url)), url, self.seq)
        else:
            self.worker.submit(("track", self.seq, url), self._download, url)
        self.on_change()

    def _download(self, url):
        if self._tmp is None:
            self._tmp = tempfile.mkdtemp(prefix="mercy-music-")
            atexit.register(shutil.rmtree, self._tmp, True)
        ext = os.path.splitext(unquote(urlparse(url).path))[1].lower() or ".mp3"
        path = os.path.join(self._tmp, f"track-{abs(hash(url))}{ext}")
        if not os.path.exists(path):
            data = fetch.fetch_bytes(url)
            with open(path, "wb") as f:
                f.write(data)
        return path

    def poll(self):
        for tag, result, err in self.worker.drain():
            _, seq, url = tag
            if seq != self.seq:
                continue
            if err:
                status = getattr(getattr(err, "response", None), "status_code", None)
                self._fail(f"Couldn't download this track ({status or type(err).__name__})")
            else:
                self._start(result, url, seq)

    def _start(self, path, url, seq):
        try:
            # some decoders only recognise lower-case extensions (e.g. "Song.MP3")
            name = os.path.splitext(path)[0] + os.path.splitext(path)[1].lower()
            if name != path:
                self._file = open(path, "rb")  # streaming reads it during playback; closed in _drop_player
                self.source = pyglet.media.load(name, file=self._file, streaming=True)
            else:
                self.source = pyglet.media.load(path, streaming=True)
        except Exception as e:
            self._fail(f"Can't play this file ({type(e).__name__})")
            return
        if seq != self.seq:
            return
        self.title, self.artist = read_tags(path, url)
        info = getattr(self.source, "info", None)
        if info is not None:
            self.title = getattr(info, "title", "") or self.title
            self.artist = getattr(info, "author", "") or self.artist
        self.duration = self.source.duration or 0.0
        self.player = pyglet.media.Player()
        self.player.volume = self.volume
        self.player.push_handlers(on_eos=self._on_eos)
        self.player.queue(self.source)
        self.player.play()
        self.loading = False
        self.on_change()

    def _fail(self, message):
        self.loading = False
        self.error = message
        self.on_change()

    def _on_eos(self):
        # pyglet calls this from its own clock; move on to the next track
        pyglet.clock.schedule_once(lambda dt: self.next(auto=True), 0)

    def _drop_player(self):
        if self.player:
            self.player.pause()
            self.player.delete()
        self.player, self.source = None, None
        if self._file:
            self._file.close()
            self._file = None

    def toggle(self):
        if not self.active:
            return
        if self.player is None and not self.loading:
            self.load(max(0, self.index))
        elif self.player:
            self.player.pause() if self.player.playing else self.player.play()
        self.on_change()

    def next(self, auto=False):
        if not self.active:
            return
        if self.index + 1 < len(self.queue):
            self.load(self.index + 1)
        elif auto:  # end of the queue: stop on the last track, ready to replay
            self._drop_player()
            self.on_change()
        else:
            self.load(0)

    def previous(self):
        if not self.active:
            return
        if self.position > 3 or self.index == 0:
            self.seek(0)
        else:
            self.load(self.index - 1)

    def seek(self, seconds):
        if self.player and self.duration:
            self.player.seek(max(0.0, min(seconds, self.duration - 0.05)))
            self.on_change()

    def set_volume(self, v):
        self.volume = max(0.0, min(1.0, v))
        if self.player:
            self.player.volume = self.volume

    def stop(self):
        self._drop_player()
        self.queue, self.index, self.loading, self.error = [], -1, False, ""
        self.seq += 1
        self.on_change()


def fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds < 3600 else \
        f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
