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
