"""The few places where Linux, Windows and macOS differ."""

import os
import sys
from pathlib import Path

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform.startswith("win")


def config_dir() -> Path:
    """Where settings and bookmarks live, following each platform's convention."""
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Mercy"
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / "Mercy"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "mercy"


def shortcut(text: str) -> str:
    """Write a shortcut the way this platform's users expect: Cmd rather than Ctrl on a Mac.

    Ctrl+Tab stays as it is, since macOS uses Ctrl for tab switching too.
    """
    if not IS_MAC or text.startswith("Ctrl+Tab"):
        return text
    return text.replace("Ctrl+", "Cmd+").replace("Alt+", "Option+")


def prepare_process():
    """Per-platform setup that must happen before the window is created."""
    if IS_WINDOWS:
        try:
            # Group the window under its own taskbar entry (and icon) rather than Python's.
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Mercy.MarkdownBrowser")
        except (AttributeError, OSError):
            pass
