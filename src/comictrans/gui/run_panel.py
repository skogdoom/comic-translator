"""The dock a pipeline pass runs in: a progress bar, then what needs a look.

A panel rather than a modal, for two reasons. A modal progress dialog would
stop you reading the plan while a chapter renders, which is the one thing
there is to do while waiting for it. And the report it leaves behind is a
list of pages and regions to go and look at — a thing to work through, not a
thing to dismiss — so its rows select what they name, and the window follows.

One dock for both passes, because the two are never both current: an extract
ends by opening the plan it wrote, at which point the last render's report
describes a plan that is no longer open. What counts as worth a second look
is decided in ``run_report``, which owns no widgets and is tested on its
own. This module only draws it.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt, Signal
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
from ..extract import ExtractReport
from .run_report import (
    RunRow,
    extract_counts,
    extract_headline,
    extract_rows,
    render_counts,
    render_headline,
    render_rows,
)

_IMAGE, _REGION, _PROBLEM, _DETAIL = range(4)

NOTHING_TO_CHECK = QCoreApplication.translate("RunPanel", "Nothing needs a second look.")
IDLE = QCoreApplication.translate("RunPanel", "Nothing has been run yet.")


class RunPanel(QWidget):
    """Progress while a pass is going, and its report once it is done."""

    cancel_requested = Signal()

    row_activated = Signal(str, str)
    """``(image, region_id)`` — a row was chosen and wants going to. The
    region id is empty for a row that names only a page."""

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
        self._cancel = QPushButton(self.tr("Cancel"))
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
        self._rows.setHeaderLabels(
            [self.tr("page"), self.tr("region"), self.tr("problem"), self.tr("detail")]
        )
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

    def start_render(self, total: int, where: Path) -> None:
        """A render is starting, writing its pages to ``where``."""
        self._start(
            self.tr("Rendering %n page(s) — {0}", None, total).format(where),
            total,
        )

    def start_extract(self, total: int, where: Path) -> None:
        """An extract is starting, writing its plan to ``where``."""
        self._start(
            self.tr("Reading %n page(s) — {0}", None, total).format(where),
            total,
        )

    def _start(self, headline: str, total: int) -> None:
        """One whole sentence, not a verb and a tail.

        The two passes read the same in English but for one word, which is
        exactly the shape that does not survive translation: a language that
        inflects the noun for the verb, or puts the count last, cannot be
        served by a sentence assembled here out of pieces chosen elsewhere.
        """
        self._rows.clear()
        self._headline.setText(headline)
        self._counts.setText("")
        self._progress.setRange(0, total)
        self._progress.setValue(0)
        self._progress.setFormat(self.tr("starting…"))
        self._cancel.setEnabled(True)
        self._cancel.setText(self.tr("Cancel"))
        self._running.show()

    def advance(self, index: int, total: int, image: str) -> None:
        """One page is starting. ``index`` counts from zero."""
        self._progress.setRange(0, total)
        self._progress.setValue(index)
        self._progress.setFormat(self.tr("{0}  ({1} of {2})").format(image, index + 1, total))

    def _on_cancel(self) -> None:
        # Disabled rather than left live: cancelling takes effect after the
        # page in flight, and a button that still looks pressable invites
        # the assumption that the first press did not land.
        self._cancel.setEnabled(False)
        self._cancel.setText(self.tr("Cancelling…"))
        self.cancel_requested.emit()

    # -- once it is done -------------------------------------------------

    def show_render(self, report: ApplyReport, output: Path) -> None:
        self._show(render_headline(report, output), render_counts(report), render_rows(report))

    def show_extract(self, report: ExtractReport, plan_path: Path) -> None:
        self._show(
            extract_headline(report, plan_path),
            extract_counts(report),
            extract_rows(report),
        )

    def show_failure(self, message: str) -> None:
        self._running.hide()
        self._headline.setText(self.tr("Could not run: {0}").format(message))
        self._counts.setText("")
        self._rows.clear()

    def _show(self, headline: str, counts: str, rows: tuple[RunRow, ...]) -> None:
        self._running.hide()
        self._headline.setText(headline)
        self._counts.setText(counts if rows else f"{counts}\n{NOTHING_TO_CHECK}")
        self._fill(rows)

    def _fill(self, rows: tuple[RunRow, ...]) -> None:
        self._rows.clear()
        for row in rows:
            item = QTreeWidgetItem([row.image, row.region_id or "—", row.problem, row.detail])
            if row.selectable:
                item.setData(_IMAGE, Qt.ItemDataRole.UserRole, (row.image, row.region_id or ""))
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
            self.row_activated.emit(image, region_id)

    # -- for the window and the tests ------------------------------------

    def row_count(self) -> int:
        return self._rows.topLevelItemCount()

    def select_row(self, index: int) -> None:
        """Choose a row as a click would, which emits ``row_activated``."""
        item = self._rows.topLevelItem(index)
        if item is not None:
            self._rows.setCurrentItem(item)


__all__ = ["IDLE", "NOTHING_TO_CHECK", "RunPanel"]
