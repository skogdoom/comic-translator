"""The shapes a region can be drawn as, in a grid under one toolbar button.

Add Region opens this rather than drawing straight away, so the shape is
picked first: a polygon clicked corner by corner, or a rectangle or an
ellipse dragged out. Three to a row, in as many rows as there are shapes, so
the ones still to come — a region rotated, a region painted with a brush —
join it without the toolbar growing a button for each.

Each button stands for an action the window owns, the same one the Edit menu
lists, so the check mark, the drawing, the shortcut and the tooltip are the
action's own and cannot disagree between the two places it is offered.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QGridLayout, QMenu, QToolButton, QWidget, QWidgetAction

COLUMNS = 3
"""Buttons to a row. More shapes than a row holds start the next one."""


class ShapePalette(QMenu):
    """A popup grid of shape buttons, closed again by picking one."""

    def __init__(
        self,
        actions: Sequence[QAction],
        icon_size: QSize,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        grid = QWidget(self)
        layout = QGridLayout(grid)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        self.buttons: list[QToolButton] = []
        for index, action in enumerate(actions):
            button = QToolButton(grid)
            button.setDefaultAction(action)
            button.setAutoRaise(True)
            button.setIconSize(icon_size)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            # A menu closes itself when one of its own entries is chosen,
            # and a widget inside it is not one of those: without this the
            # palette would stay open over the page the shape is for.
            button.clicked.connect(self.close)
            layout.addWidget(button, index // COLUMNS, index % COLUMNS)
            self.buttons.append(button)
        holder = QWidgetAction(self)
        holder.setDefaultWidget(grid)
        self.addAction(holder)


__all__ = ["COLUMNS", "ShapePalette"]
