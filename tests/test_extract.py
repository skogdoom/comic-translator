from __future__ import annotations

from pathlib import Path

import pytest

from comictrans.config import ExtractConfig, OcrConfig
from comictrans.errors import InputError
from comictrans.extract import default_plan_path, extract
from comictrans.model import Box, Geometry, TextCase
from comictrans.planfile import load_plan, write_plan
from comictrans.util import sha256_file

from .conftest import (
    ART_DARK,
    BALLOON_WHITE,
    INK_BLACK,
    FakeRecognizer,
    lines_for,
    make_page_array,
    save_page,
)


def _write_balloon_page(path: Path, boxes: list[Box]) -> Path:
    array = make_page_array(
        (600, 800),
        ART_DARK,
        [("ellipse", Box(120, 100, 420, 260), BALLOON_WHITE, INK_BLACK, boxes)],
    )
    return save_page(array, path)


@pytest.fixture
def pages(tmp_path: Path) -> tuple[Path, FakeRecognizer, list[Box]]:
    directory = tmp_path / "pages"
    directory.mkdir()
    boxes = [Box(160, 140, 360, 164), Box(160, 180, 340, 204)]
    for name in ("page1.png", "page2.png", "page10.png"):
        _write_balloon_page(directory / name, boxes)
    recognizer = FakeRecognizer(
        {
            name: lines_for(boxes, ["NON CI POSSO", "CREDERE!"])
            for name in ("page1.png", "page2.png", "page10.png")
        }
    )
    return directory, recognizer, boxes


def _run(
    directory: Path,
    recognizer: FakeRecognizer,
    plan_path: Path | None = None,
    config: ExtractConfig | None = None,
):
    plan_path = plan_path or default_plan_path(directory)
    return extract(
        directory,
        plan_path,
        recognizer,
        "Comic Sans MS",
        config or ExtractConfig(),
    )


def test_default_plan_path_for_directory_and_single_file(tmp_path: Path) -> None:
    directory = tmp_path / "pages"
    directory.mkdir()
    assert default_plan_path(directory) == directory / "comic-plan.yaml"
    image = tmp_path / "page-001.png"
    image.touch()
    assert default_plan_path(image) == tmp_path / "page-001-plan.yaml"


def test_extract_visits_pages_in_natural_order(
    pages: tuple[Path, FakeRecognizer, list[Box]],
) -> None:
    directory, recognizer, _ = pages
    _run(directory, recognizer)
    assert [p.name for p in recognizer.calls] == ["page1.png", "page2.png", "page10.png"]


def test_extract_writes_no_images(pages: tuple[Path, FakeRecognizer, list[Box]]) -> None:
    directory, recognizer, _ = pages
    before = {p.name: p.stat().st_mtime_ns for p in directory.iterdir()}
    plan, _ = _run(directory, recognizer)
    write_plan(plan, directory / "comic-plan.yaml")
    after = {p.name: p.stat().st_mtime_ns for p in directory.iterdir() if p.suffix == ".png"}
    assert after == {k: v for k, v in before.items() if k.endswith(".png")}


def test_region_ids_and_order_restart_per_page(
    pages: tuple[Path, FakeRecognizer, list[Box]],
) -> None:
    directory, recognizer, _ = pages
    plan, report = _run(directory, recognizer)
    assert report.pages_read == 3
    assert report.regions == 3
    assert [r.id for r in plan.regions] == ["page1-001", "page2-001", "page10-001"]
    assert {r.order for r in plan.regions} == {1}


def test_region_ids_are_stable_across_runs(
    pages: tuple[Path, FakeRecognizer, list[Box]],
) -> None:
    directory, recognizer, _ = pages
    first, _ = _run(directory, recognizer)
    second, _ = _run(directory, recognizer)
    assert [r.id for r in first.regions] == [r.id for r in second.regions]
    assert [r.polygon for r in first.regions] == [r.polygon for r in second.regions]


def test_regions_record_the_image_hash_and_a_relative_path(
    pages: tuple[Path, FakeRecognizer, list[Box]],
) -> None:
    directory, recognizer, _ = pages
    plan, _ = _run(directory, recognizer)
    region = plan.regions[0]
    assert region.image == "page1.png"
    assert region.image_sha256 == sha256_file(directory / "page1.png")


def test_plan_written_beside_pages_round_trips_and_verifies_hashes(
    pages: tuple[Path, FakeRecognizer, list[Box]],
) -> None:
    directory, recognizer, _ = pages
    plan_path = directory / "comic-plan.yaml"
    plan, _ = _run(directory, recognizer, plan_path)
    write_plan(plan, plan_path)
    assert load_plan(plan_path) == plan


def test_plan_in_another_directory_uses_a_relative_path(
    pages: tuple[Path, FakeRecognizer, list[Box]], tmp_path: Path
) -> None:
    directory, recognizer, _ = pages
    plan_path = tmp_path / "plans" / "comic-plan.yaml"
    plan, _ = _run(directory, recognizer, plan_path)
    write_plan(plan, plan_path)
    assert plan.regions[0].image == "../pages/page1.png"
    load_plan(plan_path)


def test_translations_start_empty_and_notes_are_present(
    pages: tuple[Path, FakeRecognizer, list[Box]],
) -> None:
    directory, recognizer, _ = pages
    plan, _ = _run(directory, recognizer)
    assert all(r.translation == "" and r.notes == "" for r in plan.regions)
    assert all(not r.is_actionable for r in plan.regions)


