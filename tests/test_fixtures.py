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
from comictrans.model import polygon_area, polygon_is_simple
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
        assert polygon_is_simple(region.polygon), "detector produced a self-intersecting polygon"
        assert 0 < polygon_area(region.polygon) <= page.area * cfg.max_contour_area_ratio
        for x, y in region.polygon:
            assert 0 <= x <= page.width and 0 <= y <= page.height
        assert region.lines, "a region with no OCR lines should not exist"


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
