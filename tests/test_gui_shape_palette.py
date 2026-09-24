"""The shape palette: a grid three wide, in as many rows as it needs."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSize
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QGridLayout

from comictrans.gui.shape_palette import COLUMNS, ShapePalette


def test_more_shapes_than_a_row_holds_start_the_next_row(qapp: object) -> None:
    """The room the palette was asked to leave: shapes still to come join it."""
    actions = [QAction(f"shape {number}") for number in range(7)]
    palette = ShapePalette(actions, QSize(24, 24))

    layout = palette.buttons[0].parentWidget().layout()
    assert isinstance(layout, QGridLayout)
    places = [layout.getItemPosition(layout.indexOf(button))[:2] for button in palette.buttons]

    assert COLUMNS == 3
    assert places == [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2), (2, 0)]


def test_each_button_is_its_action(qapp: object) -> None:
    """Ticked, pictured and worded by the action, as the menu's entry is."""
    action = QAction("Ellipse")
    action.setCheckable(True)
    palette = ShapePalette([action], QSize(24, 24))
    button = palette.buttons[0]

    button.click()

    assert action.isChecked() and button.isChecked()
    assert button.iconSize() == QSize(24, 24)
