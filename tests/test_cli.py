from __future__ import annotations

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
    from comictrans.model import Plan
    from comictrans.planfile import dumps, load_plan, write_plan

    assert main(["extract", str(page_dir), "--force"]) == EXIT_OK
    plan_path = page_dir / "comic-plan.yaml"
    plan = load_plan(plan_path)
    translated = Plan(
        header=plan.header,
        regions=tuple(r.with_translation(translation) for r in plan.regions),
    )
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
    from comictrans.model import Plan
    from comictrans.planfile import load_plan, write_plan

    plan_path = page_dir / "comic-plan.yaml"
    plan = load_plan(plan_path)
    write_plan(
        Plan(
            header=plan.header,
            regions=tuple(r.with_translation(translation) for r in plan.regions),
        ),
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

    from comictrans.model import Plan
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
    write_plan(Plan(header=plan.header, regions=(*plan.regions, ghost)), plan_path, force=True)

    assert main(["extract", str(page_dir), "--merge"]) == EXIT_PROBLEMS
