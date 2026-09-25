"""Compositing a page: what gets erased, what gets drawn, and in what order."""

from __future__ import annotations

import logging
import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from comictrans.config import ApplyConfig, TypesetConfig
from comictrans.fonts import FontFace, resolve
from comictrans.imaging import PageImage, PageMeta
from comictrans.model import Box, Color, Geometry, Region, TextCase, rotate_polygon
from comictrans.render import (
    FRAME_MARGIN,
    RegionStyle,
    RenderCancelled,
    _turn_onto,
    plan_region,
    render_page,
    upright_frame,
)

INK = Color(20, 20, 20)
FILL = Color(250, 250, 250)
ART = (90, 140, 90)

# A wide box on the left, and one that overlaps only a narrow strip of its
# right edge. The second box's own lettering sits well away from that strip,
# so any change to the strip is the second region interfering with the first.
LEFT = Box(40, 120, 420, 300)
RIGHT = Box(390, 60, 660, 360)
SHARED = Box(390, 120, 420, 300)


@pytest.fixture
def face(font_dir: Path) -> FontFace:
    return resolve("Comic Sans MS")


@pytest.fixture
def style(face: FontFace) -> RegionStyle:
    return RegionStyle(face=face, case=TextCase.UPPER, size=None)


def _page() -> PageImage:
    image = Image.new("RGB", (700, 420), ART)
    draw = ImageDraw.Draw(image)
    for box in (LEFT, RIGHT):
        draw.rectangle(
            [box.left, box.top, box.right, box.bottom],
            fill=FILL.as_tuple(),
            outline=INK.as_tuple(),
            width=3,
        )
    return PageImage(
        path=Path("p.png"),
        rgb=np.asarray(image, dtype=np.uint8),
        sha256="0" * 64,
        meta=PageMeta(format="PNG", mode="RGB", dpi=None, icc_profile=None),
    )


def _region(region_id: str, box: Box, order: int, translation: str) -> Region:
    return Region(
        id=region_id,
        image="p.png",
        order=order,
        geometry=Geometry.EXACT,
        polygon=box.as_polygon(),
        fill_color=FILL,
        text_color=INK,
        confidence=0.9,
        source_text="ORIGINALE",
        translation=translation,
    )


def _ink(image: Image.Image, box: Box) -> int:
    patch = np.asarray(image.convert("RGB"), dtype=np.float32)[
        box.top : box.bottom, box.left : box.right
    ]
    return int(
        (np.linalg.norm(patch - np.array(INK.as_tuple(), dtype=np.float32), axis=2) < 60).sum()
    )


def test_an_overlapping_region_does_not_erase_its_neighbours_lettering(
    style: RegionStyle,
) -> None:
    """The bug: erasing and drawing one region at a time.

    A later region's erase pass would wipe lettering an earlier region had
    already drawn, wherever two polygons overlap — and silently, since both
    regions still reported success. Every erase now happens before any text is
    drawn.
    """
    wide = _region("wide", LEFT, 1, "WIDE ENOUGH TO REACH THE RIGHT EDGE OF THIS BOX")
    neighbour = _region("neighbour", RIGHT, 2, "B")
    styles = {"wide": style, "neighbour": style}

    alone, _ = render_page(_page(), (wide,), {"wide": style}, ApplyConfig())
    together, outcomes = render_page(_page(), (wide, neighbour), styles, ApplyConfig())

    assert [o.status for o in outcomes] == ["rendered", "rendered"]
    assert _ink(alone, SHARED) > 0, "the fixture must put lettering in the shared strip"
    assert _ink(together, SHARED) == _ink(alone, SHARED)


def test_substantially_overlapping_regions_are_reported(
    style: RegionStyle, caplog: pytest.LogCaptureFixture
) -> None:
    # Erasing first stops one region cutting into another's lettering. It
    # cannot stop two overlapping polygons drawing over each other, so say so.
    # The narrow strip the other tests use is under the threshold on purpose;
    # this pair genuinely sits on top of each other.
    regions = (
        _region("wide", LEFT, 1, "SOME TEXT"),
        _region("on-top", Box(200, 140, 560, 320), 2, "B"),
    )
    with caplog.at_level(logging.WARNING):
        render_page(_page(), regions, {"wide": style, "on-top": style}, ApplyConfig())
    assert "overlap" in caplog.text
    assert "wide" in caplog.text and "on-top" in caplog.text


