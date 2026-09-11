"""Compositing a page: what gets erased, what gets drawn, and in what order."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from comictrans.config import ApplyConfig, TypesetConfig
from comictrans.fonts import FontFace, resolve
from comictrans.imaging import PageImage, PageMeta
from comictrans.model import Box, Color, Geometry, Region, TextCase
from comictrans.render import RegionStyle, RenderCancelled, plan_region, render_page

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
