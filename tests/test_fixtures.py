"""Regression tests over real scans dropped into tests/fixtures/.

These skip when the directory is empty. They assert the properties that must
hold on any page rather than exact geometry, which would change with every
detector tweak.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path

import cv2
import numpy as np
import pytest

from comictrans.config import DetectConfig, EraseConfig, ExtractConfig, OcrConfig
from comictrans.detect import find_regions
from comictrans.errors import OcrUnavailableError
from comictrans.extract import read_region
from comictrans.imaging import IMAGE_SUFFIXES, load_page
from comictrans.model import Color, polygon_area, polygon_is_simple
from comictrans.ocr import get_recognizer
from comictrans.ocr.grouping import utterance_text

FIXTURES = Path(__file__).parent / "fixtures"

_RECOGNIZED: dict[Path, tuple[object, list[object]]] = {}
_DETECTED: dict[Path, tuple[object, ...]] = {}


_SPACE = re.compile(r"\s+")


def _flat(text: str) -> str:
    """Text with its line breaks and case taken out, for comparing readings."""
    return _SPACE.sub(" ", text).strip().casefold()


def _recognizer() -> object:
    """The backend the fixture tests are already using, or a skip."""
    try:
        return get_recognizer(OcrConfig())
    except OcrUnavailableError as exc:
        pytest.skip(f"no OCR backend: {exc}")


def _page_and_lines(path: Path) -> tuple[object, list[object]]:
    """Decode and OCR a fixture once, then reuse it.

    Three tests run over every fixture, and OCR is the expensive half of each.
    """
    cached = _RECOGNIZED.get(path)
    if cached is None:
        try:
            recognizer = get_recognizer(OcrConfig())
        except OcrUnavailableError as exc:
            pytest.skip(f"no OCR backend: {exc}")
        page = load_page(path)
        cached = (page, recognizer.recognize(page, OcrConfig()))
        _RECOGNIZED[path] = cached
    return cached


def _regions(path: Path) -> tuple[object, ...]:
    """Detect once per fixture and reuse, the way OCR already is.

    Detection used to run four times per image — once for geometry, once
    for colours, and twice for the determinism check — and three of those
    four asked the same question of the same pixels. Now it runs twice: this
    one, and the fresh one the determinism test compares against.

    A tuple rather than the list ``find_regions`` returns, so that a test
    reading the shared result cannot quietly reorder it for the next one.
    """
    cached = _DETECTED.get(path)
    if cached is None:
        page, lines = _page_and_lines(path)
        cached = tuple(find_regions(page, lines, DetectConfig()))
        _DETECTED[path] = cached
    return cached


def _fixture_images() -> list[Path]:
    if not FIXTURES.is_dir():
        return []
    return sorted(p for p in FIXTURES.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)


def _a_region_over(rgb: np.ndarray, how: str) -> object:
    """A balloon-sized region in the middle of the page, coloured off it.

    Built here rather than detected, because this asks nothing about
    detection and OCR is not always installed: what it needs is a polygon
    over real pixels — a real balloon, its outline, screentone, artwork —
    which is what makes the ground mask do something a synthetic page does
    not ask of it.
    """
    from comictrans.model import Erase, Geometry, Region

    height, width = rgb.shape[0], rgb.shape[1]
    left, top = int(width * 0.30), int(height * 0.35)
    right, bottom = int(width * 0.62), int(height * 0.52)
    patch = rgb[top:bottom, left:right].reshape(-1, 3)
    lightest = patch[patch.sum(axis=1).argmax()]
    darkest = patch[patch.sum(axis=1).argmin()]
    return Region(
        id="r1",
        image="page.png",
        order=1,
        geometry=Geometry.EXACT,
        polygon=((left, top), (right, top), (right, bottom), (left, bottom)),
        fill_color=Color(*(int(v) for v in lightest)),
        text_color=Color(*(int(v) for v in darkest)),
        confidence=0.9,
        source_text="X",
        translation="Y",
        erase=Erase(how),
    )


@pytest.mark.parametrize("path", _fixture_images(), ids=lambda p: p.name)
def test_erasing_in_a_window_gives_the_whole_pages_answer(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The crop erase works in, held to the page it used to work on.

    ``erase`` computes its mask inside a window around the region rather
    than over the page, which is worth 386MB and 1.3s a region on an
    eleven-megapixel page — and would be worth nothing if it changed what a
    page renders as. The synthetic pages in ``test_erase.py`` ask the same
    question, but a flat balloon drawn by a test is exactly the case where
    the ground mask has nothing to do; a real scan has screentone, an
    outline the polygon steps over, and artwork behind it.

    The flat fill only, because what a real page exercises that a drawn one
    does not is the *mask* — the ground, the outline, the fallback when the
    ground cannot be read — and every strategy is handed the same one. The
    reach each strategy needs of its own is asked in ``test_erase.py``,
    where a page costs a millisecond rather than a second.

    It earns its half-second a page, which was checked rather than assumed:
    take the ground-closing term out of ``reach`` and two of these thirteen
    pages come out different, while every synthetic page in ``test_erase.py``
    goes on passing. A flat balloon drawn by a test never asks the ground
    mask anything — the lettering is thicker than the closing kernel, the
    constraint collapses, and the fallback hands back the unconstrained mask.
    """
    from comictrans import erase as erase_module

    rgb = load_page(path).rgb
    region = _a_region_over(rgb, "flat")
    cfg = EraseConfig()

    windowed = erase_module.erase(rgb, region, cfg, page_height=rgb.shape[0])

    monkeypatch.setattr(erase_module, "reach", lambda _cfg, _height: max(rgb.shape))
    whole_page = erase_module.erase(rgb, region, cfg, page_height=rgb.shape[0])

    assert np.array_equal(windowed, whole_page)


