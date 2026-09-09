"""A colour field: a swatch, its hex value, and where a new one comes from.

Three ways to set one, because there are three situations. Most of the time
the colour measured from the page is right and nothing here is touched. When
it is wrong — a balloon the sampler read the shadow of, a caption drawn over
art — the colour you want is usually somewhere else on the same page, so it
is picked off the page itself. When it is not on the page at all, because the
balloon is one you are about to letter for the first time, a short list of
standard values covers it and a full colour dialog is behind them.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QColorDialog, QMenu, QPushButton, QWidget

from ..model import Color

SWATCH = 14
"""Side of the colour square, in pixels."""

STANDARD_COLORS: tuple[tuple[str, Color], ...] = (
    ("White", Color(255, 255, 255)),
    ("Paper", Color(250, 250, 250)),
    ("Light grey", Color(224, 224, 224)),
    ("Mid grey", Color(128, 128, 128)),
    ("Dark grey", Color(48, 48, 48)),
    ("Ink", Color(20, 20, 20)),
    ("Black", Color(0, 0, 0)),
    ("Caption yellow", Color(255, 230, 128)),
    ("Red", Color(208, 32, 32)),
    ("Blue", Color(32, 80, 176)),
)
"""What comic lettering is actually drawn in, plus the three colours a
coloured caption tends to use. Not a palette to design with — the page is
where a colour should come from — but enough to letter a balloon that has
nothing on it to sample yet."""


def swatch_icon(color: Color) -> QIcon:
    """A filled square with a hairline border, so white is still a square."""
    pixmap = QPixmap(SWATCH, SWATCH)
    pixmap.fill(QColor(*color.as_tuple()))
    painter = QPainter(pixmap)
    painter.setPen(QColor(120, 120, 120))
    painter.drawRect(0, 0, SWATCH - 1, SWATCH - 1)
    painter.end()
    return QIcon(pixmap)


class ColorBox(QPushButton):
    """One colour, shown as a swatch and its hex, and a menu to change it."""

    picked = Signal(object)
    """A colour was chosen here: a :class:`Color`. Not emitted by
    :meth:`set_color`, which is how the field is filled in from the plan."""

    sample_requested = Signal()
    """The page itself is where this colour should come from. The canvas is
    not reachable from here, so whoever owns both is asked to arrange it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = Color(255, 255, 255)

        menu = QMenu(self)
        self._sample_action = menu.addAction("Sample from the page…")
        menu.addSeparator()
        for name, color in STANDARD_COLORS:
            action = menu.addAction(swatch_icon(color), name)
            # The colour travels in a default argument rather than through
            # the action's data, so it stays a Color instead of becoming a
            # string that has to be parsed back on the way out.
            action.triggered.connect(lambda _checked=False, chosen=color: self._announce(chosen))
        menu.addSeparator()
        self._choose_action = menu.addAction("Choose…")
        self.setMenu(menu)

        self._sample_action.triggered.connect(self.sample_requested.emit)
        self._choose_action.triggered.connect(self._on_choose)
        self.set_color(self._color)

    def value(self) -> Color:
        return self._color

    def set_color(self, color: Color) -> None:
        """Show a colour without reporting it: this is how the field is filled in."""
        self._color = color
        self.setIcon(swatch_icon(color))
        self.setText(color.to_hex())

    def _on_choose(self) -> None:
        chosen = QColorDialog.getColor(QColor(*self._color.as_tuple()), self, "Choose a colour")
        if chosen.isValid():
            self._announce(Color(chosen.red(), chosen.green(), chosen.blue()))

    def _announce(self, color: Color) -> None:
        """Take a new colour and say so, unless it is the one already shown."""
        if color == self._color:
            return
        self.set_color(color)
        self.picked.emit(color)
