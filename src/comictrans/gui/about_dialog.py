"""The About dialog: what this is, whose it is, and what it is built on.

Every fact on it comes from :mod:`comictrans.gui.about`, which reads the
installed distribution rather than repeating it. The two things that module
cannot answer are the Python and Qt versions, which are properties of the
interpreter and the binding running right now and are read here.

There is no update check, here or anywhere. The pipeline makes no network
calls at all — that is what makes it safe to run against someone else's
scans — and an About dialog is where that idea usually turns up first.
"""

from __future__ import annotations

import platform

import PySide6
from PySide6.QtCore import Qt, qVersion
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import about, icons

_TEXT_HEIGHT = 160
_ICON_SIZE = 96
"""How big the dog is here.

Large enough to be the drawing it is rather than a decoration, and small
enough that the version under it is still the first thing read.
"""


def library_lines() -> tuple[str, ...]:
    """One ``name  version`` line per installed dependency, column-aligned."""
    installed = about.libraries()
    if not installed:
        return ("(no distribution metadata: running from an uninstalled source tree)",)
    width = max(len(library.name) for library in installed)
    return tuple(f"{library.name.ljust(width)}  {library.version}" for library in installed)


def _read_only_text(lines: tuple[str, ...]) -> QPlainTextEdit:
    box = QPlainTextEdit("\n".join(lines))
    box.setReadOnly(True)
    box.setMinimumHeight(_TEXT_HEIGHT)
    # The lines are padded into columns with spaces, which only lines up in a
    # font whose spaces are as wide as its digits.
    box.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
    return box


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {about.NAME}")

        heading = QLabel(f"{about.NAME} {about.package_version()}")
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)

        blurb = QLabel(about.summary())
        blurb.setWordWrap(True)

        # Centred above the words rather than beside them. The icon is a
        # decoration, and a missing one leaves no gap where a picture was
        # going to be: the row is left out rather than kept as an empty
        # label holding everything below it down.
        picture = icons.app_icon().pixmap(_ICON_SIZE, _ICON_SIZE)
        stamp: QLabel | None = None
        if not picture.isNull():
            stamp = QLabel()
            stamp.setPixmap(picture)

        facts = QFormLayout()
        facts.addRow("author", QLabel(about.author()))
        facts.addRow("licence", QLabel(f"{about.licence()} — full text in LICENSE"))
        facts.addRow("python", QLabel(platform.python_version()))
        # The binding and the Qt library it wraps are versioned separately and
        # do drift apart, so neither one stands in for the other.
        facts.addRow("Qt", QLabel(f"{qVersion()} (PySide6 {PySide6.__version__})"))

        self._libraries = _read_only_text(library_lines())

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        if stamp is not None:
            layout.addWidget(stamp, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(heading)
        layout.addWidget(blurb)
        layout.addLayout(facts)
        layout.addWidget(QLabel("libraries"))
        layout.addWidget(self._libraries)
        layout.addWidget(buttons)
        self.setMinimumWidth(460)


__all__ = ["AboutDialog", "library_lines"]
