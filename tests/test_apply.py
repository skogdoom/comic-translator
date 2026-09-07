from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from comictrans.apply import ApplyReport, apply_plan, check_output_dir, resolve_styles
from comictrans.config import ApplyConfig, EraseConfig, TypesetConfig
from comictrans.errors import ComictransError, InputError
from comictrans.imaging import load_page, output_format_for, output_path
from comictrans.model import Box, Color, Geometry, Plan, PlanHeader, Region, TextCase
from comictrans.planfile import write_plan
from comictrans.util import sha256_file

from .conftest import (
    ART_DARK,
    BALLOON_WHITE,
    INK_BLACK,
    make_page_array,
    save_page,
)

BALLOON = Box(80, 80, 520, 320)
TEXT_BOX = Box(140, 170, 460, 210)


def _page_array() -> np.ndarray:
    return make_page_array(
        (600, 400), ART_DARK, [("ellipse", BALLOON, BALLOON_WHITE, INK_BLACK, [TEXT_BOX])]
    )


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
def project(tmp_path: Path) -> tuple[Path, Path, Plan]:
    """A source directory, a plan beside it, and an output directory."""
    source = tmp_path / "pages"
    source.mkdir()
    image = save_page(_page_array(), source / "page-001.png")
    plan_path = source / "comic-plan.yaml"
    plan = Plan(header=_header(), regions=(_region("page-001.png", sha256_file(image)),))
    write_plan(plan, plan_path)
    return plan_path, tmp_path / "out", plan


def _apply(project: tuple[Path, Path, Plan], **kwargs: object) -> ApplyReport:
    plan_path, output, plan = project
    plan = kwargs.pop("plan", plan)  # type: ignore[assignment]
    config = kwargs.pop("config", ApplyConfig())
    return apply_plan(plan, plan_path, output, config, **kwargs)  # type: ignore[arg-type]


def test_apply_writes_a_page_and_leaves_the_source_alone(
    project: tuple[Path, Path, Plan], font_dir: Path
) -> None:
    plan_path, output, _ = project
    source = plan_path.parent / "page-001.png"
    before = sha256_file(source)

    report = _apply(project)

    assert report.ok
    assert report.rendered == 1
    assert (output / "page-001.png").is_file()
    assert sha256_file(source) == before, "source image was modified"


def test_the_original_lettering_is_gone_and_new_text_is_drawn(
    project: tuple[Path, Path, Plan], font_dir: Path
) -> None:
    plan_path, output, _plan = project
    _apply(project)

    source = np.asarray(Image.open(plan_path.parent / "page-001.png").convert("RGB"))
    result = np.asarray(Image.open(output / "page-001.png").convert("RGB"))

    inside = (
        slice(BALLOON.top + 5, BALLOON.bottom - 5),
        slice(BALLOON.left + 5, BALLOON.right - 5),
    )
    assert not np.array_equal(source[inside], result[inside]), "nothing was drawn"
    outside = (slice(0, 40), slice(0, 40))
    assert np.array_equal(source[outside], result[outside]), "artwork outside was touched"


def test_output_dimensions_and_metadata_survive(
    project: tuple[Path, Path, Plan], font_dir: Path
) -> None:
    plan_path, output, plan = project
    source = plan_path.parent / "page-001.png"
    Image.fromarray(_page_array()).save(source, dpi=(300, 300))
    plan = Plan(header=plan.header, regions=(_region("page-001.png", sha256_file(source)),))

    _apply(project, plan=plan)

    with Image.open(output / "page-001.png") as written:
        assert written.size == (600, 400)
        assert written.info["dpi"] == pytest.approx((300.0, 300.0), abs=0.01)


def test_apply_is_deterministic(project: tuple[Path, Path, Plan], font_dir: Path) -> None:
    _, output, _ = project
    _apply(project)
    first = (output / "page-001.png").read_bytes()
    _apply(project, force=True)
    assert (output / "page-001.png").read_bytes() == first


def test_editing_a_translation_changes_only_the_text(
    project: tuple[Path, Path, Plan], font_dir: Path
) -> None:
    _, output, plan = project
    _apply(project)
    before = np.asarray(Image.open(output / "page-001.png").convert("RGB"), dtype=np.int16)

    edited = Plan(header=plan.header, regions=(plan.regions[0].with_translation("SOMETHING ELSE"),))
    _apply(project, plan=edited, force=True)
    after = np.asarray(Image.open(output / "page-001.png").convert("RGB"), dtype=np.int16)

    changed = np.abs(before - after).max(axis=2) > 0
    assert changed.any(), "the edit did nothing"
    rows, cols = np.nonzero(changed)
    assert rows.min() >= BALLOON.top and rows.max() < BALLOON.bottom
    assert cols.min() >= BALLOON.left and cols.max() < BALLOON.right


