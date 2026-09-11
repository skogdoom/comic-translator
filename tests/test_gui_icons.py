"""The toolbar's drawings: that they ship, render, and follow the palette.

A missing icon file is invisible from the code — ``icon()`` deliberately
returns an empty ``QIcon`` and lets the window open — so the test that a
name and a file agree has to be here. The rest is about the tinting, which
is the only reason these are loaded through a module rather than handed
straight to ``QAction.setIcon``.

Skips entirely when PySide6 is not installed or no display can be opened —
see the ``qapp`` fixture.
"""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QSize
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication

from comictrans.gui import icons
from comictrans.gui.main_window import MainWindow

PREVIEW_ICONS = ("preview", "overlay")
"""The two the preview action swaps between, which ``TOOLBAR_ICONS`` cannot
name because one action holds both."""


def _wanted() -> set[str]:
    return set(MainWindow.TOOLBAR_ICONS.values()) | set(PREVIEW_ICONS)


def _opaque_colors(icon: QIcon, size: int = 24) -> list[QColor]:
    """The colours of the pixels the drawing actually covers.

    Anti-aliased edges come out part-transparent and part-tint, so only the
    solid core of a stroke says what colour the thing was painted.
    """
    image = icon.pixmap(size, size).toImage()
    return [
        image.pixelColor(x, y)
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() == 255
    ]


def test_every_drawing_a_toolbar_action_asks_for_ships() -> None:
    """A typo here is a gap in the toolbar, silently, at run time."""
    missing = sorted(_wanted() - icons.available())
    assert not missing, f"no file for {missing} in {icons.ICON_DIR}"


def test_no_drawing_ships_that_nothing_asks_for() -> None:
    """The other direction: a file left behind by a renamed action."""
    unused = sorted(icons.available() - _wanted())
    assert not unused, f"{unused} ship but no action uses them"


def test_the_drawings_render_at_every_size_the_window_asks_for(qapp: object) -> None:
    """SVG needs Qt's SVG plugin, and its absence is silent: a null pixmap.

    Every icon at every baked size, because a file that fails to parse
    renders empty rather than raising, and one empty icon is as invisible
    from the code as a missing one.
    """
    for name in sorted(icons.available()):
        icon = icons.icon(name, QColor(0, 0, 0))
        assert icon.availableSizes() == [QSize(size, size) for size in icons.SIZES], name
        for size in icons.SIZES:
            pixmap = icon.pixmap(size, size)
            assert not pixmap.isNull(), f"{name} at {size}px is a null pixmap"
        assert _opaque_colors(icon), f"{name} renders nothing at 24px"


def _ink_box(name: str, size: int = 64) -> tuple[float, float, float, float]:
    """``(width, height, centre x, centre y)`` of a drawing, in grid units.

    Measured off the pixmap the window actually gets rather than off the
    file, so the answer includes everything between the two: the stroke, the
    rasteriser, and the tint. Grid units rather than pixels so the numbers
    read as the drawing was laid out.
    """
    image = icons.icon(name, QColor(0, 0, 0)).pixmap(size, size).toImage()
    left, top, right, bottom = size, size, -1, -1
    for y in range(size):
        for x in range(size):
            if image.pixelColor(x, y).alpha():
                left, right = min(left, x), max(right, x)
                top, bottom = min(top, y), max(bottom, y)
    assert right >= 0, f"{name} drew nothing"
    unit = icons.GRID / size
    return (
        (right - left + 1) * unit,
        (bottom - top + 1) * unit,
        (left + right + 1) / 2 * unit - icons.GRID / 2,
        (top + bottom + 1) / 2 * unit - icons.GRID / 2,
    )


def test_every_drawing_fills_the_same_box_and_sits_in_the_middle_of_it(qapp: object) -> None:
    """What makes a row of them read as one set rather than fifteen pictures.

    Measured before this rule existed: the set ran from 14 grid units across
    to 22, and as much as 2 off centre — the up arrow high, the down arrow
    low, next to each other on the bar. The toolbar looked ragged and small,
    and that was why.

    The tolerances are what a 64px raster of a 24-unit grid can say: one
    pixel is 0.375 of a unit, so a drawing is held to half a unit of centre
    and a unit of size rather than to the number it was fitted to.
    """
    for name in sorted(icons.available()):
        width, height, centre_x, centre_y = _ink_box(name)

        assert abs(max(width, height) - icons.INK) <= 1.0, (
            f"{name} is {max(width, height):.2f} units where the set is {icons.INK}"
        )
        assert abs(centre_x) <= 0.5 and abs(centre_y) <= 0.5, (
            f"{name} sits at ({centre_x:+.2f}, {centre_y:+.2f}) rather than in the middle"
        )
        assert min(width, height) > 0, name


