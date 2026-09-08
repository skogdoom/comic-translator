from __future__ import annotations

from pathlib import Path

import numpy as np
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


def test_pages_record_the_image_hash_and_a_relative_path(
    pages: tuple[Path, FakeRecognizer, list[Box]],
) -> None:
    directory, recognizer, _ = pages
    plan, _ = _run(directory, recognizer)
    assert plan.regions[0].image == "page1.png"
    assert plan.image_names()[0] == "page1.png"
    assert plan.sha256_for("page1.png") == sha256_file(directory / "page1.png")


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


def test_translations_are_seeded_with_the_extracted_text(
    pages: tuple[Path, FakeRecognizer, list[Box]],
) -> None:
    # Seeded rather than blank so the Italian can be edited into English in
    # place instead of retyped.
    directory, recognizer, _ = pages
    plan, _ = _run(directory, recognizer)

    assert all(r.translation == r.source_text for r in plan.regions)
    assert all(r.source_text for r in plan.regions)
    assert all(r.notes == "" for r in plan.regions)


def test_a_freshly_extracted_region_reads_as_untranslated(
    pages: tuple[Path, FakeRecognizer, list[Box]],
) -> None:
    directory, recognizer, _ = pages
    plan, _ = _run(directory, recognizer)

    assert all(r.is_untranslated for r in plan.regions)
    # Seeded text is renderable, so apply will letter it as-is unless edited.
    assert all(r.is_actionable for r in plan.regions)


def test_editing_the_translation_clears_the_untranslated_flag(
    pages: tuple[Path, FakeRecognizer, list[Box]],
) -> None:
    directory, recognizer, _ = pages
    plan, _ = _run(directory, recognizer)
    edited = plan.regions[0].with_translation("I CANNOT BELIEVE IT!")
    assert not edited.is_untranslated


def test_seeded_translations_survive_the_plan_file_round_trip(
    pages: tuple[Path, FakeRecognizer, list[Box]],
) -> None:
    directory, recognizer, _ = pages
    plan_path = directory / "comic-plan.yaml"
    plan, _ = _run(directory, recognizer, plan_path)
    write_plan(plan, plan_path)
    assert load_plan(plan_path) == plan


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
    # And so did the empty one: it is a page of the comic, with nothing on it
    # yet, and apply copies it through.
    assert plan.image_names() == ("page1.png", "page2.png", "page10.png")
    assert plan.regions_for("page2.png") == ()


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
    # A page that could not be read is not a page this plan covers: listing it
    # would promise apply a file it cannot open.
    assert plan.image_names() == ("page1.png", "page10.png")


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


def test_languages_default_to_the_configured_pair(
    pages: tuple[Path, FakeRecognizer, list[Box]],
) -> None:
    directory, recognizer, _ = pages
    plan, _ = _run(directory, recognizer)
    assert (plan.header.source_language, plan.header.target_language) == ("it", "en")


def test_languages_are_recorded_from_the_arguments(
    pages: tuple[Path, FakeRecognizer, list[Box]],
) -> None:
    directory, recognizer, _ = pages
    plan, _ = extract(
        directory,
        default_plan_path(directory),
        recognizer,
        "Comic Sans MS",
        ExtractConfig(),
        source_language="ja",
        target_language="de",
    )
    assert plan.header.source_language == "ja"
    assert plan.header.target_language == "de"


def test_a_region_whose_text_is_an_artefact_is_not_seeded(tmp_path: Path) -> None:
    # OCR finds "text" in artwork. Those regions stay in the plan so they can
    # be checked, but seeding them would make apply erase the art to letter
    # nonsense onto it.
    directory = tmp_path / "pages"
    directory.mkdir()
    boxes = [Box(160, 140, 360, 164)]
    _write_balloon_page(directory / "page1.png", boxes)
    recognizer = FakeRecognizer({"page1.png": lines_for(boxes, ["o ©"])})

    plan, report = _run(directory, recognizer)

    region = plan.regions[0]
    assert region.source_text == "o ©", "the reading is kept for inspection"
    assert region.translation == "", "but it is not seeded"
    assert not region.is_actionable
    assert report.artefacts == 1


def test_real_lettering_is_still_seeded(
    pages: tuple[Path, FakeRecognizer, list[Box]],
) -> None:
    directory, recognizer, _ = pages
    plan, report = _run(directory, recognizer)
    assert all(r.translation == r.source_text for r in plan.regions)
    assert report.artefacts == 0