def test_an_empty_translation_is_skipped_and_fails_the_run(
    project: tuple[Path, Path, Plan], font_dir: Path
) -> None:
    plan_path, output, plan = project
    blank = Plan(header=plan.header, regions=(plan.regions[0].with_translation("  "),))
    report = _apply(project, plan=blank)

    assert report.skipped_empty == 1
    assert report.rendered == 0
    assert not report.ok, "an unfinished translation must fail the run"

    source = np.asarray(Image.open(plan_path.parent / "page-001.png").convert("RGB"))
    result = np.asarray(Image.open(output / "page-001.png").convert("RGB"))
    assert np.array_equal(source, result), "a skipped region must be left untouched"


def test_skip_true_is_deliberate_and_passes(
    project: tuple[Path, Path, Plan], font_dir: Path
) -> None:
    plan_path, output, plan = project
    marked = Plan(
        header=plan.header,
        regions=(_region("page-001.png", plan.regions[0].image_sha256, skip=True),),
    )
    report = _apply(project, plan=marked)

    assert report.skipped_flag == 1
    assert report.ok, "an explicit skip is a decision, not a failure"
    source = np.asarray(Image.open(plan_path.parent / "page-001.png").convert("RGB"))
    assert np.array_equal(source, np.asarray(Image.open(output / "page-001.png").convert("RGB")))


def test_a_region_that_will_not_fit_is_reported_and_left_alone(
    project: tuple[Path, Path, Plan], font_dir: Path
) -> None:
    plan_path, output, plan = project
    # The floor is raised too, so shrinking below the readable minimum cannot
    # rescue it either: this is a region that genuinely will not fit.
    config = ApplyConfig(
        typeset=TypesetConfig(font_size_min_ratio=0.25, font_size_floor_ratio=0.22, hyphenate=False)
    )
    long = plan.regions[0].with_translation(
        "A TRANSLATION FAR TOO LONG TO EVER FIT INSIDE THIS BALLOON AT THAT SIZE"
    )
    report = _apply(project, plan=Plan(header=plan.header, regions=(long,)), config=config)

    assert report.failed == 1
    assert not report.ok
    assert "page-001" in [outcome.region_id for _, outcome in report.outcomes]
    source = np.asarray(Image.open(plan_path.parent / "page-001.png").convert("RGB"))
    result = np.asarray(Image.open(output / "page-001.png").convert("RGB"))
    assert np.array_equal(source, result), "a failed region must not be half-erased"


def test_unbalanced_markup_fails_that_region_only(
    project: tuple[Path, Path, Plan], font_dir: Path
) -> None:
    _, _, plan = project
    broken = plan.regions[0].with_translation("THIS **NEVER CLOSES")
    report = _apply(project, plan=Plan(header=plan.header, regions=(broken,)))
    assert report.failed == 1
    assert "unbalanced" in report.outcomes[0][1].detail


def test_existing_output_is_not_overwritten_without_force(
    project: tuple[Path, Path, Plan], font_dir: Path
) -> None:
    _, output, _ = project
    output.mkdir(parents=True)
    (output / "page-001.png").write_bytes(b"precious")

    report = _apply(project)

    assert not report.ok
    assert "already exists" in report.page_failures[0][1]
    assert (output / "page-001.png").read_bytes() == b"precious"

    report = _apply(project, force=True)
    assert report.ok
    assert (output / "page-001.png").read_bytes() != b"precious"


def test_output_inside_the_source_directory_is_refused(
    project: tuple[Path, Path, Plan], font_dir: Path
) -> None:
    plan_path, _, plan = project
    with pytest.raises(InputError, match="inside the source directory"):
        apply_plan(plan, plan_path, plan_path.parent / "out", ApplyConfig())


def test_the_source_directory_itself_is_refused(
    project: tuple[Path, Path, Plan], font_dir: Path
) -> None:
    plan_path, _, plan = project
    with pytest.raises(InputError, match="inside the source directory"):
        apply_plan(plan, plan_path, plan_path.parent, ApplyConfig())


def test_check_output_dir_allows_a_sibling(tmp_path: Path) -> None:
    source = tmp_path / "pages"
    source.mkdir()
    check_output_dir(tmp_path / "out", [source])


def test_font_precedence_is_cli_then_region_then_header(font_dir: Path) -> None:
    plan = Plan(
        header=_header(font="Comic Sans MS"),
        regions=(
            _region("a.png", "0" * 64, id="header-font"),
            _region("a.png", "0" * 64, id="region-font", font="Marker Felt"),
        ),
    )
    styles = resolve_styles(plan, None, require_bold=False)
    assert styles["header-font"].face.family == "Comic Sans MS"
    assert styles["region-font"].face.family == "Marker Felt"

    overridden = resolve_styles(plan, "Comic Sans MS", require_bold=False)
    assert {s.face.family for s in overridden.values()} == {"Comic Sans MS"}


def test_a_missing_font_is_an_error_not_a_substitution(tmp_path: Path, font_dir: Path) -> None:
    pages = tmp_path / "pages"
    pages.mkdir()
    plan = Plan(header=_header(font="Nonexistent Face"), regions=(_region("a.png", "0" * 64),))
    with pytest.raises(ComictransError, match="not found"):
        apply_plan(plan, pages / "plan.yaml", tmp_path / "out", ApplyConfig())


