from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from comictrans.apply import apply_plan
from comictrans.config import ApplyConfig
from comictrans.gui.document import PlanDocument
from comictrans.gui.preview import render_preview
from comictrans.imaging import load_page
from comictrans.model import Box, Color, Geometry, Plan, PlanHeader, Region, TextCase
from comictrans.planfile import write_plan
from comictrans.util import sha256_file

from .conftest import ART_DARK, BALLOON_WHITE, INK_BLACK, make_page_array, save_page

BALLOON = Box(80, 80, 520, 320)
TEXT_BOX = Box(140, 170, 460, 210)


def _header(**overrides: object) -> PlanHeader:
    base: dict[str, object] = {
        "version": 1,
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


def _region(image: str, digest: str, **overrides: object) -> Region:
    base: dict[str, object] = {
        "id": "page-001",
        "image": image,
        "image_sha256": digest,
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
    plan = Plan(
        header=_header(),
        regions=(_region("page-001.png", sha256_file(image)),),
    )
    write_plan(plan, plan_path)
    return PlanDocument.open(plan_path)


def test_render_preview_matches_apply_pixel_for_pixel(
    document: PlanDocument, font_dir: Path
) -> None:
    # The whole point of calling render_page directly is that this can never
    # drift from what a real `comictrans apply` of the same plan writes.
    preview = render_preview(document, "page-001.png")
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
    before = render_preview(document, "page-001.png")

    document.set_translation(region_id, "A COMPLETELY DIFFERENT LINE")
    after = render_preview(document, "page-001.png")

    assert not np.array_equal(
        np.asarray(before.image.convert("RGB")), np.asarray(after.image.convert("RGB"))
    )


def test_render_preview_never_writes_the_plan_file(document: PlanDocument, font_dir: Path) -> None:
    original = document.path.read_text(encoding="utf-8")
    document.set_translation(document.regions_for("page-001.png")[0].id, "SOMETHING ELSE")
    render_preview(document, "page-001.png")
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
    plan = Plan(
        header=_header(),
        regions=(
            _region(
                "page-001.png",
                sha256_file(image),
                polygon=tiny_polygon,
                translation="A TRANSLATION FAR TOO LONG TO EVER FIT INSIDE THIS TINY BOX",
            ),
        ),
    )
    plan_path = source / "comic-plan.yaml"
    write_plan(plan, plan_path)
    document = PlanDocument.open(plan_path)

    preview = render_preview(document, "page-001.png")
    assert preview.problems
    assert preview.problems[0].failed