def _page_with_a_window_frame(path: Path, boxes: list[Box], frame: Box) -> Path:
    """A balloon with ordinary lettering, plus a drawn frame OCR can misread."""
    from PIL import Image, ImageDraw

    array = make_page_array(
        (600, 800),
        ART_DARK,
        [("ellipse", Box(120, 100, 420, 260), BALLOON_WHITE, INK_BLACK, boxes)],
    )
    image = Image.fromarray(array)
    draw = ImageDraw.Draw(image)
    # Structure, not a flat ground: the uprights and rail of a window, which is
    # what OCR reads as tall letters.
    draw.rectangle([frame.left, frame.top, frame.right, frame.bottom], fill=(214, 150, 40))
    for x in (frame.left + 30, frame.right - 30):
        draw.line([(x, frame.top), (x, frame.bottom)], fill=(120, 60, 20), width=14)
    draw.line(
        [(frame.left, frame.top + 60), (frame.right, frame.top + 60)],
        fill=(120, 60, 20),
        width=14,
    )
    return save_page(np.asarray(image, dtype=np.uint8), path)


def test_artwork_read_as_a_real_word_is_not_seeded(tmp_path: Path) -> None:
    # The costly artefact. "INA", read off a window frame, is a perfectly good
    # run of letters, so the language test passes it. Seeded, apply erases the
    # frame it was read from and letters "INA" over the hole — measured at 28%
    # of that region's pixels destroyed on a real page.
    directory = tmp_path / "pages"
    directory.mkdir()
    boxes = [Box(160, 140, 360, 164), Box(160, 180, 340, 204)]
    frame = Box(430, 330, 590, 560)
    _page_with_a_window_frame(directory / "page1.png", boxes, frame)
    recognizer = FakeRecognizer(
        {"page1.png": lines_for([*boxes, frame], ["NON CI POSSO", "CREDERE!", "INA"])}
    )

    plan, report = _run(directory, recognizer)

    frames = [r for r in plan.regions if r.source_text == "INA"]
    assert len(frames) == 1, "the region is kept in the plan so it can be checked"
    assert frames[0].translation == "", "but apply must leave the artwork alone"
    assert not frames[0].is_actionable
    assert report.on_artwork == 1
    assert report.artefacts == 0, "it reads as language; it is the size that gives it away"

    speech = [r for r in plan.regions if r.source_text != "INA"]
    assert speech and all(r.translation == r.source_text for r in speech), (
        "real lettering on the same page must still be seeded"
    )


def test_oversized_lettering_on_a_flat_ground_is_still_seeded(tmp_path: Path) -> None:
    # The other half of the test. Size alone is not enough to condemn a region:
    # a display caption is genuinely bigger than body text, and on the
    # screentoned fixture halftone noise drags the page median down until four
    # real captions look oversized. What separates them is what they sit on.
    directory = tmp_path / "pages"
    directory.mkdir()
    boxes = [Box(160, 140, 360, 164), Box(160, 180, 340, 204)]
    # One line of display lettering, 3.75x the page's own, in a caption box
    # with the leading a real one has around it.
    shout = Box(160, 430, 440, 520)
    array = make_page_array(
        (600, 800),
        ART_DARK,
        [
            ("ellipse", Box(120, 100, 420, 260), BALLOON_WHITE, INK_BLACK, boxes),
            ("rect", Box(120, 340, 480, 620), BALLOON_WHITE, INK_BLACK, [shout]),
        ],
    )
    save_page(array, directory / "page1.png")
    recognizer = FakeRecognizer(
        {"page1.png": lines_for([*boxes, shout], ["NON CI POSSO", "CREDERE!", "BASTA"])}
    )

    plan, report = _run(directory, recognizer)

    big = [r for r in plan.regions if r.source_text == "BASTA"]
    assert len(big) == 1
    assert big[0].translation == "BASTA", "a flat ground under big lettering is a caption"
    assert report.on_artwork == 0


def test_a_one_word_balloon_is_not_mistaken_for_an_artefact(tmp_path: Path) -> None:
    directory = tmp_path / "pages"
    directory.mkdir()
    boxes = [Box(160, 140, 360, 164)]
    _write_balloon_page(directory / "page1.png", boxes)
    recognizer = FakeRecognizer({"page1.png": lines_for(boxes, ["BASTA!"])})

    plan, report = _run(directory, recognizer)

    assert plan.regions[0].translation == "BASTA!"
    assert report.artefacts == 0
