"""A line or two of quiet help under a field, as tall as it has to be.

``QLabel`` with ``setWordWrap(True)`` does not report a height for the width
it is about to be given. Its ``sizeHint`` picks a width of its own — Qt looks
for one that makes the text a pleasant shape — and answers for that. Put one
in a ``QFormLayout`` field, where the column width is settled by the widest
field in the whole form rather than by this label, and the two disagree: the
label is laid out narrower than it guessed, needs another line to hold the
text at that width, and is allocated the height it asked for rather than the
height it turned out to need. The last line is cut off, and on a form whose
rows are already tight the row below is drawn over what is left.

It shows up on macOS and not here, which is what made it expensive: the
default ``FieldGrowthPolicy`` there is ``FieldsStayAtSizeHint`` where
everywhere else it is ``AllNonFixedFieldsGrow``, and the system font is wider
— so the field column is narrower and the text is longer at the same time.
Three dialogs have now met it, so this is one widget rather than a third fix.

**How it holds.** The label asks itself, every time it is given a width, how
tall the text is at *that* width, and makes that its minimum height. A
minimum is not a request a layout can trim: ``QLayout`` raises the window's
own minimum to cover it, so the row cannot be squeezed and the window grows
instead. The guard against a layout loop is that the minimum is only set when
it changes.

**The measurement is taken off the font, not off** ``heightForWidth``, which
would have been the obvious thing to ask. ``QLabel.heightForWidth`` never
answers with less than the widget's own ``minimumHeight``, so using it here
would have latched: the first narrow width sets a minimum, and from then on
every width answers with that minimum and the note can only ever grow.
Measured — a note needing 112px at 160 wide and 28 at 600 answered 112 at
both once the minimum was set.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPalette, QResizeEvent
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget


def quieten(widget: QWidget) -> None:
    """Draw this widget's text in the placeholder colour: help, not a field.

    Four widgets want it — this one, the hint line under the canvas, the run
    panel's tallies and the extract dialog's page count — and all four had
    their own copy of these four lines. It is one recipe and it belongs in
    one place, so that "quiet" cannot come to mean two different greys.

    A palette rather than a stylesheet, so it follows a light window and a
    dark one without a second set of colours: the rule everywhere in this
    package.
    """
    palette = widget.palette()
    palette.setColor(
        QPalette.ColorRole.WindowText,
        palette.color(QPalette.ColorRole.PlaceholderText),
    )
    widget.setPalette(palette)


class Note(QLabel):
    """Wrapped help text that is never allocated less height than it needs."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        # Minimum rather than Preferred vertically: the height is decided by
        # the width, so a vertical size hint has nothing to say that the
        # minimum set in ``refit`` does not say better.
        #
        # Deliberately without ``setHeightForWidth`` on the policy, which is
        # the answer everyone reaches for first. It buys nothing here — the
        # minimum is what forces the row, and Qt's own ``heightForWidth`` is
        # clamped to that minimum anyway, so a layout asking it would be told
        # what ``refit`` already decided. Removing it changes no test.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.quieten()

    def quieten(self) -> None:
        """The placeholder colour: help beside a field, not a field."""
        quieten(self)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt override
        super().setText(text)
        self.refit()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self.refit()

    def needed_height(self, width: int) -> int:
        """How tall this text is when it is ``width`` wide.

        Off the font metrics rather than :meth:`heightForWidth`, which is
        clamped to the minimum this method is used to set — see the module
        docstring. Nothing here reads any state the answer is meant to
        decide, so it is safe to ask during a layout pass.
        """
        margins = self.contentsMargins()
        room = width - margins.left() - margins.right()
        if not self.text() or room <= 0:
            return 0
        drawn = self.fontMetrics().boundingRect(
            QRect(0, 0, room, 0), Qt.TextFlag.TextWordWrap, self.text()
        )
        return drawn.height() + margins.top() + margins.bottom()

    def refit(self) -> None:
        """Make the height the width asks for the least this may be given."""
        if self.width() <= 0:
            return
        needed = self.needed_height(self.width())
        # Guarded because setting a minimum is what starts the layout pass
        # that calls this again.
        if needed != self.minimumHeight():
            self.setMinimumHeight(needed)


__all__ = ["Note", "quieten"]
