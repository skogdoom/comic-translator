"""The page view: an image, and the region polygons drawn over it.

Polygon coordinates are exactly the pixel coordinates in the plan file — the
scene is never scaled to fit them, only the *view* zooms, so a polygon drawn
here is always the polygon that would be erased and typeset into, with
nothing lost in translation between what you see and what apply would do.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QNativeGestureEvent,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPixmap,
    QPolygonF,
    QResizeEvent,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QWidget,
)

from ..model import (
    Point,
    Polygon,
    ellipse_polygon,
    polygon_bounds,
    polygon_is_simple,
    rectangle_polygon,
    rotate_polygon,
)
from ..planfile.schema import MIN_POLYGON_POINTS
from .brush import BRUSH_SIZES, brush_diameter, stroke_outline

COLOR_EXACT = QColor(40, 170, 70)
COLOR_APPROXIMATE = QColor(230, 140, 30)
"""The same convention ``--debug-dir`` overlays use: green for a traced
contour, orange for a padded box around the text."""

COLOR_FLAGGED = QColor(215, 40, 40)
"""A flagged region's outline goes dashed in this colour, layered over its
geometry colour rather than replacing it — geometry and "needs a look" are
two different facts about a region and both stay visible."""

COLOR_MANUAL = QColor(120, 90, 200)
"""A polygon someone drew or reshaped here. Neither traced nor padded, and
neither green nor orange, because it is not what either of those means."""

COLOR_SELECTED = QColor(30, 120, 230)

_WIDTH_NORMAL = 2.0
_WIDTH_SELECTED = 4.0

HANDLE_SIZE = 9.0
"""On-screen size of a vertex handle, in device pixels at any zoom.

Handles ignore the view transform, so a page zoomed to 800% does not get
handles eight times the size — a corner stays something you can put a
pointer on rather than something that swallows the balloon.
"""

HANDLE_GRAB = 10.0
"""How near a handle a press has to land to take hold of it, on screen."""

EDGE_GRAB = 8.0
"""How near an edge a double-click has to land to put a corner in it."""

TURN_REACH = 28.0
"""How far from the selected region its turning handle sits, in screen pixels.

Far enough that it is not mistaken for a corner of a region whose top edge is
a corner away from it, near enough to read as belonging to that region."""

TURN_SNAP = 15.0
"""Degrees a turn goes in with Shift held: the angles lettering is set at."""

NUDGE_STEP = 1
NUDGE_STRIDE = 20
"""How far an arrow key moves the selected region, plain and with Shift.

One pixel because that is the unit the plan file is written in and the
smallest thing worth correcting; twenty because crossing a balloon one pixel
at a time is not a correction, it is a chore.
"""

NUDGE_ACCELERATES_AFTER = 3
NUDGE_MAX_STEP = 12
"""How a held arrow key speeds up: every three repeats it moves one step
further, up to this many pixels a repeat.

So that one gesture covers both jobs. A tap is a pixel, which is what makes
the keys worth having at all — no drag lands on an exact pixel — and holding
the key turns into a glide rather than a slow crawl at the same pixel a
repeat. The count resets on the next fresh press, so precision is always one
tap away.
"""

MOVE_MODIFIER = Qt.KeyboardModifier.ControlModifier
"""Held to drag the selected region instead of the page. Command on macOS.

Plain dragging cannot be it: dragging inside a region is also how the page is
panned, and taking that away would cost most at the zoom where one balloon
fills the viewport. The wheel already pairs with this modifier to zoom;
dragging with it was unclaimed.
"""

ZOOM_MIN = 0.05
ZOOM_MAX = 8.0
ZOOM_STEP = 1.25
"""Zoom is the view's scale factor, where 1.0 is one screen pixel per page pixel.

The scene holds page pixels at their own coordinates and only the view scales,
so zooming re-renders nothing and loses nothing — the same property that makes
a polygon drawn here exactly the polygon apply would use.
"""


@dataclass(frozen=True, slots=True)
class RegionAppearance:
    """How one region's outline should be drawn, decided by the caller.

    The canvas only draws; deciding what a colour or a dashed line *means* —
    geometry, confidence, overlap — belongs to whoever has the ``Region`` and
    its flags, not to a widget whose job is pixels on screen.
    """

    region_id: str
    polygon: Polygon
    color: QColor
    flagged: bool
    locked: bool = False
    """Finished, so the canvas offers no gesture that would reshape or move
    it. Decided by the caller like everything else here — the canvas is told
    which regions are locked rather than asking what a lock means."""


@dataclass(frozen=True, slots=True)
class ViewState:
    """Where the canvas was looking at a page: how far in, and at what.

    Held by whoever knows which page is which, so that going back to a page
    goes back to how it was being read rather than to a default.
    """

    zoom: float
    fitting: bool
    """True when the page was following the window rather than a chosen zoom.
    Restoring that refits to the window as it is now, not to the old factor."""

    centre: tuple[float, float]
    """The scene point at the middle of the viewport. Ignored when fitting."""


class CanvasMode(StrEnum):
    """What a click on the page does.

    One mode at a time rather than a set of independent switches: they
    contradict each other — a click cannot both place a corner and take hold
    of one — and a canvas that could be in two of them at once would be a
    canvas nobody could predict.
    """

    SELECT = "select"
    """Click a region to select it, drag to pan. The default."""

    RESHAPE = "reshape"
    """The selected region's corners can be dragged, added and removed."""

    DRAW = "draw"
    """Clicks place the corners of a new region until the outline closes."""

    RECTANGLE = "rectangle"
    """A drag from one corner to the opposite one draws a new region."""

    ELLIPSE = "ellipse"
    """A drag across the box an ellipse fills draws a new region."""

    BRUSH = "brush"
    """A drag paints, and letting go makes what was painted a new region."""

    PICK = "pick"
    """The next click reports the page pixel under it, then this ends."""

    MERGE = "merge"
    """The next click reports the region under it, then this ends."""


DRAWING_MODES = (CanvasMode.DRAW, CanvasMode.RECTANGLE, CanvasMode.ELLIPSE, CanvasMode.BRUSH)
"""Every mode that draws a new region: what the palette offers."""

_BOX_MODES = frozenset({CanvasMode.RECTANGLE, CanvasMode.ELLIPSE})
"""The shapes drawn by dragging across a box rather than clicking corners."""

