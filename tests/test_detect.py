from __future__ import annotations

import numpy as np

from comictrans.config import DetectConfig
from comictrans.detect import find_regions, reading_order
from comictrans.detect.color import glyph_mask, polygon_mask
from comictrans.detect.contour import build_candidates, enclosing_candidate
from comictrans.detect.fallback import approximate_polygon, cluster_lines
from comictrans.imaging import PageImage, PageMeta
from comictrans.model import Box, Geometry, polygon_area, polygon_bounds, polygon_is_simple
from comictrans.ocr.base import OcrLine

from .conftest import (
    ART_DARK,
    ART_LIGHT,
    BALLOON_WHITE,
    INK_BLACK,
    lines_for,
    make_page_array,
)

CFG = DetectConfig()


def _page(array: np.ndarray, name: str = "page-001.png") -> PageImage:
    from pathlib import Path

    return PageImage(
        path=Path(name),
        rgb=array,
        sha256="0" * 64,
        meta=PageMeta(format="PNG", mode="RGB", dpi=None, icc_profile=None),
    )


def test_balloon_gives_an_exact_polygon_not_a_bounding_box(
    balloon_page: tuple[np.ndarray, list[Box]],
) -> None:
    array, boxes = balloon_page
    regions = find_regions(_page(array), lines_for(boxes, ["NON CI POSSO", "CREDERE!"]), CFG)

    assert len(regions) == 1
    region = regions[0]
    assert region.geometry is Geometry.EXACT
    # A traced ellipse has many vertices and covers well under its bounding box.
    assert len(region.polygon) > 4
    bounds = region.bounds
    assert polygon_area(region.polygon) < bounds.area * 0.9
    assert bounds.left <= 130 and bounds.right >= 410


def test_multiple_lines_in_one_balloon_merge_into_one_region(
    balloon_page: tuple[np.ndarray, list[Box]],
) -> None:
    array, boxes = balloon_page
    regions = find_regions(_page(array), lines_for(boxes, ["NON CI POSSO", "CREDERE!"]), CFG)
    assert len(regions) == 1
    assert len(regions[0].lines) == 2


def test_colors_are_sampled_not_assumed(balloon_page: tuple[np.ndarray, list[Box]]) -> None:
    array, boxes = balloon_page
    region = find_regions(_page(array), lines_for(boxes, ["A", "B"]), CFG)[0]
    assert region.fill_color.r > 200
    assert region.text_color.r < 80


def test_white_on_black_caption_round_trips(
    inverted_caption_page: tuple[np.ndarray, list[Box]],
) -> None:
    array, boxes = inverted_caption_page
    regions = find_regions(_page(array), lines_for(boxes, ["MEANWHILE"]), CFG)

    assert len(regions) == 1
    region = regions[0]
    assert region.geometry is Geometry.EXACT
    assert region.fill_color.r < 80, "dark caption fill"
    assert region.text_color.r > 200, "light caption text"


def test_text_on_bare_art_falls_back_to_approximate_geometry(
    borderless_page: tuple[np.ndarray, list[Box]],
) -> None:
    array, boxes = borderless_page
    regions = find_regions(_page(array), lines_for(boxes, ["CRASH"]), CFG)

    assert len(regions) == 1
    region = regions[0]
    assert region.geometry is Geometry.APPROXIMATE
    # Union of the text boxes plus a margin, so it is strictly larger.
    assert region.bounds.left < boxes[0].left
    assert region.bounds.right > boxes[0].right


def test_no_lines_gives_no_regions() -> None:
    array = make_page_array((200, 200), ART_DARK, [])
    assert find_regions(_page(array), [], CFG) == []


def test_contour_candidates_reject_page_sized_blobs() -> None:
    # A single flat page thresholds into one huge blob, which is not a balloon.
    array = make_page_array((400, 400), ART_DARK, [])
    from cv2 import COLOR_RGB2GRAY, cvtColor

    gray = cvtColor(array, COLOR_RGB2GRAY)
    page_area = 400 * 400
    for candidate in build_candidates(gray, CFG):
        assert candidate.area <= page_area * CFG.max_contour_area_ratio


def test_enclosing_candidate_prefers_the_tightest_container(
    balloon_page: tuple[np.ndarray, list[Box]],
) -> None:
    array, boxes = balloon_page
    from cv2 import COLOR_RGB2GRAY, cvtColor

    candidates = build_candidates(cvtColor(array, COLOR_RGB2GRAY), CFG)
    index = enclosing_candidate(candidates, boxes[0], CFG)
    assert index is not None
    chosen = candidates[index]
    smaller = [c for c in candidates if c.area < chosen.area and c.contains_box(boxes[0])]
    assert smaller == []


