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

from PySide6.QtCore import QCoreApplication, QSignalBlocker, Qt, Signal
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
pushes at (see 4 in known-bugs.md), and the tooltip carries the detail."""

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

        self._id_label = QLabel("—")
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

        enabled = region is not None
        for widget in self._fields():
            widget.setEnabled(enabled)

        # After the blanket enable, not inside _populate: a region nothing is
        # painted over in has no use for a fill colour, and the field saying
        # so beats a note nobody reads.
        painting = region is not None and region.erase is not Erase.NONE
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
            self._erase,
            self._fill_color,
            self._text_color,
            self._font,
            self._font_size,
        )

    def _populate(self, document: PlanDocument | None, region: Region | None) -> None:
        if document is None or region is None:
            self._id_label.setText("—")
            self._flags.set_flags(None)
            self._source_text.setPlainText("")
            self._translation.setPlainText("")
            self._notes.setPlainText("")
            self._skip.setChecked(False)
            self._erase.setCurrentIndex(0)
            self._fill_color.set_color(Color(255, 255, 255))
            self._text_color.set_color(Color(0, 0, 0))
            self._font.set_value(None)
            self._font_size.setValue(_FONT_SIZE_AUTO)
            return

        # The geometry stays as the plan spells it — ``exact``, ``approximate``,
        # ``manual`` are the file's own vocabulary and the file is never
        # translated. The word around it is this window's, and is.
        self._id_label.setText(
            self.tr("{0}  ({1}, order {2})").format(region.id, region.geometry.value, region.order)
        )
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
