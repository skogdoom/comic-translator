"""The dock a render runs in: a progress bar, then what needs a second look.

A panel rather than a modal, for two reasons. A modal progress dialog would
stop you reading the plan while a chapter renders, which is the one thing
there is to do while waiting for it. And the report it leaves behind is a
list of regions to go and look at — a thing to work through, not a thing to
dismiss — so its rows select the region they name, and the window follows.

What counts as worth a second look is decided in ``render_report``, which
needs no Qt and is tested on its own. This module only draws it.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..apply import ApplyReport
from .render_report import RenderRow, counts, headline, rows_for

_IMAGE, _REGION, _PROBLEM, _DETAIL = range(4)

NOTHING_TO_CHECK = "Nothing needs a second look."
IDLE = "No pages rendered yet."


class RenderPanel(QWidget):
    """Progress while a run is going, and its report once it is done."""

    cancel_requested = Signal()

    region_activated = Signal(str, str)
    """``(image, region_id)`` — a row was chosen and wants selecting."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._headline = QLabel(IDLE)
        self._headline.setWordWrap(True)
        self._counts = QLabel()
        self._counts.setWordWrap(True)
        palette = self._counts.palette()
        palette.setColor(
            QPalette.ColorRole.WindowText,
            palette.color(QPalette.ColorRole.PlaceholderText),
        )
        self._counts.setPalette(palette)

        self._progress = QProgressBar()
        self._progress.setTextVisible(True)
        self._cancel = QPushButton("Cancel")
        self._cancel.clicked.connect(self._on_cancel)
        running = QHBoxLayout()
        running.setContentsMargins(0, 0, 0, 0)
        running.addWidget(self._progress, 1)
        running.addWidget(self._cancel)
        self._running = QWidget()
        self._running.setLayout(running)
        self._running.hide()

        self._rows = QTreeWidget()
        self._rows.setColumnCount(4)
        self._rows.setHeaderLabels(["page", "region", "problem", "detail"])
        self._rows.setRootIsDecorated(False)
        self._rows.setUniformRowHeights(True)
        self._rows.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._rows.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        # Single-click, not double: every row here is a place to go, and the
        # panel exists to be worked through one row at a time.
        self._rows.itemSelectionChanged.connect(self._on_row_chosen)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self._headline)
        layout.addWidget(self._counts)
        layout.addWidget(self._running)
        layout.addWidget(self._rows, 1)

    # -- while it runs ---------------------------------------------------

    def start(self, total: int, output: Path) -> None:
        self._rows.clear()
        self._headline.setText(f"Rendering {total} page{'' if total == 1 else 's'} to {output}")
        self._counts.setText("")
        self._progress.setRange(0, total)
        self._progress.setValue(0)
        self._progress.setFormat("starting…")
        self._cancel.setEnabled(True)
        self._cancel.setText("Cancel")
        self._running.show()

    def advance(self, index: int, total: int, image: str) -> None:
        """One page is starting. ``index`` counts from zero."""
        self._progress.setRange(0, total)
        self._progress.setValue(index)
        self._progress.setFormat(f"{image}  ({index + 1} of {total})")

    def _on_cancel(self) -> None:
        # Disabled rather than left live: cancelling takes effect after the
        # page in flight, and a button that still looks pressable invites
        # the assumption that the first press did not land.
        self._cancel.setEnabled(False)
        self._cancel.setText("Cancelling…")
        self.cancel_requested.emit()

    # -- once it is done -------------------------------------------------

    def show_report(self, report: ApplyReport, output: Path) -> None:
        self._running.hide()
        self._headline.setText(headline(report, output))
        self._counts.setText(counts(report))
        self._fill(rows_for(report))

    def show_failure(self, message: str) -> None:
        self._running.hide()
        self._headline.setText(f"Could not render: {message}")
        self._counts.setText("")
        self._rows.clear()

    def _fill(self, rows: tuple[RenderRow, ...]) -> None:
        self._rows.clear()
        if not rows:
            self._counts.setText(f"{self._counts.text()}\n{NOTHING_TO_CHECK}")
            return
        for row in rows:
            item = QTreeWidgetItem([row.image, row.region_id or "—", row.problem, row.detail])
            if row.selectable:
                item.setData(_IMAGE, Qt.ItemDataRole.UserRole, (row.image, row.region_id))
            else:
                for column in (_IMAGE, _REGION, _PROBLEM, _DETAIL):
                    item.setToolTip(column, row.detail)
            self._rows.addTopLevelItem(item)

    def _on_row_chosen(self) -> None:
        items = self._rows.selectedItems()
        if not items:
            return
        target = items[0].data(_IMAGE, Qt.ItemDataRole.UserRole)
        if target is not None:
            image, region_id = target
            self.region_activated.emit(image, region_id)

    # -- for the window and the tests ------------------------------------

    def row_count(self) -> int:
        return self._rows.topLevelItemCount()

    def select_row(self, index: int) -> None:
        """Choose a row as a click would, which emits ``region_activated``."""
        item = self._rows.topLevelItem(index)
        if item is not None:
            self._rows.setCurrentItem(item)


__all__ = ["IDLE", "NOTHING_TO_CHECK", "RenderPanel"]
