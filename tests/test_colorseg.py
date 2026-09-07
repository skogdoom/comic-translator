"""Colour segmentation: finding a region a grey threshold cannot separate."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from comictrans.config import DetectConfig
from comictrans.detect.colorseg import background_color, segment
from comictrans.model import Box, polygon_area, polygon_bounds, polygon_is_simple

from .conftest import draw_glyph_marks

PAGE = (600, 800)
CFG = DetectConfig()
TEXT = Box(180, 380, 420, 410)
CAPTION_BOX = Box(140, 340, 460, 450)


def _page(
    fill: tuple[int, int, int],
    art: tuple[int, int, int],
    ink: tuple[int, int, int] = (20, 20, 20),
    box: Box | None = None,
) -> np.ndarray:
    """A flat caption box on flat art, with lettering in it."""
    box = box or CAPTION_BOX
    image = Image.new("RGB", PAGE, art)
    draw = ImageDraw.Draw(image)
    draw.rectangle([box.left, box.top, box.right, box.bottom], fill=fill)
    draw_glyph_marks(draw, TEXT, ink)
    return np.asarray(image, dtype=np.uint8)


def test_a_box_the_same_brightness_as_its_surroundings_is_still_found() -> None:
    # Tan on blue-grey: near-identical luma, so a grey threshold puts both on
    # the same side and finds no boundary at all. Colour separates them.
    fill, art = (201, 149, 78), (120, 168, 150)
    assert abs(np.dot(fill, [0.299, 0.587, 0.114]) - np.dot(art, [0.299, 0.587, 0.114])) < 12

    polygon = segment(_page(fill, art), TEXT, CFG, page_height=PAGE[1])

    assert polygon is not None
    bounds = polygon_bounds(polygon)
    assert 300 < bounds.width < 340
    assert 95 < bounds.height < 130
    assert polygon_is_simple(polygon)


def test_light_lettering_on_a_dark_fill_seeds_from_the_fill() -> None:
    # The centre of a text box is as likely to be a glyph as background, and
    # on light-on-dark it reliably is. The seed is the modal colour, not the
    # centre pixel.
    page = _page((25, 25, 30), (240, 240, 235), ink=(250, 250, 250))
    assert tuple(int(v) for v in background_color(page, TEXT)) == (25, 25, 30)
    assert segment(page, TEXT, CFG, page_height=PAGE[1]) is not None


def test_a_region_far_larger_than_its_lettering_is_still_returned() -> None:
    # The size-against-lettering judgement needs every line that belongs to
    # the shape, so it lives in the caller, not here.
    page = _page((201, 149, 78), (120, 168, 150), box=Box(20, 40, 580, 700))
    polygon = segment(page, TEXT, CFG, page_height=PAGE[1])
    assert polygon is None or polygon_area(polygon) > 0


def test_text_on_flat_art_with_no_box_finds_the_whole_area_or_nothing() -> None:
    # There is no box here, so whatever comes back is the art itself, and the
    # size guards are what stop it being taken for a balloon.
    image = Image.new("RGB", PAGE, (240, 235, 220))
    draw_glyph_marks(ImageDraw.Draw(image), TEXT, (20, 20, 20))
    polygon = segment(np.asarray(image, dtype=np.uint8), TEXT, CFG, page_height=PAGE[1])
    assert polygon is None, "a page-sized colour region is not a balloon"


def test_tolerance_controls_what_counts_as_the_same_fill() -> None:
    page = _page((201, 149, 78), (206, 156, 86))  # only a few levels apart
    loose = segment(page, TEXT, DetectConfig(color_tolerance=60.0), page_height=PAGE[1])
    tight = segment(page, TEXT, DetectConfig(color_tolerance=4.0), page_height=PAGE[1])
    # Loose enough and the box merges with the art, so the guards refuse it.
    assert loose is None or polygon_area(loose) > polygon_area(tight or loose)


def test_an_empty_box_yields_no_seed() -> None:
    page = _page((201, 149, 78), (120, 168, 150))
    assert background_color(page, Box(10, 10, 10, 10)) is None


def test_segmentation_can_be_turned_off(balloon_page: tuple[np.ndarray, list[Box]]) -> None:
    from pathlib import Path

    from comictrans.detect import find_regions
    from comictrans.imaging import PageImage, PageMeta

    from .conftest import lines_for

    fill, art = (201, 149, 78), (120, 168, 150)
    array = _page(fill, art)
    page = PageImage(
        path=Path("p.png"),
        rgb=array,
        sha256="0" * 64,
        meta=PageMeta(format="PNG", mode="RGB", dpi=None, icc_profile=None),
    )
    lines = lines_for([TEXT], ["CAPTION"])
    with_color = find_regions(page, lines, DetectConfig())
    without = find_regions(page, lines, DetectConfig(color_segmentation=False))

    assert polygon_area(with_color[0].polygon) > polygon_area(without[0].polygon)