def test_regions_that_do_not_overlap_are_not_reported(
    style: RegionStyle, caplog: pytest.LogCaptureFixture
) -> None:
    apart = _region("apart", Box(500, 60, 660, 200), 2, "B")
    regions = (_region("wide", LEFT, 1, "SOME TEXT"), apart)
    with caplog.at_level(logging.WARNING):
        render_page(_page(), regions, {"wide": style, "apart": style}, ApplyConfig())
    assert "overlap" not in caplog.text


def test_a_region_that_will_not_fit_is_never_erased(style: RegionStyle) -> None:
    # Planning happens before any pixel is touched, so a region with no layout
    # is not erased either: it keeps its original lettering.
    config = ApplyConfig(
        typeset=TypesetConfig(font_size_min_ratio=0.5, font_size_floor_ratio=0.45, hyphenate=False)
    )
    impossible = _region("wide", LEFT, 1, "FAR TOO MUCH TEXT FOR THIS BOX AT THAT SIZE")
    page = _page()

    rendered, outcomes = render_page(page, (impossible,), {"wide": style}, config)

    assert outcomes[0].status == "failed"
    assert np.array_equal(np.asarray(rendered.convert("RGB")), page.rgb)


def test_plan_region_touches_no_pixels(style: RegionStyle) -> None:
    page = _page()
    before = page.rgb.copy()
    layout, outcome = plan_region(
        _region("wide", LEFT, 1, "HELLO"),
        style,
        ApplyConfig(),
        page_width=page.width,
        page_height=page.height,
    )
    assert layout is not None
    assert outcome.status == "rendered"
    assert np.array_equal(page.rgb, before)


def test_a_skipped_region_is_left_completely_alone(style: RegionStyle) -> None:
    page = _page()
    skipped = Region(**{**vars_of(_region("wide", LEFT, 1, "HELLO")), "skip": True})
    rendered, outcomes = render_page(page, (skipped,), {"wide": style}, ApplyConfig())
    assert outcomes[0].status == "skipped_flag"
    assert np.array_equal(np.asarray(rendered.convert("RGB")), page.rgb)


def vars_of(region: Region) -> dict[str, object]:
    from dataclasses import fields

    return {f.name: getattr(region, f.name) for f in fields(region)}


# -- stopping and reporting, for the preview only ------------------------------


def _two_regions() -> tuple[Region, ...]:
    return (
        _region("left", LEFT, 1, "HELLO"),
        _region("right", RIGHT, 2, "THERE"),
    )


def test_render_page_reports_each_region_as_it_erases_it(style: RegionStyle) -> None:
    """The units the preview's bar counts in.

    Regions erased, not regions planned or drawn: measured on an
    eleven-megapixel page, erasing is 80% of a preview at 1.28s a region
    against 0.099s to plan one and 0.002s to draw one.
    """
    seen: list[tuple[int, int]] = []
    styles = {"left": style, "right": style}

    render_page(
        _page(),
        _two_regions(),
        styles,
        ApplyConfig(),
        on_region=lambda done, total: seen.append((done, total)),
    )

    assert seen == [(0, 2), (1, 2), (2, 2)], (
        "before each erase and once at the end, so the bar neither starts full "
        "nor stops short of full"
    )


def test_render_page_stops_between_regions_when_asked(style: RegionStyle) -> None:
    """And stops *between* them: an erase that started is allowed to finish.

    A half-erased region would be a page with part of a balloon repainted,
    which is the one thing this must not produce even for something thrown
    away.
    """
    erased: list[tuple[int, int]] = []
    styles = {"left": style, "right": style}

    def stop_after_the_first() -> bool:
        return len(erased) >= 1

    with pytest.raises(RenderCancelled):
        render_page(
            _page(),
            _two_regions(),
            styles,
            ApplyConfig(),
            on_region=lambda done, total: erased.append((done, total)),
            should_cancel=stop_after_the_first,
        )

    assert erased == [(0, 2)], "it stopped before erasing the second"


