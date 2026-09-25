from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from comictrans.config import TypesetConfig
from comictrans.fonts import FontFace, resolve
from comictrans.markup import tokenize
from comictrans.model import Box, Polygon, polygon_bounds
from comictrans.typeset import (
    FitFailure,
    Layout,
    band_span,
    interior_mask,
    layout_text,
)

PAGE = 800
CFG = TypesetConfig()


@pytest.fixture
def face(font_dir: Path) -> FontFace:
    return resolve("Comic Sans MS")


def _ellipse(cx: int, cy: int, rx: int, ry: int, points: int = 48) -> Polygon:
    return tuple(
        (
            int(cx + rx * np.cos(2 * np.pi * i / points)),
            int(cy + ry * np.sin(2 * np.pi * i / points)),
        )
        for i in range(points)
    )


def _fit(text: str, polygon: Polygon, face: FontFace, **kwargs: object) -> Layout | FitFailure:
    cfg = kwargs.pop("cfg", CFG)
    return layout_text(
        tokenize(text),
        polygon,
        face,
        cfg,  # type: ignore[arg-type]
        page_width=PAGE,
        page_height=PAGE,
        **kwargs,  # type: ignore[arg-type]
    )


def test_band_span_finds_the_widest_run_present_on_every_row() -> None:
    mask = np.zeros((10, 20), dtype=np.uint8)
    mask[0:5, 2:18] = 255
    mask[5:10, 6:12] = 255
    assert band_span(mask, 0, 5) == (2, 18)
    assert band_span(mask, 5, 10) == (6, 12)
    # Spanning both halves, only the narrow overlap is present on every row.
    assert band_span(mask, 0, 10) == (6, 12)


def test_band_span_returns_none_outside_the_shape() -> None:
    mask = np.zeros((10, 20), dtype=np.uint8)
    mask[0:2, 0:5] = 255
    assert band_span(mask, 5, 9) is None
    assert band_span(mask, 9, 5) is None


def test_interior_mask_insets_from_the_polygon_edge() -> None:
    polygon = Box(100, 100, 300, 300).as_polygon()
    mask = interior_mask(polygon, PAGE, PAGE, CFG)
    assert mask[200, 200] > 0, "centre is inside"
    assert mask[100, 100] == 0, "corner is inset away"


def test_text_is_fitted_to_the_polygon_not_its_bounding_box(face: FontFace) -> None:
    # In an ellipse the top line has less room than the middle one. Fitting to
    # the bounding box would let it run out through the curve.
    result = _fit("ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT", _ellipse(400, 400, 220, 130), face)
    assert isinstance(result, Layout)
    assert len(result.lines) >= 3
    widths = [line.band_width for line in result.lines]
    assert widths[0] < max(widths), "top band should be narrower than the middle"
    for line in result.lines:
        assert line.width <= line.band_width


def test_a_short_line_gets_a_bigger_font_than_a_long_one(face: FontFace) -> None:
    polygon = _ellipse(400, 400, 220, 130)
    short = _fit("HI", polygon, face)
    long = _fit("A MUCH LONGER PIECE OF DIALOGUE THAN THE OTHER ONE", polygon, face)
    assert isinstance(short, Layout) and isinstance(long, Layout)
    assert short.font_size > long.font_size


def test_lines_are_vertically_centred_in_the_polygon(face: FontFace) -> None:
    polygon = Box(200, 200, 600, 500).as_polygon()
    result = _fit("ONE TWO", polygon, face)
    assert isinstance(result, Layout)
    block_top = result.lines[0].top
    block_bottom = result.lines[-1].top + result.line_height
    bounds = polygon_bounds(polygon)
    above, below = block_top - bounds.top, bounds.bottom - block_bottom
    assert abs(above - below) <= result.line_height


def test_condensing_is_only_reached_at_the_minimum_size(face: FontFace) -> None:
    # Roomy polygon: it fits at a comfortable size, so nothing is condensed.
    result = _fit("SHORT ENOUGH", _ellipse(400, 400, 250, 150), face)
    assert isinstance(result, Layout)
    assert result.condense == 1.0
    assert not result.condensed


def test_a_tight_polygon_condenses_rather_than_overflowing(face: FontFace) -> None:
    # Build the one situation condensing exists for: a box that fits exactly
    # one line at the minimum size, and is a few percent too narrow for it.
    cfg = TypesetConfig(font_size_min_ratio=0.05, padding_ratio=0.0, hyphenate=False)
    minimum = round(cfg.font_size_min_ratio * PAGE)
    text = "TOO WIDE BY A WHISKER"
    natural = face.regular.load(minimum).getlength(text)
    width = int(natural * 0.95)
    line_height = round(minimum * cfg.line_spacing)
    polygon = Box(100, 300, 100 + width, 300 + line_height).as_polygon()

    result = _fit(text, polygon, face, cfg=cfg)

    assert isinstance(result, Layout), "condensing should have rescued this fit"
    assert result.condensed, "should have needed condensing"
    assert cfg.condense_min <= result.condense < 1.0
    assert result.font_size == minimum
    for line in result.lines:
        assert line.width * result.condense <= line.band_width + 1


