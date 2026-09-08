"""The page view: an image, and the region polygons drawn over it.

Polygon coordinates are exactly the pixel coordinates in the plan file — the
scene is never scaled to fit them, only the *view* zooms, so a polygon drawn
here is always the polygon that would be erased and typeset into, with
nothing lost in translation between what you see and what apply would do.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPixmap, QPolygonF, QResizeEvent
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

    def show_page(self, pixmap: QPixmap, regions: Sequence[RegionAppearance] = ()) -> None:
        """Replace the page and its overlay. Resets pan and zoom to fit."""
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

    def fit(self) -> None:
        if self._pixmap_item is not None:
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self.fit()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        region_id = self.region_at(event.position())
        if region_id is not None:
            self.region_selected.emit(region_id)
        super().mousePressEvent(event)
