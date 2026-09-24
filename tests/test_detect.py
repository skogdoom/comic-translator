from __future__ import annotations

import cv2
import numpy as np

from comictrans.config import DetectConfig
from comictrans.detect import (
    _build,
    _merge_overlapping,
    find_regions,
    lines_inside,
    reading_order,
)
from comictrans.detect.color import glyph_mask, polygon_mask
from comictrans.detect.contour import build_candidates, enclosing_candidate, simplified_rings
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


def test_a_panel_full_of_artwork_is_not_mistaken_for_a_balloon() -> None:
    # Every other guard is a fraction of the page, so on a page of small
    # panels a whole panel passes them all. Interior uniformity is what
    # separates a flat balloon fill from a panel of art.
    import numpy as np
    from PIL import Image, ImageDraw

    from .conftest import draw_glyph_marks

    boxes = [Box(200, 300, 420, 330)]
    image = Image.new("RGB", (600, 800), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle([100, 200, 500, 500], fill=(180, 120, 90), outline=INK_BLACK, width=4)
    # Busy enough that no single colour dominates, the way real art is.
    for i in range(20):
        draw.rectangle(
            [104 + i * 20, 204, 124 + i * 20, 496],
            fill=(20 + i * 11, 240 - i * 11, 40 + (i * 37) % 200),
        )
    draw_glyph_marks(draw, boxes[0], INK_BLACK)

    regions = find_regions(
        _page(np.asarray(image, dtype=np.uint8)), lines_for(boxes, ["ON THE ART"]), CFG
    )

    assert len(regions) == 1
    assert regions[0].geometry is Geometry.APPROXIMATE, "the panel was traced as a balloon"
    assert regions[0].bounds.width < 400, "the region swallowed the panel"


def test_interior_uniformity_separates_a_flat_fill_from_artwork() -> None:
    import numpy as np
    from PIL import Image, ImageDraw

    from comictrans.detect.color import interior_uniformity

    flat = np.full((300, 300, 3), 250, dtype=np.uint8)
    polygon = Box(50, 50, 250, 250).as_polygon()
    assert interior_uniformity(flat, polygon, page_height=300, tolerance=24.0) > 0.9

    busy = Image.new("RGB", (300, 300), (250, 250, 250))
    draw = ImageDraw.Draw(busy)
    for i in range(10):
        draw.rectangle([50 + i * 20, 50, 70 + i * 20, 250], fill=(i * 25, 255 - i * 25, 128))
    score = interior_uniformity(
        np.asarray(busy, dtype=np.uint8), polygon, page_height=300, tolerance=24.0
    )
    assert score < 0.6, f"artwork scored {score:.2f} as uniform"


def test_a_line_grazing_the_outline_is_admitted_by_the_tolerance() -> None:
    # Lettering touches the balloon outline and the polygon traces the
    # interior inside it, so a text box can fall a pixel or two short. Without
    # the tolerance the line breaks away and the utterance arrives split.
    import numpy as np

    from comictrans.detect.contour import ContourCandidate

    square = np.array([[[100, 100]], [[300, 100]], [[300, 300]], [[100, 300]]], dtype=np.int32)
    candidate = ContourCandidate(
        points=square, bounds=Box(100, 100, 301, 301), area=40000.0, inverted=False
    )

    inside = Box(120, 120, 280, 280)
    assert candidate.contains_box(inside, 0.0)

    grazing = Box(96, 120, 280, 280)  # four pixels past the left edge
    assert not candidate.contains_box(grazing, 0.0)
    assert not candidate.contains_box(grazing, 2.0), "tolerance must stay tight"
    assert candidate.contains_box(grazing, 6.0)


def test_regions_tracing_the_same_shape_are_merged(
    balloon_page: tuple[np.ndarray, list[Box]],
) -> None:
    # A balloon appears on both threshold polarities. If the two contours end
    # up holding a line each, the utterance must still come back whole.
    array, boxes = balloon_page
    regions = find_regions(_page(array), lines_for(boxes, ["ONE", "TWO"]), CFG)
    assert len(regions) == 1
    assert [line.text for line in regions[0].lines] == ["ONE", "TWO"]


def test_a_trace_that_stops_partway_down_a_balloon_merges_into_the_whole_one() -> None:
    # Measured on a real page, from Apple Vision: one balloon came back as two
    # exact regions. The second traced the same outline but stopped at a
    # horizontal cut two thirds of the way down, taking the balloon's opening
    # line with it and leaving the rest behind. 98% of it sat inside the full
    # trace, yet only 0.64 IoU, so a duplicate test on bounding boxes let both
    # through and apply typeset two translations into the one balloon, one on
    # top of the other.
    boxes = [
        Box(160, 120, 380, 148),  # the opening line, above the cut
        Box(160, 160, 380, 188),
        Box(160, 200, 380, 228),  # below it, reached only by the full trace
    ]
    page = _page(
        make_page_array(
            (600, 800),
            ART_DARK,
            [("ellipse", Box(120, 100, 420, 260), BALLOON_WHITE, INK_BLACK, boxes)],
        )
    )

    whole = (
        (124, 180),
        (150, 125),
        (220, 104),
        (320, 104),
        (396, 128),
        (416, 180),
        (396, 232),
        (320, 256),
        (270, 300),
        (260, 252),
        (180, 232),
    )
    # Cut above the last line's centre, so the truncated trace does not speak
    # for it. 0.67 of the full polygon's area, close to the 0.66 measured on
    # the page this came from.
    cut = 202
    truncated = (*(p for p in whole if p[1] <= cut), (396, cut), (150, cut))

    lines = lines_for(boxes, ["OPENING LINE", "SECOND LINE", "THIRD LINE"])
    regions = [
        _build(page, truncated, Geometry.EXACT, lines[:1]),
        _build(page, whole, Geometry.EXACT, lines[1:]),
    ]

    merged = _merge_overlapping(page, regions, CFG)

    assert len(merged) == 1, "one balloon must not survive as two regions"
    assert [line.text for line in merged[0].lines] == [
        "OPENING LINE",
        "SECOND LINE",
        "THIRD LINE",
    ], "the opening line must rejoin the rest of the utterance, in reading order"
    # The truncated trace is the smaller polygon, but it stops above the last
    # line. Keeping it would leave that line outside the region meant to erase
    # and typeset it.
    assert merged[0].polygon == whole


def test_merging_two_traces_of_one_balloon_keeps_the_tighter_polygon() -> None:
    # The other way round: when both traces hold every line, the tighter one is
    # the balloon's interior, inside its own dark outline, and is what apply
    # should erase into. The looser one has the outline itself inside it.
    boxes = [Box(160, 140, 360, 168), Box(160, 180, 360, 208)]
    page = _page(
        make_page_array(
            (600, 800),
            ART_DARK,
            [("ellipse", Box(120, 100, 420, 260), BALLOON_WHITE, INK_BLACK, boxes)],
        )
    )

    inner = (
        (130, 180),
        (155, 128),
        (225, 108),
        (320, 108),
        (395, 130),
        (412, 180),
        (395, 230),
        (320, 252),
        (225, 252),
        (155, 230),
    )
    outer = (
        (124, 180),
        (150, 122),
        (222, 102),
        (322, 102),
        (400, 124),
        (418, 180),
        (400, 236),
        (322, 258),
        (222, 258),
        (150, 236),
    )

    lines = lines_for(boxes, ["ONE", "TWO"])
    regions = [
        _build(page, outer, Geometry.EXACT, lines[:1]),
        _build(page, inner, Geometry.EXACT, lines[1:]),
    ]

    merged = _merge_overlapping(page, regions, CFG)

    assert len(merged) == 1
    assert [line.text for line in merged[0].lines] == ["ONE", "TWO"]
    assert merged[0].polygon == inner


def test_a_polygon_grows_to_cover_text_assigned_to_it() -> None:
    # Erase clips its glyph mask to the polygon, so lettering outside it is
    # never removed and the original text stays under the translation. A line
    # absorbed from outside the balloon must drag the polygon out with it.
    import numpy as np

    boxes = [Box(160, 140, 360, 164), Box(160, 180, 340, 204)]
    array = make_page_array(
        (600, 800),
        ART_DARK,
        [("ellipse", Box(120, 100, 420, 260), BALLOON_WHITE, INK_BLACK, boxes)],
    )
    page = _page(array)
    # A line whose box overshoots the balloon, the way an over-wide OCR box
    # does, but whose centre still sits inside it.
    overshooting = Box(130, 108, 410, 136)
    lines = lines_for([*boxes, overshooting], ["ONE", "TWO", "OVERSHOOTING"])

    regions = find_regions(page, lines, CFG)

    assert len(regions) == 1
    outline = np.array(regions[0].polygon, dtype=np.int32)
    for line in regions[0].lines:
        for x, y in line.box.corners():
            assert cv2.pointPolygonTest(outline, (float(x), float(y)), False) >= 0, (
                f"{line.text!r} sits outside the polygon that is meant to erase it"
            )


def test_growing_a_polygon_keeps_it_simple_and_does_not_hull_it(
    balloon_page: tuple[np.ndarray, list[Box]],
) -> None:
    # Unioning the boxes in, rather than taking a convex hull, is what keeps a
    # tail or a burst balloon's spikes from being filled in.
    array, boxes = balloon_page
    regions = find_regions(_page(array), lines_for(boxes, ["ONE", "TWO"]), CFG)
    assert polygon_is_simple(regions[0].polygon)


# -- which lines a polygon speaks for ------------------------------------

_SQUARE = ((100, 100), (200, 100), (200, 200), (100, 200))


def test_a_line_is_inside_when_its_centre_is() -> None:
    """Looser than covering it: lettering grazing an outline still counts."""
    middle = OcrLine(text="IN", box=Box(120, 120, 180, 150), confidence=0.9)
    grazing = OcrLine(text="EDGE", box=Box(90, 140, 210, 170), confidence=0.9)
    away = OcrLine(text="OUT", box=Box(300, 300, 340, 320), confidence=0.9)

    kept = lines_inside(_SQUARE, [middle, grazing, away])

    assert [line.text for line in kept] == ["IN", "EDGE"]


def test_lines_read_from_a_crop_are_placed_by_where_the_crop_starts() -> None:
    """A crop's lines start at its own corner; the polygon is on the page."""
    # The same line, in the coordinates of a crop that began at (90, 90).
    line = OcrLine(text="IN", box=Box(30, 30, 90, 60), confidence=0.9)

    assert lines_inside(_SQUARE, [line], (90, 90)) == [line]
    assert lines_inside(_SQUARE, [line]) == [], "read as page pixels, it is nowhere near"


def test_nothing_inside_is_nothing_rather_than_everything() -> None:
    away = OcrLine(text="OUT", box=Box(300, 300, 340, 320), confidence=0.9)

    assert lines_inside(_SQUARE, [away]) == []
    assert lines_inside(_SQUARE, []) == []


HAIRPIN = (
    (11, 11),
    (15, 8),
    (20, 9),
    (24, 11),
    (31, 10),
    (56, 11),
    (22, 12),
    (18, 10),
    (16, 12),
    (14, 10),
)
"""An outline out along one side and back along the other, two pixels apart.

Found by searching: at a two-pixel tolerance, the coarsest step and the one
after it both flatten it onto one line doubled back over itself, and only the
finest keeps its two sides apart."""


def test_an_outline_a_coarse_step_folds_is_simplified_at_a_finer_one() -> None:
    contour = np.array([[point] for point in HAIRPIN], dtype=np.int32)

    rings = list(simplified_rings(contour, 2.0))

    assert rings == [HAIRPIN], "only the ring a plan can hold, from the step that gives one"
    assert all(polygon_is_simple(ring) for ring in rings)


def test_simplified_rings_are_moved_back_onto_the_page() -> None:
    contour = np.array([[point] for point in HAIRPIN], dtype=np.int32)

    (ring,) = simplified_rings(contour, 2.0, (100, 200))

    assert ring == tuple((x + 100, y + 200) for x, y in HAIRPIN)
