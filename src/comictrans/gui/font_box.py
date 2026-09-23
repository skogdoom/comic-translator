"""A font field that only offers fonts this tool can actually render with.

The list comes from :func:`comictrans.fonts.available_families`, never from
``QFontDatabase``. Those answer different questions: Qt lists what Qt can
draw with, while ``fonts.py`` resolves a family by looking for files in the
search directories and demands a real bold face, because emphasis is bold and
a bold is never synthesised. A list built from Qt would offer families that
``apply`` then refuses, turning a two-click choice into a render failure.

It stays editable, but what it records is always a family from that list —
typed in full, picked, or completed from part of a name. A fragment that
matches nothing installed is never written anywhere; leaving the field puts
back what it held, and so does Escape, whatever was typed. That is how a plan
came to be saved naming "Sans": the field used to record whatever it showed.

It never rewrites a name it was *given*, though. A plan written on another
Mac can name a font this one does not have; silently swapping that for
something installed is the one thing the spec says never happens. So a name
the field was handed stays exactly as it came, marked so you find out before
you render rather than after, until somebody chooses another.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QSignalBlocker, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFocusEvent,
    QFontMetrics,
    QGuiApplication,
    QHideEvent,
    QKeyEvent,
    QPalette,
)
from PySide6.QtWidgets import QApplication, QComboBox, QWidget

from .. import fonts

PLAN_DEFAULT = QCoreApplication.translate("FontBox", "(plan default)")
"""The entry meaning "no override", which the plan file records as absent.

The wording is the region inspector's, where the fallback really is the
plan's header. Somewhere else the same absence means something else — in
preferences it means "whatever extract finds" — so the text is a parameter
and this is only its default.
"""

_TEXT_ROLE = QPalette.ColorRole.Text


def _unresolvable_color(palette: QPalette) -> QColor:
    """A red that reads on this window's own background.

    Dark red on a dark window is a warning nobody can see: measured, Qt's
    ``darkRed`` manages 10.9:1 against white and 1.5:1 against a dark base,
    which is invisible. The palette says which way round the window is, so
    the mark is dark on light chrome and light on dark — 8.1:1 and 6.9:1,
    both past the 4.5:1 that ordinary text is held to.
    """
    background = palette.color(QPalette.ColorRole.Base)
    dark_chrome = background.lightness() < 128
    return QColor(255, 130, 120) if dark_chrome else QColor(160, 20, 20)


_MIN_CHARS = 16
_MAX_CHARS = 24
"""How wide the field may get, in characters, to show the name it holds.

A floor because a field too narrow to read its own value is no use, and a
ceiling because this width becomes the panel's minimum width: a font called
something enormous must not be able to force the Region dock wide. The region
line was the other field that could, and stopped — see ``inspector``'s
``ElidedLabel`` for how, and for what it cost.
"""


_NOT_LEAVING = (Qt.FocusReason.ActiveWindowFocusReason, Qt.FocusReason.PopupFocusReason)
"""Focus lost to another application, or to the list of suggestions."""

_NO_MATCH = object()
"""What :meth:`FontBox._best_match` says when nothing installed fits.

