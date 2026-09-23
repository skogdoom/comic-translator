from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from comictrans.cli import EXIT_FATAL, EXIT_OK, EXIT_PROBLEMS, main
from comictrans.model import Box

from .conftest import (
    ART_DARK,
    BALLOON_WHITE,
    INK_BLACK,
    FakeRecognizer,
    lines_for,
    make_page_array,
    save_page,
)

BOXES = [Box(160, 140, 360, 164), Box(160, 180, 340, 204)]


@pytest.fixture
def page_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "pages"
    directory.mkdir()
    array = make_page_array(
        (600, 800),
        ART_DARK,
        [("ellipse", Box(120, 100, 420, 260), BALLOON_WHITE, INK_BLACK, BOXES)],
    )
    save_page(array, directory / "page1.png")
    recognizer = FakeRecognizer({"page1.png": lines_for(BOXES, ["NON CI POSSO", "CREDERE!"])})
    monkeypatch.setattr("comictrans.cli.get_recognizer", lambda config: recognizer)
    return directory


def test_extract_writes_a_plan_and_exits_zero(page_dir: Path, font_dir: Path) -> None:
    assert main(["extract", str(page_dir)]) == EXIT_OK
    assert (page_dir / "comic-plan.yaml").is_file()


def test_extract_refuses_to_overwrite_without_force(page_dir: Path, font_dir: Path) -> None:
    assert main(["extract", str(page_dir)]) == EXIT_OK
    (page_dir / "comic-plan.yaml").write_text("hand edited", encoding="utf-8")
    assert main(["extract", str(page_dir)]) == EXIT_FATAL
    assert (page_dir / "comic-plan.yaml").read_text() == "hand edited"
    assert main(["extract", str(page_dir), "--force"]) == EXIT_OK
    assert "regions:" in (page_dir / "comic-plan.yaml").read_text()


def test_plan_path_is_overridable(page_dir: Path, font_dir: Path, tmp_path: Path) -> None:
    plan = tmp_path / "elsewhere" / "my-plan.yaml"
    assert main(["extract", str(page_dir), "--plan", str(plan)]) == EXIT_OK
    assert plan.is_file()
    assert not (page_dir / "comic-plan.yaml").exists()


def test_debug_dir_inside_the_source_directory_is_refused(page_dir: Path, font_dir: Path) -> None:
    assert main(["extract", str(page_dir), "--debug-dir", str(page_dir / "debug")]) == EXIT_FATAL
    assert not (page_dir / "debug").exists()


def test_debug_dir_outside_the_source_is_written(
    page_dir: Path, font_dir: Path, tmp_path: Path
) -> None:
    debug = tmp_path / "debug"
    assert main(["extract", str(page_dir), "--debug-dir", str(debug)]) == EXIT_OK
    assert (debug / "page1-regions.png").is_file()


