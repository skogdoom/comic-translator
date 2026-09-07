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


def test_no_two_fixtures_are_byte_identical() -> None:
    """Duplicate fixtures cost test time and buy no coverage.

    Renaming a fixture by adding the new name without removing the old one is
    an easy slip, and the result looks like extra coverage rather than the
    same page twice.
    """
    import hashlib

    by_digest: dict[str, list[str]] = {}
    for path in _fixture_images():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        by_digest.setdefault(digest, []).append(path.name)

    duplicates = {d: names for d, names in by_digest.items() if len(names) > 1}
    assert not duplicates, "identical fixture images: " + "; ".join(
        " == ".join(sorted(names)) for names in duplicates.values()
    )


ROUND_TRIP_FIXTURES = (
    "1-plain_white_balloon_on_flat_art.png",
    "2-white_on_black_caption_box.png",
    "9-burst_balloon_with_lightning_tail.png",
)


def _round_trip_paths() -> list[Path]:
    return [p for p in _fixture_images() if p.name in ROUND_TRIP_FIXTURES]


@pytest.mark.parametrize("path", _round_trip_paths(), ids=lambda p: p.name)
def test_extract_then_apply_round_trip(path: Path, tmp_path: Path, font_dir: Path) -> None:
    """The whole pipeline over a real page: extract, translate, apply.

    Asserts the two rules that matter most — the source is never written to,
    and everything outside a region's polygon is byte-identical to the source.
    """
    import numpy as np
    from PIL import Image

    from comictrans.apply import apply_plan
    from comictrans.config import ApplyConfig, ExtractConfig
    from comictrans.erase import polygon_mask
    from comictrans.extract import extract
    from comictrans.model import Plan
    from comictrans.planfile import write_plan
    from comictrans.util import sha256_file

    try:
        recognizer = get_recognizer(OcrConfig())
    except OcrUnavailableError as exc:
        pytest.skip(f"no OCR backend: {exc}")

    source_dir = tmp_path / "pages"
    source_dir.mkdir()
    source = source_dir / path.name
    source.write_bytes(path.read_bytes())
    digest_before = sha256_file(source)

    plan_path = source_dir / "comic-plan.yaml"
    plan, _ = extract(source, plan_path, recognizer, "Comic Sans MS", ExtractConfig())
    if not plan.regions:
        pytest.skip(f"no regions detected on {path.name}")

    translated = Plan(
        header=plan.header,
        regions=tuple(r.with_translation("TRANSLATED **TEXT** HERE") for r in plan.regions),
    )
    write_plan(translated, plan_path, force=True)

    output = tmp_path / "out"
    report = apply_plan(translated, plan_path, output, ApplyConfig())

    assert sha256_file(source) == digest_before, "the source image was written to"
    assert report.rendered + report.failed == len(plan.regions)

    written = output / f"{source.stem}.png"
    assert written.is_file()

    # Compare against what the pipeline read, not a raw decode: the fixtures
    # carry transparency, which load_page flattens onto white.
    from comictrans.imaging import load_page

    page = load_page(source)
    original = page.rgb.astype(np.int16)
    result = np.asarray(Image.open(written).convert("RGB"), dtype=np.int16)
    assert result.shape == original.shape

    if page.alpha is not None:
        written_alpha = np.asarray(Image.open(written).convert("RGBA").getchannel("A"))
        assert np.array_equal(written_alpha, page.alpha), "source transparency was dropped"

    touched = np.zeros(original.shape[:2], dtype=bool)
    for region in translated.regions:
        touched |= polygon_mask(region, original.shape[0], original.shape[1]) > 0
    changed = np.abs(original - result).max(axis=2) > 0
    assert not (changed & ~touched).any(), "pixels outside every polygon were altered"
