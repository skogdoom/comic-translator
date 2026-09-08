"""The plan header: the settings every region in the file is drawn under.

Split the way the header itself is: what you decide, and what the tool
recorded. Font, case, the two fit limits and the language pair are yours to
change. ``generator``, ``created``, ``ocr_engine`` and ``version`` describe
what produced this plan, and are shown but not editable — you are not the
authority on which OCR engine ran, and a plan that claims a different one is
a plan that lies about where its text came from.

Fields write straight through to the document as they are edited, the same
as the region inspector, so there is nothing to apply and nothing to cancel.
Ctrl+Z after closing takes a change back, one field at a time.
"""

from __future__ import annotations

from contextlib import ExitStack

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ..model import TextCase
from ..planfile.schema import CONDENSE_MIN_RANGE, FONT_SIZE_MIN_RATIO_RANGE
from .document import PlanDocument
from .font_box import FontBox

_RATIO_DECIMALS = 4
_CONDENSE_DECIMALS = 2


def font_reach(document: PlanDocument) -> str:
    """How much of the plan the header font actually decides."""
    following = document.regions_using_header_font()
    total = len(document.plan.regions)
    if total and following == total:
        return f"used by all {total} regions"
    return f"used by {following} of {total} regions; the rest override it"


class HeaderDialog(QDialog):
    edited = Signal()

    def __init__(self, document: PlanDocument, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._document = document
        self.setWindowTitle("Plan Header")

        header = document.plan.header

        # allow_default=False: every region without an override falls back
        # to the header font, so it has nothing to fall back to itself.
        self._font = FontBox(allow_default=False)
        self._font.set_value(header.font)
        self._font_reach = QLabel(font_reach(document))

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

        self._source_language = QLineEdit(header.source_language)
        self._target_language = QLineEdit(header.target_language)

        form = QFormLayout()
        form.addRow("font", self._font)
        form.addRow("", self._font_reach)
        form.addRow("case", self._case)
        form.addRow("smallest text", self._min_ratio)
        form.addRow("condensing floor", self._condense)
        form.addRow("source language", self._source_language)
        form.addRow("target language", self._target_language)

        recorded = QFormLayout()
        recorded.addRow("written by", QLabel(header.generator))
        recorded.addRow("created", QLabel(header.created))
        recorded.addRow("OCR engine", QLabel(header.ocr_engine))
        recorded.addRow("plan version", QLabel(str(header.version)))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addSpacing(8)
        layout.addWidget(QLabel("recorded when this plan was written"))
        layout.addLayout(recorded)
        layout.addWidget(buttons)
        self.setMinimumWidth(420)

        self._font.currentTextChanged.connect(self._on_font_changed)
        self._case.currentIndexChanged.connect(self._on_case_changed)
        self._min_ratio.valueChanged.connect(self._on_min_ratio_changed)
        self._condense.valueChanged.connect(self._on_condense_changed)
        self._source_language.textChanged.connect(self._on_source_language_changed)
        self._target_language.textChanged.connect(self._on_target_language_changed)

    def _commit(self) -> None:
        self._font_reach.setText(font_reach(self._document))
        self.edited.emit()

    def _on_font_changed(self, text: str) -> None:
        # An empty font is not a legal header value, so a field being cleared
        # on the way to a new name writes nothing rather than raising.
        if text.strip():
            self._document.set_header_font(text)
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

    def _on_source_language_changed(self, text: str) -> None:
        if text.strip():
            self._document.set_header_source_language(text)
            self._commit()

    def _on_target_language_changed(self, text: str) -> None:
        if text.strip():
            self._document.set_header_target_language(text)
            self._commit()

    def repopulate(self) -> None:
        """Show the header as it now stands, without writing anything back.

        Needed if the plan changes underneath this dialog — an undo, say.
        Every field's signal is blocked, or restoring a value would write it
        out again as a fresh edit.
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
            ):
                blockers.enter_context(QSignalBlocker(widget))
            self._font.set_value(header.font)
            self._case.setCurrentIndex(self._case.findData(header.case))
            self._min_ratio.setValue(header.font_size_min_ratio)
            self._condense.setValue(header.condense_min)
            self._source_language.setText(header.source_language)
            self._target_language.setText(header.target_language)
        self._font_reach.setText(font_reach(self._document))


__all__ = ["HeaderDialog", "font_reach"]