MODE_HINTS: dict[CanvasMode, str] = {
    CanvasMode.SELECT: QCoreApplication.translate(
        "Canvas",
        "click a region to select it · {move}-drag or the arrow keys move it · "
        "drag to pan · {move} and the wheel zooms",
    ),
    CanvasMode.RESHAPE: QCoreApplication.translate(
        "Canvas",
        "drag a corner to reshape · drag the round handle to turn it, Shift in 15° steps · "
        "drag inside or use the arrow keys to move · "
        "double-click an edge to add a corner or a corner to remove it · Esc cancels · "
        "Enter finishes",
    ),
    CanvasMode.DRAW: QCoreApplication.translate(
        "Canvas",
        "click to place each corner · click the first again, double-click or Enter to "
        "close it · Backspace takes one back · Esc cancels",
    ),
    CanvasMode.RECTANGLE: QCoreApplication.translate(
        "Canvas", "drag from one corner to the other · Shift keeps it square · Esc cancels"
    ),
    CanvasMode.ELLIPSE: QCoreApplication.translate(
        "Canvas",
        "drag from one corner of its box to the other · Shift keeps it round · Esc cancels",
    ),
    CanvasMode.BRUSH: QCoreApplication.translate(
        "Canvas",
        "drag to paint a region · letting go makes it one, with any hole filled in · Esc cancels",
    ),
    CanvasMode.MERGE: QCoreApplication.translate(
        "Canvas", "click the region to merge the selected one with · Esc cancels"
    ),
    CanvasMode.PICK: QCoreApplication.translate(
        "Canvas", "click a colour on the page · Esc cancels"
    ),
}
"""What each mode does to a click, in one line, for the window to show.

``{move}`` is filled in with what this platform calls the move modifier, by
:func:`mode_hint`, which is what the window asks for rather than reaching in
here directly.

``QCoreApplication.translate`` rather than ``tr``: a module-level table has
no ``self`` to ask, and the call is written out in full because ``lupdate``
reads the source rather than running it — see ``translations``.

Beside the modes rather than in the window, because the gestures are this
widget's own behaviour: a mode that gains a gesture should gain its line in
the same edit. Written to be read at a glance and to stay short — the line
sits under the canvas, where a long one would be cut off rather than wrap.
"""


def move_modifier_name() -> str:
    """What to call :data:`MOVE_MODIFIER` on the platform this is running on.

    Qt maps ``ControlModifier`` to Command on macOS, so a hint that said
    "Ctrl" would be wrong on the machine this tool is written for. Asked of
    Qt rather than decided from ``sys.platform``: whatever it renders the
    modifier as in a menu is what the keyboard in front of you is labelled.

    Read by pairing the modifier with a key and taking that key's own text
    back off — a bare modifier renders as an empty string.
    """
    key = QKeySequence(Qt.Key.Key_A).toString(QKeySequence.SequenceFormat.NativeText)
    combined = QKeySequence(MOVE_MODIFIER | Qt.Key.Key_A).toString(
        QKeySequence.SequenceFormat.NativeText
    )
    return combined.removesuffix(key).rstrip("+") or "Ctrl"


def mode_hint(mode: CanvasMode) -> str:
    """One line saying what a click does in this mode, named for this platform."""
    return MODE_HINTS[mode].format(move=move_modifier_name())


@dataclass(frozen=True, slots=True)
class _ShapeDrag:
    """A polygon being dragged, and what it looked like before it started.

    The original is kept so Escape can put it back, and so a drag that ends
    where it began can be recognised and reported as nothing at all.
    """

    region_id: str
    vertex: int | None
    """Which corner is moving. ``None`` moves the whole polygon."""

    origin: QPointF
    """Scene point the press landed on."""

    polygon: Polygon

    turning: bool = False
    """The whole shape turns about the middle of its box, rather than moving."""


class RegionItem(QGraphicsPolygonItem):
    """One region's outline. Knows its own id so a click can be reported."""

    def __init__(self, region_id: str, polygon: QPolygonF) -> None:
        super().__init__(polygon)
        self.region_id = region_id
        self.setZValue(1.0)
        self.setBrush(Qt.BrushStyle.NoBrush)

    def points(self) -> Polygon:
        """The outline as plan-file coordinates: whole pixels, top-left origin."""
        polygon = self.polygon()
        return tuple(
            (round(polygon.at(index).x()), round(polygon.at(index).y()))
            for index in range(polygon.count())
        )


class VertexHandle(QGraphicsRectItem):
    """A grab point on one corner of the selected region.

    Ignores the view transform, so it is the same size on screen whatever the
    zoom — the page is what is being magnified, not the furniture for editing
    it.
    """

    def __init__(self, index: int, point: QPointF) -> None:
        half = HANDLE_SIZE / 2.0
        super().__init__(QRectF(-half, -half, HANDLE_SIZE, HANDLE_SIZE))
        self.index = index
        self.setPos(point)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setZValue(2.0)
        self.setBrush(COLOR_SELECTED)
        self.setPen(QPen(QColor(255, 255, 255)))


class TurnHandle(QGraphicsEllipseItem):
    """The grab point that turns the selected region about its middle.

    Round where the corners are square, so the two are not taken for each
    other, and on a stem back to the region it turns. Placed at the middle of
    the region's top edge and drawn :data:`TURN_REACH` screen pixels above it
    — or below its bottom edge, ``upward`` false, when there is no room above
    on the page. Ignores the view transform like a corner handle, so the
    reach is the same on screen at any zoom.
    """

    def __init__(self, anchor: QPointF, *, upward: bool = True) -> None:
        radius = HANDLE_SIZE / 2.0 + 1.0
        self.reach = -TURN_REACH if upward else TURN_REACH
        super().__init__(QRectF(-radius, self.reach - radius, 2 * radius, 2 * radius))
        self.setPos(anchor)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setZValue(2.0)
        self.setBrush(COLOR_SELECTED)
        self.setPen(QPen(QColor(255, 255, 255)))
        pen = QPen(COLOR_SELECTED)
        pen.setCosmetic(True)
        self.stem = QGraphicsLineItem(
            0.0, 0.0, 0.0, self.reach - math.copysign(radius, self.reach), self
        )
        self.stem.setPen(pen)

    def grip_offset(self) -> QPoint:
        """Where the round grip is on screen, from where the handle is placed."""
        return QPoint(0, round(self.reach))


def _distance_to_segment(point: QPointF, start: QPointF, end: QPointF) -> float:
    """Shortest distance from a point to a line segment, in the same units."""
    dx, dy = end.x() - start.x(), end.y() - start.y()
    span = dx * dx + dy * dy
    if span == 0.0:
        nearest = start
    else:
        along = ((point.x() - start.x()) * dx + (point.y() - start.y()) * dy) / span
        along = max(0.0, min(1.0, along))
        nearest = QPointF(start.x() + along * dx, start.y() + along * dy)
    return float(((point.x() - nearest.x()) ** 2 + (point.y() - nearest.y()) ** 2) ** 0.5)


def _pen_for(color: QColor, *, flagged: bool, selected: bool) -> QPen:
    pen = QPen(COLOR_SELECTED if selected else color)
    pen.setWidthF(_WIDTH_SELECTED if selected else _WIDTH_NORMAL)
    pen.setCosmetic(True)  # constant on-screen width regardless of zoom
    if flagged and not selected:
        pen.setColor(COLOR_FLAGGED)
        pen.setStyle(Qt.PenStyle.DashLine)
    return pen