def test_a_page_with_no_regions_exits_nonzero(
    page_dir: Path, font_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("comictrans.cli.get_recognizer", lambda config: FakeRecognizer({}))
    assert main(["extract", str(page_dir)]) == EXIT_PROBLEMS


def test_unresolvable_font_is_fatal_and_mentions_the_env_var(
    page_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("COMICTRANS_FONT_PATH", str(tmp_path / "nowhere"))
    monkeypatch.setattr("comictrans.fonts.SEARCH_DIRS", ())
    assert main(["extract", str(page_dir)]) == EXIT_FATAL
    assert "COMICTRANS_FONT_PATH" in capsys.readouterr().err


def test_explicit_font_is_recorded_in_the_header(page_dir: Path, font_dir: Path) -> None:
    from comictrans.planfile import load_plan

    assert main(["extract", str(page_dir), "--font", "Marker Felt"]) == EXIT_FATAL, (
        "Marker Felt has no real bold face, so it must be refused, not faked"
    )
    assert main(["extract", str(page_dir), "--font", "Comic Sans MS"]) == EXIT_OK
    plan = load_plan(page_dir / "comic-plan.yaml")
    assert plan.header.font == "Comic Sans MS"


def test_case_flag_reaches_the_plan_header(page_dir: Path, font_dir: Path) -> None:
    from comictrans.planfile import load_plan

    assert main(["extract", str(page_dir), "--case", "preserve"]) == EXIT_OK
    assert load_plan(page_dir / "comic-plan.yaml").header.case.value == "preserve"


def _extract_then_translate(page_dir: Path, translation: str) -> Path:
    """Run extract, fill in the translation by hand, return the plan path."""
    from dataclasses import replace

    from comictrans.planfile import dumps, load_plan, write_plan

    assert main(["extract", str(page_dir), "--force"]) == EXIT_OK
    plan_path = page_dir / "comic-plan.yaml"
    plan = load_plan(plan_path)
    translated = replace(plan, regions=tuple(r.with_translation(translation) for r in plan.regions))
    write_plan(translated, plan_path, force=True)
    assert "translation:" in dumps(translated)
    return plan_path


def test_apply_renders_pages_into_the_output_directory(
    page_dir: Path, font_dir: Path, tmp_path: Path
) -> None:
    plan_path = _extract_then_translate(page_dir, "I CANNOT BELIEVE IT")
    output = tmp_path / "out"

    assert main(["apply", str(plan_path), "--output", str(output)]) == EXIT_OK
    assert (output / "page1.png").is_file()


def test_apply_requires_an_output_directory(page_dir: Path, font_dir: Path) -> None:
    plan_path = _extract_then_translate(page_dir, "HELLO")
    with pytest.raises(SystemExit):
        main(["apply", str(plan_path)])


def test_apply_refuses_to_write_into_the_source_tree(page_dir: Path, font_dir: Path) -> None:
    plan_path = _extract_then_translate(page_dir, "HELLO")
    assert main(["apply", str(plan_path), "--output", str(page_dir / "out")]) == EXIT_FATAL
    assert not (page_dir / "out").exists()


def test_apply_refuses_to_overwrite_without_force(
    page_dir: Path, font_dir: Path, tmp_path: Path
) -> None:
    plan_path = _extract_then_translate(page_dir, "HELLO THERE")
    output = tmp_path / "out"
    assert main(["apply", str(plan_path), "--output", str(output)]) == EXIT_OK
    stamp = (output / "page1.png").stat().st_mtime_ns

    assert main(["apply", str(plan_path), "--output", str(output)]) == EXIT_PROBLEMS
    assert (output / "page1.png").stat().st_mtime_ns == stamp
    assert main(["apply", str(plan_path), "--output", str(output), "--force"]) == EXIT_OK


def test_apply_exits_nonzero_when_a_translation_is_missing(
    page_dir: Path, font_dir: Path, tmp_path: Path
) -> None:
    plan_path = _extract_then_translate(page_dir, "")
    assert main(["apply", str(plan_path), "--output", str(tmp_path / "out")]) == EXIT_PROBLEMS


def test_apply_rejects_a_plan_whose_source_image_changed(
    page_dir: Path, font_dir: Path, tmp_path: Path
) -> None:
    plan_path = _extract_then_translate(page_dir, "HELLO")
    save_page(make_page_array((600, 800), (10, 10, 10), []), page_dir / "page1.png")

    assert main(["apply", str(plan_path), "--output", str(tmp_path / "out")]) == EXIT_FATAL
    assert (
        main(["apply", str(plan_path), "--output", str(tmp_path / "out"), "--skip-hash-check"])
        == EXIT_OK
    )


def test_apply_font_flag_overrides_the_plan(
    page_dir: Path, font_dir: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    plan_path = _extract_then_translate(page_dir, "HELLO")
    with caplog.at_level(logging.WARNING):
        code = main(
            ["apply", str(plan_path), "--output", str(tmp_path / "out"), "--font", "Marker Felt"]
        )
    # Marker Felt has no real bold, so it is refused rather than faked.
    assert code == EXIT_FATAL


def test_apply_format_override(page_dir: Path, font_dir: Path, tmp_path: Path) -> None:
    plan_path = _extract_then_translate(page_dir, "HELLO")
    output = tmp_path / "out"
    assert main(["apply", str(plan_path), "--output", str(output), "--format", "tiff"]) == EXIT_OK
    assert (output / "page1.tif").is_file()


def test_apply_erase_strategies_all_run(page_dir: Path, font_dir: Path, tmp_path: Path) -> None:
    plan_path = _extract_then_translate(page_dir, "HELLO THERE")
    for strategy in ("flat", "polygon", "inpaint"):
        code = main(
            [
                "apply",
                str(plan_path),
                "--output",
                str(tmp_path / strategy),
                "--erase",
                strategy,
            ]
        )
        assert code == EXIT_OK, strategy


def test_apply_writes_a_cbz_when_the_output_is_named_one(
    page_dir: Path, font_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_path = _extract_then_translate(page_dir, "I CANNOT BELIEVE IT")
    archive = tmp_path / "chapter-01.cbz"

    assert main(["apply", str(plan_path), "--output", str(archive)]) == EXIT_OK

    with zipfile.ZipFile(archive) as packed:
        assert packed.namelist() == ["001-page1.png"]
    assert not (tmp_path / "chapter-01").exists(), "the workspace is temporary and goes away"
    assert archive.name in capsys.readouterr().out


def test_apply_will_not_overwrite_a_chapter_file_without_force(
    page_dir: Path, font_dir: Path, tmp_path: Path
) -> None:
    plan_path = _extract_then_translate(page_dir, "HELLO THERE")
    archive = tmp_path / "chapter-01.cbz"
    assert main(["apply", str(plan_path), "--output", str(archive)]) == EXIT_OK
    stamp = archive.stat().st_mtime_ns

    assert main(["apply", str(plan_path), "--output", str(archive)]) == EXIT_FATAL
    assert archive.stat().st_mtime_ns == stamp
    assert main(["apply", str(plan_path), "--output", str(archive), "--force"]) == EXIT_OK


def test_apply_says_what_is_missing_when_a_cbr_cannot_be_written(
    page_dir: Path, font_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COMICTRANS_RAR", raising=False)
    monkeypatch.setattr("comictrans.pack.shutil.which", lambda name: None)
    plan_path = _extract_then_translate(page_dir, "HELLO THERE")

    assert main(["apply", str(plan_path), "--output", str(tmp_path / "ch.cbr")]) == EXIT_FATAL
    assert not (tmp_path / "ch.cbr").exists()


def test_help_lists_both_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "extract" in out and "apply" in out


@pytest.mark.parametrize(
    "argv",
    [
        ["-v", "extract", "PAGES"],
        ["extract", "PAGES", "-v"],
        ["extract", "-v", "PAGES"],
        ["extract", "PAGES", "--verbose"],
    ],
)
def test_verbose_is_accepted_on_either_side_of_the_subcommand(
    argv: list[str], page_dir: Path, font_dir: Path
) -> None:
    resolved = [str(page_dir) if arg == "PAGES" else arg for arg in argv]
    assert main([*resolved, "--force"]) == EXIT_OK


@pytest.mark.parametrize(
    "argv",
    [["-q", "extract", "PAGES"], ["extract", "PAGES", "-q"]],
)
def test_quiet_is_accepted_on_either_side_of_the_subcommand(
    argv: list[str], page_dir: Path, font_dir: Path
) -> None:
    resolved = [str(page_dir) if arg == "PAGES" else arg for arg in argv]
    assert main([*resolved, "--force"]) == EXIT_OK


def test_verbosity_flags_reach_the_log_level(page_dir: Path, font_dir: Path) -> None:
    import logging

    main(["extract", str(page_dir), "-q", "--force"])
    assert logging.getLogger().level == logging.WARNING
    main(["extract", str(page_dir), "-v", "--force"])
    assert logging.getLogger().level == logging.DEBUG
    main(["extract", str(page_dir), "--force"])
    assert logging.getLogger().level == logging.INFO


def test_verbose_and_quiet_together_are_rejected_on_one_parser(page_dir: Path) -> None:
    with pytest.raises(SystemExit):
        main(["extract", str(page_dir), "-v", "-q"])


def test_verbose_does_not_turn_on_third_party_debug_logging(page_dir: Path, font_dir: Path) -> None:
    import logging

    main(["extract", str(page_dir), "-v", "--force"])
    assert logging.getLogger("PIL").level == logging.INFO


def test_detection_tuning_flags_reach_the_config(
    page_dir: Path, font_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, float] = {}

    import comictrans.cli as cli_module

    original = cli_module._build_config

    def capture(args: object) -> object:
        config = original(args)  # type: ignore[arg-type]
        seen["extent"] = config.detect.max_extent_ratio
        seen["solidity"] = config.detect.min_solidity
        seen["area"] = config.detect.max_contour_area_ratio
        return config

    monkeypatch.setattr(cli_module, "_build_config", capture)
    main(
        [
            "extract",
            str(page_dir),
            "--force",
            "--max-extent-ratio",
            "0.5",
            "--min-solidity",
            "0.6",
            "--max-region-area",
            "0.3",
        ]
    )
    assert seen == {"extent": 0.5, "solidity": 0.6, "area": 0.3}


def test_language_flags_reach_the_plan_header(page_dir: Path, font_dir: Path) -> None:
    from comictrans.planfile import load_plan

    assert main(["extract", str(page_dir), "--source-lang", "fr", "--target-lang", "sv"]) == EXIT_OK
    header = load_plan(page_dir / "comic-plan.yaml").header
    assert (header.source_language, header.target_language) == ("fr", "sv")


def test_ocr_language_defaults_to_the_source_language(
    page_dir: Path, font_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, tuple[str, ...]] = {}

    import comictrans.cli as cli_module

    original = cli_module._build_config

    def capture(args: object) -> object:
        config = original(args)  # type: ignore[arg-type]
        seen["languages"] = config.ocr.languages
        return config

    monkeypatch.setattr(cli_module, "_build_config", capture)
    main(["extract", str(page_dir), "--source-lang", "pt", "--force"])
    assert seen["languages"] == ("pt",)

    main(["extract", str(page_dir), "--source-lang", "pt", "--lang", "pt-BR", "--force"])
    assert seen["languages"] == ("pt-BR",), "--lang overrides the derived default"


def test_hyphenation_language_comes_from_the_plan_header(
    page_dir: Path, font_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, str] = {}

    import comictrans.cli as cli_module

    original = cli_module._apply_config

    def capture(args: object, plan: object) -> object:
        config = original(args, plan)  # type: ignore[arg-type]
        seen["language"] = config.typeset.hyphenation_language
        return config

    assert main(["extract", str(page_dir), "--target-lang", "sv", "--force"]) == EXIT_OK
    plan_path = _translate_plan(page_dir, "HEJ DAR")
    monkeypatch.setattr(cli_module, "_apply_config", capture)
    main(["apply", str(plan_path), "--output", str(tmp_path / "out")])
    assert seen["language"] == "sv"


def _translate_plan(page_dir: Path, translation: str) -> Path:
    from dataclasses import replace

    from comictrans.planfile import load_plan, write_plan

    plan_path = page_dir / "comic-plan.yaml"
    plan = load_plan(plan_path)
    write_plan(
        replace(plan, regions=tuple(r.with_translation(translation) for r in plan.regions)),
        plan_path,
        force=True,
    )
    return plan_path


def test_merge_carries_hand_work_across_a_re_extraction(page_dir: Path, font_dir: Path) -> None:
    from comictrans.planfile import load_plan

    plan_path = _extract_then_translate(page_dir, "I CANNOT BELIEVE IT")
    assert main(["extract", str(page_dir), "--merge"]) == EXIT_OK

    plan = load_plan(plan_path)
    assert [r.translation for r in plan.regions] == ["I CANNOT BELIEVE IT"]


def test_force_still_discards_everything(page_dir: Path, font_dir: Path) -> None:
    from comictrans.planfile import load_plan

    plan_path = _extract_then_translate(page_dir, "I CANNOT BELIEVE IT")
    assert main(["extract", str(page_dir), "--force"]) == EXIT_OK

    plan = load_plan(plan_path)
    assert plan.regions[0].translation != "I CANNOT BELIEVE IT"
    assert plan.regions[0].is_untranslated


def test_merge_and_force_are_mutually_exclusive(page_dir: Path, font_dir: Path) -> None:
    with pytest.raises(SystemExit):
        main(["extract", str(page_dir), "--merge", "--force"])


def test_merge_without_an_existing_plan_writes_a_fresh_one(page_dir: Path, font_dir: Path) -> None:
    assert main(["extract", str(page_dir), "--merge"]) == EXIT_OK
    assert (page_dir / "comic-plan.yaml").is_file()


def test_lost_hand_work_fails_the_run(page_dir: Path, font_dir: Path) -> None:
    from dataclasses import replace

    from comictrans.planfile import load_plan, write_plan

    plan_path = _extract_then_translate(page_dir, "I CANNOT BELIEVE IT")
    plan = load_plan(plan_path)
    # A region that this page's detection will never produce again.
    ghost = replace(
        plan.regions[0],
        id="ghost",
        polygon=((5, 5), (40, 5), (40, 40), (5, 40)),
        translation="TRANSLATION WITH NOWHERE TO GO",
        source_text="X",
    )
    write_plan(replace(plan, regions=(*plan.regions, ghost)), plan_path, force=True)

    assert main(["extract", str(page_dir), "--merge"]) == EXIT_PROBLEMS


def test_validate_passes_a_plan_extract_just_wrote(
    page_dir: Path, font_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["extract", str(page_dir)]) == EXIT_OK

    assert main(["validate", str(page_dir / "comic-plan.yaml")]) == EXIT_OK
    printed = capsys.readouterr().out
    assert "no problems" in printed
    assert "Comic Sans MS" in printed


def test_validate_exits_nonzero_and_names_what_is_wrong(
    page_dir: Path, font_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["extract", str(page_dir)]) == EXIT_OK
    save_page(make_page_array((600, 800), (10, 10, 10), []), page_dir / "page1.png")

    assert main(["validate", str(page_dir / "comic-plan.yaml")]) == EXIT_PROBLEMS
    assert "  page1.png has changed since extract" in capsys.readouterr().out


def test_validate_writes_nothing_at_all(page_dir: Path, font_dir: Path, tmp_path: Path) -> None:
    assert main(["extract", str(page_dir)]) == EXIT_OK
    plan_path = page_dir / "comic-plan.yaml"
    before = {path: path.stat().st_mtime_ns for path in sorted(page_dir.iterdir())}

    assert main(["validate", str(plan_path)]) == EXIT_OK
    assert {path: path.stat().st_mtime_ns for path in sorted(page_dir.iterdir())} == before


@pytest.fixture
def chapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One page of comic in a .cbz, and a recogniser that knows its new name.

    The page is keyed by what it is called once unpacked, which is the whole
    point of the fixture: what the pipeline reads is the folder, not the
    archive.
    """
    array = make_page_array(
        (600, 800),
        ART_DARK,
        [("ellipse", Box(120, 100, 420, 260), BALLOON_WHITE, INK_BLACK, BOXES)],
    )
    raw = save_page(array, tmp_path / "page1.png")
    archive = tmp_path / "chapter.cbz"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("Chapter 1/page1.png", raw.read_bytes())
    raw.unlink()
    recognizer = FakeRecognizer({"001-page1.png": lines_for(BOXES, ["NON CI POSSO", "CREDERE!"])})
    monkeypatch.setattr("comictrans.cli.get_recognizer", lambda config: recognizer)
    return archive


def test_extract_unpacks_a_chapter_and_the_rest_of_the_tool_reads_the_folder(
    chapter: Path, font_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["extract", str(chapter)]) == EXIT_OK
    assert "unpacked chapter.cbz" in capsys.readouterr().out

    pages = tmp_path / "chapter-pages"
    plan_path = pages / "comic-plan.yaml"
    assert (pages / "001-page1.png").is_file()
    assert plan_path.is_file()
    assert chapter.is_file(), "the chapter file itself is a source and is never written to"

    # The point of unpacking rather than reading on demand: every pass after
    # extract is looking at an ordinary folder of images.
    assert main(["validate", str(plan_path)]) == EXIT_OK


def test_a_second_extract_reuses_the_pages_already_unpacked(
    chapter: Path, font_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["extract", str(chapter)]) == EXIT_OK
    page = tmp_path / "chapter-pages" / "001-page1.png"
    stamp = page.stat().st_mtime_ns

    assert main(["extract", str(chapter), "--force"]) == EXIT_OK

    assert page.stat().st_mtime_ns == stamp
    assert "already there:     1" in capsys.readouterr().out


def test_unpack_dir_decides_where_the_pages_land(
    chapter: Path, font_dir: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "unpacked"

    assert main(["extract", str(chapter), "--unpack-dir", str(elsewhere)]) == EXIT_OK

    assert (elsewhere / "001-page1.png").is_file()
    assert (elsewhere / "comic-plan.yaml").is_file()
    assert not (tmp_path / "chapter-pages").exists()


def test_unpack_dir_means_nothing_for_a_folder_of_images(
    page_dir: Path, font_dir: Path, tmp_path: Path
) -> None:
    assert main(["extract", str(page_dir), "--unpack-dir", str(tmp_path / "out")]) == EXIT_FATAL
    assert not (tmp_path / "out").exists()


def test_a_debug_dir_inside_the_pages_a_chapter_would_unpack_into_is_refused(
    chapter: Path, font_dir: Path, tmp_path: Path
) -> None:
    # The source tree a chapter file has is the folder it unpacks into, which
    # is somewhere else entirely from the folder the file itself sits in.
    moved = tmp_path / "chapters" / "chapter.cbz"
    moved.parent.mkdir()
    chapter.rename(moved)
    unpacked = tmp_path / "unpacked"

    assert (
        main(
            [
                "extract",
                str(moved),
                "--unpack-dir",
                str(unpacked),
                "--debug-dir",
                str(unpacked / "debug"),
            ]
        )
        == EXIT_FATAL
    )

    assert not unpacked.exists(), "refused before anything was unpacked"


def test_a_run_that_cannot_start_leaves_no_pages_behind(
    chapter: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Unpacking is the first thing extract writes, so it goes after the
    # checks that can refuse the run outright.
    monkeypatch.setenv("COMICTRANS_FONT_PATH", str(tmp_path / "nowhere"))
    monkeypatch.setattr("comictrans.fonts.SEARCH_DIRS", ())

    assert main(["extract", str(chapter)]) == EXIT_FATAL

    assert not (tmp_path / "chapter-pages").exists()


def test_a_language_tesseract_lacks_is_refused_before_anything_is_unpacked(
    chapter: Path,
    tmp_path: Path,
    font_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Once, in a line, and not as a traceback from the first page.

    The real recogniser lookup, not the fixture's fake: what is under test is
    that it is what refuses.
    """
    from comictrans import ocr
    from comictrans.ocr import tesseract

    monkeypatch.setattr("comictrans.cli.get_recognizer", ocr.get_recognizer)
    monkeypatch.setattr(tesseract, "available", lambda: True)
    monkeypatch.setattr(tesseract.pytesseract, "get_languages", lambda config="": ["eng", "ita"])

    code = main(["extract", str(chapter), "--ocr", "tesseract", "--source-lang", "sv"])

    err = capsys.readouterr().err
    assert code == EXIT_FATAL
    assert "Tesseract has no data for sv (swe.traineddata). It has: en, it." in err
    assert "Traceback" not in err
    assert not (tmp_path / "chapter-pages").exists()


def test_a_page_that_may_not_be_a_scan_is_named_in_the_summary(
    tmp_path: Path,
    font_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A born-digital PDF unpacks, and says which pages to go and look at."""
    from PIL import Image
    from pypdf import PdfReader, PdfWriter, Transformation

    monkeypatch.setattr("comictrans.cli.get_recognizer", lambda config: FakeRecognizer({}))
    scan = Image.fromarray(make_page_array((600, 800), ART_DARK, []))
    scan.save(tmp_path / "scan.pdf", resolution=150)
    logo = Image.fromarray(make_page_array((100, 100), ART_DARK, []))
    logo.save(tmp_path / "logo.pdf", resolution=72)

    writer = PdfWriter()
    writer.append(tmp_path / "scan.pdf")
    writer.add_blank_page(width=612, height=792)
    writer.pages[1].merge_transformed_page(
        PdfReader(tmp_path / "logo.pdf").pages[0], Transformation().translate(40, 650)
    )
    chapter = tmp_path / "chapter.pdf"
    with chapter.open("wb") as handle:
        writer.write(handle)

    main(["extract", str(chapter)])

    printed = capsys.readouterr().out
    assert "  pages:             2" in printed
    assert "LOOK AT:           page 2:" in printed
    assert "may not be a scan of the page they came from" in printed


def test_the_summary_says_which_languages_vision_read_without(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from comictrans.cli import _report_summary
    from comictrans.extract import ExtractReport

    _report_summary(ExtractReport(pages_read=1, unread_languages=("sv", "xx")), Path("plan.yaml"))
    out = capsys.readouterr().out

    assert "NOT READ BY OCR:   sv, xx" in out
    assert "Apple Vision cannot read sv, xx" in out
