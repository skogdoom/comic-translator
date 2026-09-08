"""The page list: one row per source image the plan refers to."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QWidget

from .document import ImageSummary, PlanDocument

_IMAGE_ROLE = Qt.ItemDataRole.UserRole


def row_text(summary: ImageSummary) -> str:
    """The label for one row, kept as a plain function so it is testable
    without building a widget."""
    regions = f"{summary.region_count} region{'' if summary.region_count == 1 else 's'}"
    flags = f", {summary.flagged_count} flagged" if summary.flagged_count else ""
    return f"{summary.image}  —  {regions}{flags}"


class PageList(QListWidget):
    image_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.currentItemChanged.connect(self._on_current_changed)

    def set_document(self, document: PlanDocument | None) -> None:
        self.blockSignals(True)
        try:
            self.clear()
            if document is not None:
                for image in document.images():
                    item = QListWidgetItem(row_text(document.summary(image)))
                    item.setData(_IMAGE_ROLE, image)
                    self.addItem(item)
        finally:
            self.blockSignals(False)

    def refresh_row(self, document: PlanDocument, image: str) -> None:
        """Update one row's label after an edit, without disturbing selection."""
        for index in range(self.count()):
            item = self.item(index)
            if item.data(_IMAGE_ROLE) == image:
                item.setText(row_text(document.summary(image)))
                return

    def select_image(self, image: str) -> None:
        for index in range(self.count()):
            item = self.item(index)
            if item.data(_IMAGE_ROLE) == image:
                self.setCurrentItem(item)
                return

    def _on_current_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is not None:
            self.image_selected.emit(str(current.data(_IMAGE_ROLE)))
