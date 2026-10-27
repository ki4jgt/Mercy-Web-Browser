# Help

## Addresses

Mercy understands several kinds of address:

| You type                         | Mercy opens |
|:---------------------------------|:------------|
| `https://example.com/notes.md`   | the Markdown file |
| `github.com/owner/repo`          | the repository's README |
| `github.com/owner/repo/blob/…`   | the raw file behind it |
| `~/Documents` or `/some/folder`  | the folder's README, or a list of its files if it has none |
| `./notes.md`                     | a local file |
| `mercy://home`                   | the start page |

Source files such as `.py`, `.json` or `.toml` are shown as highlighted code.

## Keyboard

| Keys | Action |
|:-----|:-------|
| <kbd>Ctrl</kbd> + <kbd>L</kbd> or <kbd>F6</kbd> | Edit the address |
| <kbd>Alt</kbd> + <kbd>←</kbd> / <kbd>Backspace</kbd> | Back |
| <kbd>Alt</kbd> + <kbd>→</kbd> | Forward |
| <kbd>Ctrl</kbd> + <kbd>[</kbd> / <kbd>]</kbd> | Back / forward |
| <kbd>F5</kbd> or <kbd>Ctrl</kbd> + <kbd>R</kbd> | Reload |
| <kbd>Alt</kbd> + <kbd>Home</kbd> | Start page |
| <kbd>Ctrl</kbd> + <kbd>T</kbd> | New tab |
| <kbd>Ctrl</kbd> + <kbd>W</kbd> | Close tab |
| <kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>T</kbd> | Reopen the last closed tab |
| <kbd>Ctrl</kbd> + <kbd>Tab</kbd> / <kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>Tab</kbd> | Next / previous tab (also <kbd>Ctrl</kbd> + <kbd>PgDn</kbd> / <kbd>PgUp</kbd>) |
| <kbd>Ctrl</kbd> + <kbd>1</kbd> … <kbd>8</kbd>, <kbd>9</kbd> | Go to that tab, or the last one |
| <kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>S</kbd> | Next color style |
| <kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>P</kbd> | Play / pause music |
| <kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>.</kbd> / <kbd>,</kbd> | Next / previous track |
| <kbd>Ctrl</kbd> + <kbd>+</kbd> / <kbd>-</kbd> / <kbd>0</kbd> | Zoom in / out / reset |
| <kbd>↑</kbd> / <kbd>↓</kbd> | Scroll; hold to glide, speeding up the longer you hold |
| <kbd>Space</kbd>, <kbd>PgUp</kbd>, <kbd>PgDn</kbd> | Scroll a screen |
| <kbd>Home</kbd> / <kbd>End</kbd> | Top / bottom of the page |
| <kbd>Ctrl</kbd> + <kbd>C</kbd> | Copy the selection |
| <kbd>Ctrl</kbd> + <kbd>A</kbd> | Select the whole page (or the whole address while editing) |
| <kbd>Ctrl</kbd> + <kbd>V</kbd> | On the page: open a pasted address, or show pasted Markdown |
| <kbd>Ctrl</kbd> + <kbd>X</kbd> / <kbd>V</kbd> | Cut / paste in the address bar |
| <kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>V</kbd> | Paste into the address bar and go |
| <kbd>Ctrl</kbd> + <kbd>D</kbd> | Bookmark this page (or remove its bookmark) |
| <kbd>Ctrl</kbd> + <kbd>B</kbd> | Open the bookmarks panel |
| <kbd>Ctrl</kbd> + <kbd>E</kbd> | Show or hide the header explorer |
| <kbd>Ctrl</kbd> + <kbd>P</kbd> | Print, or save as PDF |
| <kbd>Ctrl</kbd> + <kbd>Q</kbd> | Quit |

You can also hold <kbd>Ctrl</kbd> while scrolling to zoom, drag the scrollbar on the right, and use your mouse's side buttons for back and forward.

## Tabs

