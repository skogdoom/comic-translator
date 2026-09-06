from __future__ import annotations

import numpy as np

from comictrans.config import DetectConfig
from comictrans.detect import find_regions, reading_order
from comictrans.detect.color import glyph_mask, polygon_mask
from comictrans.detect.contour import build_candidates, enclosing_candidate
from comictrans.detect.fallback import approximate_polygon, cluster_lines
from comictrans.imaging import PageImage, PageMeta
from comictrans.model import Box, Geometry, polygon_area, polygon_bounds
from comictrans.ocr.base import OcrLine

from .conftest import ART_DARK, BALLOON_WHITE, INK_BLACK, lines_for, make_page_array

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
    clusters = cluster_lines([*stacked, far_away], CFG)
    assert [len(c) for c in clusters] == [2, 1]


def test_cluster_lines_keeps_side_by_side_text_apart() -> None:
    clusters = cluster_lines(
        [
            OcrLine("SX", Box(100, 100, 200, 130), 0.9),
            OcrLine("DX", Box(400, 105, 500, 135), 0.9),
        ],
        CFG,
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
