"""The region inspector: what one region says, and the fields that edit it.

Every field writes straight through to the :class:`PlanDocument` as it is
edited — there is no separate "apply" step — because the document is already
the one source of truth the rest of the window reads from. ``edited`` is
emitted after every write so the window can refresh whatever depends on it
(the window title's dirty marker, the page list's flag counts, the canvas's
outline for this region) without the inspector needing to know about any of
those things itself.

**Every typed field commits on each keystroke, not on focus loss.** A
half-written value is still worth keeping: committing on focus loss would
leave an edit sitting in a widget nothing else in the window knows about, so
the dirty marker would be wrong and closing the window straight after typing
would discard the text without asking. That holds for the font override as
much as for the prose — a name typed but not tabbed away from is an edit,
and a half-typed font name is not a problem to guard against here, because
nothing resolves a font until something renders. The cost is that an undo
stack built over this has to coalesce consecutive keystrokes rather than
treat each one as a step of its own.
"""

from __future__ import annotations

from contextlib import ExitStack

from PySide6.QtCore import QCoreApplication, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QListWidget,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..model import Color, Erase, Region
from . import erase_choices
from .color_box import ColorBox
from .document import PlanDocument, RegionFlags
from .font_box import FontBox
from .prose import ProseEdit

# The words in this table and in _FLAG_LABELS go through
# ``QCoreApplication.translate`` rather than ``tr``: both are module-level and
# have no ``self`` to ask. They are evaluated when this module is imported,
# which is after ``gui.app`` installs the translator — see ``translations``.
# Every call spells the whole thing out rather than going through a helper or
# a short alias, because ``lupdate`` reads the source rather than running it:
# behind either, it extracts nothing at all. Measured, not assumed.
ERASE_CHOICES: tuple[tuple[str, Erase | None, str], ...] = (
    (
        QCoreApplication.translate("RegionInspector", "(plan default)"),
        None,
        QCoreApplication.translate(
            "RegionInspector", "whatever the run is set to erase: --erase, or the render dialog"
        ),
    ),
    *erase_choices.CHOICES,
)
"""What apply paints over inside this region, in words rather than strategy
names.

The four real ones come from ``erase_choices``, which the render dialog and
preferences read too, so the three boxes cannot come to call the same thing
by different names. The row this adds is the one only a region has: a region
may decline to decide and follow the run, and a run has nothing to follow.

Short words: this box sits in a dock whose width every field's size hint
pushes at, and the tooltip carries the detail. The region line above it is
the one field that no longer pushes — see :class:`ElidedLabel`."""

_FONT_SIZE_AUTO = 0
"""The spin box's special value for "no override", shown as the word "auto"."""

_NOTES_HEIGHT = 60
"""Enough for a few lines. Notes are usually a sentence, not a paragraph."""

_SPECIAL_VALUE_PADDING_CHARS = 2
"""Air around the word "auto", beyond the room the widest number needs."""


def _widen_for_special_value(box: QSpinBox) -> None:
    """Give a spin box's special value text as much room as its widest number.

    ``QAbstractSpinBox.sizeHint`` is computed from the numeric range and
    ignores ``specialValueText`` — measured rather than assumed: the hint
    comes out identical with and without one set. That goes unnoticed
    wherever the form stretches its fields to the panel width, which is what
    every platform this suite runs on does. macOS is the one that does not:
    its style asks ``QFormLayout`` for ``FieldsStayAtSizeHint``, and the word
    "auto" then has to sit in a box measured for three digits.

    Measured from the font rather than set to a pixel count, so it holds at
    whatever size the UI is actually running at.
    """
    metrics = QFontMetrics(box.font())
    widest_number = max(
        metrics.horizontalAdvance(str(box.minimum())),
        metrics.horizontalAdvance(str(box.maximum())),
    )
    chrome = box.sizeHint().width() - widest_number
    # Plus a couple of characters of air. Matching the slack a three-digit
    # number gets is enough to fit the word and not enough to read it
    # comfortably: measured at eight pixels, which looks like a mistake.
    padding = metrics.averageCharWidth() * _SPECIAL_VALUE_PADDING_CHARS
    box.setMinimumWidth(metrics.horizontalAdvance(box.specialValueText()) + chrome + padding)


