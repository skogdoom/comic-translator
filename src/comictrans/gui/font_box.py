"""A font field that only offers fonts this tool can actually render with.

The list comes from :func:`comictrans.fonts.available_families`, never from
``QFontDatabase``. Those answer different questions: Qt lists what Qt can
draw with, while ``fonts.py`` resolves a family by looking for files in the
search directories and demands a real bold face, because emphasis is bold and
a bold is never synthesised. A list built from Qt would offer families that
``apply`` then refuses, turning a two-click choice into a render failure.

It stays editable, and it never rewrites a name it does not recognise. A plan
written on another Mac can name a font this one does not have; silently
swapping that for something installed is the one thing the spec says never
happens. Instead the name stays, and is marked so you find out before you
render rather than after.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QComboBox, QWidget

from .. import fonts

PLAN_DEFAULT = "(plan default)"
"""The entry meaning "no override", which the plan file records as absent."""

_TEXT_ROLE = QPalette.ColorRole.Text


class FontBox(QComboBox):
    """An editable font picker. ``allow_default`` adds the "no override" entry.

    A region's font override may be absent, which is what ``allow_default``
    is for. The plan header's font may not: every region without an override
    falls back to it, so there is nothing for it to fall back to itself.
    """

    def __init__(self, *, allow_default: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._allow_default = allow_default
        self._loaded = False
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)

        completer = self.completer()
        if completer is not None:
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)

        self.currentTextChanged.connect(lambda _text: self._mark_resolvable())

    # -- the list --------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Read the installed families, once, the first time they are wanted.

        Measured at about 3ms per font file, so a few hundred of them is a
        pause worth a wait cursor but not worth a thread. Doing it on first
        use rather than at construction keeps opening the window instant.
        """
        if self._loaded:
            return
        self._loaded = True
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            families = fonts.available_families()
        finally:
            QGuiApplication.restoreOverrideCursor()
        self._fill(families)

    def _fill(self, families: tuple[str, ...]) -> None:
        current = self.currentText()
        self.blockSignals(True)
        try:
            self.clear()
            if self._allow_default:
                self.addItem(PLAN_DEFAULT)
            self.addItems(families)
            self.setCurrentText(current)
        finally:
            self.blockSignals(False)
        self._mark_resolvable()

    def rescan(self) -> None:
        """Look at the filesystem again, for a font installed mid-session."""
        fonts.forget_available_families()
        self._loaded = False
        self._ensure_loaded()

    def showPopup(self) -> None:  # noqa: N802 - Qt override
        self._ensure_loaded()
        super().showPopup()

    # -- the value -------------------------------------------------------

    def value(self) -> str | None:
        """The chosen family, or ``None`` for "no override"."""
        text = self.currentText().strip()
        if not text or (self._allow_default and text == PLAN_DEFAULT):
            return None
        return text

    def set_value(self, family: str | None) -> None:
        """Show a family without writing it back out as an edit."""
        self._ensure_loaded()
        self.blockSignals(True)
        try:
            self.setCurrentText(
                PLAN_DEFAULT if family is None and self._allow_default else family or ""
            )
        finally:
            self.blockSignals(False)
        self._mark_resolvable()

    def resolvable(self) -> bool:
        """Whether the name shown is one that will render.

        Membership of the scanned list rather than a fresh ``resolve_family``
        call: the list is exactly the families that were put through it, so
        the two agree, and this can be asked on every keystroke.
        """
        family = self.value()
        if family is None:
            return True
        return family in fonts.available_families()

    def _mark_resolvable(self) -> None:
        line_edit = self.lineEdit()
        if line_edit is None or not self._loaded:
            return
        palette = line_edit.palette()
        if self.resolvable():
            palette.setColor(_TEXT_ROLE, QApplication.palette().color(_TEXT_ROLE))
            self.setToolTip("")
        else:
            palette.setColor(_TEXT_ROLE, Qt.GlobalColor.darkRed)
            self.setToolTip(
                f"{self.value()!r} is not installed here, or has no bold face. "
                "apply will refuse it rather than substitute another font."
            )
        line_edit.setPalette(palette)


__all__ = ["PLAN_DEFAULT", "FontBox"]
