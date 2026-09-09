from __future__ import annotations

import pytest

from comictrans.model import (
    Box,
    Color,
    Geometry,
    PlanHeader,
    Region,
    TextCase,
    convex_hull,
    point_in_polygon,
    polygon_area,
    polygon_bounds,
    polygon_is_simple,
    polygons_overlap,
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
