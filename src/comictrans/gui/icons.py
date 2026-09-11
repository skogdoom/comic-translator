"""The toolbar's pictures: drawn for this tool, tinted to whatever theme.

**Why files rather than the platform's icon theme.** ``QIcon.fromTheme``
returns nothing on macOS, and half of these commands — reshape a region,
merge two, walk to the next flagged one — have no standard pixmap in any Qt
style to return. There is no route that avoids shipping images, so they are
shipped, and shipping them is the same packaging the ``.icns`` icon and the
application bundle need anyway.

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

**They are drawn to one grid, and that is what makes a row of them look
like a set.** 24 units square, stroke 2, and the ink — the drawing plus its
stroke — fits a 20-unit box centred on the canvas. A drawing that ignores
that is not wrong on its own and is wrong beside the others: measured before
the rule existed, this set ranged from 14 units across (the region arrows)
to 22 (the eye), and sat as much as 2 units off centre — with the up arrow
high and the down arrow low, side by side on the bar. That reads as icons of
different sizes that do not line up, which is exactly what it was.

:data:`GRID`, :data:`INK` and the tolerance a test holds them to are below.
The rule is about the *ink*, not the coordinates: stroke width counts, since
what a person sees is where the paint ends.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPalette, QPixmap

log = logging.getLogger(__name__)

ICON_DIR = Path(__file__).parent / "resources" / "icons"
SUFFIX = ".svg"

APPICON = Path(__file__).parent / "resources" / "appicon" / "dog-book-icon-512.svg"
"""The application's own icon, which is a different kind of thing.

Full colour and drawn to be looked at, where the toolbar's are line art
drawn to be tinted — so this one is never passed through :func:`_tinted`,
and it does not follow the palette. It lives beside its 1024 master and the
script that derives it; edit the master, not this.
"""

GRID = 24
"""The side of the square every drawing is laid out on."""

INK = 20
"""How much of that square the drawing, stroke included, fills and centres on.

Its larger dimension, not both: an arrow is narrow and an eye is wide, and
forcing either to a square would distort it. What has to match across the set
is how much room each one takes and where its middle is.
"""

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


def app_icon() -> QIcon:
    """The dog on its book, for the window and the About box.

    Rendered on demand rather than baked into a pixmap set: nothing tints
    it, so Qt's SVG engine can answer any size asked for, including the
    large ones an application icon gets asked for and the toolbar never is.

    Missing, it costs the picture and nothing else — the same bargain
    :func:`icon` strikes, and it matters more here: this is called while the
    application is starting up, and a window that refused to open over a
    decoration would be the worse failure by far.
    """
    if not APPICON.is_file():
        log.warning("no application icon at %s", APPICON)
        return QIcon()
    return QIcon(str(APPICON))


def forget() -> None:
    """Drop the cache, so the next call re-tints. For a palette change."""
    _cache.clear()


__all__ = ["APPICON", "ICON_DIR", "SIZES", "app_icon", "available", "forget", "icon"]
