from __future__ import annotations

import math
from pathlib import Path

import pytest

from comictrans.model import (
    SHAPE_TOLERANCE,
    Box,
    Color,
    Geometry,
    PlanHeader,
    Region,
    TextCase,
    boxes_overlap,
    convex_hull,
    ellipse_polygon,
    normalised_angle,
    point_in_polygon,
    polygon_area,
    polygon_bounds,
    polygon_is_simple,
    polygons_overlap,
    rectangle_polygon,
    rotate_polygon,
    source_path,
    with_image_order,
)

from .conftest import make_plan


def test_box_geometry() -> None:
    box = Box(10, 20, 40, 60)
    assert (box.width, box.height, box.area) == (30, 40, 1200)
    assert box.center == (25.0, 40.0)
    assert box.corners()[2] == (39, 59)


def test_box_rejects_inversion() -> None:
    with pytest.raises(ValueError, match="inverted"):
        Box(40, 0, 10, 10)


def test_box_union_and_intersection() -> None:
    a, b = Box(0, 0, 10, 10), Box(5, 5, 20, 20)
    assert a.union(b) == Box(0, 0, 20, 20)
    assert a.intersection(b) == Box(5, 5, 10, 10)
    assert a.intersection(Box(50, 50, 60, 60)) is None


def test_box_clipping_keeps_inside_page() -> None:
    assert Box(-10, -10, 50, 50).clipped(30, 30) == Box(0, 0, 30, 30)


def test_boxes_overlap_measures_the_share_of_the_smaller_box() -> None:
    # A small region wholly inside a large one draws over it, whatever the
    # large one's area — which is the whole reason the share is of the
    # smaller rather than of either box or of their union.
    assert boxes_overlap(Box(0, 0, 10, 10), Box(0, 0, 100, 100))


def test_boxes_overlap_wants_more_than_the_ratio_not_exactly_it() -> None:
    # 15 shared pixels of a 100-pixel box: the threshold exactly, which is
    # not past it. One pixel column more is.
    assert not boxes_overlap(Box(0, 0, 10, 10), Box(7, 0, 30, 5)), (
        "15/100 is the threshold, not over"
    )
    assert boxes_overlap(Box(0, 0, 10, 10), Box(6, 0, 30, 5)), "20/100 is over it"


def test_boxes_that_only_touch_do_not_overlap() -> None:
    assert not boxes_overlap(Box(0, 0, 100, 100), Box(100, 0, 200, 100))


def test_source_path_resolves_the_name_a_plan_gives_its_page(tmp_path: Path) -> None:
    """A plan names its pages relative to itself, and ``..`` is a real answer.

    Unpacked chapters put the plan beside the pages; a hand-written plan can
    point out of its own directory. Resolving is what makes the two spellings
    of one file the same path, which is what the per-page hash check and the
    preview cache both compare on.
    """
    plan_path = tmp_path / "chapter" / "comic-plan.yaml"
    assert source_path(plan_path, "page-001.png") == tmp_path / "chapter" / "page-001.png"
    assert source_path(plan_path, "../pages/page-001.png") == tmp_path / "pages" / "page-001.png"


def test_color_hex_round_trip() -> None:
    assert Color.from_hex("#1B2C3D").to_hex() == "#1b2c3d"
    with pytest.raises(ValueError, match="hex colour"):
        Color.from_hex("#fff")


def test_color_clamps_out_of_range_channels() -> None:
    assert Color.from_rgb((-5, 300, 128)) == Color(0, 255, 128)


def test_polygon_bounds_and_area() -> None:
    square = ((0, 0), (10, 0), (10, 10), (0, 10))
    assert polygon_bounds(square) == Box(0, 0, 11, 11)
    assert polygon_area(square) == 100.0


def test_polygon_is_simple_rejects_bowtie() -> None:
    assert polygon_is_simple(((0, 0), (10, 0), (10, 10), (0, 10)))
    assert not polygon_is_simple(((0, 0), (10, 10), (10, 0), (0, 10)))
    assert not polygon_is_simple(((0, 0), (1, 1)))