Open a new tab with the **+** button, <kbd>Ctrl</kbd> + <kbd>T</kbd>, or a double-click on the empty part of the tab strip. Middle-click or <kbd>Ctrl</kbd>-click a link to open it in a background tab, or right-click it and choose **Open Link in New Tab**.

Drag tabs to reorder them, middle-click one to close it, or scroll over the strip to flip between them. Right-click a tab to duplicate it, close the others, or reopen one you closed. Every tab keeps its own history and scroll position. Your tabs are restored the next time you start Mercy without an address. Each one loads when you first open it.

## Music

Click a link to a music file (MP3, OGG, FLAC, WAV, M4A and others) and Mercy plays it in a player at the bottom of the window instead of leaving the page. Every other track linked on the same page joins the queue in order. Folders with music in them get a **Play all** link at the top.

The player has previous, play/pause and next buttons, a progress line you can click or drag to seek, and a volume slider. You can also scroll over the player to change the volume. Music keeps playing while you browse and switch tabs. Close the player with **×**.

## Printing

Press <kbd>Ctrl</kbd> + <kbd>P</kbd>, or right-click the page and choose **Print…**. Mercy lays the page out again for paper, in black on white whatever color style you're using, with a running header and page numbers. Page breaks never cut through a line of text or a picture.

The print panel shows a preview you can flip through with the arrows, the arrow keys, or the scroll wheel. Choose a printer, A4 or Letter paper, and the number of copies, then press **Print**. Choose **Save as PDF** to keep a copy in your Documents folder, or **Open PDF** to open it in your PDF viewer. On Linux and macOS, Mercy prints through the system's printing service (CUPS). On Windows it uses the default printer.

## Selecting and copying

Drag across the page to select text. Double-click selects a word, and triple-click selects a line. Hold <kbd>Shift</kbd> and click to extend a selection. Copied text keeps its shape: paragraphs stay separate, list items keep their markers, table cells are separated by tabs, and code comes out exactly as written.

Right-click for a menu with **Copy**, **Select All**, **Paste & Go**, **Copy Link Address** and **Copy Page Address**.

## Header explorer

The button at the far left of the toolbar (<kbd>Ctrl</kbd> + <kbd>E</kbd>) opens a sidebar listing every heading on the page, indented by level. The section you're reading is highlighted as you scroll. Click any entry to jump there, and press Back to return. In narrow windows the explorer floats over the page and closes after you pick an entry. Mercy remembers whether you left it open.

## Bookmarks

Click the star at the end of the address bar, or press <kbd>Ctrl</kbd> + <kbd>D</kbd>, to bookmark the page you're reading. The ribbon button in the toolbar (<kbd>Ctrl</kbd> + <kbd>B</kbd>) opens a quick panel: start typing to filter, use the arrow keys and <kbd>Enter</kbd> to open one, or click **×** to remove it.

The full manager lives at [mercy://bookmarks](bookmarks). There you can rename bookmarks, sort them into folders, reorder them, and copy the whole collection as Markdown. Bookmarks are saved with your settings: in `~/.config/mercy` on Linux, `%APPDATA%\Mercy` on Windows, and `~/Library/Application Support/Mercy` on macOS.

## Code blocks

Every code block has a **COPY** button in its header. Click it and the code is on your clipboard.

## Paragraph indents

As in a printed book, a paragraph that follows another paragraph has its first line indented. The first paragraph after a heading, list, quote, picture or aligned line starts flush with the margin, since the break before it already marks something new.

## Aligning lines

Start a line with a colon to right-justify it, or wrap it in colons to center it:

```markdown
:Signed, the author
:A centered line:
```

:Signed, the author
:A centered line:

## Auto-numbered lines

Start lines with a dot and a space, and Mercy numbers them for you. Indent to nest:

```markdown
. Boil the water
. Warm the pot
  . swirl a little hot water
. Steep for four minutes
```

. Boil the water
. Warm the pot
  . swirl a little hot water
. Steep for four minutes

## Links inside a page

Links like [this one](#addresses) jump to a heading on the same page, and the back button brings you back.

[← Back home](home)
