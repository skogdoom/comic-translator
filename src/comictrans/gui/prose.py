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

Undo turned out not to be the only one. **Cmd+Up and Cmd+Down are Previous
and Next Region in this window, and on macOS they are also "go to the start
and the end of the document" in a text field** — which claims them the same
way, so walking the regions stopped working as soon as the caret was in a
translation. Listing the offenders one at a time would mean finding each one
by being bitten by it, and the list is platform-specific: the same two keys
are `Ctrl+Home` and `Ctrl+End` everywhere but macOS.

So the rule is the general one. **A key the window has bound to an action
belongs to the window**; everything else belongs to the field. Cut, copy,
paste and select all stay here because nothing in this window binds them,
and a shortcut added later cannot be quietly swallowed by a text box.

Tab is the exception in the other direction, and not a shortcut at all: a
text field takes it as a character, which stops it walking the panel. These
fields are two lines of prose, not a place to lay out a table, so they let
it go by.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QAction, QKeyEvent, QKeySequence, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QWidget

_CARRIES_A_SHORTCUT = (
    Qt.KeyboardModifier.ControlModifier
    | Qt.KeyboardModifier.MetaModifier
    | Qt.KeyboardModifier.AltModifier
)
"""What a key has to hold before it is worth asking the window about it.

``ShortcutOverride`` arrives for ordinary typing too, and walking the
window's actions for every letter of every translation is work for nothing:
a shortcut in this application always carries one of these. Shift alone does
not, which is why it is not here."""


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
        # Tab walks the panel instead of being typed into it — see the
        # module docstring. Without this, Tab stops dead at the first of
        # these three fields.
        self.setTabChangesFocus(True)

    def put_the_caret_at_the_end(self) -> None:
        """Where a caret belongs after the text under it was replaced.

        ``setPlainText`` leaves it at the start, which is the wrong end of a
        translation somebody is about to add to — and it is what they were
        looking at after every Next Region, because the panel repopulates
        under the caret without the focus ever moving.
        """
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)

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
            and self._the_window_means_it(event)
        ):
            # Ignored rather than accepted: an accepted override is this
            # widget saying it wants the key, which is what stopped the
            # window's action from ever seeing it.
            event.ignore()
            return False
        return super().event(event)

    def _the_window_means_it(self, event: QKeyEvent) -> bool:
        """Whether this key is one the window has an action for.

        Asked of the window rather than answered from a list here, so that
        the answer is right on a platform this was not written on and stays
        right when a shortcut is added. Every action in this window is built
        with the window as its parent, which is what makes them findable.
        """
        if not event.modifiers() & _CARRIES_A_SHORTCUT:
            return False
        pressed = QKeySequence(event.keyCombination())
        return any(pressed in action.shortcuts() for action in self.window().findChildren(QAction))


__all__ = ["ProseEdit"]
