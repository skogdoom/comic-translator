"""A text field whose undo is the window's undo.

The inspector's three prose fields — the source text, the translation and
the notes — keep no undo history of their own. One history covers the whole
plan, which is what makes a drag one step, a plugin rewriting every region
one step, and a document-level undo able to put text back that a widget
never saw leave. Two stacks over the same text would disagree the moment
either was used.

Switching a field's own history off is one line, and it was there. What was
not obvious is that **it does not give the key back**. ``QPlainTextEdit``
accepts the ``ShortcutOverride`` for Undo and Redo whether or not its own
undo is enabled — measured, accepted in both cases — and accepting that
event means "deliver this to me as an ordinary key press". With nothing to
undo it then does nothing, and the window's Undo action never fires. So
Cmd+Z inside a translation did not undo the typing, or anything else: it was
swallowed on the way past.

This class refuses that one claim, and only that one. Every other editing
shortcut a text field owns — cut, copy, paste, select all, the arrow keys —
it keeps.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import QPlainTextEdit, QWidget

_GIVEN_BACK = (QKeySequence.StandardKey.Undo, QKeySequence.StandardKey.Redo)
"""The shortcuts this field does not claim, because the window means them."""


class ProseEdit(QPlainTextEdit):
    """Wrapped text with no undo history of its own, and no claim on Cmd+Z."""

    escaped = Signal()
    """Escape was pressed here. The window puts focus back on the page.

    A field that takes focus when a region is clicked needs one key that
    gives it back, or the arrow keys — which nudge a region a pixel — are
    gone for as long as the caret is in a translation."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setUndoRedoEnabled(False)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Escape:
            self.escaped.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def event(self, event: QEvent) -> bool:  # Qt override
        if (
            isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.ShortcutOverride
            and any(event.matches(key) for key in _GIVEN_BACK)
        ):
            # Ignored rather than accepted: an accepted override is this
            # widget saying it wants the key, which is what stopped the
            # window's action from ever seeing it.
            event.ignore()
            return False
        return super().event(event)


__all__ = ["ProseEdit"]
