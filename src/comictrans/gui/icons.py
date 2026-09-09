"""The toolbar's pictures: drawn for this tool, tinted to whatever theme.

**Why files rather than the platform's icon theme.** ``QIcon.fromTheme``
returns nothing on macOS, and half of these commands — reshape a region,
merge two, walk to the next flagged one — have no standard pixmap in any Qt
style to return. There is no route that avoids shipping images, so they are
shipped, and shipping them is the same packaging the ``.icns`` icon and
milestone 4.10's build config need anyway.

**Why drawn here rather than taken from a set.** An icon set carries a
licence, and a licence has to be recorded in ``LICENSE`` and in the About
dialog and honoured by anyone who redistributes this. Fifteen line drawings
are a smaller thing to make than a licence is to carry, and these can say
what this tool actually does: the merge icon is two outlines sharing area,
which is the condition ``merge_regions`` refuses one without.

**Why they are tinted rather than shipped in two colours.** A black line on
dark chrome is invisible, and a set per theme is a set to keep in step. The
drawings are shape only; the colour comes from the palette at load time, so
they follow a light window, a dark one, and a disabled button without
anything being drawn twice.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPalette, QPixmap

log = logging.getLogger(__name__)

ICON_DIR = Path(__file__).parent / "resources" / "icons"
SUFFIX = ".svg"

SIZES = (16, 20, 24, 32, 40, 48, 64)
"""Sizes to bake into each icon.

The drawings are vector and Qt would render any size on demand, but tinting
needs a real pixmap to paint into, so a set is baked instead of writing a
``QIconEngine``. These cover a toolbar at 16-32px on a plain display and the
same doubled on a Retina one, which is every size this window asks for.
"""

_cache: dict[tuple[str, int], QIcon] = {}


def available() -> frozenset[str]:
    """Every icon name that ships, without the suffix."""
    if not ICON_DIR.is_dir():
        return frozenset()
    return frozenset(path.stem for path in ICON_DIR.glob(f"*{SUFFIX}"))


def _tinted(source: QPixmap, color: QColor) -> QPixmap:
    """The drawing's shape, in one colour. Alpha is what carries the line."""
    out = QImage(source.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.drawPixmap(0, 0, source)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(out.rect(), color)
    painter.end()
    return QPixmap.fromImage(out)


def icon(name: str, color: QColor | None = None) -> QIcon:
    """One icon, tinted to ``color`` or to the palette's button text.

    An icon that is missing costs the picture and nothing else: the action
    keeps its text, its shortcut and its tooltip, and the window opens. A
    toolbar that refused to build over a missing file would be a worse
    failure than a toolbar with a gap in it.
    """
    tint = color if color is not None else QPalette().color(QPalette.ColorRole.ButtonText)
    key = (name, tint.rgba())
    if key in _cache:
        return _cache[key]

    path = ICON_DIR / f"{name}{SUFFIX}"
    built = QIcon()
    if not path.is_file():
        log.warning("no icon named %r in %s", name, ICON_DIR)
    else:
        source = QIcon(str(path))
        for size in SIZES:
            built.addPixmap(_tinted(source.pixmap(size, size), tint))
    _cache[key] = built
    return built


def forget() -> None:
    """Drop the cache, so the next call re-tints. For a palette change."""
    _cache.clear()


__all__ = ["ICON_DIR", "SIZES", "available", "forget", "icon"]
