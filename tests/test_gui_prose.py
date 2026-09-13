"""A text field whose undo is the window's undo."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import QApplication, QPlainTextEdit

from comictrans.gui.prose import ProseEdit


def _override(key: QKeySequence.StandardKey) -> QKeyEvent:
    """The event Qt sends a focused widget to ask whether it wants a shortcut."""
    sequence = QKeySequence(key)
    return QKeyEvent(
        QEvent.Type.ShortcutOverride,
        Qt.Key(sequence[0].key()),
        sequence[0].keyboardModifiers(),
    )


def test_the_field_qt_gives_you_swallows_undo_even_with_undo_switched_off(
    qapp: object,
) -> None:
    """The defect, in the widget it came from.

    Switching a text field's own history off is the obvious way to leave undo
    to the document, and it is not enough: the field still tells Qt it wants
    Cmd+Z, and then does nothing with it. The window's action never fires.
    """
    plain = QPlainTextEdit()
    plain.setUndoRedoEnabled(False)

    claim = _override(QKeySequence.StandardKey.Undo)
    QApplication.sendEvent(plain, claim)

    assert claim.isAccepted(), "it wants the key it has no use for"


def test_a_prose_field_leaves_undo_and_redo_to_the_window(qapp: object) -> None:
    for key in (QKeySequence.StandardKey.Undo, QKeySequence.StandardKey.Redo):
        claim = _override(key)
        QApplication.sendEvent(ProseEdit(), claim)
        assert not claim.isAccepted(), key


@pytest.mark.parametrize(
    "key",
    [
        QKeySequence.StandardKey.Cut,
        QKeySequence.StandardKey.Copy,
        QKeySequence.StandardKey.Paste,
        QKeySequence.StandardKey.SelectAll,
    ],
)
def test_every_other_editing_shortcut_is_still_the_field_s(
    qapp: object, key: QKeySequence.StandardKey
) -> None:
    """Two keys are given back, not the keyboard."""
    claim = _override(key)
    QApplication.sendEvent(ProseEdit(), claim)

    assert claim.isAccepted()


def test_it_keeps_no_history_of_its_own(qapp: object) -> None:
    assert not ProseEdit().isUndoRedoEnabled()


def test_escape_asks_for_focus_back(qapp: object) -> None:
    field = ProseEdit()
    field.setPlainText("HELLO")
    asked: list[int] = []
    field.escaped.connect(lambda: asked.append(1))

    field.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    )

    assert asked == [1]
    assert field.toPlainText() == "HELLO", "and the text is not what it took"


def test_an_ordinary_key_is_typed(qapp: object) -> None:
    field = ProseEdit()

    field.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, "A")
    )

    assert field.toPlainText() == "A"
