"""The OCR languages field: typed codes, a menu of what is installed, names.

Which languages to hand the recogniser, in order. It stays a typed line of
codes — ``it, en`` — because it is plural and ordered, which neither a
dropdown nor a checklist is, and because a code the menu does not offer,
``pt-BR`` or Tesseract's own ``jpn_vert``, must still go in. What it gains is
everything around the typing:

- **a menu of what the recogniser can read**, by name, which adds its pick to
  the end — asked of the recogniser the field beside it names, since Vision
  and Tesseract read different things, and ``automatic`` is whichever of the
  two this machine would use;
- **the codes read back as names**, in order, underneath — and a language
  the recogniser does not have is said to be missing there, the way a font
  this machine lacks is marked, rather than dropped or corrected.

The codes are language tags whichever recogniser runs, so one list serves
both: Tesseract gets them through its own table, ``sv`` as ``swe``.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import ocr
from .inspector import ElidedLabel
from .language_box import language_label, language_name

SAME_AS_SOURCE = QCoreApplication.translate("OcrLanguagesField", "same as the source language")
"""What an empty field means, and says, as its placeholder."""

_ENGINE_NAMES = {"vision": "Apple Vision", "tesseract": "Tesseract"}
"""Product names, not translated."""


def split_languages(text: str) -> tuple[str, ...]:
    """``"it, en"`` -> ``("it", "en")``: the typed list, empty parts dropped."""
    return tuple(part.strip() for part in text.split(",") if part.strip())


class OcrLanguagesField(QWidget):
    """A typed list of language codes, with a menu to add from and names under it."""

    changed = Signal()
    """The list was edited, by typing or by the menu. Read :meth:`text`."""

    def __init__(self, text: str = "", engine: str = "auto", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._engine = engine
        # Per recogniser, asked once: which one "auto" means here, and what
        # it has installed. Asking Tesseract runs it — 12ms warm, 650ms the
        # first time on a cold disk, measured — which is fine once and not
        # once a keystroke.
        self._resolved: dict[str, str] = {}
        self._installed: dict[str, tuple[str, ...]] = {}

        self._line = QLineEdit(text)
        self._line.setPlaceholderText(SAME_AS_SOURCE)
        self._line.setToolTip(
            self.tr(
                "Languages to hand the recogniser, in order, separated by commas. "
                "Left empty this is the source language. + adds one the "
                "recogniser has installed; a code it does not offer, such as "
                "pt-BR, can still be typed."
            )
        )

        self._menu = QMenu(self)
        self._menu.aboutToShow.connect(self._fill_menu)
        self._add = QToolButton()
        self._add.setText("+")
        self._add.setToolTip(self.tr("Add a language the recogniser can read"))
        self._add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._add.setMenu(self._menu)

        self._names = ElidedLabel()

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._line, 1)
        row.addWidget(self._add)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(row)
        layout.addWidget(self._names)

        self._line.textChanged.connect(self._on_text_changed)
        self._read_back()

    # -- the value -------------------------------------------------------

    def text(self) -> str:
        return self._line.text().strip()

    def languages(self) -> tuple[str, ...]:
        return split_languages(self._line.text())

    def set_text(self, text: str) -> None:
        """Show ``text`` without it counting as an edit."""
        with QSignalBlocker(self._line):
            self._line.setText(text)
        self._read_back()

    def line_edit(self) -> QLineEdit:
        return self._line

    # -- the recogniser --------------------------------------------------

    def set_engine(self, engine: str) -> None:
        """The recogniser field beside this one changed: follow it."""
        self._engine = engine
        self._read_back()

    def _recogniser(self) -> str:
        if self._engine not in self._resolved:
            self._resolved[self._engine] = ocr.resolved_engine(self._engine)
        return self._resolved[self._engine]

    def installed(self) -> tuple[str, ...]:
        """What the recogniser can read here, asked once and kept."""
        recogniser = self._recogniser()
        if recogniser not in self._installed:
            QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self._installed[recogniser] = ocr.installed_languages(recogniser)
            finally:
                QGuiApplication.restoreOverrideCursor()
        return self._installed[recogniser]

    def _has(self, tag: str) -> bool:
        return ocr.reads(self._recogniser(), tag, self.installed())

    # -- the menu --------------------------------------------------------

    def _fill_menu(self) -> None:
        """What the recogniser has, by name, with what is listed already ticked.

        Filled as it opens rather than once, so a recogniser changed in the
        field beside it is the one asked.
        """
        self._menu.clear()
        recogniser = self._recogniser()
        if not recogniser:
            self._say(self.tr("no recogniser here to ask"))
            return
        installed = self.installed()
        if not installed:
            self._say(self.tr("nothing installed for {0}").format(_ENGINE_NAMES[recogniser]))
            return
        listed = self.languages()
        for tag in sorted(installed, key=lambda code: language_label(code).casefold()):
            action = self._menu.addAction(language_label(tag))
            present = any(ocr.reads(recogniser, typed, (tag,)) for typed in listed)
            action.setCheckable(True)
            action.setChecked(present)
            action.setEnabled(not present)
            action.triggered.connect(lambda _checked=False, code=tag: self._append(code))

    def _say(self, text: str) -> None:
        action = self._menu.addAction(text)
        action.setEnabled(False)

    def _append(self, tag: str) -> None:
        self._line.setText(", ".join((*self.languages(), tag)))

    # -- reading it back -------------------------------------------------

    def _on_text_changed(self, _text: str) -> None:
        self._read_back()
        self.changed.emit()

    def _read_back(self) -> None:
        """The codes as names, in order; what the recogniser lacks, said so.

        Nothing is checked when there is no recogniser here to ask: saying
        every language is missing would be saying something about the
        languages that is only true of the machine.
        """
        recogniser = self._recogniser()
        names = []
        for tag in self.languages():
            name = language_name(tag)
            if recogniser and not self._has(tag):
                name = self.tr("{0}, which {1} does not have").format(
                    name, _ENGINE_NAMES[recogniser]
                )
            names.append(name)
        self._names.set_parts(self.tr(", then ").join(names))


__all__ = ["SAME_AS_SOURCE", "OcrLanguagesField", "split_languages"]