def test_condensing_never_goes_below_the_floor(face: FontFace) -> None:
    cfg = TypesetConfig(font_size_min_ratio=0.05, condense_min=0.9, hyphenate=False)
    result = _fit(
        "AN IMPOSSIBLY LONG UNBROKEN STRETCH OF DIALOGUE FOR SUCH A TINY BALLOON",
        Box(300, 380, 500, 420).as_polygon(),
        face,
        cfg=cfg,
    )
    if isinstance(result, Layout):
        assert result.condense >= cfg.condense_min


def test_text_that_cannot_fit_fails_and_says_why(face: FontFace) -> None:
    cfg = TypesetConfig(font_size_min_ratio=0.08, hyphenate=False)
    result = _fit(
        "FAR TOO MANY WORDS TO EVER FIT INSIDE THIS PARTICULAR LITTLE SHAPE",
        Box(380, 390, 420, 410).as_polygon(),
        face,
        cfg=cfg,
    )
    assert isinstance(result, FitFailure)
    assert "fit" in result.reason


def test_nothing_overflows_the_polygon_on_a_successful_fit(face: FontFace) -> None:
    result = _fit("ERASE AND TYPESET THIS DIALOGUE PLEASE", _ellipse(400, 400, 200, 140), face)
    assert isinstance(result, Layout)
    for line in result.lines:
        assert line.width * result.condense <= line.band_width + 1
        assert line.band_left >= 0
        assert line.top >= 0


def test_hyphenation_breaks_a_word_too_long_for_its_line(face: FontFace) -> None:
    cfg = TypesetConfig(font_size_min_ratio=0.030, hyphenate=True)
    narrow = Box(300, 340, 500, 460).as_polygon()
    result = _fit("EXTRAORDINARY", narrow, face, cfg=cfg)
    if isinstance(result, Layout) and len(result.lines) > 1:
        rendered = "".join(run.text for line in result.lines for run in line.runs)
        assert "-" in rendered


def test_hyphenation_can_be_turned_off(face: FontFace) -> None:
    cfg = TypesetConfig(font_size_min_ratio=0.02, hyphenate=False)
    result = _fit("EXTRAORDINARY", Box(300, 340, 520, 470).as_polygon(), face, cfg=cfg)
    if isinstance(result, Layout):
        rendered = "".join(run.text for line in result.lines for run in line.runs)
        assert rendered == "EXTRAORDINARY"


def test_a_fixed_size_is_used_as_given(face: FontFace) -> None:
    polygon = _ellipse(400, 400, 250, 160)
    result = _fit("SIZE ME", polygon, face, fixed_size=30)
    assert isinstance(result, Layout)
    assert result.font_size == 30


def test_a_fixed_size_still_refuses_to_overflow(face: FontFace) -> None:
    # A pinned size may be shrunk to make the text fit, but whatever comes
    # back must sit inside the polygon. Overflowing is never an option.
    result = _fit(
        "THIS WILL NOT FIT AT NINETY PIXELS NO MATTER HOW IT IS ARRANGED",
        Box(350, 380, 450, 420).as_polygon(),
        face,
        fixed_size=90,
    )
    if isinstance(result, Layout):
        assert result.font_size < 90
        assert result.undersized
        for line in result.lines:
            assert line.width * result.condense <= line.band_width + 1


def test_emphasis_is_carried_into_the_placed_runs(face: FontFace) -> None:
    result = _fit("A **BOLD** WORD", _ellipse(400, 400, 220, 140), face)
    assert isinstance(result, Layout)
    runs = [run for line in result.lines for run in line.runs]
    assert any(run.bold and run.text == "BOLD" for run in runs)
    assert any(not run.bold for run in runs)


def test_empty_text_is_a_fit_failure(face: FontFace) -> None:
    result = layout_text(
        (), Box(0, 0, 100, 100).as_polygon(), face, CFG, page_width=PAGE, page_height=PAGE
    )
    assert isinstance(result, FitFailure)


def test_a_degenerate_polygon_fails_rather_than_dividing_by_zero(face: FontFace) -> None:
    result = _fit("TEXT", ((10, 10), (11, 10), (11, 11), (10, 11)), face)
    assert isinstance(result, FitFailure)


