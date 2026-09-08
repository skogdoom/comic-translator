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
from PySide6.QtCore import QByteArray, QSettings, Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QToolBar,
)

from ..errors import ComictransError
from ..imaging import load_page
from ..model import Geometry, Region
from .about_dialog import AboutDialog
from .canvas import COLOR_APPROXIMATE, COLOR_EXACT, PageCanvas, RegionAppearance
from .document import PlanDocument
from .inspector import RegionInspector
from .page_list import PageList
from .preview import render_preview
from .qimage import to_pixmap

log = logging.getLogger(__name__)

_GEOMETRY_KEY = "window/geometry"
_STATE_KEY = "window/state"
"""Where the dock and toolbar layout is remembered between sessions.

``QMainWindow.saveState`` identifies each dock and toolbar by its
``objectName``, so every one of them is given a stable one below. Without
that the state saves as unrestorable and Qt warns about it at runtime.
"""


def _appearance_for(region: Region, document: PlanDocument) -> RegionAppearance:
    color = COLOR_EXACT if region.geometry is Geometry.EXACT else COLOR_APPROXIMATE
    return RegionAppearance(
        region_id=region.id,
        polygon=region.polygon,
        color=color,
        flagged=document.flags(region.id).any,
    )


class MainWindow(QMainWindow):
    def __init__(
        self, initial_plan: Path | None = None, *, settings: QSettings | None = None
    ) -> None:
        """``settings`` opts into remembering the layout between sessions.

        Left out, nothing is read or written: a window built without it — as
        every test builds one — starts from the same default layout every
        time and cannot leak state into the next one, or into whoever is
        running the suite.
        """
        super().__init__()
        self.document: PlanDocument | None = None
        self._current_image: str | None = None
        self._current_region: str | None = None
        self._showing_preview = False
        self._settings = settings

        self._pages = PageList()
        self._canvas = PageCanvas()
        self._inspector = RegionInspector()

        self.setCentralWidget(self._canvas)
        self._pages_dock = QDockWidget("Pages", self)
        self._pages_dock.setObjectName("pages_dock")
        self._pages_dock.setWidget(self._pages)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._pages_dock)
        self._inspector_dock = QDockWidget("Region", self)
        self._inspector_dock.setObjectName("inspector_dock")
        self._inspector_dock.setWidget(self._inspector)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._inspector_dock)

        self._pages.image_selected.connect(self._on_image_selected)
        self._canvas.region_selected.connect(self._on_region_selected)
        self._inspector.edited.connect(self._on_edited)

        self._build_menus()
        self._build_toolbar()
        self._update_actions_enabled()
        self._update_title()
        self.resize(1200, 800)
        self.statusBar().showMessage("Open a plan file to begin (File > Open Plan…)")

        # Captured before anything saved is restored, so Reset Layout has
        # something to go back to that no earlier session can have moved.
        self._default_state = self.saveState()
        self._restore_layout()

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

        # One undo history, covering everything. The text fields' own
        # histories are switched off in the inspector rather than left to
        # compete: every keystroke is already a document edit, so a second
        # per-widget stack would be an invisible one that disagrees with the
        # visible one about what the last change was.
        edit_menu = self.menuBar().addMenu("&Edit")
        self._undo_action = QAction("&Undo", self)
        self._undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self._undo_action.triggered.connect(self._on_undo)
        edit_menu.addAction(self._undo_action)

        self._redo_action = QAction("&Redo", self)
        self._redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        self._redo_action.triggered.connect(self._on_redo)
        edit_menu.addAction(self._redo_action)

        view_menu = self.menuBar().addMenu("&View")
        self._preview_action = QAction("&Render Preview", self)
        self._preview_action.setShortcut(QKeySequence("Ctrl+R"))
        self._preview_action.triggered.connect(self._on_render_preview)
        view_menu.addAction(self._preview_action)

        self._overlay_action = QAction("Back to &Overlay", self)
        self._overlay_action.triggered.connect(self._on_back_to_overlay)
        view_menu.addAction(self._overlay_action)

        view_menu.addSeparator()

        # Ctrl+Up/Down rather than a bare key: the inspector's text fields
        # hold the focus for most of a review session and would swallow
        # anything unmodified. The cost is shadowing "jump to the start/end
        # of the field", which is a small loss in boxes this short.
        self._previous_region_action = QAction("&Previous Region", self)
        self._previous_region_action.setShortcut(QKeySequence("Ctrl+Up"))
        self._previous_region_action.triggered.connect(self._on_previous_region)
        view_menu.addAction(self._previous_region_action)

        self._next_region_action = QAction("&Next Region", self)
        self._next_region_action.setShortcut(QKeySequence("Ctrl+Down"))
        self._next_region_action.triggered.connect(self._on_next_region)
        view_menu.addAction(self._next_region_action)

        self._next_flagged_action = QAction("Next &Flagged Region", self)
        self._next_flagged_action.setShortcut(QKeySequence("Ctrl+Shift+Down"))
        self._next_flagged_action.triggered.connect(self._on_next_flagged_region)
        view_menu.addAction(self._next_flagged_action)

        window_menu = self.menuBar().addMenu("&Window")
        window_menu.addAction(self._pages_dock.toggleViewAction())
        window_menu.addAction(self._inspector_dock.toggleViewAction())
        window_menu.addSeparator()
        self._reset_layout_action = QAction("&Reset Layout", self)
        self._reset_layout_action.triggered.connect(self._on_reset_layout)
        window_menu.addAction(self._reset_layout_action)

        help_menu = self.menuBar().addMenu("&Help")
        self._about_action = QAction("&About comictrans review", self)
        self._about_action.triggered.connect(self._on_about)
        help_menu.addAction(self._about_action)

    def _build_toolbar(self) -> None:
        """The same actions the menus hold, not a second set of them.

        Text rather than icons: half of these have no standard pixmap in any
        Qt style, and a toolbar of four icons and three words reads worse
        than seven words.
        """
        self._toolbar = QToolBar("Main", self)
        self._toolbar.setObjectName("main_toolbar")
        self._toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(self._toolbar)

        self._toolbar.addAction(self._open_action)
        self._toolbar.addAction(self._save_action)
        self._toolbar.addSeparator()
        self._toolbar.addAction(self._undo_action)
        self._toolbar.addAction(self._redo_action)
        self._toolbar.addSeparator()
        self._toolbar.addAction(self._previous_region_action)
        self._toolbar.addAction(self._next_region_action)
        self._toolbar.addAction(self._next_flagged_action)
        self._toolbar.addSeparator()
        self._toolbar.addAction(self._preview_action)
        self._toolbar.addAction(self._overlay_action)

    # -- layout ----------------------------------------------------------

    def _restore_layout(self) -> None:
        if self._settings is None:
            return
        geometry = self._settings.value(_GEOMETRY_KEY)
        state = self._settings.value(_STATE_KEY)
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)
        if isinstance(state, QByteArray):
            self.restoreState(state)

    def _save_layout(self) -> None:
        if self._settings is None:
            return
        self._settings.setValue(_GEOMETRY_KEY, self.saveGeometry())
        self._settings.setValue(_STATE_KEY, self.saveState())

    def _on_reset_layout(self) -> None:
        """Put every dock and the toolbar back where they started.

        Both of them shown again, whatever was closed: a dock dragged
        somewhere unhelpful, or closed and forgotten, otherwise has no way
        back that does not involve knowing about the Window menu first.
        """
        self.restoreState(self._default_state)
        for dock in (self._pages_dock, self._inspector_dock):
            dock.setVisible(True)
        self.resize(1200, 800)

    def _update_actions_enabled(self) -> None:
        has_document = self.document is not None
        for action in (self._save_action, self._save_as_action, self._reload_action):
            action.setEnabled(has_document)
        self._undo_action.setEnabled(has_document and self.document.can_undo)  # type: ignore[union-attr]
        self._redo_action.setEnabled(has_document and self.document.can_redo)  # type: ignore[union-attr]
        has_image = has_document and self._current_image is not None
        self._preview_action.setEnabled(has_image)
        self._overlay_action.setEnabled(has_image and self._showing_preview)

        # Disabled at the ends of the plan rather than silently doing
        # nothing, so the toolbar says where you are.
        document, region_id = self.document, self._current_region
        self._previous_region_action.setEnabled(
            document is not None and document.adjacent_region(region_id, forward=False) is not None
        )
        self._next_region_action.setEnabled(
            document is not None and document.adjacent_region(region_id, forward=True) is not None
        )
        self._next_flagged_action.setEnabled(
            document is not None
            and document.adjacent_region(region_id, forward=True, flagged_only=True) is not None
        )

    def _update_title(self) -> None:
        """``[*]`` is Qt's placeholder for the platform's own modified marker.

        An asterisk on most platforms, a dot in the close button on macOS.
        Qt substitutes it from ``isWindowModified``, which is why that is set
        rather than the title rewritten — and why ``windowTitle()`` keeps the
        placeholder whatever the state.
        """
        if self.document is None:
            self.setWindowTitle("comictrans review")
            self.setWindowModified(False)
            return
        self.setWindowTitle(f"{self.document.path.name}[*] — comictrans review")
        self.setWindowModified(self.document.dirty)

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
        self._current_region = None
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
        self._current_region = None
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
        if self.document is not None:
            # Typing into one region, going to look at another and coming
            # back is two acts, and undo should treat them as two.
            self.document.end_edit_run()
        self._current_region = region_id
        self._canvas.set_selected(region_id)
        self._inspector.set_region(self.document, region_id)
        self._update_actions_enabled()

    def _go_to_region(self, region_id: str) -> None:
        """Select a region anywhere in the plan, changing page if it is on another.

        Selecting the page lands on its first region, which this then
        overrides — walking off the end of one page continues onto the next
        rather than stopping there, because the job is every balloon in the
        chapter, not every balloon on this page.
        """
        if self.document is None:
            return
        image = self.document.region(region_id).image
        if image != self._current_image:
            self._pages.select_image(image)
        self._on_region_selected(region_id)

    def _step_region(self, *, forward: bool, flagged_only: bool = False) -> None:
        if self.document is None:
            return
        target = self.document.adjacent_region(
            self._current_region, forward=forward, flagged_only=flagged_only
        )
        if target is None:
            self.statusBar().showMessage("no more regions in that direction", 3000)
            return
        self._go_to_region(target)

    def _on_previous_region(self) -> None:
        self._step_region(forward=False)

    def _on_next_region(self) -> None:
        self._step_region(forward=True)

    def _on_next_flagged_region(self) -> None:
        self._step_region(forward=True, flagged_only=True)

    def _refresh_page_visuals(self) -> None:
        """The window title, the current page's row, and its region outlines."""
        self._update_title()
        if self.document is None or self._current_image is None:
            return
        self._pages.refresh_row(self.document, self._current_image)
        # An edit to one region (skip, translation) can change whether it, or
        # another region on the same page, still counts as overlapping —
        # restyle every region rather than track exactly which ones moved.
        if not self._showing_preview:
            for region in self.document.regions_for(self._current_image):
                self._canvas.set_appearance(_appearance_for(region, self.document))

    def _on_edited(self) -> None:
        self._refresh_page_visuals()
        # Filling in a translation can clear a flag, which is the difference
        # between there being another flagged region ahead and there not. It
        # also makes undo available where a moment ago it was not.
        self._update_actions_enabled()

    def _on_undo(self) -> None:
        if self.document is not None and self.document.undo():
            self._reload_from_document()

    def _on_redo(self) -> None:
        if self.document is not None and self.document.redo():
            self._reload_from_document()

    def _reload_from_document(self) -> None:
        """After undo or redo, when the plan changed under everything at once.

        Unlike an edit, this has to put the inspector's fields back too — the
        change did not come from them. ``set_region`` blocks their signals
        while it repopulates, so restoring a translation does not write
        itself straight back out as a fresh edit.
        """
        self._refresh_page_visuals()
        self._inspector.set_region(self.document, self._current_region)
        self._update_actions_enabled()

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

    def _on_about(self) -> None:
        AboutDialog(self).exec()

    def _on_back_to_overlay(self) -> None:
        if self._current_image is not None:
            self._on_image_selected(self._current_image)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        if self._confirm_discard_if_dirty():
            self._save_layout()
            event.accept()
        else:
            event.ignore()


__all__ = ["MainWindow"]
