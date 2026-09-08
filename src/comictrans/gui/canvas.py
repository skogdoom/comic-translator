"""The page view: an image, and the region polygons drawn over it.

Polygon coordinates are exactly the pixel coordinates in the plan file — the
scene is never scaled to fit them, only the *view* zooms, so a polygon drawn
here is always the polygon that would be erased and typeset into, with
nothing lost in translation between what you see and what apply would do.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QMouseEvent,
    QNativeGestureEvent,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
    QResizeEvent,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsPolygonItem,
    QGraphicsScene,
    QGraphicsView,
    QWidget,
)

from ..model import Polygon

COLOR_EXACT = QColor(40, 170, 70)
COLOR_APPROXIMATE = QColor(230, 140, 30)
"""The same convention ``--debug-dir`` overlays use: green for a traced
contour, orange for a padded box around the text."""

COLOR_FLAGGED = QColor(215, 40, 40)
"""A flagged region's outline goes dashed in this colour, layered over its
geometry colour rather than replacing it — geometry and "needs a look" are
two different facts about a region and both stay visible."""

COLOR_SELECTED = QColor(30, 120, 230)

_WIDTH_NORMAL = 2.0
_WIDTH_SELECTED = 4.0

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


class RegionItem(QGraphicsPolygonItem):
    """One region's outline. Knows its own id so a click can be reported."""

    def __init__(self, region_id: str, polygon: QPolygonF) -> None:
        super().__init__(polygon)
        self.region_id = region_id
        self.setZValue(1.0)
        self.setBrush(Qt.BrushStyle.NoBrush)


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

    def show_page(self, pixmap: QPixmap, regions: Sequence[RegionAppearance] = ()) -> None:
        """Replace the page and its overlay.

        A zoom the reader chose survives this, and so does roughly where they
        were looking. Turning a page or switching to the rendered preview and
        snapping back to fit would make zoom useless for the two things it is
        for: reading small lettering across a chapter, and comparing the
        overlay against what apply would write.
        """
        keep_view = not self._fit_to_window and self._pixmap_item is not None
        centre = self.mapToScene(self.viewport().rect().center()) if keep_view else None

        self._scene.clear()
        self._items.clear()
        self._appearances.clear()
        self._selected_id = None

        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))

        for appearance in regions:
            polygon = QPolygonF([QPointF(x, y) for x, y in appearance.polygon])
            item = RegionItem(appearance.region_id, polygon)
            item.setPen(_pen_for(appearance.color, flagged=appearance.flagged, selected=False))
            self._scene.addItem(item)
            self._items[appearance.region_id] = item
            self._appearances[appearance.region_id] = appearance

        if centre is not None:
            self.centerOn(centre)
        else:
            self.fit()

    def set_appearance(self, appearance: RegionAppearance) -> None:
        """Restyle one region in place, without touching the pixmap, pan, or zoom.

        What an edit changes about a region's flags — a translation filled in,
        a font pinned — should not reset where you were looking.
        """
        self._appearances[appearance.region_id] = appearance
        item = self._items.get(appearance.region_id)
        if item is not None:
            selected = appearance.region_id == self._selected_id
            item.setPen(_pen_for(appearance.color, flagged=appearance.flagged, selected=selected))

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
        region_id = self.region_at(event.position())
        if region_id is not None:
            self.region_selected.emit(region_id)
        super().mousePressEvent(event)
