"""Loads the real "OpenHands Neon SVG Kit" icons (resources/icons/*.svg) --
not hand-approximated shapes. Each source file is a 64x64 tile: a rounded
gradient rect background plus a centered ~24x24 glyph at
`translate(20 20)`, with a glow filter.

Two render modes:
- `tile_icon(name, size)`: the file as-is (tile background + glyph),
  for standalone/decorative placement (welcome cards, sidebar logo).
- `icon(name, size)`: just the glyph, tile/gradient/filter stripped and
  re-centered in a bare transparent viewBox, for inline use inside
  QPushButton/QComboBox where the widget already provides its own
  background -- a second background tile there would clash.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from openhands_desktop.ui.palette import (
    COLOR_ACCENT,
    COLOR_PRIMARY,
    COLOR_PRIMARY_GLOW,
    COLOR_SUCCESS,
    COLOR_TERMINAL_ICON,
    COLOR_THINKING_ACCENT,
    COLOR_WARNING,
    TEXT_MUTED,
)

_ICONS_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"
_MENU_ICONS_DIR = Path(__file__).resolve().parent.parent / "resources" / "menu_icons"
_BADGES_DIR = Path(__file__).resolve().parent.parent / "resources" / "badges"

_GLYPH_RE = re.compile(
    r'<g transform="translate\(20 20\)"[^>]*>(?P<body>.*?)</g>', re.DOTALL
)
_STROKE_RE = re.compile(r'<g transform="translate\(20 20\)"[^>]*\bstroke="(?P<color>#[0-9A-Fa-f]{6})"')

_STROKE_COLOR_IN_BARE_RE = re.compile(r'stroke="#[0-9A-Fa-f]{6}"')

_BARE_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
    "{body}</svg>"
)


@lru_cache(maxsize=None)
def _read_source(name: str) -> str:
    path = _ICONS_DIR / f"{name}.svg"
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def _bare_svg(name: str) -> str:
    source = _read_source(name)
    glyph_match = _GLYPH_RE.search(source)
    stroke_match = _STROKE_RE.search(source)
    if not glyph_match or not stroke_match:
        raise ValueError(f"icon {name!r}: could not extract glyph from source SVG")
    return _BARE_TEMPLATE.format(color=stroke_match.group("color"), body=glyph_match.group("body"))


def _render(svg_text: str, size: int) -> QIcon:
    renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


def icon(name: str, size: int = 20, color: str | None = None) -> QIcon:
    """Bare glyph, transparent background, for inline button/combo icons.
    Each source file has its own semantic default color (e.g. stop.svg is
    red); pass `color` to override it (e.g. a neutral gray while a health
    check is still pending, before it's known to be OK or bad)."""
    svg = _bare_svg(name)
    if color is not None:
        svg = _STROKE_COLOR_IN_BARE_RE.sub(f'stroke="{color}"', svg, count=1)
    return _render(svg, size)


def tile_icon(name: str, size: int = 48) -> QIcon:
    """Full tile (rounded background + glow + glyph), for standalone use."""
    return _render(_read_source(name), size)


@lru_cache(maxsize=None)
def _read_menu_source(name: str) -> str:
    path = _MENU_ICONS_DIR / f"{name}.svg"
    return path.read_text(encoding="utf-8")


def menu_icon(name: str, size: int = 18, color: str = TEXT_MUTED) -> QIcon:
    """Settings sidebar icons (resources/menu_icons/*.svg) -- a plain flat
    24x24 glyph using stroke="currentColor", unlike the neon kit's tiled
    files above. Since Qt's SVG renderer doesn't resolve CSS `currentColor`,
    it's substituted with an explicit color here instead."""
    svg = _read_menu_source(name).replace("currentColor", color)
    return _render(svg, size)


@lru_cache(maxsize=None)
def _read_badge_source(name: str) -> str:
    path = _BADGES_DIR / f"{name}.svg"
    return path.read_text(encoding="utf-8")


def badge_icon(name: str, size: int = 20) -> QIcon:
    """resources/badges/*.svg -- real "Agentic AI Color SVG Pack" tool
    badges (2026-07-30): a 40x40 tile with its own baked-in gradient and
    glyph color, no currentColor/recoloring involved. Used in place of the
    plainer single-color tile_icon()/icon() ones wherever a matching badge
    exists (currently: browser, task, terminal, thinking)."""
    return _render(_read_badge_source(name), size)


# tool_name substring (checked in order, first match wins) -> (tile icon,
# color). tool_name comes straight from the OpenHands agent-server and isn't
# a fixed enum from this client's point of view -- new server-side tools can
# show up with names we've never seen, hence the substring match instead of
# an exact lookup, and the neutral fallback at the bottom instead of raising.
_TOOL_ICON_RULES: list[tuple[str, tuple[str, str]]] = [
    ("finish", ("shield-success", COLOR_SUCCESS)),
    ("browser", ("browser", COLOR_ACCENT)),
    ("task", ("plan-tasks", COLOR_PRIMARY)),
    ("terminal", ("terminal", COLOR_TERMINAL_ICON)),
    ("bash", ("terminal", COLOR_TERMINAL_ICON)),
    ("execute", ("terminal", COLOR_TERMINAL_ICON)),
    ("shell", ("terminal", COLOR_TERMINAL_ICON)),
    ("grep", ("search", COLOR_PRIMARY_GLOW)),
    ("glob", ("search", COLOR_PRIMARY_GLOW)),
    ("search", ("search", COLOR_PRIMARY_GLOW)),
    ("find", ("search", COLOR_PRIMARY_GLOW)),
    ("edit", ("code", COLOR_WARNING)),
    ("replace", ("code", COLOR_WARNING)),
    ("write", ("code", COLOR_WARNING)),
    ("file", ("code", COLOR_WARNING)),
]
_TOOL_ICON_FALLBACK = ("code", TEXT_MUTED)

# tile name -> matching resources/badges/*.svg, for the tool categories a
# real badge asset exists for. Everything else still falls back to the
# plain single-color tile_icon()/icon() rendering above.
_TILE_TO_BADGE = {
    "browser": "browser-badge",
    "plan-tasks": "task-badge",
    "terminal": "terminal-badge",
}


_SPARKLE_BARE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{color}">'
    '<path d="M12 2.75c.55 3.55 2.7 5.7 6.25 6.25-3.55.55-5.7 2.7-6.25 6.25'
    'C11.45 11.7 9.3 9.55 5.75 9 9.3 8.45 11.45 6.3 12 2.75Z"/>'
    '<path d="M18.25 14.25c.25 1.7 1.3 2.75 3 3-1.7.25-2.75 1.3-3 3'
    "-.25-1.7-1.3-2.75-3-3 1.7-.25 2.75-1.3 3-3Z\" opacity=\".75\"/></svg>"
)


