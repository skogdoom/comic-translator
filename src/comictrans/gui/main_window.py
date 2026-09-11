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
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

from PIL import Image
from PySide6.QtCore import QByteArray, QEvent, QSettings, QSignalBlocker, Qt, QUrl
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDesktopServices,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QDialog,
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import fonts
from ..apply import ApplyReport
from ..errors import ComictransError
from ..extract import ExtractReport
from ..imaging import PageImage, load_page
from ..model import Color, Geometry, Point, Polygon, Region, convex_hull
from . import about, alerts, help_dialog, icons, recent
from .about_dialog import AboutDialog
from .busy_bar import BusyBar
from .canvas import (
    COLOR_APPROXIMATE,
    COLOR_EXACT,
    COLOR_MANUAL,
    CanvasMode,
    PageCanvas,
    RegionAppearance,
    ViewState,
    mode_hint,
)
from .document import PlanDocument
from .extract_dialog import ExtractDialog
from .header_dialog import HeaderDialog
from .help_dialog import HelpDialog
from .hint_line import HintLine
from .inspector import RegionInspector
from .logfile import log_directory, set_notifier
from .page_list import PageList
from .preferences import Preferences, load_preferences, save_preferences
from .preferences_dialog import PreferencesDialog
from .preview import Preview
from .qimage import to_pixmap
from .render_dialog import RenderDialog
from .run_job import (
    ExtractJob,
    PreviewJob,
    PreviewRequest,
    RenderJob,
    RenderRequest,
    RunJob,
)
from .run_panel import RunPanel
from .sampling import color_at, sample_region_colors

log = logging.getLogger(__name__)

PREVIEW_WORKING = "rendering preview…"
"""Shown while the render blocks the window, and replaced by its result."""

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


