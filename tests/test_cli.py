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


def test_apply_reports_that_it_is_not_implemented_yet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["apply", str(tmp_path / "plan.yaml")]) == EXIT_FATAL
    assert "milestone 2" in capsys.readouterr().err


def test_help_lists_both_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "extract" in out and "apply" in out