def _region(image: str, region_id: str) -> Region:
    return Region(
        id=region_id,
        image=image,
        order=1,
        geometry=Geometry.EXACT,
        polygon=((0, 0), (5, 0), (5, 5)),
        fill_color=Color(255, 255, 255),
        text_color=Color(0, 0, 0),
        confidence=0.9,
        source_text="CIAO",
    )


def test_plan_groups_regions_by_image_in_first_seen_order() -> None:
    plan = make_plan(
        PlanHeader(2, "g", "now", "it", "en", "fake", "F", TextCase.UPPER, 0.012, 0.9),
        (_region("b.png", "b-1"), _region("a.png", "a-1"), _region("b.png", "b-2")),
    )
    assert plan.image_names() == ("b.png", "a.png")
    assert [r.id for r in plan.regions_for("b.png")] == ["b-1", "b-2"]


def _three_page_plan() -> object:
    return make_plan(
        PlanHeader(2, "g", "now", "it", "en", "fake", "F", TextCase.UPPER, 0.012, 0.9),
        (
            _region("a.png", "a-1"),
            _region("a.png", "a-2"),
            _region("b.png", "b-1"),
            _region("c.png", "c-1"),
        ),
    )


def test_with_image_order_moves_the_regions_with_the_pages() -> None:
    """The two lists are one order, and this is where that is enforced.

    Walking region by region follows the regions list and takes for granted
    that it is grouped by page in page order. Reordering pages alone would
    leave the page list and the next-region key disagreeing, on a plan that
    still reads as perfectly valid.
    """
    reordered = with_image_order(_three_page_plan(), ("c.png", "a.png", "b.png"))

    assert reordered.image_names() == ("c.png", "a.png", "b.png")
    assert [r.id for r in reordered.regions] == ["c-1", "a-1", "a-2", "b-1"]


def test_with_image_order_keeps_the_order_within_a_page() -> None:
    """Pages move; the reading order inside one is not this function's business."""
    reordered = with_image_order(_three_page_plan(), ("b.png", "a.png", "c.png"))

    assert [r.id for r in reordered.regions_for("a.png")] == ["a-1", "a-2"]


def test_a_page_the_caller_did_not_name_keeps_its_place_at_the_end() -> None:
    """A partial list reorders what it knows about and cannot lose a page."""
    reordered = with_image_order(_three_page_plan(), ("c.png",))

    assert reordered.image_names() == ("c.png", "a.png", "b.png")
    assert len(reordered.regions) == 4


def test_a_name_the_plan_does_not_have_is_ignored() -> None:
    """A stale list — from a page list not yet rebuilt — must not throw."""
    reordered = with_image_order(_three_page_plan(), ("gone.png", "b.png"))

    assert reordered.image_names() == ("b.png", "a.png", "c.png")


def test_reordering_to_the_order_already_in_place_changes_nothing() -> None:
    plan = _three_page_plan()

    reordered = with_image_order(plan, plan.image_names())

    assert reordered.image_names() == plan.image_names()
    assert [r.id for r in reordered.regions] == [r.id for r in plan.regions]


def test_region_is_actionable_only_with_a_translation() -> None:
    region = _region("a.png", "a-1")
    assert not region.is_actionable
    assert region.with_translation("I CAN'T BELIEVE IT!").is_actionable
    assert not region.with_translation("   ").is_actionable


def test_is_untranslated_compares_translation_with_source() -> None:
    region = _region("a.png", "a-1")  # source_text "CIAO", translation ""
    assert not region.is_untranslated, "an empty translation is not 'still Italian'"
    assert region.with_translation("CIAO").is_untranslated
    assert region.with_translation("  CIAO  ").is_untranslated, "whitespace is ignored"
    assert not region.with_translation("HELLO").is_untranslated


def test_a_translation_that_legitimately_matches_its_source_still_renders() -> None:
    # "NO!" is a correct translation of "NO!". It reads as untranslated and is
    # reported as such, but it is never skipped on that basis.
    region = _region("a.png", "a-1").with_translation("CIAO")
    assert region.is_untranslated
    assert region.is_actionable


