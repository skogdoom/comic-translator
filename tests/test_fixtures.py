"""Regression tests over real scans dropped into tests/fixtures/.

These skip when the directory is empty. They assert the properties that must
hold on any page rather than exact geometry, which would change with every
detector tweak.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from comictrans.config import DetectConfig, OcrConfig
from comictrans.detect import find_regions
from comictrans.errors import OcrUnavailableError
from comictrans.imaging import IMAGE_SUFFIXES, load_page
from comictrans.model import Color, polygon_area, polygon_is_simple
from comictrans.ocr import get_recognizer

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_images() -> list[Path]:
    if not FIXTURES.is_dir():
        return []
    return sorted(p for p in FIXTURES.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)


@pytest.mark.parametrize("path", _fixture_images(), ids=lambda p: p.name)
def test_real_page_produces_sane_geometry(path: Path) -> None:
    try:
        recognizer = get_recognizer(OcrConfig())
    except OcrUnavailableError as exc:
        pytest.skip(f"no OCR backend: {exc}")

    page = load_page(path)
    cfg = DetectConfig()
    regions = find_regions(page, recognizer.recognize(page, OcrConfig()), cfg)

    assert regions, f"no regions found on {path.name}"
    for region in regions:
        assert len(region.polygon) >= 3
        # The plan file reader rejects a self-intersecting polygon, so extract
        # writing one would produce a plan it cannot load back.
        assert polygon_is_simple(region.polygon), "detector produced a self-intersecting polygon"
        assert 0 < polygon_area(region.polygon) <= page.area * cfg.max_contour_area_ratio
        for x, y in region.polygon:
            assert 0 <= x <= page.width and 0 <= y <= page.height
        assert region.lines, "a region with no OCR lines should not exist"

        # Contour escape: a region spanning the page is a band of artwork or a
        # panel, and erasing it would wipe out the art.
        bounds = region.bounds
        assert bounds.width <= page.width * cfg.max_extent_ratio + 1
        assert bounds.height <= page.height * cfg.max_extent_ratio + 1


@pytest.mark.parametrize("path", _fixture_images(), ids=lambda p: p.name)
def test_colors_are_sampled_from_the_page(path: Path) -> None:
    """Fill and text colour must differ, whichever way round the page is."""
    try:
        recognizer = get_recognizer(OcrConfig())
    except OcrUnavailableError as exc:
        pytest.skip(f"no OCR backend: {exc}")

    page = load_page(path)
    regions = find_regions(page, recognizer.recognize(page, OcrConfig()), DetectConfig())

    for region in regions:
        fill, text = region.fill_color, region.text_color
        assert fill != text, f"{region.lines[0].text!r} sampled one colour for both"

    if "white_on_black" in path.name:
        # A light-on-dark caption has to round-trip, not be assumed black-on-white.
        region = min(regions, key=lambda r: r.bounds.top)
        assert _luminance(region.fill_color) < _luminance(region.text_color), (
            "white-on-black caption came back as dark text on a light fill"
        )


def _luminance(color: Color) -> float:
    return 0.299 * color.r + 0.587 * color.g + 0.114 * color.b


@pytest.mark.parametrize("path", _fixture_images(), ids=lambda p: p.name)
def test_detection_is_deterministic(path: Path) -> None:
    try:
        recognizer = get_recognizer(OcrConfig())
    except OcrUnavailableError as exc:
        pytest.skip(f"no OCR backend: {exc}")

    page = load_page(path)
    lines = recognizer.recognize(page, OcrConfig())
    first = find_regions(page, lines, DetectConfig())
    second = find_regions(page, lines, DetectConfig())
    assert [r.polygon for r in first] == [r.polygon for r in second]
