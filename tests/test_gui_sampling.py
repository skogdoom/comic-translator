"""Colours measured off the page for a region drawn by hand.

No Qt here: this module is numpy and OpenCV, the same as the rest of detect.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from comictrans.gui.sampling import color_at, sample_region_colors
from comictrans.imaging import load_page
from comictrans.model import Box

from .conftest import ART_DARK, ART_LIGHT, BALLOON_WHITE, INK_BLACK, make_page_array, save_page

BALLOON = Box(120, 100, 420, 260)
TEXT = Box(160, 140, 360, 164)


def _page(tmp_path: Path, array: np.ndarray) -> object:
    return load_page(save_page(array, tmp_path / "page.png"))


def test_a_hand_drawn_outline_takes_its_colours_from_what_is_inside_it(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        make_page_array(
            (600, 800), ART_DARK, [("ellipse", BALLOON, BALLOON_WHITE, INK_BLACK, [TEXT])]
        ),
    )

    fill, text = sample_region_colors(page, BALLOON.as_polygon())  # type: ignore[arg-type]

    assert fill.as_tuple() == BALLOON_WHITE, "the balloon, not the art around it"
    assert text.as_tuple() == INK_BLACK, "the lettering inside it, not a guess"


def test_light_lettering_on_a_dark_caption_is_not_read_upside_down(tmp_path: Path) -> None:
    # The case the whole sampler exists for: never assume black on white.
    page = _page(
        tmp_path,
        make_page_array(
            (600, 800), ART_LIGHT, [("rect", BALLOON, INK_BLACK, BALLOON_WHITE, [TEXT])]
        ),
    )

    fill, text = sample_region_colors(page, BALLOON.as_polygon())  # type: ignore[arg-type]

    assert fill.as_tuple() == INK_BLACK
    assert text.as_tuple() == BALLOON_WHITE


def test_an_empty_balloon_still_gets_a_readable_pair(tmp_path: Path) -> None:
    # Nothing to split into ink and ground, so the sampler's contrast floor
    # decides — which is what a balloon you are about to letter wants.
    page = _page(
        tmp_path,
        make_page_array((600, 800), ART_DARK, [("ellipse", BALLOON, BALLOON_WHITE, INK_BLACK, [])]),
    )

    fill, text = sample_region_colors(page, BALLOON.as_polygon())  # type: ignore[arg-type]

    assert fill.as_tuple() == BALLOON_WHITE
    assert text.as_tuple() == (0, 0, 0), "black on a light balloon"


def test_a_picked_colour_is_the_median_of_the_pixels_around_the_point(tmp_path: Path) -> None:
    array = make_page_array(
        (600, 800), ART_DARK, [("ellipse", BALLOON, BALLOON_WHITE, INK_BLACK, [TEXT])]
    )
    page = _page(tmp_path, array)

    assert color_at(page, (270, 220)).as_tuple() == BALLOON_WHITE  # type: ignore[arg-type]
    assert color_at(page, (20, 20)).as_tuple() == ART_DARK  # type: ignore[arg-type]
    # A corner: the sample square is clipped to the page rather than empty.
    assert color_at(page, (0, 0)).as_tuple() == ART_DARK  # type: ignore[arg-type]
