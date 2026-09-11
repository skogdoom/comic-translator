"""The page list: one row per source image the plan refers to."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDropEvent
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

    order_changed = Signal(list)
    """The page names as they now stand, after a row was dragged."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Rows move within this list and nowhere else: a page cannot be
        # dragged in from outside, because the plan's pages are the files
        # extract found and this is about their order, not their membership.
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.currentItemChanged.connect(self._on_current_changed)

    def current_order(self) -> tuple[str, ...]:
        """The page names in the order the rows are in now."""
        return tuple(str(self.item(index).data(_IMAGE_ROLE)) for index in range(self.count()))

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt override
        """Report the new order once the rows have actually moved.

        After ``super()``, not before: the base class is what takes the row
        out and puts it back, so asking any earlier would report the order
        that is about to stop being true. A drop that lands where it started
        still arrives here, and is turned away by the document rather than
        by a guess made from the event.
        """
        super().dropEvent(event)
        self.order_changed.emit(list(self.current_order()))

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