def test_region_font_size_override_is_honoured(
    project: tuple[Path, Path, Plan], font_dir: Path
) -> None:
    _, _, plan = project
    styles = resolve_styles(
        Plan(header=plan.header, regions=(_region("a.png", "0" * 64, font_size=17),)), None
    )
    assert styles["page-001"].size == 17


def test_jpeg_sources_are_written_as_png_to_avoid_generational_loss(tmp_path: Path) -> None:
    from comictrans.imaging import PageMeta

    meta = PageMeta(format="JPEG", mode="RGB", dpi=None, icc_profile=None)
    assert output_format_for(meta) == "PNG"
    assert output_path(Path("a/page-004.jpg"), tmp_path, meta).name == "page-004.png"


def test_other_formats_match_the_source(tmp_path: Path) -> None:
    from comictrans.imaging import PageMeta

    for source_format, expected in (("PNG", ".png"), ("TIFF", ".tif")):
        meta = PageMeta(format=source_format, mode="RGB", dpi=None, icc_profile=None)
        assert output_path(Path(f"a/p{expected}"), tmp_path, meta).suffix == expected


def test_format_override_wins(tmp_path: Path) -> None:
    from comictrans.imaging import PageMeta

    meta = PageMeta(format="PNG", mode="RGB", dpi=None, icc_profile=None)
    assert output_path(Path("a/p.png"), tmp_path, meta, "jpeg").suffix == ".jpg"
    with pytest.raises(InputError, match="unsupported output format"):
        output_format_for(meta, "webp")


def test_a_16_bit_source_is_refused_rather_than_downconverted(tmp_path: Path) -> None:
    from comictrans.imaging import check_writable

    path = tmp_path / "deep.tif"
    Image.new("I;16", (20, 20)).save(path)
    meta = load_page(path).meta
    with pytest.raises(InputError, match="not supported"):
        check_writable(meta, path)


def test_output_is_written_flat_from_a_nested_source(tmp_path: Path, font_dir: Path) -> None:
    source = tmp_path / "chapter" / "pages"
    source.mkdir(parents=True)
    image = save_page(_page_array(), source / "page-001.png")
    plan_path = tmp_path / "plan.yaml"
    plan = Plan(
        header=_header(),
        regions=(_region("chapter/pages/page-001.png", sha256_file(image)),),
    )
    write_plan(plan, plan_path)

    report = apply_plan(plan, plan_path, tmp_path / "out", ApplyConfig())

    assert report.ok
    assert (tmp_path / "out" / "page-001.png").is_file()


def test_erase_strategy_is_configurable(project: tuple[Path, Path, Plan], font_dir: Path) -> None:
    for strategy in ("flat", "polygon", "inpaint"):
        report = _apply(
            project, config=ApplyConfig(erase=EraseConfig(strategy=strategy)), force=True
        )
        assert report.ok, strategy


def test_an_unedited_translation_is_rendered_but_reported(
    project: tuple[Path, Path, Plan], font_dir: Path
) -> None:
    # Extract seeds translation with source_text, so a balloon you never got
    # to renders its own Italian back onto the page. It must not pass silently.
    _, _, plan = project
    seeded = _region("page-001.png", plan.regions[0].image_sha256, translation="CIAO A TUTTI")
    report = _apply(project, plan=Plan(header=plan.header, regions=(seeded,)))

    assert report.rendered == 1, "a seeded region still renders"
    assert [outcome.region_id for _, outcome in report.unedited] == ["page-001"]


def test_an_edited_translation_is_not_reported_as_unedited(
    project: tuple[Path, Path, Plan], font_dir: Path
) -> None:
    report = _apply(project)
    assert report.rendered == 1
    assert report.unedited == []


def test_whitespace_only_edits_do_not_count_as_translated(
    project: tuple[Path, Path, Plan], font_dir: Path
) -> None:
    _, _, plan = project
    seeded = _region("page-001.png", plan.regions[0].image_sha256, translation="  CIAO A TUTTI\n")
    report = _apply(project, plan=Plan(header=plan.header, regions=(seeded,)))
    assert len(report.unedited) == 1


def test_a_region_rendered_below_the_minimum_is_reported(
    project: tuple[Path, Path, Plan], font_dir: Path
) -> None:
    _, _, plan = project
    config = ApplyConfig(
        typeset=TypesetConfig(font_size_min_ratio=0.12, font_size_floor_ratio=0.01, hyphenate=False)
    )
    long = plan.regions[0].with_translation(
        "CONSIDERABLY MORE DIALOGUE THAN THIS BALLOON WAS DRAWN TO HOLD AT THAT SIZE"
    )
    report = _apply(project, plan=Plan(header=plan.header, regions=(long,)), config=config)

    assert report.rendered == 1, "it should render small rather than fail"
    assert report.failed == 0
    ((_, outcome),) = report.undersized
    assert outcome.region_id == "page-001"
    assert 0 < outcome.font_size < round(0.12 * 400)


def test_a_comfortable_region_is_not_reported_as_undersized(
    project: tuple[Path, Path, Plan], font_dir: Path
) -> None:
    report = _apply(project)
    assert report.rendered == 1
    assert report.undersized == []
