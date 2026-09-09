"""The page view: an image, and the region polygons drawn over it.

Polygon coordinates are exactly the pixel coordinates in the plan file — the
scene is never scaled to fit them, only the *view* zooms, so a polygon drawn
here is always the polygon that would be erased and typeset into, with
nothing lost in translation between what you see and what apply would do.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QMouseEvent,
    QNativeGestureEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QResizeEvent,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QWidget,
)

from ..model import Point, Polygon
from ..planfile.schema import MIN_POLYGON_POINTS

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

    PICK = "pick"
    """The next click reports the page pixel under it, then this ends."""

    MERGE = "merge"
    """The next click reports the region under it, then this ends."""


MODE_HINTS: dict[CanvasMode, str] = {
    CanvasMode.SELECT: "click a region to select it · drag to pan · Ctrl and the wheel zooms",
    CanvasMode.RESHAPE: (
        "drag a corner to reshape · drag inside to move · double-click an edge to add "
        "a corner or a corner to remove it · Esc cancels"
    ),
    CanvasMode.DRAW: (
        "click to place each corner · click the first again, double-click or Enter to "
        "close it · Backspace takes one back · Esc cancels"
    ),
    CanvasMode.MERGE: "click the region to merge the selected one with · Esc cancels",
    CanvasMode.PICK: "click a colour on the page · Esc cancels",
}
"""What each mode does to a click, in one line, for the window to show.

Beside the modes rather than in the window, because the gestures are this
widget's own behaviour: a mode that gains a gesture should gain its line in
the same edit. Written to be read at a glance and to stay short — the line
sits under the canvas, where a long one would be cut off rather than wrap.
"""


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
        self._drag: _ShapeDrag | None = None
        self._draft: list[Point] = []
        self._draft_item: QGraphicsPathItem | None = None
        self._draft_handles: list[VertexHandle] = []

    def show_page(self, pixmap: QPixmap, regions: Sequence[RegionAppearance] = ()) -> None:
        """Replace the page and its overlay, fitted to the window.

        Fitted, because the canvas does not know which page it is being handed
        and so cannot know what zoom that page was last read at. Whoever does
        know follows this with :meth:`apply_view_state`.
        """
        self._scene.clear()  # deletes every item, handles included
        self._items.clear()
        self._appearances.clear()
        self._handles.clear()
        self._draft.clear()
        self._draft_handles.clear()
        self._draft_item = None
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
        """
        if mode == self._mode:
            return
        self._cancel_drag()
        self._clear_draft()
        self._mode = mode
        self._refresh_handles()
        self.setDragMode(
            QGraphicsView.DragMode.ScrollHandDrag
            if mode in (CanvasMode.SELECT, CanvasMode.RESHAPE)
            else QGraphicsView.DragMode.NoDrag
        )
        self.viewport().setCursor(
            Qt.CursorShape.CrossCursor
            if mode in (CanvasMode.DRAW, CanvasMode.PICK)
            else Qt.CursorShape.PointingHandCursor
            if mode is CanvasMode.MERGE
            else Qt.CursorShape.ArrowCursor
        )
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
        self._refresh_draft()

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
        """Put a handle on every corner of the selected region, or none at all."""
        for handle in self._handles:
            self._scene.removeItem(handle)
        self._handles.clear()
        if self._mode is not CanvasMode.RESHAPE or self._selected_id is None:
            return
        item = self._items.get(self._selected_id)
        if item is None:
            return
        for index, (x, y) in enumerate(item.points()):
            handle = VertexHandle(index, QPointF(x, y))
            self._scene.addItem(handle)
            self._handles.append(handle)

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

        # Moving the whole shape clamps the offset, not each corner: clamping
        # them one by one would flatten the polygon against the edge of the
        # page instead of stopping it there.
        rect = self._page_rect()
        xs = [x for x, _ in drag.polygon]
        ys = [y for _, y in drag.polygon]
        dx = max(rect.left() - min(xs), min(rect.right() - 1 - max(xs), dx))
        dy = max(rect.top() - min(ys), min(rect.bottom() - 1 - max(ys), dy))
        return tuple(self._clamped(x + dx, y + dy) for x, y in drag.polygon)

    def _draw_polygon(self, region_id: str, polygon: Polygon) -> None:
        item = self._items.get(region_id)
        if item is None:
            return
        item.setPolygon(QPolygonF([QPointF(x, y) for x, y in polygon]))
        self._refresh_handles()

    def _begin_drag(self, view_pos: QPointF) -> bool:
        """Take hold of a corner, or of the whole shape. False if neither."""
        region_id = self._selected_id
        item = self._items.get(region_id) if region_id else None
        if region_id is None or item is None:
            return False
        scene_point = self.mapToScene(view_pos.toPoint())
        vertex = self.handle_at(view_pos)
        if vertex is None and not item.contains(item.mapFromScene(scene_point)):
            return False
        self._drag = _ShapeDrag(
            region_id=region_id, vertex=vertex, origin=scene_point, polygon=item.points()
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
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        drag = self._drag
        if drag is None:
            super().mouseReleaseEvent(event)
            return
        self._drag = None
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
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
        """Escape abandons what is half-done; drawing also takes Enter and Backspace.

        Escape leaves a dragged shape as it was and throws away a half-drawn
        outline — in both cases the plan is untouched, because neither has
        reached it yet.
        """
        key = event.key()
        if key == Qt.Key.Key_Escape and self._drag is not None:
            self._cancel_drag()
            event.accept()
            return
        if key == Qt.Key.Key_Escape and self._mode in (CanvasMode.PICK, CanvasMode.MERGE):
            self.set_mode(CanvasMode.SELECT)
            event.accept()
            return
        if self._mode is CanvasMode.DRAW:
            if key == Qt.Key.Key_Escape:
                self._clear_draft()
                event.accept()
                return
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