def test_no_drawing_reaches_the_edge_of_its_canvas(qapp: object) -> None:
    """The margin the grid leaves is what stops a button looking crowded.

    Two units all round at :data:`icons.INK` of :data:`icons.GRID`. A drawing
    that fills its canvas would sit tighter in the toolbar than the rest and
    risk being clipped by a style that insets the icon at all.
    """
    for name in sorted(icons.available()):
        width, height, _x, _y = _ink_box(name)
        assert max(width, height) < icons.GRID - 1, f"{name} nearly fills its canvas"


def test_a_drawing_comes_out_in_the_colour_it_was_asked_for(qapp: object) -> None:
    """The point of tinting: one set of files, whatever colour the theme is."""
    for asked in (QColor(255, 0, 0), QColor(0, 128, 255)):
        painted = _opaque_colors(icons.icon("open", asked))
        assert painted, "nothing was drawn"
        assert {(c.red(), c.green(), c.blue()) for c in painted} == {
            (asked.red(), asked.green(), asked.blue())
        }


def test_the_same_name_and_colour_are_only_built_once(qapp: object) -> None:
    """Baking seven pixmaps per icon is worth doing once per palette."""
    icons.forget()
    first = icons.icon("save", QColor(10, 20, 30))
    assert icons.icon("save", QColor(10, 20, 30)) is first
    assert icons.icon("save", QColor(30, 20, 10)) is not first, "colour is part of the key"
    icons.forget()
    assert icons.icon("save", QColor(10, 20, 30)) is not first, "forget drops the cache"


def test_a_missing_drawing_costs_the_picture_and_nothing_else(
    qapp: object, caplog: pytest.LogCaptureFixture
) -> None:
    """A toolbar with a gap in it beats a window that refuses to open."""
    icons.forget()
    with caplog.at_level(logging.WARNING, logger="comictrans.gui.icons"):
        icon = icons.icon("no-such-drawing")

    assert icon.isNull()
    assert icon.availableSizes() == []
    assert "no-such-drawing" in caplog.text


def test_every_toolbar_button_carries_a_drawing(qapp: object) -> None:
    window = MainWindow()

    for attribute in (*MainWindow.TOOLBAR_ICONS, "_preview_action"):
        action = getattr(window, attribute)
        assert not action.icon().isNull(), f"{attribute} has no icon"


def test_every_toolbar_button_carries_a_word_to_hover(qapp: object) -> None:
    """An icon-only bar has nothing to read, so the tooltip is the label.

    Checked against the toolbar's own actions rather than the dictionary, so
    that an action added to the bar and forgotten here is caught too.
    """
    window = MainWindow()

    for action in window._toolbar.actions():
        if action.isSeparator():
            continue
        assert action.toolTip().strip(), f"{action.text()!r} has no tooltip"
        assert "&" not in action.toolTip(), "the menu's accelerator is not a tooltip"


def test_the_preview_button_swaps_its_drawing_for_the_overlay(qapp: object) -> None:
    """One action, two states, and the icon is what says which is showing."""
    window = MainWindow()
    showing_page = _opaque_colors(window._preview_action.icon())

    window._showing_preview = True
    window._apply_toolbar_icons()
    showing_preview = _opaque_colors(window._preview_action.icon())

    assert showing_page and showing_preview
    assert len(showing_page) != len(showing_preview), "the same drawing in both states"


def test_the_drawings_follow_the_window_between_light_and_dark(qapp: object) -> None:
    """A black line on dark chrome is a button nobody can see.

    The tint comes from the palette at load time, so switching the
    application between light and dark and letting the change reach the
    window has to repaint the set.
    """
    application = QApplication.instance()
    assert isinstance(application, QApplication)
    original = application.palette()
    window = MainWindow()
    try:
        light = QPalette()
        light.setColor(QPalette.ColorRole.ButtonText, QColor(20, 20, 20))
        application.setPalette(light)
        window.changeEvent(QEvent(QEvent.Type.PaletteChange))
        on_light = _opaque_colors(window._open_action.icon())

        dark = QPalette()
        dark.setColor(QPalette.ColorRole.ButtonText, QColor(235, 235, 235))
        application.setPalette(dark)
        window.changeEvent(QEvent(QEvent.Type.PaletteChange))
        on_dark = _opaque_colors(window._open_action.icon())
    finally:
        application.setPalette(original)
        icons.forget()

    assert {(c.red(), c.green(), c.blue()) for c in on_light} == {(20, 20, 20)}
    assert {(c.red(), c.green(), c.blue()) for c in on_dark} == {(235, 235, 235)}