def test_render_page_cannot_be_stopped_when_nobody_passes_a_way_to(
    style: RegionStyle,
) -> None:
    """The default, and the half of apply's promise that lives here.

    With no hooks there is no way in: the page is rendered whole. What apply
    passes is asserted in ``test_apply.py``, where apply is.
    """
    styles = {"left": style, "right": style}
    image, outcomes = render_page(_page(), _two_regions(), styles, ApplyConfig())

    assert len(outcomes) == 2, "a page rendered in full, with no hooks to stop it"
    assert image.size == (700, 420)


# -- tilted lettering --------------------------------------------------------

RED = Color(200, 30, 30)
SENTENCE = "THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG AGAIN"


def _blank(width: int = 900, height: int = 700) -> PageImage:
    return PageImage(
        path=Path("p.png"),
        rgb=np.full((height, width, 3), 255, dtype=np.uint8),
        sha256="0" * 64,
        meta=PageMeta(format="PNG", mode="RGB", dpi=None, icc_profile=None),
    )


TILTED_BOX = Box(300, 250, 600, 400)


def _tilted(angle: float, box: Box = TILTED_BOX, **fields: object) -> Region:
    """A box turned counter-clockwise by ``angle``, lettered at that angle."""
    return Region(
        id=f"r{angle}",
        image="p.png",
        order=1,
        geometry=Geometry.MANUAL,
        polygon=rotate_polygon(box.as_polygon(), -angle),
        fill_color=Color(255, 255, 255),
        text_color=RED,
        confidence=1.0,
        source_text="X",
        translation=SENTENCE,
        angle=angle,
        **fields,  # type: ignore[arg-type]
    )


def _render_one(region: Region, style: RegionStyle, page: PageImage | None = None) -> tuple:
    image, (outcome,) = render_page(page or _blank(), (region,), {region.id: style}, ApplyConfig())
    return np.asarray(image, dtype=np.int16), outcome


def _inked(pixels: np.ndarray) -> np.ndarray:
    return np.argwhere((pixels != 255).any(axis=2))


def test_a_tilted_region_fits_at_the_size_it_would_level(style: RegionStyle) -> None:
    """The measurement the milestone was planned on: turning costs no fit."""
    sizes = {angle: _render_one(_tilted(angle), style)[1].font_size for angle in (0, 20, 30, 45)}

    assert len(set(sizes.values())) == 1, sizes
    assert sizes[0] > 0


def test_tilted_lettering_stays_inside_its_outline(style: RegionStyle) -> None:
    import cv2

    region = _tilted(30)
    pixels, outcome = _render_one(region, style)
    assert outcome.rendered

    inside = np.zeros(pixels.shape[:2], dtype=np.uint8)
    cv2.fillPoly(inside, [np.array(region.polygon, dtype=np.int32)], 255)
    inked = _inked(pixels)
    assert len(inked) > 500, "something was drawn"
    assert all(inside[y, x] for y, x in inked)


def test_a_positive_angle_turns_the_lettering_counter_clockwise(style: RegionStyle) -> None:
    """The plan's convention, and Pillow's and OpenCV's: up to the right."""
    region = _tilted(20, Box(150, 300, 750, 360), font_size=24)
    pixels, _outcome = _render_one(region, style)

    inked = _inked(pixels).astype(float)
    ys, xs = inked[:, 0] - inked[:, 0].mean(), inked[:, 1] - inked[:, 1].mean()
    slope = math.degrees(math.atan2(-(xs * ys).sum(), (xs * xs).sum()))

    assert abs(slope - 20) < 3, f"the line runs at {slope:.1f} degrees"


def test_tilted_lettering_is_its_own_colour_to_the_edge(style: RegionStyle) -> None:
    """Turned, not darkened: every edge pixel is the colour thinned toward white.

    Resampled against empty pixels that were black, the colour would bleed
    toward black at every edge, which on white shows as a darker red than the
    lettering itself.
    """
    pixels, _outcome = _render_one(_tilted(30), style)

    red = pixels[:, :, 0][(pixels != 255).any(axis=2)]
    assert red.min() >= RED.r - 1


@pytest.mark.parametrize("spelled", [360.0, -360.0])
def test_a_whole_turn_is_lettered_as_level(style: RegionStyle, spelled: float) -> None:
    """And level is the path every plan took before angles existed, untouched."""
    level, _ = _render_one(replace(_tilted(0), angle=0.0), style)
    turned, _ = _render_one(replace(_tilted(0), angle=spelled), style)

    assert upright_frame(replace(_tilted(0), angle=spelled)) is None
    assert np.array_equal(level, turned)