def test_an_unknown_hyphenation_language_degrades_instead_of_failing(
    face: FontFace, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    cfg = TypesetConfig(hyphenation_language="zz", font_size_min_ratio=0.02)
    with caplog.at_level(logging.WARNING):
        result = _fit("SOME ORDINARY WORDS", Box(200, 300, 600, 500).as_polygon(), face, cfg=cfg)

    assert isinstance(result, Layout), "an unknown language must not break the fit"
    assert "no hyphenation dictionary" in caplog.text


def test_the_hyphenation_language_is_the_target_language(face: FontFace) -> None:
    # A bare code is enough; pyphen resolves 'sv' as readily as 'en_US'.
    cfg = TypesetConfig(hyphenation_language="sv", font_size_min_ratio=0.03)
    result = _fit("OERHORT", Box(300, 340, 500, 460).as_polygon(), face, cfg=cfg)
    assert isinstance(result, Layout | FitFailure)


def test_text_too_long_for_the_minimum_shrinks_below_it_rather_than_failing(
    face: FontFace,
) -> None:
    # An empty balloon is worse than small lettering, so the last resort is to
    # go under the readable minimum and say so.
    cfg = TypesetConfig(font_size_min_ratio=0.06, font_size_floor_ratio=0.004, hyphenate=False)
    minimum = round(cfg.font_size_min_ratio * PAGE)
    result = _fit(
        "A GREAT DEAL MORE DIALOGUE THAN THIS PARTICULAR BALLOON WAS EVER "
        "DRAWN TO HOLD AT A COMFORTABLE READING SIZE",
        _ellipse(400, 400, 240, 150),
        face,
        cfg=cfg,
    )

    assert isinstance(result, Layout), "should have shrunk instead of failing"
    assert result.undersized
    assert result.font_size < minimum
    assert result.font_size >= round(cfg.font_size_floor_ratio * PAGE)
    for line in result.lines:
        assert line.width * result.condense <= line.band_width + 1


def test_a_comfortable_fit_is_not_flagged_undersized(face: FontFace) -> None:
    result = _fit("SHORT", _ellipse(400, 400, 240, 150), face)
    assert isinstance(result, Layout)
    assert not result.undersized
    assert result.font_size >= round(CFG.font_size_min_ratio * PAGE)


def test_the_floor_is_the_end_of_the_line(face: FontFace) -> None:
    cfg = TypesetConfig(font_size_min_ratio=0.05, font_size_floor_ratio=0.045, hyphenate=False)
    result = _fit(
        "FAR MORE WORDS THAN COULD EVER BE SET INTO THIS SHAPE AT FORTY "
        "PIXELS A GLYPH NO MATTER HOW THEY ARE ARRANGED ON THE PAGE",
        _ellipse(400, 400, 200, 120),
        face,
        cfg=cfg,
    )
    assert isinstance(result, FitFailure)
    assert str(round(cfg.font_size_floor_ratio * PAGE)) in result.reason


def test_a_pinned_size_shrinks_as_a_fallback_and_says_so(face: FontFace) -> None:
    # A pinned size is where the fit starts, not a wall: an optimistic
    # font_size still renders, and the gap is reported rather than silent.
    result = _fit(
        "MUCH MORE TEXT THAN WILL EVER GO INTO THIS SHAPE AT NINETY PIXELS",
        _ellipse(400, 400, 220, 140),
        face,
        fixed_size=90,
    )
    assert isinstance(result, Layout)
    assert result.undersized
    assert result.font_size < 90
    for line in result.lines:
        assert line.width * result.condense <= line.band_width + 1


def test_a_pinned_size_that_cannot_fit_even_at_the_floor_fails(face: FontFace) -> None:
    cfg = TypesetConfig(font_size_floor_ratio=0.09, hyphenate=False)
    result = _fit(
        "FAR MORE WORDS THAN COULD EVER BE SET INTO THIS TINY BOX AT ANY SIZE "
        "THIS SIDE OF THE FLOOR, HOWEVER THEY ARE ARRANGED",
        Box(350, 380, 450, 420).as_polygon(),
        face,
        cfg=cfg,
        fixed_size=90,
    )
    assert isinstance(result, FitFailure)
    assert "90px this region asks for" in result.reason


def test_a_pinned_size_that_fits_is_used_exactly(face: FontFace) -> None:
    result = _fit("PINNED", _ellipse(400, 400, 240, 150), face, fixed_size=28)
    assert isinstance(result, Layout)
    assert result.font_size == 28
    assert not result.undersized


def test_a_polygon_in_a_frame_of_its_own_is_fitted_in_that_frame(face: FontFace) -> None:
    """How a tilted region is fitted: level, in a frame that is not the page.

    The polygon lies beyond both edges of the page, which on the page would
    be no room at all; given the frame it is in, it fits, and at the size the
    page's own height decides it may be.
    """
    beyond = Box(PAGE + 20, PAGE + 20, PAGE + 300, PAGE + 160).as_polygon()

    off_page = _fit("HELLO THERE", beyond, face)
    # A frame ten pages tall: were its height taken for the page's, the
    # smallest readable size would be ten times what it is.
    in_frame = _fit("HELLO THERE", beyond, face, canvas=(PAGE + 400, 10 * PAGE))
    on_page = _fit("HELLO THERE", Box(20, 20, 300, 160).as_polygon(), face)

    assert isinstance(off_page, FitFailure)
    assert isinstance(in_frame, Layout) and isinstance(on_page, Layout)
    assert all(line.band_left >= PAGE + 20 and line.top >= PAGE + 20 for line in in_frame.lines)
    assert (in_frame.font_size, in_frame.undersized) == (on_page.font_size, on_page.undersized), (
        "sized as the same box on the page would be"
    )
