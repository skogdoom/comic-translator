"""A brush stroke, as the region it paints.

No Qt here: this module is numpy and OpenCV, the same as ``sampling``.
"""

from __future__ import annotations

import math
from itertools import pairwise

import pytest

from comictrans.gui.brush import BRUSH_SIZES, MIN_BRUSH, brush_diameter, stroke_outline
from comictrans.model import SHAPE_TOLERANCE, Point, point_in_polygon, polygon_bounds

PAGE = (2840, 3880)
"""The six-panel fixture's size, which the brush sizes were measured on."""


def _distance_to_path(x: float, y: float, points: list[Point]) -> float:
    """How far a pixel's middle is from the line the brush's middle drew."""
    pairs = list(pairwise(points)) or [(points[0], points[0])]
    best = math.inf
    for (ax, ay), (bx, by) in pairs:
        dx, dy = bx - ax, by - ay
        span = dx * dx + dy * dy
        along = 0.0 if span == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / span))
        best = min(best, math.hypot(x - ax - along * dx, y - ay - along * dy))
    return best


def _painted_but_outside(points: list[Point], diameter: int) -> list[Point]:
    """Pixels well inside the stroke that the ring leaves out.

    "Well inside" is the stroke narrowed by the tolerance the ring is allowed
    and one pixel for rounding a pixel's middle, so what is asked is the
    promise and not how OpenCV rounds the edge of a band.
    """
    ring = stroke_outline(points, diameter, PAGE)
    assert ring is not None
    reach = diameter / 2 - SHAPE_TOLERANCE - 1
    xs = [x for x, _y in points]
    ys = [y for _x, y in points]
    return [
        (x, y)
        for y in range(min(ys) - diameter, max(ys) + diameter + 1)
        for x in range(min(xs) - diameter, max(xs) + diameter + 1)
        if _distance_to_path(x, y, points) <= reach and not point_in_polygon((x, y), ring)
    ]


def test_a_dab_is_a_disc_as_wide_as_the_brush() -> None:
    ring = stroke_outline([(500, 400)], 40, PAGE)

    assert ring is not None
    box = polygon_bounds(ring)
    assert abs(box.width - 40) <= 2 and abs(box.height - 40) <= 2
    assert abs((box.left + box.right) / 2 - 500) <= 1
    assert abs((box.top + box.bottom) / 2 - 400) <= 1
    assert all(abs(math.hypot(x - 500, y - 400) - 20) <= 1.5 for x, y in ring), "round"


def test_a_loop_is_filled_in() -> None:
    """Round a balloon's lettering and back: a doughnut, stored as its outside."""
    loop = [
        (
            round(1000 + 300 * math.cos(t / 40 * math.tau)),
            round(800 + 200 * math.sin(t / 40 * math.tau)),
        )
        for t in range(41)
    ]

    ring = stroke_outline(loop, 20, PAGE)

    assert ring is not None
    assert point_in_polygon((1000, 800), ring), "the middle, which nothing painted"
    box = polygon_bounds(ring)
    assert abs(box.width - 620) <= 3 and abs(box.height - 420) <= 3


def test_nothing_painted_is_left_out_of_a_long_thin_stroke() -> None:
    """The stroke detect's own tolerance loses most of, measured.

    Detect's tolerance grows with the outline, and a long zig-zag with a fine
    brush has a long outline: at 0.4% of it, the ring cuts across the band.
    """
    zigzag = [(300 + 40 * step, 500 + (60 if step % 2 else 0)) for step in range(40)]

    assert _painted_but_outside(zigzag, 5) == []


def test_nothing_painted_is_left_out_of_a_broad_scribble() -> None:
    scribble = [(600, 600), (900, 640), (650, 760), (980, 820), (700, 900), (1000, 960)]

    assert _painted_but_outside(scribble, 78) == []


def test_a_brush_near_the_edge_is_clipped_to_the_page() -> None:
    along_the_corner = [(0, 300), (0, 0), (300, 0)]

    ring = stroke_outline(along_the_corner, 155, PAGE)

    assert ring is not None
    assert min(x for x, _y in ring) == 0 and min(y for _x, y in ring) == 0
    assert (0, 0) in ring or point_in_polygon((1, 1), ring), "the corner itself was painted"

    far_corner = stroke_outline([(PAGE[0] - 1, PAGE[1] - 1)], 155, PAGE)
    assert far_corner is not None
    assert max(x for x, _y in far_corner) == PAGE[0] - 1
    assert max(y for _x, y in far_corner) == PAGE[1] - 1


def test_a_stroke_that_crosses_itself_is_one_outline() -> None:
    """One stroke is one connected band, so one ring — never a piece to drop."""
    figure_eight = [
        (
            round(800 + 200 * math.sin(t / 60 * math.tau)),
            round(800 + 150 * math.sin(t / 30 * math.tau)),
        )
        for t in range(61)
    ]

    ring = stroke_outline(figure_eight, 10, PAGE)

    assert ring is not None
    assert point_in_polygon((700, 800), ring) and point_in_polygon((900, 800), ring)


def test_a_brush_is_the_same_share_of_a_page_at_any_resolution() -> None:
    """The fixture page scanned at two resolutions, 1680 and 504 tall."""
    for share in BRUSH_SIZES:
        high, low = brush_diameter(share, 1680), brush_diameter(share, 504)
        assert abs(high / 1680 - low / 504) <= 1 / 504, share


def test_the_brushes_run_from_a_line_of_lettering_doubling() -> None:
    assert brush_diameter(BRUSH_SIZES[0], 3880) == 39, "34px lettering on that page"
    assert [b / a for a, b in pairwise(BRUSH_SIZES)] == [2.0] * 3


@pytest.mark.parametrize("height", [0, 100, 250])
def test_a_brush_on_a_small_page_is_never_narrower_than_the_minimum(height: int) -> None:
    assert brush_diameter(BRUSH_SIZES[0], height) == MIN_BRUSH