_FLAG_LABELS = {
    "approximate": QCoreApplication.translate("RegionInspector", "approximate geometry"),
    "low_confidence": QCoreApplication.translate("RegionInspector", "low confidence"),
    "held_back": QCoreApplication.translate("RegionInspector", "held back (no translation)"),
    "unedited": QCoreApplication.translate("RegionInspector", "same as source"),
    "overlapping": QCoreApplication.translate("RegionInspector", "overlaps another region"),
    "skipped": QCoreApplication.translate("RegionInspector", "skipped"),
}


def flag_labels(flags: RegionFlags | None) -> tuple[str, ...]:
    """One line per reason this region is worth a second look.

    A tuple rather than a joined string: six of these can be true at once,
    and running them together into one line is what made the field outgrow
    the space it was given.
    """
    if flags is None:
        return ("—",)
    active = tuple(text for field, text in _FLAG_LABELS.items() if getattr(flags, field))
    return active or (QCoreApplication.translate("RegionInspector", "nothing flagged"),)


class FlagList(QListWidget):
    """The flags, one per row, sized to exactly the rows it holds.

    A word-wrapped ``QLabel`` was the obvious thing and the wrong one. Its
    height depends on its width, but the form lays the row out from the
    label's own size hint, which is computed for a different width than it
    ends up with — so a second line was clipped, and whether it clipped at
    all changed with the number of flags. A list reports an honest height
    for its contents and has somewhere to put an overflow.

    Not a control: nothing here is selectable and it never takes focus. It
    is a readout that happens to have rows.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setWordWrap(True)
        self.set_flags(None)

    def set_flags(self, flags: RegionFlags | None) -> None:
        self.clear()
        self.addItems(flag_labels(flags))
        self._fit_to_rows()

    def _fit_to_rows(self) -> None:
        """Take exactly the height the rows need, at the width it now has."""
        rows = sum(self.sizeHintForRow(index) for index in range(self.count()))
        height = rows + 2 * self.frameWidth()
        if height != self.height():
            self.setFixedHeight(height)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        # Wrapping depends on the width, so the height a row needs does too.
        # Guarded above against setting a height it already has, which is
        # what would otherwise bounce between resize and layout for ever.
        self._fit_to_rows()


_ID_MIN_CHARS = 24
_ID_MAX_CHARS = 44
"""How wide the region line asks to be, in characters.

The same bargain ``font_box`` strikes with a family name, and for the same
two reasons: a floor because a field too narrow to read is no use, and a
ceiling because this width is what the panel's own minimum is built from —
a region id long enough must not be able to push the Region dock wide.