_ONE_BALLOON = (
    "1-plain_white_balloon_on_flat_art.png",
    "2-white_on_black_caption_box.png",
    "5-borderless_caption_on_artwork.png",
    "8-thought_bubble_with_bubble_trail.png",
    "9-burst_balloon_with_lightning_tail.png",
)
"""The fixtures with one region on them, which is what makes them cheap to
read a second time: a crop of one balloon, not a page."""


@pytest.mark.parametrize("name", _ONE_BALLOON)
def test_reading_one_region_says_what_reading_the_page_said(name: str) -> None:
    """The claim the whole design of ``read_region`` rests on.

    A crop with a margin round it should read as the page read — that is the
    only reason it is worth handing a recogniser one balloon instead of the
    page it is on. Measured over the 31 fixture regions that read as
    language, it agrees exactly 27 times and averages 0.965 similarity; the
    five here are the one-region pages, where it agrees word for word and a
    second reading costs a crop rather than a page.

    Held as similarity rather than equality because the backend decides what
    the words are: this says the two paths agree, not what either one says.
    """
    path = FIXTURES / name
    if not path.is_file():  # the fixture directory is not in every checkout
        pytest.skip(f"{name} is not here")
    page, _lines = _page_and_lines(path)
    regions = _regions(path)
    assert regions, f"{name} has nothing to read"
    config = ExtractConfig(ocr=OcrConfig(), detect=DetectConfig())

    for region in regions:
        as_a_page = utterance_text(region.lines)  # type: ignore[attr-defined]
        as_a_crop = read_region(page, region.polygon, _recognizer(), config)  # type: ignore[arg-type,attr-defined]

        assert as_a_crop.strip(), f"{name}: nothing came back from the crop"
        agreement = SequenceMatcher(None, _flat(as_a_page), _flat(as_a_crop)).ratio()
        assert agreement >= 0.9, f"{name}: {as_a_page!r} read as {as_a_crop!r}"


@pytest.mark.parametrize("path", _fixture_images(), ids=lambda p: p.name)
def test_real_page_produces_sane_geometry(path: Path) -> None:
    # The page for its dimensions and the config for its ratios; the regions
    # themselves come from the shared detection.
    page, _lines = _page_and_lines(path)
    cfg = DetectConfig()
    regions = _regions(path)

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

        # Erase clips its glyph mask to the polygon, so a line of text
        # outside the polygon assigned to it is lettering that will never be
        # removed: the original text stays on the page under the translation.
        outline = np.array(region.polygon, dtype=np.int32)
        for line in region.lines:
            for x, y in line.box.corners():
                assert cv2.pointPolygonTest(outline, (float(x), float(y)), False) >= 0, (
                    f"{line.text!r} lies outside the polygon meant to erase it"
                )

        # Contour escape: a region spanning the page is a band of artwork or a
        # panel, and erasing it would wipe out the art.
        bounds = region.bounds
        assert bounds.width <= page.width * cfg.max_extent_ratio + 1
        assert bounds.height <= page.height * cfg.max_extent_ratio + 1


@pytest.mark.parametrize("path", _fixture_images(), ids=lambda p: p.name)
def test_colors_are_sampled_from_the_page(path: Path) -> None:
    """Fill and text colour must differ, whichever way round the page is."""
    regions = _regions(path)

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
    """The same pixels twice, separated by whatever else the session did.

    The shared result was produced earlier in the run — by whichever test
    asked for it first — and this compares a fresh detection against it.
    That is a slightly longer lever than two calls back to back in one
    function: anything that made the detector depend on accumulated state
    would have had the rest of the session to do it in.
    """
    page, lines = _page_and_lines(path)
    shared = _regions(path)
    fresh = find_regions(page, lines, DetectConfig())
    assert [r.polygon for r in shared] == [r.polygon for r in fresh]


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
    # A balloon clipped by the panel corner is the hardest shape to typeset
    # into: two sides are the balloon's own curve and two are straight
    # borrowed edges, so a layout fitted to the bounding box would spill over
    # the curve.
    "12-large_balloon_clipped_by_top_right_corner.png",
)


def _round_trip_paths() -> list[Path]:
    return [p for p in _fixture_images() if p.name in ROUND_TRIP_FIXTURES]


@pytest.mark.parametrize("path", _round_trip_paths(), ids=lambda p: p.name)
def test_extract_then_apply_round_trip(path: Path, tmp_path: Path, font_dir: Path) -> None:
    """The whole pipeline over a real page: extract, translate, apply.

    Asserts the two rules that matter most — the source is never written to,
    and everything outside a region's polygon is byte-identical to the source.
    """
    from dataclasses import replace

    import numpy as np
    from PIL import Image

    from comictrans.apply import apply_plan
    from comictrans.config import ApplyConfig, ExtractConfig
    from comictrans.erase import polygon_mask
    from comictrans.extract import extract
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

    translated = replace(
        plan, regions=tuple(r.with_translation("TRANSLATED **TEXT** HERE") for r in plan.regions)
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