Not ``None``, which is a real answer here: "no override"."""


class FontBox(QComboBox):
    """An editable font picker. ``allow_default`` adds the "no override" entry.

    A region's font override may be absent, which is what ``allow_default``
    is for; ``default_text`` is what that absence is called, since it means
    different things in different places. The plan header's font may not be
    absent: every region without an override falls back to it, so there is
    nothing for it to fall back to itself.

    Read :meth:`value` when :attr:`chosen` fires, not the text: the text is
    whatever is being typed, and the value is what was last settled on.
    """

    chosen = Signal()
    """A font was settled on — picked, completed, or typed in full — or an
    edit was cancelled back to one. Read :meth:`value`. Not emitted when the
    value did not change, and never by :meth:`set_value`."""

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
        # What the field holds, as far as anyone reading value() knows; what
        # it held when the current edit began, for Escape and for leaving
        # with nothing to take; and whether an edit is under way at all.
        self._saved: str | None = None
        self._before: str | None = None
        self._editing = False
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        # Measured before anything is in it, so this is the frame and the
        # arrow with no text to speak of: what the width of a name has to be
        # added to. A combo sizes itself from its item list, not from the
        # text typed into it, which on a form that leaves fields at their
        # size hint left six pixels for a thirteen-character font name.
        self._chrome = self.sizeHint().width()

        # A popup of every family containing what was typed, so "Sans"
        # offers Comic Sans MS. The popup is not cosmetic: Qt's default for a
        # combo is inline completion, which finishes the text as though the
        # match began with what was typed — "Sans" became "Sansc Sans MS",
        # measured.
        completer = self.completer()
        if completer is not None:
            completer.setCompletionMode(completer.CompletionMode.PopupCompletion)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            # Queued, so it runs once Qt has refiltered the list and shown
            # it: showing it clears any mark made sooner, measured, and
            # textEdited is sooner still — the list then holds the previous
            # keystroke's matches.
            completer.completionModel().modelReset.connect(
                self._mark_best_match, Qt.ConnectionType.QueuedConnection
            )

        line_edit = self.lineEdit()
        if line_edit is not None:
            line_edit.textEdited.connect(self._on_typed)
            # Enter only. Leaving is caught as the focus event itself, in
            # focusOutEvent: this signal fires on leaving only if the text
            # changed since it last fired, so after Enter on a name nothing
            # matches, leaving would never be heard.
            line_edit.editingFinished.connect(
                lambda: self._finish(left=False) if self.hasFocus() else None
            )
        # A pick from either list: the one under the arrow, and the
        # suggestions too, which the combo passes on as its own. Nothing need
        # have been typed first, so this is a pick, not the end of an edit:
        # taken as the end of one, it was ignored — measured, the field
        # showed the font picked while the plan kept the one before.
        self.activated.connect(lambda index: self._pick(self.itemText(index)))

        self.currentTextChanged.connect(lambda _text: self._on_text_changed())
        self._fit_width()

    def _on_text_changed(self) -> None:
        self._mark_resolvable()
        self._fit_width()

    # -- settling on a font ----------------------------------------------

    def _best_match(self, text: str) -> object:
        """The family ``text`` stands for, ``None`` for the default, or ``_NO_MATCH``.

        A family named in full, ignoring case; otherwise the first that
        contains it — the same one the list marks. Empty is the default
        where there is one, and matches nothing where there is not: the plan
        header has no font to fall back to.
        """
        self._ensure_loaded()
        typed = text.strip().casefold()
        if not typed:
            return None if self._allow_default else _NO_MATCH
        names = [self.itemText(index) for index in range(self.count())]
        folded = [name.casefold() for name in names]
        found = (
            names[folded.index(typed)]
            if typed in folded
            else next((name for name in names if typed in name.casefold()), None)
        )
        if found is None:
            return _NO_MATCH
        return None if self._allow_default and found == self._default_text else found

    def _on_typed(self, text: str) -> None:
        """A key was typed. A family named in full is settled on at once.

        At once rather than on leaving, because not every way out of the
        field says so: Next Region changes the region under it without the
        field ever losing focus, and would take a correctly typed name with
        it. Only a name typed in full, though — a fragment waits.
        """
        if not self._editing:
            self._editing = True
            self._before = self._saved
        if text.strip().casefold() in (name.casefold() for name in self._names()):
            self._settle(self._best_match(text))

    def _names(self) -> list[str]:
        self._ensure_loaded()
        return [self.itemText(index) for index in range(self.count())]

    def _finish(self, *, left: bool) -> None:
        """Enter, Tab, a pick from either list, or the field losing focus.

        Settles on the best match for what was typed and shows it spelled as
        the family is. With nothing to take, leaving the field puts back what
        it held, while Enter leaves the text where it is — marked as not
        installed — so it can be corrected.
        """
        if not self._editing:
            return
        match = self._best_match(self.currentText())
        if match is _NO_MATCH:
            if left:
                self._revert()
            return
        self._take(match)

    def _pick(self, text: str) -> None:
        """A choice from either list, whether or not anything was typed first."""
        match = self._best_match(text)
        if match is not _NO_MATCH:
            self._take(match)

    def _take(self, match: object) -> None:
        family = match if isinstance(match, str) else None
        self._settle(family)
        self._editing = False
        self._show(family)

    def _settle(self, match: object) -> None:
        family = match if isinstance(match, str) else None
        if family != self._saved:
            self._saved = family
            self.chosen.emit()

    def _revert(self) -> None:
        """Put back what the field held when this edit began."""
        self._settle(self._before)
        self._editing = False
        self._show(self._before)

    def _mark_best_match(self) -> None:
        """Show which suggestion Enter, Tab or leaving will take, without taking it.

        Only shows it: what is taken is worked out again by
        :meth:`_best_match` when the edit ends, so a key that beats this to
        it takes the same font.

        A family typed in full is marked over a longer one containing it, so
        "Sans" stays Sans where both exist; otherwise the first in the list.

        Marked with the popup's selection signals blocked, because the
        completer listens to them and writes whatever is marked straight
        into the field — measured: marked that way, the text was replaced
        before anything was pressed, and Escape could not give it back. The
        view hears of a new current row through those same signals, so it is
        asked to repaint here instead. Whether it would have repainted anyway
        is not something an offscreen test can see; the request costs
        nothing.
        """
        completer = self.completer()
        popup = completer.popup() if completer is not None else None
        if completer is None or popup is None:
            return
        model = completer.completionModel()
        if model.rowCount() == 0:
            return
        typed = completer.completionPrefix().strip().casefold()
        names = [str(model.index(row, 0).data()).casefold() for row in range(model.rowCount())]
        best = names.index(typed) if typed in names else 0
        selection = popup.selectionModel()
        with QSignalBlocker(selection):
            popup.setCurrentIndex(model.index(best, 0))
        popup.viewport().update()

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt override
        """Leaving the field settles the edit: the best match, or what was there.

        The combo, not its line edit: the line edit hands focus to the combo,
        so this is where losing it is heard — measured, a filter on the line
        edit saw no focus event at all. Not to another application, and not
        to the list of suggestions: coming back from either, the edit is
        still under way.
        """
        if event.reason() not in _NOT_LEAVING:
            self._finish(left=True)
        super().focusOutEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802 - Qt override
        """Closing the dialog is leaving the field, however it was closed.

        Clicking a button need not take focus — on macOS it does not — so
        the field can go without ever being told it lost focus.
        """
        self._finish(left=True)
        super().hideEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        """Escape cancels the edit, and goes no further.

        Here rather than on the line edit because keys come here first and
        reach the line edit by a direct call, which no event filter sees. The
        first Escape puts the font back; only a second one, with nothing
        left to cancel, reaches the dialog and closes it.

        With the suggestions open, the keyboard is theirs, but Qt's completer
        closes them and hands Escape and Enter on to here — measured, so
        nothing needs catching on the list itself.
        """
        if event.key() == Qt.Key.Key_Escape and self._editing:
            self._revert()
            event.accept()
            return
        super().keyPressEvent(event)

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
        """The family last settled on, or ``None`` for "no override".

        Not what the field shows while somebody is typing in it: a fragment
        is not a font, and nothing reading this should ever see one.
        """
        return self._saved

    def set_value(self, family: str | None) -> None:
        """Hold ``family``, exactly as given, without writing it back out.

        Also ends any edit under way: the plan moved on — another region,
        an undo — and what was being typed belonged to what it was before.
        """
        self._ensure_loaded()
        self._saved = self._before = family
        self._editing = False
        self._show(family)

    def _show(self, family: str | None) -> None:
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

        The name *shown*, so that Enter on a name nothing matches leaves it
        marked in the field. Membership of the scanned list rather than a
        fresh ``resolve_family`` call: the list is exactly the families that
        were put through it, so the two agree, and this can be asked on
        every keystroke.
        """
        text = self.currentText().strip()
        if not text or (self._allow_default and text == self._default_text):
            return True
        return text in fonts.available_families()

    def _mark_resolvable(self) -> None:
        line_edit = self.lineEdit()
        if line_edit is None or not self._loaded:
            return
        palette = line_edit.palette()
        if self.resolvable():
            palette.setColor(_TEXT_ROLE, QApplication.palette().color(_TEXT_ROLE))
            self.setToolTip(self._name_if_clipped())
        else:
            palette.setColor(_TEXT_ROLE, _unresolvable_color(palette))
            self.setToolTip(
                self.tr(
                    "{0} is not installed here, or has no bold face. "
                    "apply will refuse it rather than substitute another font."
                ).format(repr(self.currentText().strip()))
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