def test_a_steep_region_in_the_corner_is_lettered_though_its_frame_is_not_on_the_page(
    style: RegionStyle,
) -> None:
    """A long box at 45 degrees is longer level than its box on the page is wide.

    Tucked into the page's corner, its level frame reaches off the top and
    the left, and the lettering is still fitted and drawn in full.
    """
    diamond = rotate_polygon(Box(0, 0, 500, 70).as_polygon(), -45)
    left = min(x for x, _y in diamond)
    top = min(y for _x, y in diamond)
    region = replace(
        _tilted(45),
        polygon=tuple((x - left, y - top) for x, y in diamond),
        translation="THE QUICK BROWN FOX",
    )
    frame = upright_frame(region)
    assert frame is not None and min(frame.offset) < 0, "sanity: the frame is off the page"

    pixels, outcome = _render_one(region, style, _blank(420, 420))

    assert outcome.rendered, outcome.detail
    assert len(_inked(pixels)) > 300


def test_a_tilted_region_is_fitted_in_its_own_frame(style: RegionStyle) -> None:
    """Level, and moved clear of the page's corner, so nothing of it is lost."""
    region = _tilted(30, Box(0, 0, 300, 150))
    frame = upright_frame(region)
    assert frame is not None

    xs = [x for x, _y in frame.polygon]
    ys = [y for _x, y in frame.polygon]
    assert (min(xs), min(ys)) == (4, 4), "the frame's margin, and no further"
    assert (max(xs) - min(xs), max(ys) - min(ys)) in {
        (299, 149),
        (300, 150),
        (299, 150),
        (300, 149),
    }
    assert frame.size == (max(xs) + 5, max(ys) + 5), "and as much again beyond it"


TRIANGLE = ((120, 60), (330, 110), (150, 250))
"""Lopsided on purpose: its box's middle is nowhere near where its ink is."""


@pytest.mark.parametrize("angle", [30.0, -50.0, 75.0])
def test_a_shape_drawn_level_turns_back_onto_the_outline_it_came_from(angle: float) -> None:
    """The frame and the turn agree to the pixel, and the edge is smoothed.

    Measured: a turn about the right pivot misses by 205 to 222 pixels of the
    19,491 the triangle covers, all of it rounding along the edge; half a
    pixel off, the worst of these angles misses by 573. One resample by
    interpolation leaves about 570 edge pixels part-way between ink and
    paper, where turning by the nearest pixel would leave none.
    """
    import cv2
    from PIL import ImageDraw

    region = replace(_tilted(angle), polygon=TRIANGLE)
    frame = upright_frame(region)
    assert frame is not None
    layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).polygon(frame.polygon, fill=(0, 0, 0, 255))
    page = Image.new("RGB", (400, 320), (255, 255, 255))

    _turn_onto(page, layer, frame)

    red = np.asarray(page)[:, :, 0].astype(int)
    outline = np.zeros(red.shape, dtype=np.uint8)
    cv2.fillPoly(outline, [np.array(TRIANGLE, dtype=np.int32)], 1)
    missed = int(((red < 128) != outline.astype(bool)).sum())
    assert missed < 250, f"{missed} pixels off the outline"
    assert int(((red > 10) & (red < 245)).sum()) > 100, "the edge is smoothed, not stepped"


def test_what_is_turned_back_reaches_the_frames_margin_and_no_further() -> None:
    """A glyph's ink can pass the polygon's box by a pixel or two; the frame
    leaves room for that, and so does what is turned back onto the page."""
    region = replace(_tilted(30.0), polygon=TRIANGLE)
    frame = upright_frame(region)
    assert frame is not None
    everything = Image.new("RGBA", frame.size, (0, 0, 0, 255))
    page = Image.new("RGB", (400, 320), (255, 255, 255))

    _turn_onto(page, everything, frame)

    ys, xs = np.nonzero(np.asarray(page)[:, :, 0] < 128)
    box = region.bounds
    reach = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
    assert reach == (
        box.left - FRAME_MARGIN,
        box.top - FRAME_MARGIN,
        box.right + FRAME_MARGIN,
        box.bottom + FRAME_MARGIN,
    )
