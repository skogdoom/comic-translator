"""The region inspector: what one region says, and the fields that edit it.

Every field writes straight through to the :class:`PlanDocument` as it is
edited — there is no separate "apply" step — because the document is already
the one source of truth the rest of the window reads from. ``edited`` is
emitted after every write so the window can refresh whatever depends on it
(the window title's dirty marker, the page list's flag counts, the canvas's
outline for this region) without the inspector needing to know about any of
those things itself.
"""

from __future__ import annotations

from contextlib import ExitStack

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..model import Region
from .document import PlanDocument, RegionFlags

_FONT_SIZE_AUTO = 0
"""The spin box's special value for "no override", shown as the word "auto"."""


def _flags_text(flags: RegionFlags) -> str:
    labels = {
        "approximate": "approximate geometry",
        "low_confidence": "low confidence",
        "held_back": "held back (no translation)",
        "unedited": "same as source",
        "overlapping": "overlaps another region",
        "skipped": "skipped",
    }
    active = [text for field, text in labels.items() if getattr(flags, field)]
    return ", ".join(active) if active else "nothing flagged"


class RegionInspector(QWidget):
    edited = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._document: PlanDocument | None = None
        self._region_id: str | None = None

        self._id_label = QLabel("—")
        self._flags_label = QLabel("—")
        self._flags_label.setWordWrap(True)
        self._source_text = QPlainTextEdit()
        self._source_text.setReadOnly(True)
        self._source_text.setMaximumHeight(100)
        self._translation = QPlainTextEdit()
        self._translation.setMaximumHeight(100)
        self._notes = QLineEdit()
        self._skip = QCheckBox("skip: leave this region untouched")
        self._font = QLineEdit()
        self._font.setPlaceholderText("(plan default)")
        self._font_size = QSpinBox()
        self._font_size.setRange(_FONT_SIZE_AUTO, 999)
        self._font_size.setSpecialValueText("auto")

        form = QFormLayout()
        form.addRow("region", self._id_label)
        form.addRow("flags", self._flags_label)
        form.addRow("source text", self._source_text)
        form.addRow("translation", self._translation)
        form.addRow("notes", self._notes)
        form.addRow("", self._skip)
        form.addRow("font override", self._font)
        form.addRow("font size", self._font_size)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addStretch(1)

        self._translation.textChanged.connect(self._on_translation_changed)
        self._notes.editingFinished.connect(self._on_notes_changed)
        self._skip.toggled.connect(self._on_skip_changed)
        self._font.editingFinished.connect(self._on_font_changed)
        self._font_size.valueChanged.connect(self._on_font_size_changed)

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
            for widget in (
                self._translation,
                self._notes,
                self._skip,
                self._font,
                self._font_size,
            ):
                blockers.enter_context(QSignalBlocker(widget))
            self._populate(document, region)

        enabled = region is not None
        for widget in (
            self._translation,
            self._notes,
            self._skip,
            self._font,
            self._font_size,
        ):
            widget.setEnabled(enabled)

    def _populate(self, document: PlanDocument | None, region: Region | None) -> None:
        if document is None or region is None:
            self._id_label.setText("—")
            self._flags_label.setText("—")
            self._source_text.setPlainText("")
            self._translation.setPlainText("")
            self._notes.setText("")
            self._skip.setChecked(False)
            self._font.setText("")
            self._font_size.setValue(_FONT_SIZE_AUTO)
            return

        self._id_label.setText(f"{region.id}  ({region.geometry.value}, order {region.order})")
        self._flags_label.setText(_flags_text(document.flags(region.id)))
        self._source_text.setPlainText(region.source_text)
        self._translation.setPlainText(region.translation)
        self._notes.setText(region.notes)
        self._skip.setChecked(region.skip)
        self._font.setText(region.font or "")
        self._font_size.setValue(region.font_size or _FONT_SIZE_AUTO)

    def _commit(self) -> None:
        if self._document is not None and self._region_id is not None:
            # Flags may have changed (e.g. translation is no longer empty),
            # so the label needs refreshing even though nothing else does.
            self._flags_label.setText(_flags_text(self._document.flags(self._region_id)))
        self.edited.emit()

    def _on_translation_changed(self) -> None:
        if self._document is not None and self._region_id is not None:
            self._document.set_translation(self._region_id, self._translation.toPlainText())
        self._commit()

    def _on_notes_changed(self) -> None:
        if self._document is not None and self._region_id is not None:
            self._document.set_notes(self._region_id, self._notes.text())
        self._commit()

    def _on_skip_changed(self, checked: bool) -> None:
        if self._document is not None and self._region_id is not None:
            self._document.set_skip(self._region_id, checked)
        self._commit()

    def _on_font_changed(self) -> None:
        if self._document is not None and self._region_id is not None:
            text = self._font.text().strip()
            self._document.set_font(self._region_id, text or None)
        self._commit()

    def _on_font_size_changed(self, value: int) -> None:
        if self._document is not None and self._region_id is not None:
            self._document.set_font_size(self._region_id, value or None)
        self._commit()
