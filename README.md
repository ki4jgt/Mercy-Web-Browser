# Mercy

A web browser for **Markdown**, written in Python with [pyglet](https://pyglet.org). It doesn't render HTML: it fetches `.md` documents and typesets them with its own layout engine.

## Running

Mercy runs on Linux, Windows and macOS with Python 3.10 or newer.

```bash
pip install -r requirements.txt
python main.py                          # start page (or your tabs from last time)
python main.py github.com/pyglet/pyglet # a repository's README
python main.py ~/notes                  # a local folder
```

- **Windows:** `py -m pip install -r requirements.txt`, then double-click `Mercy.pyw` (no console window) or run `py main.py`.
- **macOS:** `python3 -m pip install -r requirements.txt`, then double-click `Mercy.command` or run `python3 main.py`. Shortcuts use <kbd>Cmd</kbd> where this README says <kbd>Ctrl</kbd>, and <kbd>Ctrl</kbd>-click works as a right-click.
- **Linux:** run `python3 main.py`. Your distribution's `python3-pyglet` package works too.

Settings, tabs and bookmarks are saved in `~/.config/mercy` on Linux, `%APPDATA%\Mercy` on Windows, and `~/Library/Application Support/Mercy` on macOS.

## Features

- Eight color styles: Aurora, Dark, Paper, Sepia, Glacier, Dusk, Forest and Synthwave. Pick one from the palette menu or press <kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>S</kbd>.
- Headings, lists, task lists, tables, block quotes, GitHub-style callouts, images (including SVG badges) and inline HTML images
- Syntax-highlighted code blocks with a copy button
- Bookmarks: a star in the address bar, a quick panel with type-to-filter (<kbd>Ctrl</kbd> + <kbd>B</kbd>), and a manager page with folders
- A header explorer sidebar (<kbd>Ctrl</kbd> + <kbd>E</kbd>) that lists the page's headings and highlights the section you're reading
- Tabs: open, close, reopen, duplicate and drag to reorder; links open in background tabs with a middle-click or <kbd>Ctrl</kbd>-click, and your tabs come back next time
- A built-in music player: click links to audio files (or **Play all** in a folder) to queue them up, and keep browsing while they play
- Smooth scrolling, back/forward history with saved scroll positions, in-page anchors, and zoom
- GitHub URLs open the raw Markdown behind them
- Local folders show as browsable listings, and source files show as highlighted code

## Layout

| File | Role |
|:-----|:-----|
| `main.py` | entry point |
| `mercy/browser.py` | window, toolbar, scrolling, input, navigation |
| `mercy/render.py` | Markdown → pyglet layout engine |
| `mercy/fetch.py` | loading pages and images from the web, disk and `mercy://` |
| `mercy/bookmarks.py` | bookmark storage and the `mercy://bookmarks` page |
| `mercy/themes.py` | color styles and font stacks |
| `mercy/gfx.py` | gradients, glows, shadows and icons |
| `mercy/pages/` | built-in pages |
| `mercy/icons/` | the app icon at 16–512 px |
| `mercy/music.py` | the music player's queue and playback |
| `mercy/system.py` | the differences between Linux, Windows and macOS |
| `Mercy.pyw`, `Mercy.command` | double-click launchers for Windows and macOS |
| `tools/make_icon.py` | draws the icon; run it to regenerate `mercy/icons/` |
