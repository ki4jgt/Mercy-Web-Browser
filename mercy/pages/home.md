# Welcome to Mercy

Mercy is a quiet little web browser that speaks **Markdown** instead of HTML. Point it at a `.md` file on the web, a GitHub repository, or a folder on your own computer, and it lays the page out like a well-set book.

> [!TIP]
> Click the address bar (or press <kbd>Ctrl</kbd> + <kbd>L</kbd>) and try `github.com/pyglet/pyglet`, a URL ending in `.md`, or a local path like `~/Documents`.

## Places to start

- [Keyboard shortcuts & help](help)
- [Your bookmarks](bookmarks)
- [Your home folder](~) — browse folders like a file manager
- [The CommonMark spec README](https://raw.githubusercontent.com/commonmark/commonmark-spec/master/README.md)
- [markdown-it-py on GitHub](https://github.com/executablebooks/markdown-it-py)

## Seven color styles

Pick one from the palette button in the top right, or cycle with <kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>S</kbd>. Your choice is remembered.

| Style      | Mood                    | Typeface |
|:-----------|:------------------------|:--------:|
| Aurora     | Midnight, northern lights | Sans   |
| Paper      | Editorial, ivory stock  | Serif    |
| Sepia      | Old library, warm lamp  | Serif    |
| Glacier    | Cool arctic slate       | Sans     |
| Dusk       | Rosy twilight           | Sans     |
| Forest     | Moss and lantern-light  | Sans     |
| Synthwave  | Neon on velvet          | Sans     |

---

## What Mercy renders

Paragraphs with *emphasis*, **strong text**, ~~strikethrough~~, `inline code`, and [links](help) — including bare ones like https://commonmark.org.

### Lists

1. Ordered lists keep their numbers
2. Nested lists get their own markers
   - like this one
     - and this one
3. Task lists become checkboxes
   - [x] Parse Markdown
   - [x] Lay it out beautifully
   - [ ] Take over the world

### Code, highlighted

```python
from mercy.browser import Browser
import pyglet

def main(address: str | None = None) -> None:
    """Open a window and start browsing."""
    Browser(address)   # themes, history, smooth scrolling…
    pyglet.app.run()
```

### Quotes and callouts

> Simplicity is prerequisite for reliability.
>
> — Edsger W. Dijkstra

> [!NOTE]
> Mercy fetches pages on a background thread, so the window stays smooth while it loads.

> [!WARNING]
> Pages that answer with HTML are politely declined — but Mercy offers to open them in your usual browser.