# -- merging geometry ---------------------------------------------------------

SQUARE = ((0, 0), (100, 0), (100, 100), (0, 100))


def test_point_in_polygon_counts_the_boundary_as_inside() -> None:
    assert point_in_polygon((50, 50), SQUARE)
    assert point_in_polygon((0, 0), SQUARE), "a corner is on it"
    assert point_in_polygon((100, 50), SQUARE), "so is an edge"
    assert not point_in_polygon((101, 50), SQUARE)
    assert not point_in_polygon((-1, 50), SQUARE), "the ray goes right; this is still out"


def test_point_in_polygon_handles_a_concave_shape() -> None:
    # A C, so that a point in its mouth is outside the shape but inside its
    # bounding box — the case a box test gets wrong.
    letter = ((0, 0), (100, 0), (100, 20), (20, 20), (20, 80), (100, 80), (100, 100), (0, 100))
    assert point_in_polygon((10, 50), letter)
    assert not point_in_polygon((60, 50), letter), "in the mouth of the C"


@pytest.mark.parametrize(
    ("other", "expected", "why"),
    [
        (((50, 50), (150, 50), (150, 150), (50, 150)), True, "corners overlap"),
        (((25, 25), (75, 25), (75, 75), (25, 75)), True, "wholly inside"),
        (((-50, -50), (150, -50), (150, 150), (-50, 150)), True, "wholly around"),
        (((100, 0), (200, 0), (200, 100), (100, 100)), True, "sharing an edge"),
        (((101, 0), (200, 0), (200, 100), (101, 100)), False, "a pixel apart"),
        (((40, -200), (60, -200), (60, -100), (40, -100)), False, "far above"),
    ],
)
def test_polygons_overlap_is_about_area_not_bounding_boxes(
    other: tuple[tuple[int, int], ...], expected: bool, why: str
) -> None:
    assert polygons_overlap(SQUARE, other) is expected, why
    assert polygons_overlap(other, SQUARE) is expected, f"{why}, the other way round"


def test_two_diagonal_boxes_do_not_overlap_though_their_boxes_do() -> None:
    # The case the bounding-box test the GUI warns with gets wrong, and the
    # reason merging cannot use it: a hull across these would swallow the
    # artwork between them.
    first = ((0, 0), (40, 0), (40, 40), (0, 40))
    second = ((60, 60), (100, 60), (100, 100), (60, 100))
    assert not polygons_overlap(first, second)


def test_the_hull_of_two_overlapping_boxes_covers_both() -> None:
    other = ((50, 50), (150, 50), (150, 150), (50, 150))

    hull = convex_hull(SQUARE + other)

    assert set(hull) == {(0, 0), (100, 0), (150, 50), (150, 150), (50, 150), (0, 100)}
    assert polygon_is_simple(hull)
    for point in (*SQUARE, *other):
        assert point_in_polygon(point, hull), f"{point} is not covered"


def test_the_hull_drops_points_that_add_no_corner() -> None:
    with_extras = (*SQUARE, (50, 0), (50, 50), (100, 50))

    assert set(convex_hull(with_extras)) == set(SQUARE), "collinear and interior points go"
    assert len(convex_hull(with_extras)) == 4


# -- shapes drawn by hand ------------------------------------------------------


def test_a_rectangle_is_the_same_whichever_corner_the_drag_began_at() -> None:
    expected = ((10, 20), (50, 20), (50, 80), (10, 80))

    for corner, opposite in (((10, 20), (50, 80)), ((50, 80), (10, 20)), ((50, 20), (10, 80))):
        assert rectangle_polygon(corner, opposite) == expected


BOXES = [
    (width, height)
    for width in (4, 5, 9, 30, 101, 400, 1029)
    for height in (4, 7, 30, 250, 697, 1200)
]
"""From the smallest drag the canvas keeps to a balloon bigger than a fixture's."""


