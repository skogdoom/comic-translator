"""The plan header: the settings every region in the file is drawn under.

Split the way the header itself is: what you decide, and what the tool
recorded. Font, case, the two fit limits and the language pair are yours to
change. So is what the comic is — its series, title, volume and number, year,
publisher, writer and reading direction — which nothing measures from the
pages, so a plan extract wrote holds none of it until it is typed here.
``generator``, ``created``, ``ocr_engine`` and ``version`` describe what
produced this plan, and are shown but not editable — you are not the
authority on which OCR engine ran, and a plan that claims a different one is
a plan that lies about where its text came from.

Fields write straight through to the document as they are edited, the same
as the region inspector, so there is nothing to apply and nothing to cancel.
Ctrl+Z after closing takes a change back, one field at a time. The font is
the one that waits for a whole name, for the reason ``font_box`` gives.
"""

from __future__ import annotations

from contextlib import ExitStack
from functools import partial

from PySide6.QtCore import QRegularExpression, QSignalBlocker, Signal
from PySide6.QtGui import QFocusEvent, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from ..model import ReadingDirection, TextCase
from ..planfile.schema import CONDENSE_MIN_RANGE, FONT_SIZE_MIN_RATIO_RANGE
from .document import CHAPTER_TEXT_FIELDS, PlanDocument
from .font_box import FontBox
from .language_box import LanguageBox

_RATIO_DECIMALS = 4
_CONDENSE_DECIMALS = 2