def test_source_text_keeps_the_original_line_breaks(
    pages: tuple[Path, FakeRecognizer, list[Box]],
) -> None:
    directory, recognizer, _ = pages
    plan, _ = _run(directory, recognizer)
    assert plan.regions[0].source_text == "NON CI POSSO\nCREDERE!"


def test_low_confidence_regions_are_flagged_not_dropped(tmp_path: Path) -> None:
    directory = tmp_path / "pages"
    directory.mkdir()
    boxes = [Box(160, 140, 360, 164)]
    _write_balloon_page(directory / "page1.png", boxes)
    recognizer = FakeRecognizer({"page1.png": lines_for(boxes, ["CIAO"], confidence=0.31)})

    plan, report = _run(directory, recognizer)

    assert report.regions == 1
    assert report.low_confidence == 1
    assert plan.regions[0].low_confidence
    assert plan.regions[0].source_text == "CIAO"


def test_confidence_threshold_is_configurable(tmp_path: Path) -> None:
    directory = tmp_path / "pages"
    directory.mkdir()
    boxes = [Box(160, 140, 360, 164)]
    _write_balloon_page(directory / "page1.png", boxes)
    recognizer = FakeRecognizer({"page1.png": lines_for(boxes, ["CIAO"], confidence=0.6)})

    config = ExtractConfig(ocr=OcrConfig(confidence_threshold=0.8))
    plan, report = _run(directory, recognizer, config=config)
    assert plan.regions[0].low_confidence
    assert report.low_confidence == 1


def test_a_page_with_no_text_is_reported_and_fails_the_run(
    pages: tuple[Path, FakeRecognizer, list[Box]],
) -> None:
    directory, recognizer, _ = pages
    recognizer.by_filename["page2.png"] = []
    plan, report = _run(directory, recognizer)

    assert [p.name for p in report.empty_pages] == ["page2.png"]
    assert report.pages_read == 3
    assert not report.ok
    # The other pages still made it into the plan.
    assert [r.id for r in plan.regions] == ["page1-001", "page10-001"]


def test_a_broken_page_does_not_abort_the_run(
    pages: tuple[Path, FakeRecognizer, list[Box]],
) -> None:
    directory, recognizer, _ = pages
    (directory / "page2.png").write_bytes(b"corrupt")
    plan, report = _run(directory, recognizer)

    assert [p.name for p, _ in report.failures] == ["page2.png"]
    assert report.pages_read == 2
    assert not report.ok
    assert [r.id for r in plan.regions] == ["page1-001", "page10-001"]


def test_non_image_files_are_skipped_and_reported(
    pages: tuple[Path, FakeRecognizer, list[Box]],
) -> None:
    directory, recognizer, _ = pages
    (directory / "notes.txt").write_text("hi")
    _, report = _run(directory, recognizer)
    assert [p.name for p, _ in report.skipped_inputs] == ["notes.txt"]
    assert report.ok, "a stray text file is not a failure"


def test_header_records_engine_font_and_case(
    pages: tuple[Path, FakeRecognizer, list[Box]],
) -> None:
    directory, recognizer, _ = pages
    plan, _ = extract(
        directory,
        default_plan_path(directory),
        recognizer,
        "Chalkboard SE",
        ExtractConfig(),
        case=TextCase.PRESERVE,
    )
    assert plan.header.ocr_engine == "fake"
    assert plan.header.font == "Chalkboard SE"
    assert plan.header.case is TextCase.PRESERVE


def test_debug_dir_dumps_overlays(
    pages: tuple[Path, FakeRecognizer, list[Box]], tmp_path: Path
) -> None:
    directory, recognizer, _ = pages
    debug = tmp_path / "debug"
    extract(
        directory,
        default_plan_path(directory),
        recognizer,
        "Comic Sans MS",
        ExtractConfig(),
        debug_dir=debug,
    )
    names = sorted(p.name for p in debug.iterdir())
    assert "page1-regions.png" in names
    assert "page1-masks.png" in names


def test_a_single_file_input_works(tmp_path: Path) -> None:
    boxes = [Box(160, 140, 360, 164)]
    image = _write_balloon_page(tmp_path / "solo.png", boxes)
    recognizer = FakeRecognizer({"solo.png": lines_for(boxes, ["CIAO"])})
    plan, report = extract(
        image, default_plan_path(image), recognizer, "Comic Sans MS", ExtractConfig()
    )
    assert report.pages_read == 1
    assert plan.regions[0].id == "solo-001"


def test_missing_input_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="does not exist"):
        extract(
            tmp_path / "nope",
            tmp_path / "plan.yaml",
            FakeRecognizer({}),
            "Comic Sans MS",
            ExtractConfig(),
        )


def test_geometry_flag_is_carried_into_the_plan(tmp_path: Path) -> None:
    directory = tmp_path / "pages"
    directory.mkdir()
    boxes = [Box(160, 140, 360, 164)]
    array = make_page_array(
        (600, 800), (200, 200, 200), [("none", Box(0, 0, 1, 1), (200, 200, 200), INK_BLACK, boxes)]
    )
    save_page(array, directory / "flat.png")
    recognizer = FakeRecognizer({"flat.png": lines_for(boxes, ["CRASH"])})

    plan, report = _run(directory, recognizer)
    assert plan.regions[0].geometry is Geometry.APPROXIMATE
    assert report.approximate == 1