44 shows ``page-001-001  (exact, order 1)`` whole, which is the shape
``extract`` writes; past that the id is elided and the tooltip has the rest.
"""


class ElidedLabel(QLabel):
    """A one-line label that shortens its text to fit instead of pushing.

    A plain ``QLabel`` reports the full width of its text as the width it
    needs, and a form passes that up to the panel and the panel to the dock —
    so the Region dock used to change width every time the selection moved to
    a region whose id was longer, taking the width from the page beside it.
    Measured on the fixture ids: 415px of dock against 538px, and 123px of
    canvas gone, on nothing but a click.

    So it asks for a width that has nothing to do with its text — a floor and
    a ceiling in characters, exactly as ``font_box`` bounds the width of a
    family name — and elides into whatever it is actually given. The hint
    being a constant is the whole of the fix: a hint that cannot grow with
    the id cannot push the panel, whichever way the form is sizing its
    fields.

    **``QSizePolicy.Ignored`` was tried here and is wrong**, which is worth
    recording because it looks right and passes on Linux. A form asks
    ``QFormLayout`` how to size its fields, and the answer is not the same
    everywhere: this project's own ``_widen_for_special_value`` already
    notes that macOS asks for ``FieldsStayAtSizeHint`` where every other
    platform the suite runs on stretches fields to the panel. Under a policy
    that stretches, ``Ignored`` gets the full width and everything looks
    fine. Under one that sizes a field to its hint, a widget whose hint is
    ignored is given **nothing** — measured at zero pixels, which is a region
    line nobody can see, and is what shipped to a Mac before this note
    existed.

    **The text is in two parts**, which is the one thing here that is not
    ``HintLine``. It elides to the right, because a hint reads from the front;
    this cannot, because what identifies a region is the *tail* of its id, and
    the geometry and order after it are a suffix that must not be eaten
    either. So the first part is elided in the middle and the second is kept
    whole.

    The full text is on the tooltip, which is what makes eliding honest rather
    than lossy — the id is also in the plan file and in the page list.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._elidable = ""
        self._fixed = ""

    def set_parts(self, elidable: str, fixed: str = "") -> None:
        """Show ``elidable`` shortened as needed, with ``fixed`` kept whole."""
        self._elidable, self._fixed = elidable, fixed
        self.setToolTip(f"{elidable}{fixed}".strip())
        self._relayout()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """As much as the line is worth, never as much as the line is."""
        return QSize(self._chars(_ID_MAX_CHARS), super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """And a floor, so a narrowed panel still shows something."""
        return QSize(self._chars(_ID_MIN_CHARS), super().minimumSizeHint().height())

    def _chars(self, count: int) -> int:
        """``count`` characters wide, measured from the font rather than set
        in pixels, so these hold at whatever size the interface is run at."""
        return QFontMetrics(self.font()).averageCharWidth() * count

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        # How much fits changed, so what to cut did too.
        self._relayout()

    def _relayout(self) -> None:
        metrics = QFontMetrics(self.font())
        room = self.contentsRect().width() - metrics.horizontalAdvance(self._fixed)
        elided = metrics.elidedText(self._elidable, Qt.TextElideMode.ElideMiddle, max(0, room))
        shown = f"{elided}{self._fixed}"
        # Guarded for the reason ``HintLine._elide`` and ``FlagList._fit_to_rows``
        # are: setting the text is what triggers the next layout pass, which is
        # what calls this again. Carried on their evidence rather than on any
        # of its own — removing it here changes nothing the suite can see, and
        # a runaway layout pass is a hang, which is the class of thing the
        # offscreen platform the widget tests run under is least likely to show.
        if shown != self.text():
            self.setText(shown)


def _erase_index(mode: Erase | None) -> int:
    """Which row of the erase box a region's value is."""
    for index, (_label, value, _hint) in enumerate(ERASE_CHOICES):
        if value == mode:
            return index
    return 0


class RegionInspector(QWidget):
    edited = Signal()

    sample_requested = Signal(str)
    """A colour field wants one taken off the page: ``"fill"`` or ``"text"``.
    The canvas is not reachable from here; the window arranges the picking
    and writes the answer back through :meth:`set_region`."""

    escaped = Signal()
    """Escape was pressed in one of the prose fields. The window puts focus
    back on the page, where the arrow keys nudge a region again."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._document: PlanDocument | None = None
        self._region_id: str | None = None

        self._id_label = ElidedLabel()
        self._id_label.set_parts("—")
        self._flags = FlagList()
        # Typeable, not just readable. A region drawn by hand has no OCR
        # reading and no other way to get one — nothing in review reads a
        # page for text — and for a detected region this is a field in a file
        # that has been hand-editable since milestone 1. What apply reports
        # as "same as source" is measured against whatever is in the plan,
        # here or in the YAML.
        # ``ProseEdit`` rather than ``QPlainTextEdit``: these fields keep no
        # undo history of their own, because the document keeps one for
        # everything and two stacks over the same text would disagree the
        # moment either was used. Switching the widget's history off is not
        # enough on its own — see that module for the measurement.
        self._source_text = ProseEdit()
        self._source_text.setMaximumHeight(100)
        self._source_text.setPlaceholderText(self.tr("what the lettering on the page says"))
        self._translation = ProseEdit()
        self._translation.setMaximumHeight(100)
        self._notes = ProseEdit()
        self._notes.setMaximumHeight(_NOTES_HEIGHT)
        self._notes.setPlaceholderText(self.tr("never rendered; kept when re-extracting"))
        self._skip = QCheckBox(self.tr("skip: leave this region untouched"))
        # Named against `skip` rather than on its own, because the two are a
        # checkbox apart and mean opposite halves of the same sentence: skip
        # stops the region being *rendered*, locked stops it being *changed*.
        # Saying "still lettered" in the label is what keeps the next person
        # from reading this one as a quieter skip.
        self._locked = QCheckBox(self.tr("locked: finished — still lettered, but not editable"))
        self._erase = QComboBox()
        for label, mode, hint in ERASE_CHOICES:
            self._erase.addItem(label, None if mode is None else str(mode))
            self._erase.setItemData(self._erase.count() - 1, hint, Qt.ItemDataRole.ToolTipRole)
        self._fill_color = ColorBox()
        self._text_color = ColorBox()
        self._font = FontBox(allow_default=True)
        self._font_size = QSpinBox()
        self._font_size.setRange(_FONT_SIZE_AUTO, 999)
        self._font_size.setSpecialValueText(self.tr("auto"))
        _widen_for_special_value(self._font_size)

        form = QFormLayout()
        form.addRow(self.tr("region"), self._id_label)
        form.addRow(self.tr("flags"), self._flags)
        form.addRow(self.tr("source text"), self._source_text)
        form.addRow(self.tr("translation"), self._translation)
        form.addRow(self.tr("notes"), self._notes)
        form.addRow("", self._skip)
        form.addRow("", self._locked)
        form.addRow(erase_choices.ERASE_FIELD, self._erase)
        form.addRow(self.tr("fill colour"), self._fill_color)
        form.addRow(self.tr("text colour"), self._text_color)
        form.addRow(self.tr("font override"), self._font)
        form.addRow(self.tr("font size"), self._font_size)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addStretch(1)

        for prose in (self._source_text, self._translation, self._notes):
            prose.escaped.connect(self.escaped)
        self._source_text.textChanged.connect(self._on_source_text_changed)
        self._translation.textChanged.connect(self._on_translation_changed)
        self._notes.textChanged.connect(self._on_notes_changed)
        self._skip.toggled.connect(self._on_skip_changed)
        self._locked.toggled.connect(self._on_locked_changed)
        self._font.currentTextChanged.connect(self._on_font_changed)
        self._font_size.valueChanged.connect(self._on_font_size_changed)
        self._erase.currentIndexChanged.connect(self._on_erase_changed)
        self._fill_color.picked.connect(self._on_fill_color_picked)
        self._text_color.picked.connect(self._on_text_color_picked)
        self._fill_color.sample_requested.connect(lambda: self.sample_requested.emit("fill"))
        self._text_color.sample_requested.connect(lambda: self.sample_requested.emit("text"))

        self.set_region(None, None)

    def set_region(self, document: PlanDocument | None, region_id: str | None) -> None:
        """Show a region's current fields, or clear and disable everything.

        Field-changed signals fire while these widgets are populated
        programmatically unless blocked — without that, setting the
        translation text here would immediately write it straight back to
        the document as if the user had typed it.
        """
        self._document = document
        self._region_id = region_id
        region = document.region(region_id) if document is not None and region_id else None

        with ExitStack() as blockers:
            for widget in self._fields():
                blockers.enter_context(QSignalBlocker(widget))
            self._populate(document, region)

        # A locked region shows what it holds and accepts nothing: the
        # document refuses every edit but the lock itself, so a field left
        # live here would be one that raises the moment it is used.
        editable = region is not None and not region.locked
        for widget in self._fields():
            widget.setEnabled(editable)

        # After the blanket enable, not inside _populate, and both for the
        # same reason: these two are exceptions to it. The lock stays live on
        # a locked region because it is the way back out of one, and a region
        # nothing is painted over in has no use for a fill colour — the field
        # saying so beats a note nobody reads.
        self._locked.setEnabled(region is not None)
        painting = editable and region is not None and region.erase is not Erase.NONE
        self._fill_color.setEnabled(painting)
        self._fill_color.setToolTip(
            "" if painting else self.tr("unused: nothing is painted over in this region")
        )

    def focus_source_text(self) -> None:
        """Put the cursor where a newly drawn region needs typing first."""
        self._source_text.setFocus()

    def focused_prose_field(self) -> ProseEdit | None:
        """Which of source text, translation or notes has the caret, if any.

        Used to put focus back in the same field after the region
        underneath it changes — stepping to the next region with the
        keyboard should not interrupt typing.
        """
        for prose in (self._source_text, self._translation, self._notes):
            if prose.hasFocus():
                return prose
        return None

    def _fields(self) -> tuple[QWidget, ...]:
        """Every widget that writes to the document when it changes."""
        return (
            self._source_text,
            self._translation,
            self._notes,
            self._skip,
            self._locked,
            self._erase,
            self._fill_color,
            self._text_color,
            self._font,
            self._font_size,
        )

    def _populate(self, document: PlanDocument | None, region: Region | None) -> None:
        if document is None or region is None:
            self._id_label.set_parts("—")
            self._flags.set_flags(None)
            self._source_text.setPlainText("")
            self._translation.setPlainText("")
            self._notes.setPlainText("")
            self._skip.setChecked(False)
            self._locked.setChecked(False)
            self._erase.setCurrentIndex(0)
            self._fill_color.set_color(Color(255, 255, 255))
            self._text_color.set_color(Color(0, 0, 0))
            self._font.set_value(None)
            self._font_size.setValue(_FONT_SIZE_AUTO)
            return

        # The geometry stays as the plan spells it — ``exact``, ``approximate``,
        # ``manual`` are the file's own vocabulary and the file is never
        # translated. The word around it is this window's, and is.
        # Two parts, because the id is the half that may be shortened and the
        # half whose *tail* identifies the region — see :class:`ElidedLabel`.
        # The gap between them is layout rather than language, so it is not in
        # the translated string.
        suffix = self.tr("({0}, order {1})").format(region.geometry.value, region.order)
        self._id_label.set_parts(region.id, f"  {suffix}")
        self._flags.set_flags(document.flags(region.id))
        self._source_text.setPlainText(region.source_text)
        self._translation.setPlainText(region.translation)
        self._notes.setPlainText(region.notes)
        # The caret goes to the end of each, not the start ``setPlainText``
        # leaves it at. Walking to the next region repopulates these under a
        # caret that never moved, and the end is where somebody about to type
        # wants it — the same place clicking a balloon puts it.
        for prose in (self._source_text, self._translation, self._notes):
            prose.put_the_caret_at_the_end()
        self._skip.setChecked(region.skip)
        self._locked.setChecked(region.locked)
        self._erase.setCurrentIndex(_erase_index(region.erase))
        self._fill_color.set_color(region.fill_color)
        self._text_color.set_color(region.text_color)
        self._font.set_value(region.font)
        self._font_size.setValue(region.font_size or _FONT_SIZE_AUTO)

    def focus_translation(self) -> None:
        """Put the caret in the translation, ready to type.

        The caret at the end, with nothing selected. Selecting the text was
        the other candidate — a fresh region's translation is seeded from
        what the recogniser read and is usually about to be replaced whole —
        and it is not what this does, because the same gesture on a
        translation somebody has already written would put one keystroke
        between them and losing it. Ctrl+A is one keystroke too, and it is
        the one that says so.
        """
        self._translation.setFocus(Qt.FocusReason.OtherFocusReason)
        self._translation.put_the_caret_at_the_end()

    def _commit(self) -> None:
        if self._document is not None and self._region_id is not None:
            # Flags may have changed (e.g. translation is no longer empty),
            # so the label needs refreshing even though nothing else does.
            self._flags.set_flags(self._document.flags(self._region_id))
        self.edited.emit()

    def _on_source_text_changed(self) -> None:
        if self._document is not None and self._region_id is not None:
            self._document.set_source_text(self._region_id, self._source_text.toPlainText())
        self._commit()

    def _on_erase_changed(self, index: int) -> None:
        if self._document is not None and self._region_id is not None:
            data = self._erase.itemData(index)
            self._document.set_erase(self._region_id, Erase(data) if data else None)
            # The fill colour's own enabled state depends on this one.
            self.set_region(self._document, self._region_id)
        self._commit()

    def _on_fill_color_picked(self, color: Color) -> None:
        if self._document is not None and self._region_id is not None:
            self._document.set_fill_color(self._region_id, color)
        self._commit()

    def _on_text_color_picked(self, color: Color) -> None:
        if self._document is not None and self._region_id is not None:
            self._document.set_text_color(self._region_id, color)
        self._commit()

    def _on_translation_changed(self) -> None:
        if self._document is not None and self._region_id is not None:
            self._document.set_translation(self._region_id, self._translation.toPlainText())
        self._commit()

    def _on_notes_changed(self) -> None:
        if self._document is not None and self._region_id is not None:
            self._document.set_notes(self._region_id, self._notes.toPlainText())
        self._commit()

    def _on_locked_changed(self, checked: bool) -> None:
        if self._document is not None and self._region_id is not None:
            self._document.set_locked(self._region_id, checked)
            # Everything else in the panel has just become live or dead, and
            # nothing else repopulates it: this is the one field whose edit
            # changes what the rest of them will accept.
            self.set_region(self._document, self._region_id)
            self.edited.emit()

    def _on_skip_changed(self, checked: bool) -> None:
        if self._document is not None and self._region_id is not None:
            self._document.set_skip(self._region_id, checked)
        self._commit()

    def _on_font_changed(self, _text: str) -> None:
        if self._document is not None and self._region_id is not None:
            self._document.set_font(self._region_id, self._font.value())
        self._commit()

    def _on_font_size_changed(self, value: int) -> None:
        if self._document is not None and self._region_id is not None:
            self._document.set_font_size(self._region_id, value or None)
        self._commit()
