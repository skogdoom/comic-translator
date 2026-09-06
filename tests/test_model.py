from __future__ import annotations

import pytest

from comictrans.model import (
    Box,
    Color,
    Geometry,
    Plan,
    PlanHeader,
    Region,
    TextCase,
    polygon_area,
    polygon_bounds,
    polygon_is_simple,
)


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
        image_sha256="0" * 64,
        order=1,
        geometry=Geometry.EXACT,
        polygon=((0, 0), (5, 0), (5, 5)),
        fill_color=Color(255, 255, 255),
        text_color=Color(0, 0, 0),
        confidence=0.9,
        source_text="CIAO",
    )


def test_plan_groups_regions_by_image_in_first_seen_order() -> None:
    plan = Plan(
        header=PlanHeader(1, "g", "now", "it", "en", "fake", "F", TextCase.UPPER, 0.012, 0.9),
        regions=(_region("b.png", "b-1"), _region("a.png", "a-1"), _region("b.png", "b-2")),
    )
    assert plan.images() == ("b.png", "a.png")
    assert [r.id for r in plan.regions_for("b.png")] == ["b-1", "b-2"]


def test_region_is_actionable_only_with_a_translation() -> None:
    region = _region("a.png", "a-1")
    assert not region.is_actionable
    assert region.with_translation("I CAN'T BELIEVE IT!").is_actionable
    assert not region.with_translation("   ").is_actionable
