"""The review window: pages on the left, the page on the canvas, one region's
fields on the right.

This is the only module that knows all three of :class:`PlanDocument`,
:class:`PageCanvas` and :class:`RegionInspector` at once. Each of them knows
only what it needs to do its own job, and reports back through a signal —
which is what makes an edit's ripple effects (the window title's dirty
marker, the page list's flag count, another region's overlap flag) something
this module handles in one place instead of three widgets each half-guessing
at the others' state.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QMainWindow,
    QMessageBox,
)

from ..errors import ComictransError
from ..imaging import load_page
from ..model import Geometry, Region
from .canvas import COLOR_APPROXIMATE, COLOR_EXACT, PageCanvas, RegionAppearance
from .document import PlanDocument
from .inspector import RegionInspector
from .page_list import PageList
from .preview import render_preview
from .qimage import to_pixmap

log = logging.getLogger(__name__)


def _appearance_for(region: Region, document: PlanDocument) -> RegionAppearance:
    color = COLOR_EXACT if region.geometry is Geometry.EXACT else COLOR_APPROXIMATE
    return RegionAppearance(
        region_id=region.id,
        polygon=region.polygon,
        color=color,
        flagged=document.flags(region.id).any,
    )


class MainWindow(QMainWindow):
    def __init__(self, initial_plan: Path | None = None) -> None:
        super().__init__()
        self.document: PlanDocument | None = None
        self._current_image: str | None = None
        self._showing_preview = False

        self._pages = PageList()
        self._canvas = PageCanvas()
        self._inspector = RegionInspector()

        self.setCentralWidget(self._canvas)
        pages_dock = QDockWidget("Pages", self)
        pages_dock.setWidget(self._pages)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, pages_dock)
        inspector_dock = QDockWidget("Region", self)
        inspector_dock.setWidget(self._inspector)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, inspector_dock)

        self._pages.image_selected.connect(self._on_image_selected)
        self._canvas.region_selected.connect(self._on_region_selected)
        self._inspector.edited.connect(self._on_edited)

        self._build_menus()
        self._update_actions_enabled()
        self._update_title()
        self.resize(1200, 800)
        self.statusBar().showMessage("Open a plan file to begin (File > Open Plan…)")

        if initial_plan is not None:
            self.open_plan(initial_plan)

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        self._open_action = QAction("&Open Plan…", self)
        self._open_action.setShortcut(QKeySequence.StandardKey.Open)
        self._open_action.triggered.connect(self.open_plan_dialog)
        file_menu.addAction(self._open_action)

        self._reload_action = QAction("&Reload", self)
        self._reload_action.triggered.connect(self._on_reload)
        file_menu.addAction(self._reload_action)

        file_menu.addSeparator()

        self._save_action = QAction("&Save", self)
        self._save_action.setShortcut(QKeySequence.StandardKey.Save)
        self._save_action.triggered.connect(self._on_save)
        file_menu.addAction(self._save_action)

        self._save_as_action = QAction("Save &As…", self)
        self._save_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        self._save_as_action.triggered.connect(self._on_save_as)
        file_menu.addAction(self._save_as_action)

        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = self.menuBar().addMenu("&View")
        self._preview_action = QAction("&Render Preview", self)
        self._preview_action.setShortcut(QKeySequence("Ctrl+R"))
        self._preview_action.triggered.connect(self._on_render_preview)
        view_menu.addAction(self._preview_action)

        self._overlay_action = QAction("Back to &Overlay", self)
        self._overlay_action.triggered.connect(self._on_back_to_overlay)
        view_menu.addAction(self._overlay_action)

    def _update_actions_enabled(self) -> None:
        has_document = self.document is not None
        for action in (self._save_action, self._save_as_action, self._reload_action):
            action.setEnabled(has_document)
        has_image = has_document and self._current_image is not None
        self._preview_action.setEnabled(has_image)
        self._overlay_action.setEnabled(has_image and self._showing_preview)

    def _update_title(self) -> None:
        if self.document is None:
            self.setWindowTitle("comictrans review")
            return
        star = "*" if self.document.dirty else ""
        self.setWindowTitle(f"{self.document.path.name}{star} — comictrans review")

    # -- opening, saving -----------------------------------------------

    def _confirm_discard_if_dirty(self) -> bool:
        """True if it is safe to proceed: nothing unsaved, or the user said so."""
        if self.document is None or not self.document.dirty:
            return True
        choice = QMessageBox.question(
            self,
            "Unsaved changes",
            f"{self.document.path.name} has unsaved changes. Discard them?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return choice == QMessageBox.StandardButton.Discard

    def open_plan(self, path: Path) -> None:
        if not self._confirm_discard_if_dirty():
            return
        try:
            document = PlanDocument.open(path)
        except ComictransError as exc:
            QMessageBox.critical(self, "Could not open plan", str(exc))
            return

        self.document = document
        self._current_image = None
        self._showing_preview = False
        self._pages.set_document(document)
        self._inspector.set_region(None, None)
        self._canvas.show_page(to_pixmap(Image.new("RGB", (1, 1))))
        self._update_actions_enabled()
        self._update_title()

        images = document.images()
        if images:
            self._pages.select_image(images[0])
        else:
            self.statusBar().showMessage(f"{path.name}: no regions in this plan")

    def open_plan_dialog(self) -> None:
        """Ask for a plan file and open it. Does nothing if the user cancels.

        Public because ``gui.app`` calls it too: ``review`` with no plan
        argument opens this rather than an empty window. It cannot be done
        from the constructor — a modal dialog opened there would block every
        test that builds a bare window, with nothing to dismiss it.
        """
        name, _filter = QFileDialog.getOpenFileName(
            self, "Open Plan", "", "Plan files (*.yaml *.yml);;All files (*)"
        )
        if name:
            self.open_plan(Path(name))

    def _on_reload(self) -> None:
        if self.document is not None:
            self.open_plan(self.document.path)

    def _on_save(self) -> None:
        if self.document is None:
            return
        try:
            self.document.save()
        except ComictransError as exc:
            QMessageBox.critical(self, "Could not save", str(exc))
            return
        self._update_title()
        self.statusBar().showMessage(f"saved {self.document.path}", 5000)

    def _on_save_as(self) -> None:
        if self.document is None:
            return
        name, _filter = QFileDialog.getSaveFileName(
            self, "Save Plan As", str(self.document.path), "Plan files (*.yaml *.yml)"
        )
        if not name:
            return
        target = Path(name)
        try:
            self.document.save_as(target)
        except ComictransError:
            overwrite = QMessageBox.question(
                self,
                "File exists",
                f"{target.name} already exists. Overwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if overwrite != QMessageBox.StandardButton.Yes:
                return
            try:
                self.document.save_as(target, force=True)
            except ComictransError as exc:
                QMessageBox.critical(self, "Could not save", str(exc))
                return
        self._pages.set_document(self.document)
        if self._current_image is not None:
            self._pages.select_image(self._current_image)
        self._update_title()
        self.statusBar().showMessage(f"saved {target}", 5000)

    # -- viewing ---------------------------------------------------------

    def _on_image_selected(self, image: str) -> None:
        if self.document is None:
            return
        self._current_image = image
        self._showing_preview = False
        try:
            page = load_page(self.document.source_path(image))
        except ComictransError as exc:
            QMessageBox.critical(self, "Could not read image", str(exc))
            return

        regions = self.document.regions_for(image)
        appearances = [_appearance_for(region, self.document) for region in regions]
        self._canvas.show_page(to_pixmap(Image.fromarray(page.rgb)), appearances)
        self._inspector.set_region(None, None)
        self._update_actions_enabled()
        summary = self.document.summary(image)
        self.statusBar().showMessage(
            f"{image}: {summary.region_count} region(s), {summary.flagged_count} flagged"
        )
        if regions:
            self._on_region_selected(regions[0].id)

    def _on_region_selected(self, region_id: str) -> None:
        self._canvas.set_selected(region_id)
        self._inspector.set_region(self.document, region_id)

    def _on_edited(self) -> None:
        if self.document is None or self._current_image is None:
            return
        self._update_title()
        self._pages.refresh_row(self.document, self._current_image)
        # An edit to one region (skip, translation) can change whether it, or
        # another region on the same page, still counts as overlapping —
        # restyle every region rather than track exactly which ones moved.
        if not self._showing_preview:
            for region in self.document.regions_for(self._current_image):
                self._canvas.set_appearance(_appearance_for(region, self.document))

    def _on_render_preview(self) -> None:
        if self.document is None or self._current_image is None:
            return
        try:
            preview = render_preview(self.document, self._current_image)
        except ComictransError as exc:
            QMessageBox.critical(self, "Could not render preview", str(exc))
            return
        self._canvas.show_page(to_pixmap(preview.image))
        self._showing_preview = True
        self._update_actions_enabled()
        if preview.problems:
            names = ", ".join(o.region_id for o in preview.problems)
            self.statusBar().showMessage(f"preview: {len(preview.problems)} problem(s) — {names}")
        else:
            self.statusBar().showMessage("preview: everything fits")

    def _on_back_to_overlay(self) -> None:
        if self._current_image is not None:
            self._on_image_selected(self._current_image)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        if self._confirm_discard_if_dirty():
            event.accept()
        else:
            event.ignore()


__all__ = ["MainWindow"]