def test_reading_order_is_top_to_bottom_then_left_to_right() -> None:
    boxes = [
        Box(400, 100, 500, 130),  # top right
        Box(100, 100, 200, 130),  # top left
        Box(100, 400, 200, 430),  # bottom left
    ]
    array = make_page_array(
        (600, 800),
        ART_DARK,
        [
            (
                "ellipse",
                Box(box.left - 40, box.top - 40, box.right + 40, box.bottom + 40),
                BALLOON_WHITE,
                INK_BLACK,
                [box],
            )
            for box in boxes
        ],
    )
    regions = find_regions(_page(array), lines_for(boxes, ["C", "A", "B"]), CFG)
    ordered = [polygon_bounds(r.polygon) for r in regions]
    assert [(b.top < 200, b.left < 300) for b in ordered] == [
        (True, True),
        (True, False),
        (False, True),
    ]


def test_reading_order_tolerates_slight_vertical_offsets() -> None:
    from comictrans.detect import DetectedRegion
    from comictrans.model import Color

    def region(left: int, top: int) -> DetectedRegion:
        return DetectedRegion(
            polygon=((left, top), (left + 100, top), (left + 100, top + 40), (left, top + 40)),
            geometry=Geometry.EXACT,
            lines=(),
            fill_color=Color(255, 255, 255),
            text_color=Color(0, 0, 0),
        )

    # The right-hand balloon sits three pixels higher; it must still read second.
    ordered = reading_order([region(300, 97), region(100, 100)], CFG)
    assert [r.bounds.left for r in ordered] == [100, 300]


def test_cluster_lines_groups_stacked_lines_only() -> None:
    stacked = [
        OcrLine("UNA", Box(100, 100, 200, 130), 0.9),
        OcrLine("DUE", Box(100, 135, 200, 165), 0.9),
    ]
    far_away = OcrLine("TRE", Box(100, 600, 200, 630), 0.9)
    clusters = cluster_lines([*stacked, far_away], CFG, width=600, height=800)
    assert [len(c) for c in clusters] == [2, 1]


def test_cluster_lines_keeps_side_by_side_text_apart() -> None:
    clusters = cluster_lines(
        [
            OcrLine("SX", Box(100, 100, 200, 130), 0.9),
            OcrLine("DX", Box(400, 105, 500, 135), 0.9),
        ],
        CFG,
        width=600,
        height=800,
    )
    assert [len(c) for c in clusters] == [1, 1]


def test_approximate_polygon_pads_and_clips_to_the_page() -> None:
    polygon = approximate_polygon([Box(0, 0, 50, 20)], CFG, width=100, height=100)
    bounds = polygon_bounds(polygon)
    assert bounds.left == 0 and bounds.top == 0  # clipped, not negative
    assert bounds.right > 50


def test_glyph_mask_finds_ink_as_the_minority_class(
    balloon_page: tuple[np.ndarray, list[Box]],
) -> None:
    array, boxes = balloon_page
    region = polygon_mask(Box(120, 100, 420, 260).as_polygon(), array.shape[0], array.shape[1])
    mask = glyph_mask(array, region, tuple(boxes))
    ink = int(np.count_nonzero(mask))
    assert 0 < ink < sum(box.area for box in boxes) * 0.6


def test_simplify_never_returns_a_self_intersecting_polygon() -> None:
    # A bowtie: approxPolyDP can fold a ragged contour over itself, and the
    # plan file reader rejects a self-intersecting polygon, so extract must
    # never emit one. The convex hull is the fallback.
    import numpy as np

    from comictrans.detect.contour import _simplify

    bowtie = np.array([[[0, 0]], [[100, 100]], [[100, 0]], [[0, 100]]], dtype=np.int32)
    polygon = _simplify(bowtie, CFG)

    assert polygon is not None
    assert polygon_is_simple(polygon)


def test_simplify_keeps_a_well_behaved_contour_intact() -> None:
    import numpy as np

    from comictrans.detect.contour import _simplify

    square = np.array([[[0, 0]], [[100, 0]], [[100, 100]], [[0, 100]]], dtype=np.int32)
    polygon = _simplify(square, CFG)
    assert polygon is not None
    assert set(polygon) == {(0, 0), (100, 0), (100, 100), (0, 100)}


