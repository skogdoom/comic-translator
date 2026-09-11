from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from comictrans.apply import apply_plan
from comictrans.config import ApplyConfig
from comictrans.gui.document import PlanDocument
from comictrans.gui.preview import Preview, PreviewRequest, render_preview
from comictrans.imaging import load_page
from comictrans.model import Box, Color, Geometry, PlanHeader, Region, TextCase
from comictrans.planfile import write_plan
from comictrans.planfile.schema import PLAN_VERSION
from comictrans.util import sha256_file

from .conftest import (
    ART_DARK,
    BALLOON_WHITE,
    INK_BLACK,
    make_page_array,
    make_plan,
    save_page,
)

BALLOON = Box(80, 80, 520, 320)
TEXT_BOX = Box(140, 170, 460, 210)


def _header(**overrides: object) -> PlanHeader:
    base: dict[str, object] = {
        "version": PLAN_VERSION,
        "generator": "comictrans test",
        "created": "2026-09-07T12:00:00Z",
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


def _region(image: str, **overrides: object) -> Region:
    base: dict[str, object] = {
        "id": "page-001",
        "image": image,
        "order": 1,
        "geometry": Geometry.EXACT,
        "polygon": BALLOON.as_polygon(),
        "fill_color": Color(250, 250, 250),
        "text_color": Color(20, 20, 20),
        "confidence": 0.9,
        "source_text": "CIAO A TUTTI",
        "translation": "HELLO EVERYONE",
    }
    base.update(overrides)
    return Region(**base)  # type: ignore[arg-type]


@pytest.fixture
def document(tmp_path: Path) -> PlanDocument:
    source = tmp_path / "pages"
    source.mkdir()
    image = save_page(
        make_page_array(
            (600, 400), ART_DARK, [("ellipse", BALLOON, BALLOON_WHITE, INK_BLACK, [TEXT_BOX])]
        ),
        source / "page-001.png",
    )
    plan_path = source / "comic-plan.yaml"
    plan = make_plan(_header(), (_region("page-001.png"),), {"page-001.png": sha256_file(image)})
    write_plan(plan, plan_path)
    return PlanDocument.open(plan_path)


def _preview(document: PlanDocument, image: str) -> Preview:
    """Render as the window does: from the snapshot, not from the document.

    ``PreviewRequest.of`` is the only place the document is read, and on the
    window's thread. Going through it here rather than calling
    ``render_preview`` with hand-assembled arguments is what keeps these
    tests honest about what a preview actually works from.
    """
    request = PreviewRequest.of(document, image)
    return render_preview(request.plan, request.plan_path, request.image)


def test_a_preview_works_from_a_snapshot_that_a_later_edit_cannot_reach(
    document: PlanDocument, font_dir: Path
) -> None:
    """The reason it can run on a worker thread at all.

    The request holds a frozen ``Plan``. Editing the document afterwards
    builds a new one and leaves the captured plan alone, so a render already
    under way cannot see half of an edit — no lock, and nothing to get wrong.
    """
    region_id = document.regions_for("page-001.png")[0].id
    request = PreviewRequest.of(document, "page-001.png")
    captured = request.plan

    document.set_translation(region_id, "EDITED WHILE THE THREAD WAS RUNNING")

    assert request.plan is captured, "the request still holds what it was given"
    assert captured.regions[0].translation != "EDITED WHILE THE THREAD WAS RUNNING"
    assert document.plan.regions[0].translation == "EDITED WHILE THE THREAD WAS RUNNING"

    from_snapshot = render_preview(request.plan, request.plan_path, request.image)
    from_document = _preview(document, "page-001.png")

    assert not np.array_equal(
        np.asarray(from_snapshot.image.convert("RGB")),
        np.asarray(from_document.image.convert("RGB")),
    ), "the snapshot renders what it captured, not what the document says now"


def test_render_preview_matches_apply_pixel_for_pixel(
    document: PlanDocument, font_dir: Path
) -> None:
    # The whole point of calling render_page directly is that this can never
    # drift from what a real `comictrans apply` of the same plan writes.
    preview = _preview(document, "page-001.png")
    assert preview.image.size == (600, 400)
    assert [o.region_id for o in preview.outcomes] == ["page-001"]
    assert preview.outcomes[0].rendered

    report = apply_plan(
        document.plan, document.path, document.path.parent.parent / "out", ApplyConfig()
    )
    applied = load_page(report.pages_written[0])
    assert np.array_equal(np.asarray(preview.image.convert("RGB")), applied.rgb)


def test_render_preview_reflects_an_unsaved_edit(document: PlanDocument, font_dir: Path) -> None:
    region_id = document.regions_for("page-001.png")[0].id
    before = _preview(document, "page-001.png")

    document.set_translation(region_id, "A COMPLETELY DIFFERENT LINE")
    after = _preview(document, "page-001.png")

    assert not np.array_equal(
        np.asarray(before.image.convert("RGB")), np.asarray(after.image.convert("RGB"))
    )


def test_render_preview_never_writes_the_plan_file(document: PlanDocument, font_dir: Path) -> None:
    original = document.path.read_text(encoding="utf-8")
    document.set_translation(document.regions_for("page-001.png")[0].id, "SOMETHING ELSE")
    _preview(document, "page-001.png")
    assert document.path.read_text(encoding="utf-8") == original


def test_problems_flags_a_region_that_does_not_fit(tmp_path: Path, font_dir: Path) -> None:
    source = tmp_path / "pages"
    source.mkdir()
    image = save_page(
        make_page_array(
            (600, 400), ART_DARK, [("ellipse", BALLOON, BALLOON_WHITE, INK_BLACK, [TEXT_BOX])]
        ),
        source / "page-001.png",
    )
    # A polygon far too small for any amount of shrinking or condensing to
    # rescue, the same recipe test_apply.py uses for a genuine fit failure.
    tiny_polygon = Box(90, 90, 100, 100).as_polygon()
    plan = make_plan(
        _header(),
        (
            _region(
                "page-001.png",
                polygon=tiny_polygon,
                translation="A TRANSLATION FAR TOO LONG TO EVER FIT INSIDE THIS TINY BOX",
            ),
        ),
        {"page-001.png": sha256_file(image)},
    )
    plan_path = source / "comic-plan.yaml"
    write_plan(plan, plan_path)
    document = PlanDocument.open(plan_path)

    preview = _preview(document, "page-001.png")
    assert preview.problems
    assert preview.problems[0].failed
