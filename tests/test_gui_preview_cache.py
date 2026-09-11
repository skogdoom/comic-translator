"""What counts as the same page, decided without a window.

The cache's whole risk is the quiet one: showing an old render as though it
were current. So these are about the key and nothing else — what it notices,
and what it deliberately does not.

No Qt, like the module they test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from comictrans.gui.document import PlanDocument
from comictrans.gui.preview import Preview, PreviewRequest
from comictrans.gui.preview_cache import PreviewCache, PreviewKey
from comictrans.model import Box, Color, Geometry, PlanHeader, Region, TextCase
from comictrans.planfile import write_plan
from comictrans.planfile.schema import PLAN_VERSION
from comictrans.util import sha256_file

from .conftest import ART_DARK, BALLOON_WHITE, INK_BLACK, make_page_array, make_plan, save_page

BALLOON = Box(80, 80, 520, 320)
TEXT_BOX = Box(140, 170, 460, 210)


def _header(**overrides: object) -> PlanHeader:
    base: dict[str, object] = {
        "version": PLAN_VERSION,
        "generator": "comictrans test",
        "created": "2026-09-11T12:00:00Z",
        "source_language": "it",
        "target_language": "en",
        "ocr_engine": "fake",
        "font": "Comic Sans MS",
        "case": TextCase.UPPER,
        "font_size_min_ratio": 0.012,
        "condense_min": 0.9,
    }
    base.update(overrides)
    return PlanHeader(**base)  # type: ignore[arg-type]


def _region(image: str, region_id: str, **overrides: object) -> Region:
    base: dict[str, object] = {
        "id": region_id,
        "image": image,
        "order": 1,
        "geometry": Geometry.EXACT,
        "polygon": BALLOON.as_polygon(),
        "fill_color": Color(250, 250, 250),
        "text_color": Color(20, 20, 20),
        "confidence": 0.9,
        "source_text": "CIAO",
        "translation": "HELLO",
    }
    base.update(overrides)
    return Region(**base)  # type: ignore[arg-type]


@pytest.fixture
def document(tmp_path: Path) -> PlanDocument:
    """Two pages, one region each, so a change to the other page is testable."""
    source = tmp_path / "pages"
    source.mkdir()
    digests = {}
    for name in ("page-001.png", "page-002.png"):
        image = save_page(
            make_page_array(
                (600, 400), ART_DARK, [("ellipse", BALLOON, BALLOON_WHITE, INK_BLACK, [TEXT_BOX])]
            ),
            source / name,
        )
        digests[name] = sha256_file(image)
    plan = make_plan(
        _header(),
        (_region("page-001.png", "page-001-001"), _region("page-002.png", "page-002-001")),
        digests,
    )
    plan_path = source / "comic-plan.yaml"
    write_plan(plan, plan_path)
    return PlanDocument.open(plan_path)


def _key(document: PlanDocument, image: str = "page-001.png") -> PreviewKey:
    return PreviewKey.of(PreviewRequest.of(document, image))


def test_the_same_page_unchanged_is_the_same_key(document: PlanDocument) -> None:
    assert _key(document) == _key(document)


def test_editing_this_page_changes_the_key(document: PlanDocument) -> None:
    before = _key(document)
    document.set_translation("page-001-001", "SOMETHING ELSE")
    assert _key(document) != before


def test_editing_another_page_does_not(document: PlanDocument) -> None:
    """The reason the key is not the plan.

    A request carries the whole ``Plan``, so keying on it would miss the
    moment anything anywhere changed — which during a review is most edits,
    and would leave the cache hitting almost never.
    """
    before = _key(document)
    document.set_translation("page-002-001", "A CHANGE ON THE OTHER PAGE")

    assert document.plan != before, "the plan really did change"
    assert _key(document) == before, "but nothing this page renders from did"


def test_editing_the_header_changes_the_key(document: PlanDocument) -> None:
    """Every region is drawn under it, so no region's render survives it."""
    before = _key(document)
    document.set_header_condense_min(0.5)
    assert _key(document) != before


def test_replacing_the_page_on_disk_changes_the_key(document: PlanDocument) -> None:
    """The plan's hash says what the page was when it opened, not what it is.

    Every uncached render re-read the file. A cache that did not look would
    be the one place this window stopped noticing.
    """
    before = _key(document)
    page = document.source_path("page-001.png")
    save_page(
        make_page_array((600, 400), INK_BLACK, [("rect", BALLOON, ART_DARK, INK_BLACK, [])]),
        page,
    )
    assert _key(document) != before


def test_a_page_that_cannot_be_read_still_makes_a_key(document: PlanDocument) -> None:
    """Missing is a state, not a crash — and not the state of a file that is there."""
    present = _key(document)
    document.source_path("page-001.png").unlink()

    absent = _key(document)
    assert absent.stamp is None
    assert absent != present


def _preview() -> Preview:
    from PIL import Image

    return Preview(Image.new("RGB", (4, 4)), ())


def test_the_cache_holds_one_and_hands_it_back(document: PlanDocument) -> None:
    cache = PreviewCache()
    request = PreviewRequest.of(document, "page-001.png")
    held = _preview()

    assert cache.get(request) is None
    assert not cache.holding

    cache.put(request, held)
    assert cache.holding
    assert cache.get(request) is held


def test_the_cache_misses_once_the_page_has_changed(document: PlanDocument) -> None:
    cache = PreviewCache()
    cache.put(PreviewRequest.of(document, "page-001.png"), _preview())

    document.set_translation("page-001-001", "EDITED")

    assert cache.get(PreviewRequest.of(document, "page-001.png")) is None


def test_the_cache_holds_one_page_not_two(document: PlanDocument) -> None:
    """Measured: a retained preview of an eleven-megapixel page is 33MB."""
    cache = PreviewCache()
    first = PreviewRequest.of(document, "page-001.png")
    second = PreviewRequest.of(document, "page-002.png")

    cache.put(first, _preview())
    cache.put(second, _preview())

    assert cache.get(second) is not None
    assert cache.get(first) is None, "the second one replaced it"


def test_clearing_lets_go(document: PlanDocument) -> None:
    cache = PreviewCache()
    request = PreviewRequest.of(document, "page-001.png")
    cache.put(request, _preview())

    cache.clear()

    assert not cache.holding
    assert cache.get(request) is None