def test_a_band_of_artwork_spanning_the_page_is_not_a_balloon() -> None:
    # Text on a wide flat band: the band is solid, convex and under the area
    # cap, so only the extent guard stops it being taken for a balloon and
    # erased wholesale.
    boxes = [Box(200, 690, 400, 714)]
    array = make_page_array(
        (600, 800),
        ART_LIGHT,
        [("rect", Box(10, 650, 590, 760), (240, 220, 180), INK_BLACK, boxes)],
    )
    regions = find_regions(_page(array), lines_for(boxes, ["ON THE SAND"]), CFG)

    assert len(regions) == 1
    assert regions[0].geometry is Geometry.APPROXIMATE
    assert regions[0].bounds.width < 600 * CFG.max_extent_ratio


def test_a_normal_balloon_is_unaffected_by_the_extent_guard(
    balloon_page: tuple[np.ndarray, list[Box]],
) -> None:
    array, boxes = balloon_page
    region = find_regions(_page(array), lines_for(boxes, ["A", "B"]), CFG)[0]
    assert region.geometry is Geometry.EXACT
    assert region.bounds.width < 600 * CFG.max_extent_ratio


def test_candidate_bounds_reject_boxes_outside_them_cheaply(
    balloon_page: tuple[np.ndarray, list[Box]],
) -> None:
    from cv2 import COLOR_RGB2GRAY, cvtColor

    candidates = build_candidates(cvtColor(balloon_page[0], COLOR_RGB2GRAY), CFG)
    far_away = Box(10, 10, 40, 40)
    for candidate in candidates:
        if not candidate.bounds.intersection(far_away):
            assert not candidate.contains_box(far_away)


def test_sampled_colors_never_come_back_indistinguishable() -> None:
    # A flat region with no real glyphs: whatever the sampler measures, apply
    # must not end up drawing text in the same colour as the fill.
    from comictrans.detect.color import sample_colors
    from comictrans.model import Color

    flat = make_page_array((200, 200), (128, 128, 128), [])
    fill, text = sample_colors(
        flat, Box(20, 20, 180, 180).as_polygon(), (Box(50, 90, 150, 110),), page_height=200
    )
    assert isinstance(fill, Color) and isinstance(text, Color)
    assert (
        abs(
            (0.299 * fill.r + 0.587 * fill.g + 0.114 * fill.b)
            - (0.299 * text.r + 0.587 * text.g + 0.114 * text.b)
        )
        >= 32.0
    )


def test_clusters_do_not_chain_across_the_page() -> None:
    # Adjacency is transitive: each line overlaps the next, so without the
    # extent cap these would fuse into one region spanning the whole page.
    chain = [
        OcrLine(f"L{i}", Box(x, 100, x + 120, 130), 0.9) for i, x in enumerate(range(0, 560, 80))
    ]
    clusters = cluster_lines(chain, CFG, width=600, height=800)

    assert len(clusters) > 1
    for cluster in clusters:
        union = cluster[0].box
        for line in cluster[1:]:
            union = union.union(line.box)
        assert union.width <= 600 * CFG.max_extent_ratio


def test_a_burst_balloon_is_traced_not_rejected_for_being_spiky() -> None:
    # A shout balloon's spikes cost it solidity: measured at 0.64 on the
    # fixture page, where a 0.80 threshold rejected it and fell back to a box
    # around the text, throwing away the shape the polygon exists to capture.
    import numpy as np
    from PIL import Image, ImageDraw

    from .conftest import draw_glyph_marks

    box = Box(220, 290, 380, 314)
    image = Image.new("RGB", (600, 800), ART_DARK)
    draw = ImageDraw.Draw(image)
    spikes: list[tuple[int, int]] = []
    for i in range(24):
        angle = i * np.pi / 12
        radius = 160 if i % 2 == 0 else 95
        spikes.append((int(300 + radius * np.cos(angle)), int(300 + radius * 0.75 * np.sin(angle))))
    draw.polygon(spikes, fill=BALLOON_WHITE, outline=INK_BLACK, width=3)
    draw_glyph_marks(draw, box, INK_BLACK)
    array = np.asarray(image, dtype=np.uint8)

    regions = find_regions(_page(array), lines_for([box], ["AAARGH!"]), CFG)

    assert len(regions) == 1
    assert regions[0].geometry is Geometry.EXACT, "burst balloon fell back to a box"
    assert len(regions[0].polygon) > 8, "spikes were flattened away"
