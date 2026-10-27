#!/usr/bin/env python3
"""Mercy — a Markdown web browser built with pyglet.

Usage:
    python main.py [address]

The address can be a URL, a GitHub repository, a local file or a folder.
"""

import sys

import pyglet

from mercy.browser import Browser


def main():
    Browser(sys.argv[1] if len(sys.argv) > 1 else None)
    pyglet.app.run()


if __name__ == "__main__":
    main()