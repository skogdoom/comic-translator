"""A text field that does not swallow the window's shortcuts."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction, QKeyEvent, QKeySequence
from PySide6.QtWidgets import QApplication, QMainWindow, QPlainTextEdit, QWidget

from comictrans.gui.prose import ProseEdit

CLAIMED_BY_A_TEXT_FIELD = QKeySequence.StandardKey.MoveToStartOfDocument
"""A key a text field takes for itself on every platform this runs on.

Which key that *is* differs — ``Cmd+Up`` on macOS, ``Ctrl+Home`` elsewhere —
and the macOS one is this window's Previous Region. Asking for it by its
standard name is what lets one test cover both."""


def _in_a_window(*shortcuts: QKeySequence) -> tuple[QMainWindow, ProseEdit]:
    """A prose field in a window that has actions bound to ``shortcuts``."""
    window = QMainWindow()
    body = QWidget()
    window.setCentralWidget(body)
    field = ProseEdit(body)
    for sequence in shortcuts:
        action = QAction("something", window)
        action.setShortcut(sequence)
        window.addAction(action)
    return window, field


def _override(sequence: QKeySequence) -> QKeyEvent:
    """The event Qt sends a focused widget to ask whether it wants a key."""
    return QKeyEvent(
        QEvent.Type.ShortcutOverride,
        Qt.Key(sequence[0].key()),
        sequence[0].keyboardModifiers(),
    )


def test_the_field_qt_gives_you_swallows_a_shortcut_it_cannot_use(qapp: object) -> None:
    """The defect, in the widget it came from.

    Switching a text field's own undo off is the obvious way to leave undo to
    the document, and it is not enough: the field still tells Qt it wants
    Cmd+Z, and then does nothing with it. The same is true of the keys this
    window uses to walk between regions, which on macOS are a text field's
    "go to the start and the end of the document".
    """
    plain = QPlainTextEdit()
    plain.setUndoRedoEnabled(False)

    for key in (QKeySequence.StandardKey.Undo, CLAIMED_BY_A_TEXT_FIELD):
        claim = _override(QKeySequence(key))
        QApplication.sendEvent(plain, claim)
        assert claim.isAccepted(), f"{key} is wanted by a field that will not use it"


def test_a_key_the_window_has_bound_belongs_to_the_window(qapp: object) -> None:
    wanted = QKeySequence(CLAIMED_BY_A_TEXT_FIELD)
    _window, field = _in_a_window(wanted)

    claim = _override(wanted)
    QApplication.sendEvent(field, claim)

    assert not claim.isAccepted()


def test_a_key_the_window_has_not_bound_stays_the_field_s(qapp: object) -> None:
    """Cut, copy, paste and select all are nothing this window binds."""
    _window, field = _in_a_window(QKeySequence(QKeySequence.StandardKey.Undo))

    for key in (
        QKeySequence.StandardKey.Cut,
        QKeySequence.StandardKey.Copy,
        QKeySequence.StandardKey.Paste,
        QKeySequence.StandardKey.SelectAll,
    ):
        claim = _override(QKeySequence(key))
        QApplication.sendEvent(field, claim)
        assert claim.isAccepted(), key


def test_ordinary_typing_never_asks_the_window_anything(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ShortcutOverride`` arrives for every key pressed, a letter included.

    Walking the window's actions for each one would be work for nothing, so a
    key with no Control, Meta or Alt on it is answered without looking. This
    holds the guard to that by making the lookup fail if it happens.
    """
    _window, field = _in_a_window(QKeySequence(QKeySequence.StandardKey.Undo))

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the window was asked about an ordinary letter")

    monkeypatch.setattr(type(field), "window", refuse)

    claim = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(field, claim)

    assert claim.isAccepted(), "the field takes its own letters"


def test_it_keeps_no_history_of_its_own(qapp: object) -> None:
    assert not ProseEdit().isUndoRedoEnabled()


def test_tab_walks_out_of_the_field_rather_than_into_it(qapp: object) -> None:
    """A text field takes Tab as a character, which stops it walking the
    panel. These are two lines of prose, not a place to lay out a table."""
    assert ProseEdit().tabChangesFocus()
    assert not QPlainTextEdit().tabChangesFocus(), "which is not the default"


def test_the_caret_goes_to_the_end_of_what_is_now_there(qapp: object) -> None:
    """``setPlainText`` leaves it at the start, which is the wrong end of a
    translation somebody is about to add to."""
    field = ProseEdit()
    field.setPlainText("HELLO THERE")
    assert field.textCursor().position() == 0

    field.put_the_caret_at_the_end()

    assert field.textCursor().position() == len("HELLO THERE")
    assert not field.textCursor().hasSelection()


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
