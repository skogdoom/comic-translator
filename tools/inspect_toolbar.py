#!/usr/bin/env python3
"""Ask the window where its toolbar actually puts things, and draw the answer.

    uv run --extra gui tools/inspect_toolbar.py

A diagnostic, not a fix. The toolbar's icons sit high and off to one side on
macOS, and every explanation for that predicts the same screenshot: the style
reserving room under an icon for a label it is not drawing, the unified title
bar handing the toolbar a taller rectangle than its content fills, or the
drag handle taking the left. They predict *different numbers*, so this prints
them — the toolbar's rectangle, each button's rectangle inside it, and where
the drawing's ink lands inside each button.

Read the last column first. An ink offset near zero means each button centres
its own drawing and the row as a whole is what is misplaced; an offset with a
consistent sign means the style is putting the icon somewhere other than the
middle of the button, and no amount of moving the row will help.

It also saves an annotated picture of the top of the window, once as the
application builds it and once with the unified title bar turned off, so the
two can be looked at rather than argued about.

Nothing here changes the application: it opens two windows, measures them,
and closes them.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from comictrans.gui.main_window import MainWindow

OUT = Path("build/toolbar")

DIFFERENT = 60
"""How far from the background a pixel has to be to count as the drawing.

A button's background is flat and its drawing is tinted to contrast with it,
so anything near the corner pixel's colour is chrome and anything far from it
is ink. Generous, because anti-aliasing puts every edge somewhere between.
"""


def ink_box(image: QImage) -> tuple[int, int, int, int] | None:
    """Where a button's drawing sits inside it, in that image's pixels."""
    background = image.pixelColor(0, 0)
    left, top, right, bottom = image.width(), image.height(), -1, -1
    for y in range(image.height()):
        for x in range(image.width()):
            colour = image.pixelColor(x, y)
            distance = (
                abs(colour.red() - background.red())
                + abs(colour.green() - background.green())
                + abs(colour.blue() - background.blue())
            )
            if distance > DIFFERENT:
                left, right = min(left, x), max(right, x)
                top, bottom = min(top, y), max(bottom, y)
    return None if right < 0 else (left, top, right, bottom)


def measure(window: MainWindow, title: str, picture: Path) -> None:
    """Print one window's toolbar, and save it with its rectangles drawn on."""
    toolbar = window._toolbar
    print(f"\n=== {title} ===")
    print(f"  window       {window.geometry()}  ratio {window.devicePixelRatio()}")
    print(f"  toolbar      {toolbar.geometry()}  height {toolbar.height()}")
    print(
        f"  margins      widget {toolbar.contentsMargins().top()}/"
        f"{toolbar.contentsMargins().bottom()} top/bottom, "
        f"{toolbar.contentsMargins().left()}/{toolbar.contentsMargins().right()} left/right"
    )
    inside = toolbar.layout()
    if inside is not None:
        layout = inside.contentsMargins()
        print(
            f"               layout {layout.top()}/{layout.bottom()} top/bottom, "
            f"{layout.left()}/{layout.right()} left/right"
        )
    print(f"  iconSize     {toolbar.iconSize().width()}  movable {toolbar.isMovable()}")

    for action in toolbar.actions():
        button = toolbar.widgetForAction(action)
        if button is None or action.isSeparator():
            continue
        rect = button.geometry()
        image = button.grab().toImage()
        ratio = image.width() / max(rect.width(), 1)
        box = ink_box(image)
        where = "nothing drawn"
        if box is not None:
            left, top, right, bottom = (value / ratio for value in box)
            where = (
                f"ink {right - left + 1:4.1f}x{bottom - top + 1:4.1f}  "
                f"off the button's middle by "
                f"({(left + right + 1) / 2 - rect.width() / 2:+5.1f}, "
                f"{(top + bottom + 1) / 2 - rect.height() / 2:+5.1f})"
            )
        name = action.text().replace("&", "")
        print(
            f"  {name:22} x={rect.x():4} y={rect.y():3} {rect.width():3}x{rect.height():3}  {where}"
        )

    below = window.height() - toolbar.geometry().bottom() - 40
    strip = window.grab(window.rect().adjusted(0, 0, 0, -max(below, 0))).toImage()
    painter = QPainter(strip)
    painter.setPen(QColor(220, 30, 30))
    painter.drawRect(toolbar.geometry())
    painter.setPen(QColor(30, 120, 220))
    for action in toolbar.actions():
        button = toolbar.widgetForAction(action)
        if button is not None and not action.isSeparator():
            painter.drawRect(button.geometry().translated(toolbar.geometry().topLeft()))
    painter.end()
    picture.parent.mkdir(parents=True, exist_ok=True)
    strip.save(str(picture))
    print(f"  picture      {picture}  (red: the toolbar, blue: each button)")


def main() -> int:
    application = QApplication(sys.argv)
    print(f"style {application.style().objectName()}")
    for unified, name in ((True, "as the application builds it"), (False, "unified title bar off")):
        window = MainWindow()
        window.setUnifiedTitleAndToolBarOnMac(unified)
        window.resize(1200, 800)
        window.show()
        for _ in range(20):
            application.processEvents()
        measure(window, name, OUT / f"{'unified' if unified else 'plain'}.png")
        window.hide()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
