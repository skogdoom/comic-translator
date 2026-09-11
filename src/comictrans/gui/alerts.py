"""Alerts, shaped the way macOS shows one.

An alert has two strings on this platform, not three: a short message that
says what happened, and an informative line under it that says the rest.
There is no title bar to put anything in, and ``QMessageBox`` knows it —
Qt overrides ``setWindowTitle`` on the class for one reason, to make it a
no-op on macOS. Every static helper (``QMessageBox.critical`` and the rest)
takes a title as its second argument, so an alert written that way loses
whatever that argument said, and what is left is the bare detail with
nothing framing it: an exception's own words, and no sentence saying which
of the window's actions produced them.

So the window does not call the static helpers. It calls :func:`report` and
:func:`ask`, which put the sentence in the message and the detail under it,
and the two strings survive on every platform.

Standard buttons rather than buttons of our own, for the same reason.
``StandardButton.Discard`` is titled "Don't Save" by Qt's Cocoa theme and
"Discard" everywhere else — measured, in Qt's own translation catalogue,
where that string carries the context ``QCocoaTheme`` — and the order the
row is laid out in is the platform's. A hand-made pair of buttons would be
neither.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

Button = QMessageBox.StandardButton
"""Shorthand, since a caller naming three of these has a long line already."""

Icon = QMessageBox.Icon


def _box(parent: QWidget, icon: Icon, message: str, detail: str) -> QMessageBox:
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setText(message)
    if detail:
        box.setInformativeText(detail)
    return box


def report(parent: QWidget, message: str, detail: str = "", icon: Icon = Icon.Critical) -> None:
    """Say that something failed. One button, and nothing to decide."""
    box = _box(parent, icon, message, detail)
    box.setStandardButtons(Button.Ok)
    box.exec()


def ask(
    parent: QWidget,
    message: str,
    detail: str,
    buttons: Button,
    default: Button,
    icon: Icon = Icon.Warning,
) -> Button:
    """Put a question, and return the button pressed.

    ``Warning`` rather than ``Question``: every question this window asks is
    about work that is on the line, which is what the caution icon is for.
    """
    box = _box(parent, icon, message, detail)
    box.setStandardButtons(buttons)
    box.setDefaultButton(default)
    return Button(box.exec())


__all__ = ["Button", "Icon", "ask", "report"]