def thinking_icon(size: int = 18, color: str = COLOR_THINKING_ACCENT) -> QIcon:
    """Thinking card icon: the real thinking-badge.svg tile when rendering
    large enough for its gradient tile to read clearly, otherwise the bare
    sparkle glyph (small inline use, e.g. beside 12px header text) where a
    39x39 tile would just look like a muddy square."""
    if size >= 16:
        return badge_icon("thinking-badge", size)
    return _render(_SPARKLE_BARE.format(color=color), size)


def _tool_icon_pair(tool_name: str | None) -> tuple[str, str]:
    name = (tool_name or "").lower()
    return next((pair for needle, pair in _TOOL_ICON_RULES if needle in name), _TOOL_ICON_FALLBACK)


def tool_call_icon(tool_name: str | None, size: int = 18) -> QIcon:
    """Type-specific icon for a tool-call row, colored per the tool's
    category (terminal/browser/task/search/file-edit) rather than by run
    status -- status is shown separately via the row's label/badge color.
    Uses the real badge tile where the asset pack has one (browser/task/
    terminal), the plain single-color glyph otherwise (finish/search/edit)."""
    tile, color = _tool_icon_pair(tool_name)
    badge = _TILE_TO_BADGE.get(tile)
    if badge is not None:
        return badge_icon(badge, size)
    return icon(tile, size, color)


def tool_accent_color(tool_name: str | None) -> str:
    """Same category color tool_call_icon() renders with, for callers (the
    tool-call card's border) that need the color without a rendered icon."""
    return _tool_icon_pair(tool_name)[1]