@pytest.mark.parametrize(("width", "height"), BOXES)
def test_an_ellipse_is_a_region_a_plan_can_hold(width: int, height: int) -> None:
    """One simple ring of three corners or more, whatever box it was drawn in."""
    polygon = ellipse_polygon((10, 10), (10 + width, 10 + height))

    assert len(polygon) >= 3
    assert len(set(polygon)) == len(polygon), "no corner twice"
    assert polygon_is_simple(polygon)


@pytest.mark.parametrize(("width", "height"), BOXES)
def test_an_ellipse_fills_the_box_it_was_drawn_in(width: int, height: int) -> None:
    """It touches all four sides and goes past none of them."""
    polygon = ellipse_polygon((10 + width, 10), (10, 10 + height))

    # Exclusive on the right and at the bottom, as every Box is.
    assert polygon_bounds(polygon) == Box(10, 10, 11 + width, 11 + height)


@pytest.mark.parametrize("radius", [20, 100, 400, 800])
def test_an_ellipse_keeps_within_its_tolerance_of_the_curve(radius: int) -> None:
    """A circle, where the furthest a chord strays is simple to measure.

    Half a pixel more than the tolerance, for the corners being whole pixels.
    """
    polygon = ellipse_polygon((0, 0), (2 * radius, 2 * radius))
    worst = 0.0
    for index, (x1, y1) in enumerate(polygon):
        x2, y2 = polygon[(index + 1) % len(polygon)]
        middle = math.hypot((x1 + x2) / 2 - radius, (y1 + y2) / 2 - radius)
        worst = max(worst, radius - middle, abs(math.hypot(x1 - radius, y1 - radius) - radius))

    assert worst <= SHAPE_TOLERANCE + 0.5


def test_an_ellipse_has_as_many_corners_as_its_size_needs() -> None:
    """Measured counts, the ones the docstring quotes; a multiple of four each."""
    counts = [len(ellipse_polygon((0, 0), (2 * radius, 2 * radius))) for radius in (100, 400)]

    assert counts == [24, 48]
    assert len(ellipse_polygon((10, 10), (1039, 707))) == 52


# -- turning a region ----------------------------------------------------------


def test_a_quarter_turn_is_clockwise_about_the_middle_of_the_box() -> None:
    """y grows downwards, so a positive angle turns the way a clock does."""
    wide = rectangle_polygon((10, 20), (50, 40))

    assert rotate_polygon(wide, 90) == ((40, 10), (40, 50), (20, 50), (20, 10))
    assert polygon_bounds(rotate_polygon(wide, 90)) == Box(20, 10, 41, 51), "same middle"


def test_a_turn_there_and_back_and_a_full_turn_leave_it_as_it_was() -> None:
    wide = rectangle_polygon((10, 20), (50, 40))

    assert rotate_polygon(wide, 360) == wide
    assert rotate_polygon(rotate_polygon(wide, 30), -30) == wide


@pytest.mark.parametrize("degrees", [7, 15, 33, 45, 90, 150, 270])
def test_a_turned_ellipse_is_still_a_region_a_plan_can_hold(degrees: int) -> None:
    ellipse = ellipse_polygon((100, 100), (500, 300))

    turned = rotate_polygon(ellipse, degrees)

    assert len(turned) == len(ellipse), "every corner kept"
    assert polygon_is_simple(turned)


@pytest.mark.parametrize(
    ("given", "written"),
    [
        (0.0, 0.0),
        (-0.0, 0.0),
        (30.0, 30.0),
        (-30.0, -30.0),
        (180.0, 180.0),
        (-180.0, 180.0),
        (190.0, -170.0),
        (-190.0, 170.0),
        (360.0, 0.0),
        (-345.0, 15.0),
        (12.345, 12.3),
        (-29.7, -29.7),
        (359.96, 0.0),
    ],
)
def test_an_angle_is_written_one_way_within_a_half_turn(given: float, written: float) -> None:
    assert normalised_angle(given) == written
    assert str(normalised_angle(given)) != "-0.0", "a level region is 0, not minus 0"
