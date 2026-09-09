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
from PySide6.QtGui import QFontMetrics, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QComboBox, QWidget

from .. import fonts

PLAN_DEFAULT = "(plan default)"
"""The entry meaning "no override", which the plan file records as absent.

The wording is the region inspector's, where the fallback really is the
plan's header. Somewhere else the same absence means something else — in
preferences it means "whatever extract finds" — so the text is a parameter
and this is only its default.
"""

_TEXT_ROLE = QPalette.ColorRole.Text

_MIN_CHARS = 16
_MAX_CHARS = 24
"""How wide the field may get, in characters, to show the name it holds.

A floor because a field too narrow to read its own value is no use, and a
ceiling because this width becomes the panel's minimum width: a font called
something enormous must not be able to force the Region dock wide, which is
the shape of the defect recorded as 4 in ``known-bugs.md``.
"""


class FontBox(QComboBox):
    """An editable font picker. ``allow_default`` adds the "no override" entry.

    A region's font override may be absent, which is what ``allow_default``
    is for; ``default_text`` is what that absence is called, since it means
    different things in different places. The plan header's font may not be
    absent: every region without an override falls back to it, so there is
    nothing for it to fall back to itself.
    """

    def __init__(
        self,
        *,
        allow_default: bool,
        default_text: str = PLAN_DEFAULT,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._allow_default = allow_default
        self._default_text = default_text
        self._loaded = False
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        # Measured before anything is in it, so this is the frame and the
        # arrow with no text to speak of: what the width of a name has to be
        # added to. A combo sizes itself from its item list, not from the
        # text typed into it, which on a form that leaves fields at their
        # size hint left six pixels for a thirteen-character font name.
        self._chrome = self.sizeHint().width()

        completer = self.completer()
        if completer is not None:
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)

        self.currentTextChanged.connect(lambda _text: self._on_text_changed())
        self._fit_width()

    def _on_text_changed(self) -> None:
        self._mark_resolvable()
        self._fit_width()

    def _room_for_text(self) -> int:
        """Pixels the name gets: what it needs, floored and capped."""
        metrics = QFontMetrics(self.font())
        shown = [self.currentText()]
        if self._allow_default:
            shown.append(self._default_text)
        widest = max(metrics.horizontalAdvance(text) for text in shown)
        average = metrics.averageCharWidth()
        return min(max(widest, average * _MIN_CHARS), average * _MAX_CHARS)

    def _fit_width(self) -> None:
        """Be wide enough to read the name held, within reason."""
        self.setMinimumWidth(self._chrome + self._room_for_text())

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
                self.addItem(self._default_text)
            self.addItems(families)
            self.setCurrentText(current)
        finally:
            self.blockSignals(False)
        self._mark_resolvable()
        self._fit_width()

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
        if not text or (self._allow_default and text == self._default_text):
            return None
        return text

    def set_value(self, family: str | None) -> None:
        """Show a family without writing it back out as an edit."""
        self._ensure_loaded()
        self.blockSignals(True)
        try:
            self.setCurrentText(
                self._default_text if family is None and self._allow_default else family or ""
            )
        finally:
            self.blockSignals(False)
        self._mark_resolvable()
        self._fit_width()

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
            self.setToolTip(self._name_if_clipped())
        else:
            palette.setColor(_TEXT_ROLE, Qt.GlobalColor.darkRed)
            self.setToolTip(
                f"{self.value()!r} is not installed here, or has no bold face. "
                "apply will refuse it rather than substitute another font."
            )
        line_edit.setPalette(palette)

    def _name_if_clipped(self) -> str:
        """The whole name, when it is longer than the field is allowed to be.

        The width is capped so that an enormous family name cannot push the
        panel wide, which means an enormous one can be cut off. Somewhere it
        has to still be readable. Judged against the room the cap allows
        rather than the widget's current width, so the answer does not depend
        on whether a layout pass has happened yet.
        """
        text = self.currentText()
        needed = QFontMetrics(self.font()).horizontalAdvance(text)
        return text if needed > self._room_for_text() else ""


__all__ = ["PLAN_DEFAULT", "FontBox"]