class YearBox(QLineEdit):
    """A year, or nothing: four digits, or empty for a plan that does not say.

    A line rather than a spin box, because empty has to be an answer, and a
    spin box has no empty — only a special value at the bottom of its range,
    which is reached by stepping down past the year 1000. Four digits not
    starting with a nought is exactly ``schema.YEAR_RANGE``, so what the field
    accepts is what the reader will load.
    """

    chosen = Signal(object)
    """A year was typed in full, or the field was cleared: an ``int``, or ``None``."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setValidator(QRegularExpressionValidator(QRegularExpression("[1-9][0-9]{0,3}"), self))
        self.setPlaceholderText(self.tr("not stated"))
        self._value: int | None = None
        self.textEdited.connect(self._on_edited)

    def value(self) -> int | None:
        return self._value

    def set_value(self, year: int | None) -> None:
        self._value = year
        self.setText("" if year is None else str(year))

    def _on_edited(self, text: str) -> None:
        if text and len(text) < 4:
            return  # on the way to four digits
        self._value = int(text) if text else None
        self.chosen.emit(self._value)

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt override
        """A year left half-typed goes back to the one the plan still holds."""
        self.set_value(self._value)
        super().focusOutEvent(event)


class HeaderDialog(QDialog):
    edited = Signal()
    """A setting that decides how regions are drawn changed."""

    described = Signal()
    """One of the comic's details changed. Separate from ``edited`` because
    none of them changes a rendered page, so none is a reason to render one
    again — and they are typed a letter at a time."""

    def __init__(self, document: PlanDocument, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._document = document
        self.setWindowTitle(self.tr("Plan Header"))

        header = document.plan.header

        # allow_default=False: every region without an override falls back
        # to the header font, so it has nothing to fall back to itself.
        self._font = FontBox(allow_default=False)
        self._font.set_value(header.font)
        self._font_reach = QLabel(self.font_reach())

        self._case = QComboBox()
        for case in TextCase:
            self._case.addItem(case.value, case)
        self._case.setCurrentIndex(self._case.findData(header.case))

        self._min_ratio = QDoubleSpinBox()
        self._min_ratio.setDecimals(_RATIO_DECIMALS)
        self._min_ratio.setRange(*FONT_SIZE_MIN_RATIO_RANGE)
        self._min_ratio.setSingleStep(0.001)
        self._min_ratio.setValue(header.font_size_min_ratio)

        self._condense = QDoubleSpinBox()
        self._condense.setDecimals(_CONDENSE_DECIMALS)
        self._condense.setRange(*CONDENSE_MIN_RANGE)
        self._condense.setSingleStep(0.01)
        self._condense.setValue(header.condense_min)

        # Shown by name, stored as the code the plan already has; see
        # ``language_box``.
        self._source_language = LanguageBox()
        self._source_language.set_value(header.source_language)
        self._target_language = LanguageBox()
        self._target_language.set_value(header.target_language)

        # What the comic is. Labelled the way the rest of the dialog is, in
        # the plan's own words, and each field is only ever what the plan
        # holds: empty for a plan that does not say.
        self._details: dict[str, QLineEdit] = {}
        for field in CHAPTER_TEXT_FIELDS:
            line = QLineEdit(str(getattr(header, field)))
            line.textEdited.connect(partial(self._on_detail_edited, field))
            self._details[field] = line
        self._year = YearBox()
        self._year.set_value(header.year)
        self._direction = QComboBox()
        self._direction.addItem(self.tr("not stated"), "")
        self._direction.addItem(self.tr("left to right"), str(ReadingDirection.LEFT_TO_RIGHT))
        self._direction.addItem(self.tr("right to left"), str(ReadingDirection.RIGHT_TO_LEFT))
        self._show_direction(header.reading_direction)

        # Volume and number side by side: they are one reference between
        # them, and both are short.
        issue = QHBoxLayout()
        issue.addWidget(self._details["volume"])
        issue.addWidget(QLabel(self.tr("number")))
        issue.addWidget(self._details["number"])
        form = QFormLayout()
        form.addRow(self.tr("font"), self._font)
        form.addRow("", self._font_reach)
        form.addRow(self.tr("case"), self._case)
        form.addRow(self.tr("smallest text"), self._min_ratio)
        form.addRow(self.tr("condensing floor"), self._condense)
        form.addRow(self.tr("source language"), self._source_language)
        form.addRow(self.tr("target language"), self._target_language)
        # The comic's section in the same form rather than one of its own,
        # so its fields line up with the ones above whatever the labels
        # measure in the font of the day.
        form.addItem(QSpacerItem(0, 8))
        form.addRow(QLabel(self.tr("the comic")))
        form.addRow(self.tr("series"), self._details["series"])
        form.addRow(self.tr("title"), self._details["title"])
        form.addRow(self.tr("volume"), issue)
        form.addRow(self.tr("year"), self._year)
        form.addRow(self.tr("publisher"), self._details["publisher"])
        form.addRow(self.tr("writer"), self._details["writer"])
        form.addRow(self.tr("reading direction"), self._direction)

        recorded = QFormLayout()
        recorded.addRow(self.tr("written by"), QLabel(header.generator))
        recorded.addRow(self.tr("created"), QLabel(header.created))
        recorded.addRow(self.tr("OCR engine"), QLabel(header.ocr_engine))
        recorded.addRow(self.tr("plan version"), QLabel(str(header.version)))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addSpacing(8)
        layout.addWidget(QLabel(self.tr("recorded when this plan was written")))
        layout.addLayout(recorded)
        layout.addWidget(buttons)
        self.setMinimumWidth(420)

        self._font.chosen.connect(self._on_font_chosen)
        self._case.currentIndexChanged.connect(self._on_case_changed)
        self._min_ratio.valueChanged.connect(self._on_min_ratio_changed)
        self._condense.valueChanged.connect(self._on_condense_changed)
        self._source_language.currentTextChanged.connect(self._on_source_language_changed)
        self._target_language.currentTextChanged.connect(self._on_target_language_changed)
        self._year.chosen.connect(self._on_year_chosen)
        self._direction.currentIndexChanged.connect(self._on_direction_changed)

    def font_reach(self) -> str:
        """How much of the plan the header font actually decides.

        A method rather than the plain function it was: the count inflects
        the noun beside it, and only ``tr`` inflects a noun for a count —
        which needs a ``QObject`` to be called on. Measured; see
        ``resources/translations/recompile.py``.
        """
        following = self._document.regions_using_header_font()
        total = len(self._document.plan.regions)
        if total and following == total:
            return self.tr("used by all %n region(s)", None, total)
        return self.tr("used by {0} of %n region(s); the rest override it", None, total).format(
            following
        )

    def _commit(self) -> None:
        self._font_reach.setText(self.font_reach())
        self.edited.emit()

    def _on_font_chosen(self) -> None:
        # The field only settles on an installed family, and never on empty
        # here, where there is no default to fall back to; the check is for
        # a field that one day does.
        if font := self._font.value():
            self._document.set_header_font(font)
            self._commit()

    def _on_case_changed(self, index: int) -> None:
        data = self._case.itemData(index)
        if data is None:  # no current item
            return
        # TextCase is a StrEnum, and Qt hands it back as the plain string it
        # subclasses rather than as the member that went in, so it has to be
        # converted rather than type-checked.
        self._document.set_header_case(TextCase(data))
        self._commit()

    def _on_min_ratio_changed(self, value: float) -> None:
        self._document.set_header_font_size_min_ratio(value)
        self._commit()

    def _on_condense_changed(self, value: float) -> None:
        self._document.set_header_condense_min(value)
        self._commit()

    def _on_source_language_changed(self, _text: str) -> None:
        # The code, not the text: the field shows "Italian (it)" and the
        # plan records ``it``. Empty is skipped, as an empty font is.
        if code := self._source_language.value():
            self._document.set_header_source_language(code)
            self._commit()

    def _on_target_language_changed(self, _text: str) -> None:
        if code := self._target_language.value():
            self._document.set_header_target_language(code)
            self._commit()

    def _on_detail_edited(self, field: str, text: str) -> None:
        self._document.set_header_detail(field, text)
        self.described.emit()

    def _on_year_chosen(self, year: int | None) -> None:
        self._document.set_header_year(year)
        self.described.emit()

    def _on_direction_changed(self, index: int) -> None:
        data = self._direction.itemData(index)
        self._document.set_header_reading_direction(ReadingDirection(data) if data else None)
        self.described.emit()

    def _show_direction(self, direction: ReadingDirection | None) -> None:
        self._direction.setCurrentIndex(
            self._direction.findData("" if direction is None else str(direction))
        )

    def repopulate(self) -> None:
        """Show the header as it now stands, without writing anything back.

        Needed if the plan changes underneath this dialog — an undo, say.
        A field that reports a value set from code has its signal blocked,
        or restoring a value would write it out again as a fresh edit.
        """
        header = self._document.plan.header
        with ExitStack() as blockers:
            for widget in (
                self._font,
                self._case,
                self._min_ratio,
                self._condense,
                self._source_language,
                self._target_language,
                self._direction,
            ):
                blockers.enter_context(QSignalBlocker(widget))
            # The comic's lines and the year are not among them: they answer
            # only to typing, and setting their text is not typing.
            self._font.set_value(header.font)
            self._case.setCurrentIndex(self._case.findData(header.case))
            self._min_ratio.setValue(header.font_size_min_ratio)
            self._condense.setValue(header.condense_min)
            self._source_language.set_value(header.source_language)
            self._target_language.set_value(header.target_language)
            for field, line in self._details.items():
                line.setText(str(getattr(header, field)))
            self._year.set_value(header.year)
            self._show_direction(header.reading_direction)
        self._font_reach.setText(self.font_reach())


__all__ = ["HeaderDialog", "YearBox"]
