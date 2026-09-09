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
from PySide6.QtCore import QByteArray, QSettings, QSignalBlocker, Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import fonts
from ..errors import ComictransError
from ..imaging import PageImage, load_page
from ..model import Color, Geometry, Point, Polygon, Region, convex_hull
from .about_dialog import AboutDialog
from .canvas import (
    COLOR_APPROXIMATE,
    COLOR_EXACT,
    COLOR_MANUAL,
    MODE_HINTS,
    CanvasMode,
    PageCanvas,
    RegionAppearance,
    ViewState,
)
from .document import PlanDocument
from .header_dialog import HeaderDialog
from .hint_line import HintLine
from .inspector import RegionInspector
from .page_list import PageList
from .preview import render_preview
from .qimage import to_pixmap
from .sampling import color_at, sample_region_colors

log = logging.getLogger(__name__)

PREVIEW_TEXT = "&Render Preview"
OVERLAY_TEXT = "Back to &Overlay"
"""The two halves of one action: what it does depends on what is on screen,
and the label says which."""

_GEOMETRY_KEY = "window/geometry"
_STATE_KEY = "window/state"
"""Where the dock and toolbar layout is remembered between sessions.

``QMainWindow.saveState`` identifies each dock and toolbar by its
``objectName``, so every one of them is given a stable one below. Without
that the state saves as unrestorable and Qt warns about it at runtime.
"""


_GEOMETRY_COLORS = {
    Geometry.EXACT: COLOR_EXACT,
    Geometry.APPROXIMATE: COLOR_APPROXIMATE,
    Geometry.MANUAL: COLOR_MANUAL,
}


