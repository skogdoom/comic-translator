"""The line under the canvas that says what a click does.

A mode's gestures used to be announced once, in a status bar message that the
next message replaced — readable for as long as it took anything else to
happen. They belong somewhere that stays put for as long as the mode does.

Wide enough to read and never wide enough to matter: the label refuses to
report a width of its own, so a long line is elided rather than allowed to set
a floor under the window. What is elided is still readable in the tooltip, the
same bargain ``font_box`` strikes with a long family name and ``inspector``'s
``ElidedLabel`` with a long region id — that last one was the same defect a
layer in, and was recorded in ``known-bugs.md`` until it was fixed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics, QResizeEvent
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from .note import quieten

MARGIN = 6
"""Air either side, so the text does not sit against the window's edge."""


class HintLine(QLabel):
    """One line of quiet text, elided to whatever width it is given."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hint = ""
        self.setContentsMargins(MARGIN, 2, MARGIN, 2)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        quieten(self)

    def hint(self) -> str:
        """The whole line, elided or not."""
        return self._hint

    def set_hint(self, hint: str) -> None:
        self._hint = hint
        self.setToolTip(hint)
        self._elide()

    def _elide(self) -> None:
        room = max(0, self.width() - 2 * MARGIN)
        shown = QFontMetrics(self.font()).elidedText(self._hint, Qt.TextElideMode.ElideRight, room)
        # Guarded because setting the text is what triggers the next layout
        # pass, which is what calls this again.
        if shown != self.text():
            self.setText(shown)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._elide()