class PageCanvas(QGraphicsView):
    """One page: a pixmap, and optionally a set of clickable region outlines."""

    region_selected = Signal(str)
    zoom_changed = Signal(float)
    polygon_edited = Signal(str, object)
    """A region's outline was dragged into a new shape: its id, and the
    polygon as a tuple of whole-pixel points. Emitted once per gesture, when
    the mouse comes up, so one drag is one edit — and only when the shape
    actually changed. Whoever receives it decides whether it is legal; the
    canvas draws shapes, it does not know what a plan file will accept."""

    polygon_nudged = Signal(str, object)
    """A key moved the selected region: its id and the polygon. Separate from
    ``polygon_edited`` because it is a step in a run rather than a finished
    gesture — forty taps of an arrow key are one thing done, and undo should
    agree."""

    region_drawn = Signal(object)
    """An outline drawn by hand closed: a polygon of whole-pixel points. What
    becomes of it — a region, with what colours and what id — is not the
    canvas's business."""

    point_picked = Signal(object)
    """A page pixel was clicked in pick mode: an ``(x, y)`` point. The page
    itself is not here, so reading the colour there is for whoever has it."""

    region_picked = Signal(str)
    """A region was clicked in merge mode: the one to fold the selected one
    into. Reported rather than selected, because the selection is the other
    half of the pair."""

    selection_refused = Signal(str)
    """A click landed on a region the mode will not switch to: its id, so
    that whoever can explain why can say so. Reshaping stays on the region it
    was chosen for; nothing else here refuses a click."""

    region_context_menu_requested = Signal(str, QPoint)
    """Right-click, or Control-click on macOS, landed on a region: its id and
    where on screen to put the menu. Reported rather than shown here, for the
    same reason ``region_drawn`` and the rest are: the commands themselves —
    extract, reshape — belong to whoever owns them, not to a widget whose job
    is pixels on screen."""

    mode_changed = Signal(str)
    """The canvas changed mode, including when it left one of its own accord
    — an outline that closed, a pixel that was picked. Whoever shows the mode
    follows this rather than assuming it is still where they put it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._items: dict[str, RegionItem] = {}
        self._appearances: dict[str, RegionAppearance] = {}
        self._selected_id: str | None = None
        self._fit_to_window = True
        self._mode = CanvasMode.SELECT
        self._handles: list[VertexHandle] = []
        self._turn_handle: TurnHandle | None = None
        self._drag: _ShapeDrag | None = None
        self._nudge_repeats = 0
        self._draft: list[Point] = []
        self._draft_item: QGraphicsPathItem | None = None
        self._draft_handles: list[VertexHandle] = []
        self._box_start: tuple[Point, QPointF] | None = None
        """Where a rectangle or ellipse drag began: on the page, and on screen."""
        self._brush = BRUSH_SIZES[0]
        self._stroke: list[Point] = []
        """Where the brush's middle has been, while its button is down."""
        self._stroke_item: QGraphicsPathItem | None = None
        self._brush_tip: QGraphicsEllipseItem | None = None
        """The brush's outline, under the pointer while the brush is in hand."""

    def show_page(self, pixmap: QPixmap, regions: Sequence[RegionAppearance] = ()) -> None:
        """Replace the page and its overlay, fitted to the window.

        Fitted, because the canvas does not know which page it is being handed
        and so cannot know what zoom that page was last read at. Whoever does
        know follows this with :meth:`apply_view_state`.
        """
        # Every Python reference to a graphics item goes *before* the scene
        # deletes them, and that order is the whole of this. ``clear()``
        # destroys the C++ objects without telling shiboken, so a wrapper
        # that outlives the call points into freed memory — measured:
        # ``Shiboken.isValid`` on the old pixmap item returns False the
        # instant ``clear()`` returns. Nothing has to *read* such a wrapper
        # to crash. Dropping it is enough, because shiboken frees what it
        # wrapped as the last reference goes and there is nothing left to
        # free. That drop used to be the rebinding of ``_pixmap_item`` a few
        # lines below, which is why the fatal trace named the line that
        # installs the new page rather than this one.
        self._pixmap_item = None
        self._draft_item = None
        self._stroke_item = None
        self._brush_tip = None
        self._items.clear()
        self._handles.clear()
        self._turn_handle = None
        self._draft_handles.clear()
        self._scene.clear()

        self._appearances.clear()
        self._draft.clear()
        self._box_start = None
        self._stroke.clear()
        self._drag = None
        self._selected_id = None

        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))

        for appearance in regions:
            self._add_region_item(appearance)

        self.fit()

    def region_ids(self) -> frozenset[str]:
        """Which regions are currently drawn over the page."""
        return frozenset(self._items)

    @property
    def _locked_ids(self) -> frozenset[str]:
        """Which drawn regions refuse to be reshaped or moved.

        Read off the appearances rather than kept beside them, so there is
        one place a region's lock is recorded and no second copy to fall out
        of step when ``set_regions`` replaces them.
        """
        return frozenset(
            region_id for region_id, appearance in self._appearances.items() if appearance.locked
        )

    def set_regions(self, regions: Sequence[RegionAppearance]) -> None:
        """Replace the outlines, keeping the page, the zoom and the selection.

        For when the set of regions has changed rather than one of them: a
        region added, deleted, or brought back by undo. Reloading the page
        would do it too, and would also throw away where you were looking.
        """
        selected = self._selected_id
        for item in self._items.values():
            self._scene.removeItem(item)
        self._items.clear()
        self._appearances.clear()
        self._selected_id = None
        for appearance in regions:
            self._add_region_item(appearance)
        self.set_selected(selected if selected in self._items else None)

    def _add_region_item(self, appearance: RegionAppearance) -> None:
        polygon = QPolygonF([QPointF(x, y) for x, y in appearance.polygon])
        item = RegionItem(appearance.region_id, polygon)
        item.setPen(_pen_for(appearance.color, flagged=appearance.flagged, selected=False))
        self._scene.addItem(item)
        self._items[appearance.region_id] = item
        self._appearances[appearance.region_id] = appearance

    def set_appearance(self, appearance: RegionAppearance) -> None:
        """Restyle one region in place, without touching the pixmap, pan, or zoom.

        What an edit changes about a region's flags — a translation filled in,
        a font pinned — should not reset where you were looking.
        """
        self._appearances[appearance.region_id] = appearance
        item = self._items.get(appearance.region_id)
        if item is None:
            return
        selected = appearance.region_id == self._selected_id
        item.setPen(_pen_for(appearance.color, flagged=appearance.flagged, selected=selected))
        # The polygon comes through here too, so undoing a reshape puts the
        # outline back: the appearance is the whole of what the canvas knows
        # about a region, not just its colour.
        if appearance.polygon != item.points():
            item.setPolygon(QPolygonF([QPointF(x, y) for x, y in appearance.polygon]))
            if selected:
                self._refresh_handles()

    def set_selected(self, region_id: str | None) -> None:
        previous, self._selected_id = self._selected_id, region_id
        for changed in (previous, region_id):
            appearance = self._appearances.get(changed) if changed else None
            item = self._items.get(changed) if changed else None
            if appearance is not None and item is not None:
                item.setPen(
                    _pen_for(
                        appearance.color,
                        flagged=appearance.flagged,
                        selected=changed == region_id,
                    )
                )
        self._refresh_handles()

    # -- modes ------------------------------------------------------------

    @property
    def mode(self) -> CanvasMode:
        """What a click on the page does right now."""
        return self._mode

    def set_mode(self, mode: CanvasMode) -> None:
        """Change what a click does, abandoning anything half-done first.

        Modes rather than always-on behaviour, because a click means
        different things: dragging inside a region is also how you pan the
        page, so without a mode to be in, reaching for the page would
        sometimes move a balloon instead, and quietly.

        Entering any mode but select also takes keyboard focus. Every one of
        them is driven from here — Escape cancels reshaping, drawing and
        picking, Enter also finishes reshaping and drawing — and the toggle
        that turns a mode on is a menu item or a shortcut, reached without
        ever clicking the page. Without this, a translation focused from
        selecting the region a moment ago would keep the keyboard, and Esc or
        Enter would land in it instead of reaching the mode it was meant for.
        """
        if mode == self._mode:
            return
        self._cancel_drag()
        self._clear_draft()
        self._show_brush_tip(None)
        self._mode = mode
        self._refresh_handles()
        self.setDragMode(
            QGraphicsView.DragMode.ScrollHandDrag
            if mode in (CanvasMode.SELECT, CanvasMode.RESHAPE)
            else QGraphicsView.DragMode.NoDrag
        )
        self.viewport().setCursor(
            Qt.CursorShape.CrossCursor
            if mode in (*DRAWING_MODES, CanvasMode.PICK)
            else Qt.CursorShape.PointingHandCursor
            if mode is CanvasMode.MERGE
            else Qt.CursorShape.ArrowCursor
        )
        if mode is not CanvasMode.SELECT:
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.mode_changed.emit(str(mode))

    # -- drawing a new region ---------------------------------------------

    @property
    def draft(self) -> Polygon:
        """The corners placed so far in draw mode. Empty when not drawing."""
        return tuple(self._draft)

    def _place_point(self, view_pos: QPointF) -> None:
        """Add a corner, or close the outline if this lands on the first one."""
        point = self._clamped(*self._scene_xy(view_pos))
        if len(self._draft) >= MIN_POLYGON_POINTS and self._near_first(view_pos):
            self._finish_drawing()
            return
        if self._draft and point == self._draft[-1]:
            return  # a double-click's second press, or a stutter
        self._draft.append(point)
        self._refresh_draft(view_pos)

    def _near_first(self, view_pos: QPointF) -> bool:
        if not self._draft:
            return False
        offset = self.mapFromScene(QPointF(*self._draft[0])) - view_pos.toPoint()
        return float((offset.x() ** 2 + offset.y() ** 2) ** 0.5) <= HANDLE_GRAB

    def _refresh_draft(self, cursor: QPointF | None = None) -> None:
        """Redraw the outline so far, with a rubber band out to the pointer."""
        for handle in self._draft_handles:
            self._scene.removeItem(handle)
        self._draft_handles.clear()
        if self._draft_item is not None:
            self._scene.removeItem(self._draft_item)
            self._draft_item = None
        if not self._draft:
            return

        path = QPainterPath(QPointF(*self._draft[0]))
        for point in self._draft[1:]:
            path.lineTo(QPointF(*point))
        if cursor is not None:
            path.lineTo(self.mapToScene(cursor.toPoint()))
        if len(self._draft) >= MIN_POLYGON_POINTS:
            # Shown closed from three corners on, because from there it is a
            # region: what you are looking at is what clicking the first
            # corner would give you.
            path.lineTo(QPointF(*self._draft[0]))

        item = QGraphicsPathItem(path)
        pen = QPen(COLOR_MANUAL)
        pen.setWidthF(_WIDTH_SELECTED)
        pen.setCosmetic(True)
        pen.setStyle(Qt.PenStyle.DashLine)
        item.setPen(pen)
        item.setZValue(3.0)
        self._scene.addItem(item)
        self._draft_item = item
        for index, (x, y) in enumerate(self._draft):
            handle = VertexHandle(index, QPointF(x, y))
            self._scene.addItem(handle)
            self._draft_handles.append(handle)

    def _finish_drawing(self) -> None:
        """Close the outline and report it, if there is enough of one."""
        if len(self._draft) < MIN_POLYGON_POINTS:
            return
        polygon = tuple(self._draft)
        self._clear_draft()
        self.set_mode(CanvasMode.SELECT)
        self.region_drawn.emit(polygon)

    def _clear_draft(self) -> None:
        """Take the half-drawn outline off the page. Nothing is reported."""
        self._draft.clear()
        self._box_start = None
        self._stroke.clear()
        self._refresh_draft()
        self._show_stroke()

    # -- dragging out a rectangle or an ellipse ----------------------------

    def _box_shape(self, view_pos: QPointF, modifiers: Qt.KeyboardModifier) -> Polygon:
        """The shape the drag so far would draw, exactly as it would be drawn.

        Shift makes the box square, on its shorter side, so the shape never
        reaches past where the pointer is — or past the page, which the start
        and the pointer are both already kept on.
        """
        assert self._box_start is not None
        (start_x, start_y), _screen = self._box_start
        end_x, end_y = self._clamped(*self._scene_xy(view_pos))
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            side = min(abs(end_x - start_x), abs(end_y - start_y))
            end_x = start_x + (side if end_x >= start_x else -side)
            end_y = start_y + (side if end_y >= start_y else -side)
        shape = rectangle_polygon if self._mode is CanvasMode.RECTANGLE else ellipse_polygon
        return shape((start_x, start_y), (end_x, end_y))

    def _show_box(self, polygon: Polygon) -> None:
        """The shape being dragged out, dashed like a half-drawn outline."""
        if self._draft_item is not None:
            self._scene.removeItem(self._draft_item)
            self._draft_item = None
        if len(polygon) < MIN_POLYGON_POINTS:
            return
        path = QPainterPath(QPointF(*polygon[0]))
        for point in polygon[1:]:
            path.lineTo(QPointF(*point))
        path.closeSubpath()
        item = QGraphicsPathItem(path)
        pen = QPen(COLOR_MANUAL)
        pen.setWidthF(_WIDTH_SELECTED)
        pen.setCosmetic(True)
        pen.setStyle(Qt.PenStyle.DashLine)
        item.setPen(pen)
        item.setZValue(3.0)
        self._scene.addItem(item)
        self._draft_item = item

    def _finish_box(self, view_pos: QPointF, modifiers: Qt.KeyboardModifier) -> None:
        """Report the shape the drag drew, unless the drag was really a click.

        A press that moved less than the platform's own drag distance is a
        click, and a click draws nothing: the mode stays on for the drag that
        was meant. So does a drag too thin to make a region — a line, or a
        shape that rounds to fewer than three corners.
        """
        assert self._box_start is not None
        _page, screen = self._box_start
        polygon = self._box_shape(view_pos, modifiers)
        self._clear_draft()
        moved = (view_pos - screen).manhattanLength()
        if (
            moved < QApplication.startDragDistance()
            or len(polygon) < MIN_POLYGON_POINTS
            or not polygon_is_simple(polygon)
        ):
            return
        self.set_mode(CanvasMode.SELECT)
        self.region_drawn.emit(polygon)

    # -- painting with the brush ------------------------------------------

    @property
    def brush(self) -> float:
        """The brush in hand, as a share of the page's height: see ``gui.brush``."""
        return self._brush

    def set_brush(self, share: float) -> None:
        """Pick up another brush. Takes effect from the next stroke."""
        self._brush = share
        if self._brush_tip is not None:
            self._show_brush_tip(self._brush_tip.pos())

    @property
    def brush_diameter(self) -> int:
        """How wide the brush in hand paints on this page, in page pixels."""
        return brush_diameter(self._brush, round(self._page_rect().height()))

    @property
    def stroke(self) -> Polygon:
        """Where the brush has been in the stroke being painted. Empty between strokes."""
        return tuple(self._stroke)

    def _show_brush_tip(self, scene_point: QPointF | None) -> None:
        """The brush's outline centred on a page point, or taken away.

        Drawn in the page's own units rather than ignoring the zoom as the
        handles do: it is the size of what a press would paint, and that is
        a size on the page.
        """
        if self._brush_tip is not None:
            self._scene.removeItem(self._brush_tip)
            self._brush_tip = None
        if scene_point is None:
            return
        radius = self.brush_diameter / 2.0
        tip = QGraphicsEllipseItem(QRectF(-radius, -radius, 2 * radius, 2 * radius))
        pen = QPen(COLOR_MANUAL)
        pen.setCosmetic(True)
        tip.setPen(pen)
        tip.setPos(scene_point)
        tip.setZValue(4.0)
        self._scene.addItem(tip)
        self._brush_tip = tip

    def _paint_to(self, view_pos: QPointF) -> None:
        """Take the stroke on to where the pointer is, and show it painted."""
        self._stroke.append(self._clamped(*self._scene_xy(view_pos)))
        self._show_stroke()

    def _show_stroke(self) -> None:
        """The stroke so far, painted as wide as the brush with round ends.

        Filled as a shape rather than drawn with a pen that wide, because a
        stroke starts as a single point and Qt strokes a path with no length
        as nothing at all — measured, with a round cap as with any other — so
        a pen would show nothing under a press until the pointer moved. The
        disc the first point paints is added to the outline for that reason;
        anywhere later it is already inside it.
        """
        if self._stroke_item is not None:
            self._scene.removeItem(self._stroke_item)
            self._stroke_item = None
        if not self._stroke:
            return
        path = QPainterPath(QPointF(*self._stroke[0]))
        for point in self._stroke[1:]:
            path.lineTo(QPointF(*point))
        stroker = QPainterPathStroker()
        stroker.setWidth(self.brush_diameter)
        stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painted = stroker.createStroke(path)
        radius = self.brush_diameter / 2.0
        painted.addEllipse(QPointF(*self._stroke[0]), radius, radius)
        paint = QColor(COLOR_MANUAL)
        paint.setAlpha(110)
        item = QGraphicsPathItem(painted)
        item.setPen(Qt.PenStyle.NoPen)
        item.setBrush(paint)
        item.setZValue(3.0)
        self._scene.addItem(item)
        self._stroke_item = item

    def _finish_stroke(self) -> None:
        """Report the outline of what the stroke painted.

        The brush is put down after one, as every other shape is after
        drawing one region: the region is selected and its text is what comes
        next. A stroke whose outline no step could make a ring of reports
        nothing and leaves the brush in hand — measured never to happen; see
        ``gui.brush``.
        """
        rect = self._page_rect()
        polygon = stroke_outline(
            self._stroke, self.brush_diameter, (round(rect.width()), round(rect.height()))
        )
        self._clear_draft()
        if polygon is None:
            return
        self.set_mode(CanvasMode.SELECT)
        self.region_drawn.emit(polygon)

    def _pick(self, view_pos: QPointF) -> None:
        # Reported before the mode changes, so that whoever asked for the
        # pixel still knows it was them who asked when it arrives.
        self.point_picked.emit(self._clamped(*self._scene_xy(view_pos)))
        self.set_mode(CanvasMode.SELECT)

    def _scene_xy(self, view_pos: QPointF) -> tuple[float, float]:
        scene_point = self.mapToScene(view_pos.toPoint())
        return scene_point.x(), scene_point.y()

    # -- reshaping --------------------------------------------------------

    def polygon_of(self, region_id: str) -> Polygon | None:
        """The outline as it is drawn right now, mid-drag included."""
        item = self._items.get(region_id)
        return item.points() if item is not None else None

    def handle_at(self, view_pos: QPointF) -> int | None:
        """Index of the corner near a view-coordinate point, if any.

        Measured on screen rather than in page pixels, so a corner is equally
        grabbable at 25% and at 400% zoom.
        """
        best: tuple[float, int] | None = None
        for handle in self._handles:
            offset = self.mapFromScene(handle.pos()) - view_pos.toPoint()
            distance = float((offset.x() ** 2 + offset.y() ** 2) ** 0.5)
            if distance <= HANDLE_GRAB and (best is None or distance < best[0]):
                best = (distance, handle.index)
        return None if best is None else best[1]

    def edge_at(self, view_pos: QPointF) -> int | None:
        """Index of the corner an edge starts at, for an edge near this point."""
        item = self._items.get(self._selected_id) if self._selected_id else None
        if item is None:
            return None
        points = item.points()
        target = QPointF(view_pos)
        best: tuple[float, int] | None = None
        for index, start in enumerate(points):
            end = points[(index + 1) % len(points)]
            distance = _distance_to_segment(
                target,
                QPointF(self.mapFromScene(QPointF(*start))),
                QPointF(self.mapFromScene(QPointF(*end))),
            )
            if distance <= EDGE_GRAB and (best is None or distance < best[0]):
                best = (distance, index)
        return None if best is None else best[1]

    def _refresh_handles(self) -> None:
        """Put a handle on every corner of the selected region, or none at all.

        And the one that turns it, which goes above the region unless the
        page stops short of where it would be drawn — then below, so that it
        can always be reached.
        """
        for handle in self._handles:
            self._scene.removeItem(handle)
        self._handles.clear()
        if self._turn_handle is not None:
            self._scene.removeItem(self._turn_handle)
            self._turn_handle = None
        if self._mode is not CanvasMode.RESHAPE or self._selected_id is None:
            return
        if self._selected_id in self._locked_ids:
            return  # nothing to drag, so nothing that looks draggable
        item = self._items.get(self._selected_id)
        if item is None:
            return
        points = item.points()
        for index, (x, y) in enumerate(points):
            handle = VertexHandle(index, QPointF(x, y))
            self._scene.addItem(handle)
            self._handles.append(handle)
        box = polygon_bounds(points)
        middle = (box.left + box.right - 1) / 2
        reach = TURN_REACH / max(self.zoom, 1e-6)
        upward = box.top - reach >= self._page_rect().top()
        anchor = QPointF(middle, box.top if upward else box.bottom - 1)
        self._turn_handle = TurnHandle(anchor, upward=upward)
        self._scene.addItem(self._turn_handle)

    def turn_handle_at(self, view_pos: QPointF) -> bool:
        """Whether a view-coordinate point is on the turning handle."""
        handle = self._turn_handle
        if handle is None:
            return False
        grip = self.mapFromScene(handle.pos()) + handle.grip_offset()
        offset = grip - view_pos.toPoint()
        return float((offset.x() ** 2 + offset.y() ** 2) ** 0.5) <= HANDLE_GRAB

    def _turned_polygon(
        self, drag: _ShapeDrag, scene_point: QPointF, modifiers: Qt.KeyboardModifier
    ) -> Polygon | None:
        """The shape turned as far as the pointer has gone round its middle.

        Measured as the angle the pointer has swept about the middle of the
        box since the press, so the grip can be taken anywhere on it. Shift
        rounds that to :data:`TURN_SNAP`. ``None`` when a corner would leave
        the page: the turn stops where it last fitted, as a moved region
        stops at the edge, rather than flattening the shape against it.
        """
        box = polygon_bounds(drag.polygon)
        centre_x = (box.left + box.right - 1) / 2
        centre_y = (box.top + box.bottom - 1) / 2
        start = math.atan2(drag.origin.y() - centre_y, drag.origin.x() - centre_x)
        now = math.atan2(scene_point.y() - centre_y, scene_point.x() - centre_x)
        degrees = math.degrees(now - start)
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            degrees = round(degrees / TURN_SNAP) * TURN_SNAP
        turned = rotate_polygon(drag.polygon, degrees)
        rect = self._page_rect()
        inside = all(
            rect.left() <= x <= rect.right() - 1 and rect.top() <= y <= rect.bottom() - 1
            for x, y in turned
        )
        return turned if inside else None

    def _page_rect(self) -> QRectF:
        return self._scene.sceneRect()

    def _clamped(self, x: float, y: float) -> tuple[int, int]:
        """A point rounded to a pixel and kept on the page.

        The plan file reader refuses a negative coordinate, so a corner
        dragged off the top or left would be an edit that could not be saved.
        Off the bottom or right is legal but pointless — nothing is rendered
        there — so the page is the limit in all four directions.
        """
        rect = self._page_rect()
        left, top = rect.left(), rect.top()
        right, bottom = max(left, rect.right() - 1), max(top, rect.bottom() - 1)
        return (round(max(left, min(right, x))), round(max(top, min(bottom, y))))

    def _offset_polygon(self, polygon: Polygon, dx: float, dy: float) -> Polygon:
        """The whole shape moved, stopped at the edge of the page.

        The offset is clamped, not each corner: clamping them one by one
        would flatten the polygon against the edge instead of stopping it
        there. Shared by the drag and the arrow keys, so both stop the same
        way and neither can put a corner off the top or left, where the plan
        file reader would refuse it.
        """
        rect = self._page_rect()
        xs = [x for x, _ in polygon]
        ys = [y for _, y in polygon]
        dx = max(rect.left() - min(xs), min(rect.right() - 1 - max(xs), dx))
        dy = max(rect.top() - min(ys), min(rect.bottom() - 1 - max(ys), dy))
        return tuple(self._clamped(x + dx, y + dy) for x, y in polygon)

    def _moved_polygon(self, drag: _ShapeDrag, scene_point: QPointF) -> Polygon:
        """The dragged polygon at this pointer position."""
        dx = scene_point.x() - drag.origin.x()
        dy = scene_point.y() - drag.origin.y()
        if drag.vertex is not None:
            x, y = drag.polygon[drag.vertex]
            moved = self._clamped(x + dx, y + dy)
            return tuple(
                moved if index == drag.vertex else point for index, point in enumerate(drag.polygon)
            )
        return self._offset_polygon(drag.polygon, dx, dy)

    def _draw_polygon(self, region_id: str, polygon: Polygon) -> None:
        item = self._items.get(region_id)
        if item is None:
            return
        item.setPolygon(QPolygonF([QPointF(x, y) for x, y in polygon]))
        self._refresh_handles()

    # -- moving -----------------------------------------------------------

    def wants_move_cursor(self, view_pos: QPointF, modifiers: Qt.KeyboardModifier) -> bool:
        """Whether the pointer is somewhere a modifier drag would move a region.

        Exposed rather than buried in the move handler so the answer can be
        asked for directly: it is the whole of what the cursor says.
        """
        if not modifiers & MOVE_MODIFIER or self._mode not in (
            CanvasMode.SELECT,
            CanvasMode.RESHAPE,
        ):
            return False
        if self._selected_id in self._locked_ids:
            return False
        item = self._items.get(self._selected_id) if self._selected_id else None
        if item is None:
            return False
        return bool(item.contains(item.mapFromScene(self.mapToScene(view_pos.toPoint()))))

    def _nudge_step(self, base: int, *, repeating: bool) -> int:
        """How far this key press moves the region, faster the longer it is held."""
        if not repeating:
            self._nudge_repeats = 0
            return base
        self._nudge_repeats += 1
        grown = base * (1 + self._nudge_repeats // NUDGE_ACCELERATES_AFTER)
        # Never below the base: Shift already asks for a stride, and a
        # ceiling meant for the one-pixel step must not shorten it.
        return min(grown, max(base, NUDGE_MAX_STEP))

    def _nudge(self, dx: int, dy: int) -> bool:
        """Move the selected region by whole pixels. False if there is none.

        False on a locked region too, so the key falls through to the view
        and scrolls the page instead of silently doing nothing.
        """
        region_id = self._selected_id
        item = self._items.get(region_id) if region_id else None
        if region_id is None or item is None or region_id in self._locked_ids:
            return False
        moved = self._offset_polygon(item.points(), dx, dy)
        if moved == item.points():
            return True  # against the edge of the page; still ours to swallow
        self._draw_polygon(region_id, moved)
        self.polygon_nudged.emit(region_id, moved)
        return True

    def _begin_drag(self, view_pos: QPointF) -> bool:
        """Take hold of a corner, or of the whole shape. False if neither.

        A locked region offers neither, which is what makes the whole of
        reshaping and moving refuse it: the corner drag and the modifier
        drag both start here.
        """
        region_id = self._selected_id
        item = self._items.get(region_id) if region_id else None
        if region_id is None or item is None or region_id in self._locked_ids:
            return False
        scene_point = self.mapToScene(view_pos.toPoint())
        turning = self._mode is CanvasMode.RESHAPE and self.turn_handle_at(view_pos)
        vertex = None if turning else self.handle_at(view_pos)
        if not turning and vertex is None and not item.contains(item.mapFromScene(scene_point)):
            return False
        self._drag = _ShapeDrag(
            region_id=region_id,
            vertex=vertex,
            origin=scene_point,
            polygon=item.points(),
            turning=turning,
        )
        # Otherwise the view pans the page under the shape being dragged.
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        return True

    def _cancel_drag(self) -> None:
        """Put the shape back as it was and let go of it."""
        drag, self._drag = self._drag, None
        if drag is None:
            return
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._draw_polygon(drag.region_id, drag.polygon)

    def _edit_by(self, region_id: str, polygon: Polygon) -> None:
        """Draw a hand edit and report it, in that order.

        Drawn first so the shape is on screen even if whoever is listening
        refuses the edit and puts it back — one place decides what is legal,
        and it is not this one.
        """
        self._draw_polygon(region_id, polygon)
        self.polygon_edited.emit(region_id, polygon)

    def region_at(self, view_pos: QPointF) -> str | None:
        """The region under a view-coordinate point, if any. Exposed for testing."""
        item = self.itemAt(view_pos.toPoint())
        while item is not None and not isinstance(item, RegionItem):
            item = item.parentItem()
        return item.region_id if isinstance(item, RegionItem) else None

    # -- zoom -------------------------------------------------------------

    @property
    def zoom(self) -> float:
        """The view's scale factor. 1.0 is one screen pixel per page pixel."""
        return float(self.transform().m11())

    @property
    def fitting(self) -> bool:
        """Whether the page is following the window rather than a chosen zoom."""
        return self._fit_to_window

    def set_zoom(self, factor: float) -> None:
        """Zoom to an absolute factor, clamped, and stop following the window."""
        clamped = max(ZOOM_MIN, min(ZOOM_MAX, factor))
        self._fit_to_window = False
        self.setTransform(QTransform.fromScale(clamped, clamped))
        self._refresh_handles()  # whether the turning handle fits above moves with the zoom
        self.zoom_changed.emit(clamped)

    def zoom_in(self) -> None:
        self.set_zoom(self.zoom * ZOOM_STEP)

    def zoom_out(self) -> None:
        self.set_zoom(self.zoom / ZOOM_STEP)

    def zoom_actual(self) -> None:
        self.set_zoom(1.0)

    def fit(self) -> None:
        """Scale the whole page into the viewport, and follow the window again."""
        self._fit_to_window = True
        if self._pixmap_item is not None:
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        self._refresh_handles()
        self.zoom_changed.emit(self.zoom)

    def view_state(self) -> ViewState:
        """What the caller needs to bring this page back exactly as it is now."""
        centre = self.mapToScene(self.viewport().rect().center())
        return ViewState(
            zoom=self.zoom, fitting=self._fit_to_window, centre=(centre.x(), centre.y())
        )

    def apply_view_state(self, state: ViewState | None) -> None:
        """Restore a page's zoom and position. ``None`` leaves it fitted.

        ``None`` is a page that has not been opened before, which has no zoom
        of its own to go back to.
        """
        if state is None or state.fitting:
            return
        self.set_zoom(state.zoom)
        self.centerOn(QPointF(*state.centre))

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        # Only while following the window. Refitting unconditionally is what
        # would make a chosen zoom vanish the moment the window was resized.
        if self._fit_to_window:
            self.fit()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt override
        """Ctrl (Command on macOS) and the wheel zooms; the wheel alone scrolls.

        ``AnchorUnderMouse`` is set, so the point under the pointer is the one
        that stays put.
        """
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    def event(self, event: QEvent) -> bool:
        """Trackpad pinch, which arrives as a native gesture rather than a wheel.

        Only macOS sends these, so nothing in the test suite reaches this
        branch — the offscreen platform the widget tests run under has no
        trackpad to pinch. Wheel zoom above is the path that is covered.
        """
        if (
            event.type() == QEvent.Type.NativeGesture
            and isinstance(event, QNativeGestureEvent)
            and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture
        ):
            self.set_zoom(self.zoom * (1.0 + event.value()))
            return True
        return bool(super().event(event))

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        """The brush's outline goes with the pointer, off the page as well."""
        self._show_brush_tip(None)
        super().leaveEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802 - Qt override
        """A region's own commands, or nothing when there is no region here.

        Select mode only. Every other mode has already given a click a
        meaning of its own — a corner to drag, a corner to place, a region to
        merge or pick — and a menu of unrelated commands popping up mid-
        gesture would contradict whatever is in progress rather than ask
        about it, which is the wrong place to find that out.

        ``region_at`` is the same hit test a left click uses, so a
        right-click and a left-click can never disagree about what was
        clicked.
        """
        if self._mode is not CanvasMode.SELECT:
            super().contextMenuEvent(event)
            return
        region_id = self.region_at(QPointF(event.pos()))
        if region_id is None:
            super().contextMenuEvent(event)
            return
        self.region_context_menu_requested.emit(region_id, event.globalPos())
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            if self._mode is CanvasMode.PICK:
                self._pick(event.position())
                event.accept()
                return
            if self._mode is CanvasMode.MERGE:
                # A click on nothing is a miss, not a cancel: the outlines
                # are thin and the one you want is easy to skim past.
                region_id = self.region_at(event.position())
                if region_id is not None:
                    self.region_picked.emit(region_id)
                    self.set_mode(CanvasMode.SELECT)
                event.accept()
                return
            if self._mode is CanvasMode.DRAW:
                self._place_point(event.position())
                event.accept()
                return
            if self._mode is CanvasMode.BRUSH:
                self._paint_to(event.position())
                event.accept()
                return
            if self._mode in _BOX_MODES:
                self._box_start = (
                    self._clamped(*self._scene_xy(event.position())),
                    event.position(),
                )
                event.accept()
                return
            if (
                self._mode is CanvasMode.SELECT
                and event.modifiers() & MOVE_MODIFIER
                and self._begin_drag(event.position())
            ):
                event.accept()
                return
            if self._mode is CanvasMode.RESHAPE:
                if self._begin_drag(event.position()):
                    event.accept()
                    return
                # A click that misses the handles does not retarget the
                # mode. Reshaping applies to the region it was chosen for,
                # and a stray click near another balloon quietly moving the
                # handles onto it is the surprise a mode exists to prevent.
                # Panning still works, so the press goes on to the view.
                landed = self.region_at(event.position())
                if landed is not None and landed != self._selected_id:
                    self.selection_refused.emit(landed)
                super().mousePressEvent(event)
                return
        region_id = self.region_at(event.position())
        if region_id is not None:
            self.region_selected.emit(region_id)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if self._drag is not None and self._drag.turning:
            scene_point = self.mapToScene(event.position().toPoint())
            turned = self._turned_polygon(self._drag, scene_point, event.modifiers())
            if turned is not None:
                self._draw_polygon(self._drag.region_id, turned)
            if self._turn_handle is not None:
                # The grip goes with the pointer while it turns, rather than
                # jumping about with the box of a shape that is turning.
                handle = self._turn_handle
                handle.stem.setVisible(False)
                handle.setPos(self.mapToScene(event.position().toPoint() - handle.grip_offset()))
            event.accept()
            return
        if self._drag is not None:
            scene_point = self.mapToScene(event.position().toPoint())
            self._draw_polygon(self._drag.region_id, self._moved_polygon(self._drag, scene_point))
            event.accept()
            return
        if self._mode is CanvasMode.DRAW and self._draft:
            # The rubber band out to the pointer, so the corner you are about
            # to place is visible before you place it.
            self._refresh_draft(event.position())
            event.accept()
            return
        if self._box_start is not None:
            self._show_box(self._box_shape(event.position(), event.modifiers()))
            event.accept()
            return
        if self._mode is CanvasMode.BRUSH:
            self._show_brush_tip(QPointF(*self._clamped(*self._scene_xy(event.position()))))
            if self._stroke:
                self._paint_to(event.position())
            event.accept()
            return
        if self._mode in (CanvasMode.SELECT, CanvasMode.RESHAPE):
            # Held over the selected region, the modifier turns the pan into a
            # move; the cursor is where that is discoverable without reading.
            self.viewport().setCursor(
                Qt.CursorShape.SizeAllCursor
                if self.wants_move_cursor(event.position(), event.modifiers())
                else Qt.CursorShape.PointingHandCursor
                if self._mode is CanvasMode.RESHAPE and self.turn_handle_at(event.position())
                else Qt.CursorShape.ArrowCursor
            )
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if self._box_start is not None and event.button() == Qt.MouseButton.LeftButton:
            self._finish_box(event.position(), event.modifiers())
            event.accept()
            return
        if self._stroke and event.button() == Qt.MouseButton.LeftButton:
            self._finish_stroke()
            event.accept()
            return
        drag = self._drag
        if drag is None:
            super().mouseReleaseEvent(event)
            return
        self._drag = None
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        if drag.turning:
            self._refresh_handles()  # back above the shape it has turned
        polygon = self.polygon_of(drag.region_id)
        # A press that went nowhere is a click, not an edit. Reporting it
        # would put an undo step behind every stray click on a balloon.
        if polygon is not None and polygon != drag.polygon:
            self.polygon_edited.emit(drag.region_id, polygon)
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        """A corner under the pointer goes; an edge under it gains one.

        Four corners are what detection produces for a caption box and never
        enough to trace a balloon, so adding and removing them is part of
        reshaping rather than a refinement of it.
        """
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        if self._mode is CanvasMode.DRAW:
            self._finish_drawing()  # the press before this placed the corner
            event.accept()
            return
        if self._mode is not CanvasMode.RESHAPE:
            super().mouseDoubleClickEvent(event)
            return
        region_id = self._selected_id
        item = self._items.get(region_id) if region_id else None
        if region_id is None or item is None:
            super().mouseDoubleClickEvent(event)
            return

        points = item.points()
        vertex = self.handle_at(event.position())
        if vertex is not None:
            if len(points) <= MIN_POLYGON_POINTS:
                event.accept()  # a triangle is as far down as a polygon goes
                return
            self._edit_by(region_id, points[:vertex] + points[vertex + 1 :])
            event.accept()
            return

        edge = self.edge_at(event.position())
        if edge is None:
            super().mouseDoubleClickEvent(event)
            return
        scene_point = self.mapToScene(event.position().toPoint())
        added = self._clamped(scene_point.x(), scene_point.y())
        self._edit_by(region_id, (*points[: edge + 1], added, *points[edge + 1 :]))
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        """Escape abandons what is half-done; drawing and reshaping also take Enter.

        Escape leaves a dragged shape as it was and throws away a half-drawn
        outline, a rectangle or ellipse still being dragged out, or a stroke
        still being painted — in each case the plan is untouched, because none
        of them has reached it yet. With nothing half-drawn, Escape puts the
        drawing tool down, the same for every shape and the brush. Enter
        closes a half-drawn outline the same way clicking its first corner
        does, and, in reshape mode, is the keyboard's way of turning Edit
        Region Shape back off — every edit it made is already on the region,
        the same as it would be leaving the mode any other way, so there is
        nothing left for Enter to commit.
        """
        key = event.key()
        base = NUDGE_STRIDE if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else NUDGE_STEP
        step = self._nudge_step(base, repeating=event.isAutoRepeat())
        # Keyed by the int QKeyEvent reports, not by the enum member.
        offsets: dict[int, tuple[int, int]] = {
            Qt.Key.Key_Left.value: (-step, 0),
            Qt.Key.Key_Right.value: (step, 0),
            Qt.Key.Key_Up.value: (0, -step),
            Qt.Key.Key_Down.value: (0, step),
        }
        if (
            key in offsets
            and self._drag is None
            and self._mode in (CanvasMode.SELECT, CanvasMode.RESHAPE)
            and self._nudge(*offsets[key])
        ):
            # Swallowed even when the region is already against the edge of
            # the page, so a held key cannot start scrolling the view out
            # from under the region it stopped moving.
            event.accept()
            return
        if key == Qt.Key.Key_Escape and self._drag is not None:
            self._cancel_drag()
            event.accept()
            return
        if key == Qt.Key.Key_Escape and self._mode in (CanvasMode.PICK, CanvasMode.MERGE):
            self.set_mode(CanvasMode.SELECT)
            event.accept()
            return
        if (
            key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and self._mode is CanvasMode.RESHAPE
            and self._drag is None
        ):
            self.set_mode(CanvasMode.SELECT)
            event.accept()
            return
        if key == Qt.Key.Key_Escape and self._mode in DRAWING_MODES:
            # What is half-drawn goes first, and the tool stays in hand for
            # another try; with nothing half-drawn, the tool is put down.
            if self._draft or self._box_start is not None or self._stroke:
                self._clear_draft()
            else:
                self.set_mode(CanvasMode.SELECT)
            event.accept()
            return
        if self._mode is CanvasMode.DRAW:
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._finish_drawing()
                event.accept()
                return
            if key == Qt.Key.Key_Backspace and self._draft:
                self._draft.pop()
                self._refresh_draft()
                event.accept()
                return
        super().keyPressEvent(event)