def _appearance_for(region: Region, document: PlanDocument) -> RegionAppearance:
    color = _GEOMETRY_COLORS[region.geometry]
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
        self._page: PageImage | None = None
        """The current page's pixels, kept for the two things that need them:
        sampling a new region's colours, and picking one off the page. Always
        the source image, never the rendered preview — a colour is a fact
        about the page, not about what has been drawn over it."""

        self._sampling: str | None = None
        """Which colour field asked for a pixel, while the canvas takes one."""
        self._settings = settings
        self._views: dict[str, ViewState] = {}
        """How each page was last being read, keyed by image.

        Zoom is per page, not per window: pages differ in size and in how much
        of one you need to see at once, and a level chosen for a dense page of
        captions is the wrong one for the splash opposite it.
        """

        self._pages = PageList()
        self._canvas = PageCanvas()
        self._inspector = RegionInspector()

        # The canvas with a hint line under it, rather than the canvas alone.
        # A mode's gestures were announced once, in a status bar message that
        # the next message replaced, so they were discoverable only in the
        # second after switching mode. This line stays put.
        self._hint = HintLine()
        self._hint.set_hint(MODE_HINTS[CanvasMode.SELECT])

        centre = QWidget()
        stack = QVBoxLayout(centre)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(0)
        stack.addWidget(self._canvas, 1)
        stack.addWidget(self._hint)
        self.setCentralWidget(centre)
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
        self._canvas.zoom_changed.connect(self._on_zoom_changed)
        self._canvas.polygon_edited.connect(self._on_polygon_edited)
        self._canvas.region_drawn.connect(self._on_region_drawn)
        self._canvas.point_picked.connect(self._on_point_picked)
        self._canvas.region_picked.connect(self._on_region_picked)
        self._canvas.selection_refused.connect(self._on_selection_refused)
        self._canvas.mode_changed.connect(self._on_canvas_mode_changed)
        self._inspector.edited.connect(self._on_edited)
        self._inspector.sample_requested.connect(self._on_sample_requested)

        # A permanent widget, so the zoom stays readable behind the transient
        # messages the status bar shows for saves and preview results.
        self._zoom_label = QLabel()
        self.statusBar().addPermanentWidget(self._zoom_label)

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

        edit_menu.addSeparator()
        # Checkable rather than always-on: dragging inside a region is also
        # how the page is panned, so without a mode to be in, reaching for
        # the page would sometimes move a balloon instead.
        self._edit_shape_action = QAction("Edit Region &Shape", self)
        self._edit_shape_action.setCheckable(True)
        self._edit_shape_action.setShortcut(QKeySequence("Ctrl+E"))
        self._edit_shape_action.toggled.connect(self._on_edit_shape_toggled)
        edit_menu.addAction(self._edit_shape_action)

        self._add_region_action = QAction("&Add Region", self)
        self._add_region_action.setCheckable(True)
        self._add_region_action.setShortcut(QKeySequence("Ctrl+Shift+A"))
        self._add_region_action.toggled.connect(self._on_add_region_toggled)
        edit_menu.addAction(self._add_region_action)

        self._merge_action = QAction("&Merge Region…", self)
        self._merge_action.setCheckable(True)
        self._merge_action.setShortcut(QKeySequence("Ctrl+M"))
        self._merge_action.toggled.connect(self._on_merge_toggled)
        edit_menu.addAction(self._merge_action)

        # No confirmation: undo is the safety net every other edit here gets,
        # and a dialog on every delete would be one to click through rather
        # than read. The status bar says what went and how to get it back.
        self._delete_region_action = QAction("&Delete Region", self)
        self._delete_region_action.setShortcut(QKeySequence("Ctrl+Backspace"))
        self._delete_region_action.triggered.connect(self._on_delete_region)
        edit_menu.addAction(self._delete_region_action)

        edit_menu.addSeparator()
        self._header_action = QAction("Plan &Header…", self)
        self._header_action.triggered.connect(self._on_edit_header)
        edit_menu.addAction(self._header_action)

        # The installed fonts are read once and cached, since reading them
        # means opening every font file on the system. This is how you tell
        # the window you have installed one since it looked.
        self._rescan_fonts_action = QAction("Rescan &Fonts", self)
        self._rescan_fonts_action.triggered.connect(self._on_rescan_fonts)
        edit_menu.addAction(self._rescan_fonts_action)

        view_menu = self.menuBar().addMenu("&View")
        # One action rather than two, because they are two halves of one
        # thing: you are looking at either the overlay or the rendered page,
        # and this says which one the other is. Its text follows the state,
        # so the button always names what pressing it will do.
        self._preview_action = QAction(PREVIEW_TEXT, self)
        self._preview_action.setShortcut(QKeySequence("Ctrl+R"))
        self._preview_action.triggered.connect(self._on_toggle_preview)
        view_menu.addAction(self._preview_action)

        view_menu.addSeparator()

        self._zoom_in_action = QAction("Zoom &In", self)
        self._zoom_in_action.setShortcut(QKeySequence.StandardKey.ZoomIn)
        self._zoom_in_action.triggered.connect(self._canvas.zoom_in)
        view_menu.addAction(self._zoom_in_action)

        self._zoom_out_action = QAction("Zoom &Out", self)
        self._zoom_out_action.setShortcut(QKeySequence.StandardKey.ZoomOut)
        self._zoom_out_action.triggered.connect(self._canvas.zoom_out)
        view_menu.addAction(self._zoom_out_action)

        self._zoom_fit_action = QAction("&Fit to Window", self)
        self._zoom_fit_action.setShortcut(QKeySequence("Ctrl+0"))
        self._zoom_fit_action.triggered.connect(self._canvas.fit)
        view_menu.addAction(self._zoom_fit_action)

        self._zoom_actual_action = QAction("&Actual Size", self)
        self._zoom_actual_action.setShortcut(QKeySequence("Ctrl+1"))
        self._zoom_actual_action.triggered.connect(self._canvas.zoom_actual)
        view_menu.addAction(self._zoom_actual_action)

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
        self._toolbar.addAction(self._edit_shape_action)
        self._toolbar.addAction(self._add_region_action)
        self._toolbar.addAction(self._merge_action)
        self._toolbar.addAction(self._delete_region_action)
        self._toolbar.addSeparator()
        self._toolbar.addAction(self._previous_region_action)
        self._toolbar.addAction(self._next_region_action)
        self._toolbar.addAction(self._next_flagged_action)
        self._toolbar.addSeparator()
        self._toolbar.addAction(self._preview_action)

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
        for action in (
            self._save_action,
            self._save_as_action,
            self._reload_action,
            self._header_action,
        ):
            action.setEnabled(has_document)
        self._undo_action.setEnabled(has_document and self.document.can_undo)  # type: ignore[union-attr]
        self._redo_action.setEnabled(has_document and self.document.can_redo)  # type: ignore[union-attr]
        has_image = has_document and self._current_image is not None
        self._preview_action.setEnabled(has_image)
        self._preview_action.setText(OVERLAY_TEXT if self._showing_preview else PREVIEW_TEXT)
        for action in (
            self._zoom_in_action,
            self._zoom_out_action,
            self._zoom_fit_action,
            self._zoom_actual_action,
        ):
            action.setEnabled(has_image)

        # Nothing to reshape while a rendered preview is on the canvas in
        # place of the outlines. Unchecked rather than left checked and
        # inert, so the mode on screen is the mode the canvas is in. Not
        # conditioned on a region being selected: the mode belongs to the
        # canvas, and dropping out of it on every page change would make it
        # something to keep switching back on.
        can_edit_shapes = has_image and not self._showing_preview
        self._edit_shape_action.setEnabled(can_edit_shapes)
        self._add_region_action.setEnabled(can_edit_shapes)
        self._delete_region_action.setEnabled(can_edit_shapes and self._current_region is not None)
        # Something to merge with: another region on this page.
        on_page = (
            len(self.document.regions_for(self._current_image))
            if self.document is not None and self._current_image is not None
            else 0
        )
        self._merge_action.setEnabled(
            can_edit_shapes and self._current_region is not None and on_page > 1
        )
        if not can_edit_shapes:
            self._canvas.set_mode(CanvasMode.SELECT)

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
        self._page = None
        self._views.clear()  # a different plan, a different set of pages
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

    def _remember_view(self) -> None:
        """Store how the page on screen is being read, before it leaves."""
        if self._current_image is not None:
            self._views[self._current_image] = self._canvas.view_state()

    def _on_image_selected(self, image: str) -> None:
        if self.document is None:
            return
        self._remember_view()  # the outgoing page, while it is still current
        self._current_image = image
        self._current_region = None
        self._showing_preview = False
        try:
            page = load_page(self.document.source_path(image))
        except ComictransError as exc:
            self._page = None
            QMessageBox.critical(self, "Could not read image", str(exc))
            return
        self._page = page

        regions = self.document.regions_for(image)
        appearances = [_appearance_for(region, self.document) for region in regions]
        self._canvas.show_page(to_pixmap(Image.fromarray(page.rgb)), appearances)
        self._canvas.apply_view_state(self._views.get(image))
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
        if self._showing_preview:
            return
        regions = self.document.regions_for(self._current_image)
        appearances = [_appearance_for(region, self.document) for region in regions]
        if {region.id for region in regions} != self._canvas.region_ids():
            # A region was added, deleted, or brought back by an undo: the
            # overlay is a different set of outlines, not the same ones in a
            # different state. Swapped rather than reloading the page, which
            # would also throw away where the reader was looking.
            self._canvas.set_regions(appearances)
            return
        # An edit to one region (skip, translation) can change whether it, or
        # another region on the same page, still counts as overlapping —
        # restyle every region rather than track exactly which ones moved.
        for appearance in appearances:
            self._canvas.set_appearance(appearance)

    def _on_edited(self) -> None:
        self._refresh_page_visuals()
        # Filling in a translation can clear a flag, which is the difference
        # between there being another flagged region ahead and there not. It
        # also makes undo available where a moment ago it was not.
        self._update_actions_enabled()

    def _on_edit_shape_toggled(self, on: bool) -> None:
        self._canvas.set_mode(CanvasMode.RESHAPE if on else CanvasMode.SELECT)

    def _on_add_region_toggled(self, on: bool) -> None:
        self._canvas.set_mode(CanvasMode.DRAW if on else CanvasMode.SELECT)

    def _on_canvas_mode_changed(self, mode: str) -> None:
        """Keep the checked action and the canvas saying the same thing.

        The canvas leaves a mode on its own — an outline that closed, a pixel
        that was picked — so the toolbar follows it rather than the other way
        round. Signals are blocked because setting a check mark here must not
        look like someone clicking it.
        """
        self._hint.set_hint(MODE_HINTS[CanvasMode(mode)])
        for action, value in (
            (self._edit_shape_action, CanvasMode.RESHAPE),
            (self._add_region_action, CanvasMode.DRAW),
            (self._merge_action, CanvasMode.MERGE),
        ):
            with QSignalBlocker(action):
                action.setChecked(mode == value)
        if mode != CanvasMode.PICK:
            self._sampling = None

    def _on_region_drawn(self, polygon: Polygon) -> None:
        """Turn a hand-drawn outline into a region, with colours off the page.

        No OCR: nothing in review reads a page for text. The region arrives
        with its text fields empty, which is what leaves it flagged as held
        back until they are filled in — so the cursor goes to the field they
        are filled in from.
        """
        if self.document is None or self._current_image is None or self._page is None:
            return
        try:
            fill, text = sample_region_colors(self._page, polygon)
            region = self.document.add_region(
                self._current_image, polygon, fill_color=fill, text_color=text
            )
        except (ValueError, ComictransError) as exc:
            self.statusBar().showMessage(f"region not added: {exc}", 5000)
            return
        self._refresh_page_visuals()  # the new outline, the row's counts, the title
        self._go_to_region(region.id)
        self._inspector.focus_source_text()
        self.statusBar().showMessage(
            f"added {region.id} — type the text on the page, then its translation"
        )

    def _on_selection_refused(self, region_id: str) -> None:
        """Say why a click on another region did nothing, rather than nothing."""
        self.statusBar().showMessage(
            f"still reshaping {self._current_region} — "
            f"turn Edit Region Shape off to select {region_id}",
            5000,
        )

    def _on_merge_toggled(self, on: bool) -> None:
        self._canvas.set_mode(CanvasMode.MERGE if on else CanvasMode.SELECT)

    def _on_region_picked(self, region_id: str) -> None:
        """The other half of a merge, clicked on the page."""
        if self.document is None or self._current_region is None:
            return
        first = self._current_region
        try:
            colors = self._merged_colors(first, region_id)
            merged = self.document.merge_regions(first, region_id, **colors)
        except (ValueError, KeyError, ComictransError) as exc:
            self.statusBar().showMessage(f"not merged: {exc}", 5000)
            return
        self._current_region = None  # one of the two is gone
        self._refresh_page_visuals()
        self._go_to_region(merged.id)
        self._update_actions_enabled()
        self.statusBar().showMessage(f"merged {first} and {region_id} into {merged.id}", 5000)

    def _merged_colors(self, first_id: str, second_id: str) -> dict[str, Color]:
        """Colours read off the page inside what the merged outline will be.

        The two halves each sampled part of the balloon; the merged shape
        covers all of it, so it is worth asking the page again. An empty
        answer leaves ``merge_regions`` to keep the earlier region's.
        """
        if self.document is None or self._page is None:
            return {}
        first, second = self.document.region(first_id), self.document.region(second_id)
        if first.image != second.image:
            return {}
        hull = convex_hull((*first.polygon, *second.polygon))
        fill, text = sample_region_colors(self._page, hull)
        return {"fill_color": fill, "text_color": text}

    def _on_delete_region(self) -> None:
        if self.document is None or self._current_region is None:
            return
        going = self._current_region
        neighbour = self._neighbour_of(going)
        self.document.delete_region(going)
        self._current_region = None
        self._refresh_page_visuals()
        if neighbour is not None:
            self._go_to_region(neighbour)
        else:
            self._inspector.set_region(self.document, None)
        self._update_actions_enabled()
        self.statusBar().showMessage(f"deleted {going} — Ctrl+Z puts it back", 5000)

    def _neighbour_of(self, region_id: str) -> str | None:
        """Somewhere to stand once this region is gone, chosen before it goes.

        The next region on the same page, or the previous one, before the
        plan's own order: deleting a balloon should leave you looking at the
        page you were reading, not at the top of the next one.
        """
        if self.document is None:
            return None
        page = [
            region.id for region in self.document.regions_for(self.document.region(region_id).image)
        ]
        index = page.index(region_id)
        if index + 1 < len(page):
            return page[index + 1]
        if index > 0:
            return page[index - 1]
        return self.document.adjacent_region(
            region_id, forward=True
        ) or self.document.adjacent_region(region_id, forward=False)

    def _on_sample_requested(self, field: str) -> None:
        """A colour field asked for a pixel off the page."""
        if self.document is None or self._page is None:
            return
        if self._showing_preview:
            self.statusBar().showMessage(
                "colours come from the page, not the preview — Back to Overlay first", 5000
            )
            return
        self._sampling = field
        self._canvas.set_mode(CanvasMode.PICK)
        # Which colour is being taken is not visible anywhere else, so the
        # line says it in place of the mode's own general one.
        self._hint.set_hint(f"click the page to take the {field} colour · Esc cancels")

    def _on_point_picked(self, point: Point) -> None:
        field, self._sampling = self._sampling, None
        if self.document is None or self._page is None or self._current_region is None:
            return
        color = color_at(self._page, point)
        if field == "fill":
            self.document.set_fill_color(self._current_region, color)
        elif field == "text":
            self.document.set_text_color(self._current_region, color)
        else:
            return
        self.document.end_edit_run()
        self._refresh_page_visuals()
        self._inspector.set_region(self.document, self._current_region)
        self._update_actions_enabled()
        self.statusBar().showMessage(f"{field} colour taken from the page: {color.to_hex()}", 5000)

    def _on_polygon_edited(self, region_id: str, polygon: Polygon) -> None:
        """A dragged outline, on its way to the document if the reader will take it.

        The canvas has already drawn it. This is the one place that decides
        whether it is a shape a plan file can hold — and puts the old one
        back on screen when it is not, so what is drawn is never something
        the document does not have.
        """
        if self.document is None:
            return
        try:
            self.document.set_polygon(region_id, polygon)
        except ValueError as exc:
            self.statusBar().showMessage(f"shape unchanged: {exc}", 5000)
            self._canvas.set_appearance(
                _appearance_for(self.document.region(region_id), self.document)
            )
            return
        # One drag, one undo step: without this the next drag on the same
        # region would coalesce into this one, the way typing does.
        self.document.end_edit_run()
        self._refresh_page_visuals()
        self._inspector.set_region(self.document, self._current_region)
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
        # Undoing a region into or out of existence can leave the selection
        # naming one the plan no longer has.
        if (
            self.document is not None
            and self._current_region is not None
            and self._current_region not in self.document.ordered_ids()
        ):
            self._current_region = None
        self._refresh_page_visuals()
        self._canvas.set_selected(self._current_region)
        self._inspector.set_region(self.document, self._current_region)
        self._update_actions_enabled()

    def _on_toggle_preview(self) -> None:
        """Swap between the overlay and the rendered page, whichever is up."""
        if self._showing_preview:
            self._on_back_to_overlay()
        else:
            self._on_render_preview()

    def _on_render_preview(self) -> None:
        if self.document is None or self._current_image is None:
            return
        try:
            preview = render_preview(self.document, self._current_image)
        except ComictransError as exc:
            QMessageBox.critical(self, "Could not render preview", str(exc))
            return
        # The same page, rendered: hold the reader's place across the swap,
        # which is what makes the overlay and the output comparable.
        self._remember_view()
        self._canvas.show_page(to_pixmap(preview.image))
        self._canvas.apply_view_state(self._views.get(self._current_image))
        self._showing_preview = True
        self._update_actions_enabled()
        if preview.problems:
            names = ", ".join(o.region_id for o in preview.problems)
            self.statusBar().showMessage(f"preview: {len(preview.problems)} problem(s) — {names}")
        else:
            self.statusBar().showMessage("preview: everything fits")

    def _on_zoom_changed(self, factor: float) -> None:
        fitting = " (fit)" if self._canvas.fitting else ""
        self._zoom_label.setText(f"{round(factor * 100)}%{fitting}")

    def _on_rescan_fonts(self) -> None:
        self._inspector._font.rescan()
        count = len(fonts.available_families())
        self.statusBar().showMessage(f"{count} font families available", 5000)

    def _on_edit_header(self) -> None:
        """Edit the settings every region is drawn under.

        Modal, and writing through as it is edited. Modal keeps it simple:
        nothing else can change the plan underneath it, so the fields cannot
        go stale while it is open. Closing applies nothing, because every
        change applied as it was made; Ctrl+Z afterwards takes them back one
        at a time.
        """
        if self.document is None:
            return
        dialog = HeaderDialog(self.document, self)
        dialog.edited.connect(self._on_header_edited)
        dialog.exec()

    def _on_header_edited(self) -> None:
        """The header decides every region without an override of its own."""
        self._update_title()
        if self._current_image is not None and self._showing_preview:
            # What is on screen was rendered under the old header.
            self._on_render_preview()
        self._update_actions_enabled()

    def _on_about(self) -> None:
        AboutDialog(self).exec()

    def _on_back_to_overlay(self) -> None:
        """Put the outlines back, on the region that was being looked at.

        Rebuilding the page selects its first region, which is right when you
        arrive at a page and wrong on the way back from its rendered form:
        checking how one balloon came out and returning to the top of the
        page is a place lost every time.
        """
        if self._current_image is None:
            return
        keep = self._current_region
        self._on_image_selected(self._current_image)
        if keep is not None and self.document is not None and keep in self.document.ordered_ids():
            self._go_to_region(keep)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        if self._confirm_discard_if_dirty():
            self._save_layout()
            event.accept()
        else:
            event.ignore()


__all__ = ["MainWindow"]