RECENT_MENU_TITLE = "Open &Recent"
CLEAR_RECENT_TEXT = "Clear Menu"
"""Named so the menu and its test cannot drift apart, as with the toolbar."""


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

        self._preview_job: PreviewJob | None = None
        """The preview being rendered, or None.

        Its own slot rather than ``_job``: that one is the chapter-wide
        pass, and starting it greys out every command that could change
        what it is rendering. A preview must not — the whole point of
        moving it off this thread is that the window goes on working
        while it runs."""

        self._preview_wanted: PreviewRequest | None = None
        """What the last preview asked for, or None once it has landed.

        A job cannot be stopped, so a superseded one is recognised on
        arrival instead: a result whose request is not this one is
        dropped. See ``_on_preview_ready``."""

        self._help: HelpDialog | None = None
        """The guide, once it has been asked for. Kept so that asking again
        raises the window rather than building a second one."""

        self._job: RunJob | None = None
        """The pass in flight, or None. At most one, of either kind: both
        actions that start one are disabled while one is going, so there is
        never a second thread to keep track of or a second report arriving
        out of order."""
        self._settings = settings
        self._preferences: Preferences = load_preferences(settings)
        """What a new run starts from. Read once at construction; a window
        built without settings gets the built-in defaults and stores
        nothing, the same as it does with the layout."""

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
        self._hint.set_hint(mode_hint(CanvasMode.SELECT))

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

        # Along the bottom, and closed until something has been run: it is
        # a panel to work through afterwards, not part of reviewing a page.
        # One dock for both passes, because the two are never both current —
        # an extract ends by opening the plan it wrote, at which point the
        # last render's report describes a plan that is no longer open.
        # Reopened from the Window menu, like the other two.
        self._run_panel = RunPanel()
        self._run_dock = QDockWidget("Run", self)
        self._run_dock.setObjectName("run_dock")
        self._run_dock.setWidget(self._run_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._run_dock)
        self._run_dock.hide()

        self._pages.image_selected.connect(self._on_image_selected)
        self._pages.order_changed.connect(self._on_pages_reordered)
        self._canvas.region_selected.connect(self._on_region_selected)
        self._canvas.zoom_changed.connect(self._on_zoom_changed)
        self._canvas.polygon_edited.connect(self._on_polygon_edited)
        self._canvas.polygon_nudged.connect(self._on_polygon_nudged)
        self._canvas.region_drawn.connect(self._on_region_drawn)
        self._canvas.point_picked.connect(self._on_point_picked)
        self._canvas.region_picked.connect(self._on_region_picked)
        self._canvas.selection_refused.connect(self._on_selection_refused)
        self._canvas.mode_changed.connect(self._on_canvas_mode_changed)
        self._inspector.edited.connect(self._on_edited)
        self._inspector.sample_requested.connect(self._on_sample_requested)
        self._run_panel.row_activated.connect(self._on_run_row_activated)
        self._run_panel.cancel_requested.connect(self._on_run_cancel)

        # Permanent widgets, so they stay put behind the transient messages
        # the status bar shows for saves and preview results. The bar sits to
        # the left of the zoom because it comes and goes, and a thing that
        # appears should not push a thing that is always there.
        self._busy = BusyBar()
        self.statusBar().addPermanentWidget(self._busy)
        self._zoom_label = QLabel()
        self.statusBar().addPermanentWidget(self._zoom_label)

        # Anything nobody caught says so here rather than nowhere. PySide6
        # prints a slot's exception and carries on, so without this the
        # window survives with its state possibly half-updated and all the
        # user sees is a button that did nothing.
        set_notifier(self._on_unhandled)

        self._build_menus()
        self._build_toolbar()
        self._update_actions_enabled()
        self._update_title()
        self.resize(1200, 800)
        self.statusBar().showMessage(
            "Open a plan file to review (File > Open Plan…), "
            "or read one off a folder of pages (File > Extract Pages…)"
        )

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

        self._recent_menu = file_menu.addMenu(RECENT_MENU_TITLE)
        # A menu's tooltips are off by default, and these carry the whole
        # path — see _rebuild_recent_menu for why the label cannot.
        self._recent_menu.setToolTipsVisible(True)
        self._rebuild_recent_menu()

        # "Revert to Saved" rather than "Reload": the macOS name for
        # re-reading the file and throwing away what is unsaved, and what
        # this does. Reload is browser and editor vocabulary.
        self._reload_action = QAction("Re&vert to Saved", self)
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

        # In File rather than View: these two are the window's other two
        # verbs, and neither of them writes the plan you are reviewing.
        # Extract first, because it is where a chapter starts.
        self._extract_action = QAction("&Extract Pages…", self)
        self._extract_action.setShortcut(QKeySequence("Ctrl+Shift+E"))
        self._extract_action.triggered.connect(self._on_extract)
        file_menu.addAction(self._extract_action)

        self._render_action = QAction("&Render Pages…", self)
        self._render_action.setShortcut(QKeySequence("Ctrl+Shift+R"))
        self._render_action.triggered.connect(self._on_render)
        file_menu.addAction(self._render_action)

        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        # Spelled out rather than left to Qt's text heuristic, which reads
        # the label for "quit" or "exit" and would stop recognising this
        # one the moment the label is translated. About and Preferences
        # already carry their roles for the same reason.
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
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

        # No ellipsis, and not Ctrl+M. The ellipsis is for a command that
        # stops to ask something; this one is a mode you are in, with a
        # tick beside it, exactly like the two above. And Cmd+M is
        # Minimise on macOS — a shortcut every window has — so this takes
        # Cmd+Shift+M and pairs with Add Region's Cmd+Shift+A.
        self._merge_action = QAction("&Merge Region", self)
        self._merge_action.setCheckable(True)
        self._merge_action.setShortcut(QKeySequence("Ctrl+Shift+M"))
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

        edit_menu.addSeparator()
        # PreferencesRole is what moves this into the application menu on
        # macOS, where it belongs and where Cmd+, opens it. Qt's standard key
        # is Cmd+, there and Ctrl+, everywhere else, so the shortcut is not
        # spelled out either.
        #
        # "Preferences…", not "Settings…", which is what the HIG has asked
        # for since macOS 13. It was renamed and then changed back, on
        # evidence: the merged item on a Mac reads "Preferences" whatever
        # this string says — seen, on a build whose text was "&Settings…".
        # Qt titles the three items it moves into the application menu
        # itself, the same way About takes the application name rather than
        # the action's text.
        #
        # So the choice is not between two names in that menu; it is between
        # matching it here and not. A command called Settings that opens a
        # window called Preferences is the thing 4.24 set out to stop, so
        # both say what the platform says. Reaching the HIG name needs a
        # translator over Qt's own catalogue, which belongs to 4.9.
        self._preferences_action = QAction("&Preferences…", self)
        self._preferences_action.setMenuRole(QAction.MenuRole.PreferencesRole)
        self._preferences_action.setShortcut(QKeySequence.StandardKey.Preferences)
        self._preferences_action.triggered.connect(self._on_preferences)
        edit_menu.addAction(self._preferences_action)

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
        # Minimise and Zoom first, then this window's own panels: the order
        # every Mac Window menu has. Qt adds neither, so a window without
        # them has no Cmd+M at all — which is the other half of why Merge
        # Region gave that shortcut up. There is no Bring All to Front
        # because there is nothing to bring: one window, and no second one
        # to open.
        self._minimise_action = QAction("&Minimise", self)
        self._minimise_action.setShortcut(QKeySequence("Ctrl+M"))
        self._minimise_action.triggered.connect(self.showMinimized)
        window_menu.addAction(self._minimise_action)

        self._zoom_window_action = QAction("&Zoom", self)
        self._zoom_window_action.triggered.connect(self._on_zoom_window)
        window_menu.addAction(self._zoom_window_action)

        window_menu.addSeparator()
        window_menu.addAction(self._pages_dock.toggleViewAction())
        window_menu.addAction(self._inspector_dock.toggleViewAction())
        window_menu.addAction(self._run_dock.toggleViewAction())
        window_menu.addSeparator()
        self._reset_layout_action = QAction("&Reset Layout", self)
        self._reset_layout_action.triggered.connect(self._on_reset_layout)
        window_menu.addAction(self._reset_layout_action)

        help_menu = self.menuBar().addMenu("&Help")
        # First, and named for the application: what macOS puts at the top
        # of every Help menu. HelpContents is Cmd+? there and F1 elsewhere.
        self._help_action = QAction(help_dialog.TITLE, self)
        self._help_action.setShortcut(QKeySequence.StandardKey.HelpContents)
        self._help_action.triggered.connect(self._on_help)
        help_menu.addAction(self._help_action)

        # Above About, because it is the one someone reaches for with a
        # problem in hand rather than curiosity.
        self._open_logs_action = QAction("Open &Log Folder", self)
        self._open_logs_action.triggered.connect(self._on_open_logs)
        help_menu.addAction(self._open_logs_action)
        help_menu.addSeparator()

        self._about_action = QAction(f"&About {about.NAME}", self)
        # macOS keeps About in the application menu, not in Help. The role is
        # what moves it; Preferences already carries its own. Both have been
        # seen doing it, on a built application — which is also where the
        # item's title turned out not to be ours to set: see the note on
        # Preferences above.
        self._about_action.setMenuRole(QAction.MenuRole.AboutRole)
        self._about_action.triggered.connect(self._on_about)
        help_menu.addAction(self._about_action)

    TOOLBAR_ICONS: ClassVar[dict[str, str]] = {
        "_open_action": "open",
        "_save_action": "save",
        "_undo_action": "undo",
        "_redo_action": "redo",
        "_edit_shape_action": "edit-shape",
        "_add_region_action": "add-region",
        "_merge_action": "merge-region",
        "_delete_region_action": "delete-region",
        "_previous_region_action": "previous-region",
        "_next_region_action": "next-region",
        "_next_flagged_action": "next-flagged",
        "_extract_action": "extract",
        "_render_action": "render",
    }
    """Which drawing goes on which action. The preview action has two, since
    it is two halves of one thing and its label already says which."""

    def _build_toolbar(self) -> None:
        """The same actions the menus hold, not a second set of them.

        Icons rather than words, now that there are icons: text-only cost
        1138px of a 1200px window for twelve commands, which is why Extract
        and Render Pages had to stay off it. All fourteen fit in 513px as
        icons — measured, against 1344px for the same set as words — so the
        two whole-chapter commands are on it after all.

        A word is still one hover away. Every action here has a tooltip from
        its own text, and the menus keep the words permanently.
        """
        self._toolbar = QToolBar("Main", self)
        self._toolbar.setObjectName("main_toolbar")
        self._toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        # The toolbar drawn into the title bar, which is what a Mac window
        # looks like. Qt ignores it everywhere else, so it costs nothing to
        # ask for unconditionally.
        self.setUnifiedTitleAndToolBarOnMac(True)
        self._apply_toolbar_icons()
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
        self._toolbar.addSeparator()
        self._toolbar.addAction(self._extract_action)
        self._toolbar.addAction(self._render_action)

    def _apply_toolbar_icons(self) -> None:
        """Give every toolbar action its drawing, tinted to this palette.

        Re-run on a palette change, which is how the set follows a window
        switched between light and dark without a second set of files.
        """
        icons.forget()
        for attribute, name in self.TOOLBAR_ICONS.items():
            getattr(self, attribute).setIcon(icons.icon(name))
        self._preview_action.setIcon(icons.icon("overlay" if self._showing_preview else "preview"))
        # An icon-only button has nothing to read, so the word the menu shows
        # becomes the tooltip rather than being lost.
        for attribute in (*self.TOOLBAR_ICONS, "_preview_action"):
            action = getattr(self, attribute)
            if not action.toolTip() or action.toolTip() == action.text():
                action.setToolTip(action.text().replace("&", ""))

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        """Re-tint when the platform switches between light and dark."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self._apply_toolbar_icons()

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
        self._close_run_dock()

    def _close_run_dock(self) -> None:
        """Shut it whatever the saved layout said, because nothing has run yet.

        ``restoreState`` brings a dock back exactly as it was left, which for
        this one means any session that rendered a chapter reopens holding
        the bottom of the window for a panel reading "Nothing has been run
        yet." It is a place to work through a report, not part of reviewing a
        page, so it opens when there is one and not before.

        Only the visibility is overruled: a dock dragged to another edge is
        still there when a run opens it, because where it belongs is the
        saved state's answer and this is only about whether it is showing.
        """
        self._run_dock.setVisible(False)

    def _save_layout(self) -> None:
        if self._settings is None:
            return
        self._settings.setValue(_GEOMETRY_KEY, self.saveGeometry())
        self._settings.setValue(_STATE_KEY, self.saveState())

    def _on_zoom_window(self) -> None:
        """The Window menu's Zoom: out to fill the screen, or back again.

        A toggle, because that is what the green button beside it does and
        what the menu item next to it on every other Mac does.
        """
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _on_reset_layout(self) -> None:
        """Put every dock and the toolbar back where they started.

        Both of them shown again, whatever was closed: a dock dragged
        somewhere unhelpful, or closed and forgotten, otherwise has no way
        back that does not involve knowing about the Window menu first.
        """
        self.restoreState(self._default_state)
        for dock in (self._pages_dock, self._inspector_dock):
            dock.setVisible(True)
        # Not the run dock: it starts closed, and a layout reset should put
        # it back to closed rather than open one holding an old report.
        self._close_run_dock()
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
        # A render covers the whole plan, so it wants pages rather than a
        # page: a plan open on no particular page can still be rendered.
        # Extract needs no document at all — it is how you get one — but
        # both wait their turn, hence the job check on each.
        idle = self._job is None
        self._render_action.setEnabled(
            has_document
            and bool(self.document.plan.image_names())  # type: ignore[union-attr]
            and idle
        )
        self._extract_action.setEnabled(idle)
        has_image = has_document and self._current_image is not None
        self._preview_action.setEnabled(has_image)
        self._preview_action.setText(OVERLAY_TEXT if self._showing_preview else PREVIEW_TEXT)
        self._preview_action.setIcon(icons.icon("overlay" if self._showing_preview else "preview"))
        self._preview_action.setToolTip(self._preview_action.text().replace("&", ""))
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
            self.setWindowTitle(about.NAME)
            self.setWindowModified(False)
            return
        self.setWindowTitle(f"{self.document.path.name}[*] — {about.NAME}")
        self.setWindowModified(self.document.dirty)

    # -- opening, saving -----------------------------------------------

    def _confirm_discard_if_dirty(self) -> bool:
        """True if it is safe to proceed: nothing unsaved, saved, or discarded.

        Three buttons rather than two. Discard and Cancel alone make Cancel
        the only way to keep the work, and keeping it then costs backing
        out, saving, and asking for the same thing again — which is the
        answer people want most often offered as the one that is most
        trouble. Qt lays the row out in the platform's order and titles
        Discard "Don't Save" on macOS; see ``alerts``.
        """
        if self.document is None or not self.document.dirty:
            return True
        choice = alerts.ask(
            self,
            f"Save the changes to {self.document.path.name}?",
            "Your changes will be lost if you do not save them.",
            alerts.Button.Save | alerts.Button.Discard | alerts.Button.Cancel,
            alerts.Button.Save,
        )
        if choice == alerts.Button.Save:
            # Saving can still fail — a plan whose directory has gone, say —
            # and a failed save must not read as permission to close over it.
            return self._save_document()
        return choice == alerts.Button.Discard

    def open_plan(self, path: Path) -> None:
        if not self._confirm_discard_if_dirty():
            return
        try:
            document = PlanDocument.open(path)
        except ComictransError as exc:
            alerts.report(self, "The plan could not be opened.", str(exc))
            return

        self.document = document
        self._remember_directory(path)
        self._remember_recent(path)
        self._current_image = None
        self._current_region = None
        self._forget_pending_preview()
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
        """Ask for a plan file and open it. Does nothing if the user cancels."""
        name, _filter = QFileDialog.getOpenFileName(
            self,
            "Open Plan",
            str(self._start_directory()),
            "Plan files (*.yaml *.yml);;All files (*)",
        )
        if name:
            self.open_plan(Path(name))

    def _on_reload(self) -> None:
        if self.document is not None:
            self.open_plan(self.document.path)

    def _on_save(self) -> None:
        self._save_document()

    def _save_document(self) -> bool:
        """Write the plan where it already lives. False if it could not be.

        Returns rather than raises because the unsaved-changes alert has to
        know: a save that failed is not a document it is safe to close.
        """
        if self.document is None:
            return False
        try:
            self.document.save()
        except ComictransError as exc:
            alerts.report(self, "The plan could not be saved.", str(exc))
            return False
        self._update_title()
        self.statusBar().showMessage(f"saved {self.document.path}", 5000)
        return True

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
            overwrite = alerts.ask(
                self,
                f"Replace {target.name}?",
                "The plan already there will be overwritten.",
                alerts.Button.Save | alerts.Button.Cancel,
                alerts.Button.Cancel,
            )
            if overwrite != alerts.Button.Save:
                return
            try:
                self.document.save_as(target, force=True)
            except ComictransError as exc:
                alerts.report(self, "The plan could not be saved.", str(exc))
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
        self._forget_pending_preview()
        try:
            page = load_page(self.document.source_path(image))
        except ComictransError as exc:
            self._page = None
            alerts.report(self, "The page image could not be read.", str(exc))
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
        self._hint.set_hint(mode_hint(CanvasMode(mode)))
        # Entering a mode is a deliberate act, so it breaks a run of nudges
        # the way moving the selection does.
        if self.document is not None:
            self.document.end_edit_run()
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
        try:
            self.document.delete_region(going)
        except (KeyError, ComictransError) as exc:  # gone underneath us
            self._report_failure(f"deleting {going}", exc)
            return
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
        """A finished gesture: a drag, or a corner added or removed."""
        # One gesture, one undo step, whatever came before it: the run is
        # broken at both ends so a drag can neither join the run of nudges in
        # front of it nor collect the one behind.
        if self.document is not None:
            self.document.end_edit_run()
        self._apply_polygon(region_id, polygon)
        if self.document is not None:
            self.document.end_edit_run()

    def _on_polygon_nudged(self, region_id: str, polygon: Polygon) -> None:
        """A step in a run: arrow keys, held down or tapped in a row.

        The run is left open, so consecutive nudges coalesce into one undo
        step the way typing into a field does. It breaks where the typing
        runs break — a different region selected, a mode entered, a gesture
        finished.
        """
        self._apply_polygon(region_id, polygon)

    def _apply_polygon(self, region_id: str, polygon: Polygon) -> None:
        """Put an edited outline into the document, if the reader will take it.

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
        self._refresh_page_visuals()
        self._inspector.set_region(self.document, self._current_region)
        self._update_actions_enabled()

    def _resync_page_rows(self) -> None:
        """Put the rows in the plan's order, when the two have parted.

        The canvas keeps the same rule for a reshape the document refuses:
        what is on screen is never something the document does not have.
        Here the rows can part from the plan two ways — an undone reorder,
        which changes the whole list rather than the one row's label that
        ``_refresh_page_visuals`` repaints, and a reorder the plan could not
        carry out exactly, since ``with_image_order`` ignores a name it does
        not know and leaves a page nobody named at the end.

        Rebuilt with the signals blocked, so that putting the selection back
        does not read as choosing a page and reload one that never changed.
        """
        if self.document is None or self._pages.current_order() == self.document.images():
            return
        with QSignalBlocker(self._pages):
            self._pages.set_document(self.document)
            if self._current_image is not None:
                self._pages.select_image(self._current_image)

    def _on_pages_reordered(self, names: list[str]) -> None:
        """A row was dragged. The widget has already moved it; record it.

        The plan's page order and its region order move together — see
        ``model.with_image_order`` — so this is also what keeps stepping
        region by region walking the comic in the order the list shows.
        """
        if self.document is None or not self.document.reorder_images(names):
            return
        self._resync_page_rows()
        self._refresh_page_visuals()
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
        self._resync_page_rows()
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

    def _forget_pending_preview(self) -> None:
        """Take the rendered page down, and stop wanting whatever is rendering.

        The two belong together. A render in flight was started for the page
        and the plan as they were; arriving at another page, or opening
        another plan, makes it an answer to a question nobody is asking any
        more — and one that would otherwise still match ``_preview_wanted``
        and be painted over whatever is on screen now.

        The thread itself is left to finish. It has nowhere to stop, and its
        result lands in ``_on_preview_ready``, which now has nothing to match
        it against.
        """
        self._showing_preview = False
        self._preview_wanted = None

    def _on_render_preview(self) -> None:
        """Start a render of the current page. Returns before it is done.

        The window stays live while it runs — that is the whole of this
        change — so what used to be one blocking call is now three places:
        here, ``_on_preview_ready`` and ``_on_preview_failed``.
        """
        if self.document is None or self._current_image is None:
            return
        request = PreviewRequest.of(self.document, self._current_image)
        self._preview_wanted = request
        if self._preview_job is not None:
            # One already going. It cannot be stopped, so it is left to
            # finish into nothing: _preview_wanted has moved on, and its
            # result will be dropped on arrival.
            return
        job = PreviewJob(request, self)
        job.completed.connect(self._on_preview_ready)
        job.failed.connect(self._on_preview_failed)
        job.finished.connect(self._on_preview_thread_done)
        self._preview_job = job
        self.statusBar().showMessage(PREVIEW_WORKING)
        self._busy.set_busy(True)
        job.start()

    def _on_preview_thread_done(self) -> None:
        """The worker has stopped. Start the next render if one is waiting.

        ``finished`` rather than ``completed``, because this has to run after
        a failure too, and because a thread must have actually stopped before
        its object is dropped.
        """
        job, self._preview_job = self._preview_job, None
        if job is not None:
            job.deleteLater()
        if self._preview_wanted is not None:
            # Superseded while it ran: whatever asked is still waiting. The
            # bar stays up across the handover rather than blinking off and
            # on again — from where anyone is sitting it is one wait.
            self._on_render_preview()
            return
        self._busy.set_busy(False)

    def _on_preview_failed(self, message: str) -> None:
        self._preview_wanted = None
        # Both down before the alert, not after it. The alert is modal and
        # sits there for as long as it takes somebody to read it, and a
        # message and a bar both claiming a render is still going would sit
        # behind it saying the opposite of what it says. ``finished`` would
        # otherwise take the bar down only once the box was dismissed.
        self.statusBar().clearMessage()
        self._busy.set_busy(False)
        alerts.report(self, "The preview could not be rendered.", message)

    def _on_preview_ready(self, preview: object) -> None:
        """Put a finished render on the canvas — if it is still the one wanted.

        What makes a result stale is not that the plan has moved on. A
        preview is of the plan as it stood when it was asked for, and an edit
        made while it rendered does not make the answer wrong, only older
        than the question — the same as before this ran on a thread, where
        the edit simply could not have happened yet.

        What makes it stale is that nobody is waiting for it: the page has
        changed, another plan is open, the overlay has been put back, or a
        newer preview has been asked for. All four clear or replace
        ``_preview_wanted``, so matching against it is the whole check. The
        job cannot be called off, so this is where a dropped one is dropped.
        """
        assert isinstance(preview, Preview)
        job = self._preview_job
        if job is None or job.request != self._preview_wanted:
            return
        self._preview_wanted = None
        # The same page, rendered: hold the reader's place across the swap,
        # which is what makes the overlay and the output comparable.
        self._remember_view()
        self._canvas.show_page(to_pixmap(preview.image))
        self._canvas.apply_view_state(self._views.get(job.request.image))
        self._showing_preview = True
        self._update_actions_enabled()
        if preview.problems:
            names = ", ".join(o.region_id for o in preview.problems)
            self.statusBar().showMessage(f"preview: {len(preview.problems)} problem(s) — {names}")
        else:
            self.statusBar().showMessage("preview: everything fits")

    # -- running a pass --------------------------------------------------

    def _on_render(self) -> None:
        """Ask what to render and where, save the plan, then start the run."""
        if self.document is None:
            return
        dialog = RenderDialog(self.document, self, preferences=self._preferences)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # Saved first, always. ``apply_plan`` would happily render what is in
        # this window, and the live preview does exactly that — but a preview
        # is ephemeral and output files are not. Pages rendered from a plan
        # that is not on disk are pages nobody can regenerate, which is the
        # property the two passes exist to have.
        if self.document.dirty:
            self._on_save()
            if self.document.dirty:  # the save failed, and said so itself
                return
        self._start_render(dialog.request())

    def _start_render(self, request: RenderRequest) -> None:
        job = RenderJob(request, self)
        job.completed.connect(self._on_render_finished)
        self._start(job, "Rendering", request.total, request.output)

    def _on_extract(self) -> None:
        """Ask what to read and where the plan goes, then start the run.

        Nothing about the open document is touched. The plan this writes did
        not exist when the run started, and the window only opens it once it
        is on disk and complete — which is also why a cancelled extract has
        nothing to show you.
        """
        dialog = ExtractDialog(self._start_directory(), self, preferences=self._preferences)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        request = dialog.request()
        self._remember_directory(request.plan_path)
        job = ExtractJob(request, self)
        job.completed.connect(self._on_extract_finished)
        self._start(job, "Reading", request.total, request.plan_path)

    def _start(self, job: RunJob, verb: str, total: int, where: Path) -> None:
        """Wire up a pass, show the panel, and set it going."""
        job.progressed.connect(self._run_panel.advance)
        job.failed.connect(self._on_run_failed)
        self._job = job
        self._run_panel.start(verb, total, where)
        self._run_dock.show()
        self._update_actions_enabled()
        self.statusBar().showMessage(f"{verb.lower()} {total} page(s) — {where}")
        job.start()

    def _on_run_cancel(self) -> None:
        if self._job is not None:
            self._job.cancel()
            self.statusBar().showMessage("stopping after the page being worked on…")

    def _finish_run(self) -> RunJob | None:
        """Let the worker thread end, and give the two actions back."""
        job, self._job = self._job, None
        if job is None:
            return None
        job.wait()
        # Parented to the window, so it would otherwise sit in its child
        # list for the rest of the session, one per run. Deferred, so the
        # request on it is still readable in the slot that asked for this.
        job.deleteLater()
        self._update_actions_enabled()
        return job

    def _on_render_finished(self, report: ApplyReport) -> None:
        job = self._finish_run()
        if not isinstance(job, RenderJob):
            return
        output = job.request.output
        self._run_panel.show_render(report, output)
        self._run_dock.show()
        pages = len(report.pages_written)
        checks = self._run_panel.row_count()
        what = "cancelled after" if report.cancelled else "rendered"
        tail = f" — {checks} to check" if checks else ""
        self.statusBar().showMessage(f"{what} {pages} page(s) to {output}{tail}")

    def _on_extract_finished(self, report: ExtractReport) -> None:
        """Show what was read, and open the plan it wrote.

        Opening it is the point of the exercise — a plan file is not a thing
        to go and find in a file dialog you just told where to put it — and
        ``open_plan`` asks about unsaved changes on the way, the same as any
        other open. A cancelled run wrote nothing, so there is nothing to
        open and the panel says so instead.
        """
        job = self._finish_run()
        if not isinstance(job, ExtractJob):
            return
        plan_path = job.request.plan_path
        self._run_panel.show_extract(report, plan_path)
        self._run_dock.show()
        if report.cancelled:
            self.statusBar().showMessage(f"cancelled after {report.pages_read} page(s)")
            return
        self.open_plan(plan_path)

    def _on_run_failed(self, message: str) -> None:
        """Nothing happened at all — an unresolvable font, or no recogniser.

        Said in the panel rather than a message box: the panel is already
        open and already the place this run reports to, and a modal here
        would be one more thing to dismiss before reading the reason.
        """
        self._finish_run()
        self._run_panel.show_failure(message)
        self._run_dock.show()
        self.statusBar().showMessage(f"failed: {message}")

    def _on_run_row_activated(self, image: str, region_id: str) -> None:
        """A row in the report names a page, or a region on one. Go there.

        The plan can have moved on since the run — an extract opens a
        different one, an edit deletes a region — so the row is checked
        against the document rather than trusted.
        """
        if self.document is None:
            return
        if region_id:
            if region_id not in self.document.ordered_ids():
                self.statusBar().showMessage(
                    f"{region_id} ({image}) is no longer in this plan", 3000
                )
                return
            self._go_to_region(region_id)
            return
        if image not in self.document.images():
            self.statusBar().showMessage(f"{image} is not in this plan", 3000)
            return
        self._pages.select_image(image)

    def _on_zoom_changed(self, factor: float) -> None:
        fitting = " (fit)" if self._canvas.fitting else ""
        self._zoom_label.setText(f"{round(factor * 100)}%{fitting}")

    def _on_rescan_fonts(self) -> None:
        # Reads every font file on the system, so it is one of the few things
        # here that can fail for reasons outside this process.
        try:
            self._inspector._font.rescan()
            count = len(fonts.available_families())
        except (OSError, ComictransError) as exc:
            self._report_failure("rescanning fonts", exc)
            return
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
        try:
            dialog = HeaderDialog(self.document, self)
        except (OSError, ComictransError) as exc:  # its font box reads the disk
            self._report_failure("opening the plan header", exc)
            return
        dialog.edited.connect(self._on_header_edited)
        dialog.exec()

    def _on_header_edited(self) -> None:
        """The header decides every region without an override of its own."""
        self._update_title()
        if self._current_image is not None and self._showing_preview:
            # What is on screen was rendered under the old header.
            self._on_render_preview()
        self._update_actions_enabled()

    def _on_preferences(self) -> None:
        """Edit what a new run starts from. Nothing here touches the plan.

        Modal and writing through as it is edited, like the header dialog:
        there is nothing to apply and nothing to cancel, which is also what
        macOS expects of a Preferences window.
        """
        try:
            dialog = PreferencesDialog(self._preferences, self)
        except (OSError, ComictransError) as exc:  # its font box reads the disk
            self._report_failure("opening preferences", exc)
            return
        dialog.changed.connect(lambda: self._on_preferences_changed(dialog.preferences()))
        dialog.exec()

    def _on_preferences_changed(self, preferences: Preferences) -> None:
        self._preferences = preferences
        save_preferences(self._settings, preferences)

    def _remember_directory(self, path: Path) -> None:
        """Where the next file dialog should start, after this one ended here."""
        self._on_preferences_changed(replace(self._preferences, last_directory=str(path.parent)))

    # -- recently opened plans -------------------------------------------

    def _rebuild_recent_menu(self) -> None:
        """Redraw Open Recent from what is stored, and grey it out when empty.

        The label is the parent directory and the filename rather than the
        filename alone, which is what a Mac usually shows: plans made by
        ``extract`` are all called ``comic-plan.yaml``, so ten of them would
        be ten identical rows. The directory is the chapter, which is the
        part worth reading. The whole path is on the tooltip, for the case
        where two chapters are named alike as well.
        """
        self._recent_menu.clear()
        paths = recent.load(self._settings) if self._settings is not None else ()
        for path in paths:
            parent = path.parent.name
            action = QAction(f"{parent}/{path.name}" if parent else path.name, self)
            action.setToolTip(str(path))
            # The path rides on the action rather than in a partial bound to
            # this window — see _on_recent_triggered for why that mattered.
            action.setData(str(path))
            action.triggered.connect(self._on_recent_triggered)
            self._recent_menu.addAction(action)
        self._recent_menu.setEnabled(bool(paths))
        if not paths:
            return
        self._recent_menu.addSeparator()
        clear_action = QAction(CLEAR_RECENT_TEXT, self)
        clear_action.triggered.connect(self._on_clear_recent)
        self._recent_menu.addAction(clear_action)

    def _on_recent_triggered(self) -> None:
        """Open whichever recent entry was clicked, read off the action itself.

        This used to be ``partial(self._on_open_recent, path)``, and the
        partial is what a crash trace from a built application ended in. The
        chain: the ``QApplication`` is destroyed at exit, which destroys this
        window, which deletes its child actions; destroying an action cleans
        its connections; cleaning this one frees the partial, which frees the
        bound method inside it, which drops the last reference to this
        window's Python wrapper — while its C++ object is part-way through
        the destructor the whole chain is running inside. shiboken then frees
        what is already being freed. A segfault, not an exception.

        A bound method connected on its own does not do that: PySide gives
        the connection the receiving ``QObject`` as its context, so Qt breaks
        it when that object goes rather than leaving a Python callable to be
        freed during the teardown. So the per-path argument the partial
        existed to carry moves onto the action, where ``QAction.data`` holds
        it as a plain string and nothing holds a reference to anything.

        It is the same shape as the canvas segfault this project has already
        had: a Python wrapper outliving its C++ object, where *dropping* it
        is what kills the process. Not reproduced here — six runs of the real
        window left alive at interpreter exit under the offscreen platform
        exit cleanly, and the crash was seen once on macOS, on an interpreter
        this suite has never run. So this removes the object the trace died
        on; it does not prove the race is gone.
        """
        action = self.sender()
        if isinstance(action, QAction) and isinstance(action.data(), str):
            self._on_open_recent(Path(action.data()))

    def _on_open_recent(self, path: Path) -> None:
        """Open a listed plan, and drop it from the list if it has gone.

        The list is not checked against the disk when the menu is built:
        that would be a stat per entry every time File is opened, and one of
        them being on a network volume that is not answering would hang the
        menu rather than the click. So the check happens here, where someone
        has asked for this particular file and is waiting on it anyway.
        """
        if not path.exists():
            self._forget_recent(path)
            alerts.report(
                self,
                f"{path.name} is not there any more.",
                f"It has been taken off the recent list. It was at {path.parent}.",
                alerts.Icon.Warning,
            )
            return
        self.open_plan(path)

    def _remember_recent(self, path: Path) -> None:
        if self._settings is None:
            return
        recent.remember(self._settings, path)
        self._rebuild_recent_menu()

    def _forget_recent(self, path: Path) -> None:
        if self._settings is None:
            return
        recent.forget(self._settings, path)
        self._rebuild_recent_menu()

    def _on_clear_recent(self) -> None:
        if self._settings is None:
            return
        recent.clear(self._settings)
        self._rebuild_recent_menu()

    def _start_directory(self) -> Path:
        """Where a file dialog opens: the plan on screen, else where you were."""
        if self.document is not None:
            return self.document.path.parent
        if self._preferences.last_directory:
            return Path(self._preferences.last_directory)
        return Path.home()

    def _report_failure(self, doing: str, exc: Exception) -> None:
        """Say what went wrong, and decide how loudly by what kind it is.

        A ``ComictransError`` is an expected failure with something the user
        can act on in it, so the message is the message. Anything else is a
        bug: the status bar gets its name and the log gets its traceback,
        because a type and a line number are what a bug report needs and
        neither belongs in a status bar.
        """
        if isinstance(exc, ComictransError):
            log.warning("%s: %s", doing, exc)
            self.statusBar().showMessage(f"{doing}: {exc}", 8000)
        else:
            log.exception("%s failed", doing, exc_info=exc)
            self.statusBar().showMessage(
                f"{doing} failed: {type(exc).__name__} — see the log", 8000
            )

    def _on_unhandled(self, message: str) -> None:
        """Something reached the excepthook. Say so; the log has the rest."""
        self.statusBar().showMessage(message, 10000)

    def _on_open_logs(self) -> None:
        """Show the folder holding the log and the crash traces.

        Both files live there, so attaching one to a bug report is a click
        rather than a paragraph of instructions about ``~/Library``.
        """
        directory = log_directory()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._report_failure("opening the log folder", exc)
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            self.statusBar().showMessage(f"logs are in {directory}", 10000)

    def _on_help(self) -> None:
        """Open the guide, or raise the one already open.

        Not modal, and kept rather than rebuilt: help is read beside the
        thing it describes, and asking for it twice should not leave two
        windows to close or lose the place you had scrolled to.
        """
        if self._help is None:
            self._help = HelpDialog(self)
        self._help.show()
        self._help.raise_()
        self._help.activateWindow()

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
        self._forget_pending_preview()
        keep = self._current_region
        self._on_image_selected(self._current_image)
        if keep is not None and self.document is not None and keep in self.document.ordered_ids():
            self._go_to_region(keep)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        if not self._confirm_discard_if_dirty():
            event.ignore()
            return
        # A QThread destroyed while it is still running aborts the process,
        # so a render in flight is stopped and waited for rather than closed
        # over the top of. It stops after the page it is on; what is on disk
        # is whole pages either way.
        if self._job is not None:
            self._job.cancel()
            self._job.wait()
            self._job = None
        # The preview thread has nothing to cancel — see PreviewJob — so this
        # waits out the page it is on. A second at worst, against a process
        # that aborts if a running QThread is destroyed.
        if self._preview_job is not None:
            self._preview_job.wait()
            self._preview_job = None
        set_notifier(None)
        self._save_layout()
        event.accept()


__all__ = ["MainWindow"]
