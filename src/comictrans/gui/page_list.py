"""The page list: one row per source image the plan refers to."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QWidget

from .document import ImageSummary, PlanDocument

_IMAGE_ROLE = Qt.ItemDataRole.UserRole


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

    def row_text(self, summary: ImageSummary) -> str:
        """The label for one row.

        A method rather than the plain function it was: the count inflects
        the noun beside it, and the only thing that inflects a noun for a
        count is ``tr``, which needs a ``QObject`` to be called on. Measured
        — ``QCoreApplication.translate`` is extracted without its plural
        forms and dropped outright when the count is an attribute rather than
        a bare name; see ``resources/translations/recompile.py``.

        Two whole sentences for the same reason, rather than a tally with a
        clause stuck on the end.
        """
        if summary.flagged_count:
            return self.tr("{0}  —  %n region(s), {1} flagged", None, summary.region_count).format(
                summary.image, summary.flagged_count
            )
        return self.tr("{0}  —  %n region(s)", None, summary.region_count).format(summary.image)

    def set_document(self, document: PlanDocument | None) -> None:
        self.blockSignals(True)
        try:
            self.clear()
            if document is not None:
                for image in document.images():
                    item = QListWidgetItem(self.row_text(document.summary(image)))
                    item.setData(_IMAGE_ROLE, image)
                    self.addItem(item)
        finally:
            self.blockSignals(False)

    def refresh_row(self, document: PlanDocument, image: str) -> None:
        """Update one row's label after an edit, without disturbing selection."""
        for index in range(self.count()):
            item = self.item(index)
            if item.data(_IMAGE_ROLE) == image:
                item.setText(self.row_text(document.summary(image)))
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
