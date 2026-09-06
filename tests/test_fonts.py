from __future__ import annotations

from pathlib import Path

import pytest

from comictrans.errors import FontError
from comictrans.fonts import FALLBACK_CHAIN, resolve, resolve_default, resolve_family


def test_fallback_chain_order_is_the_documented_one() -> None:
    assert FALLBACK_CHAIN == (
        "Comic Sans MS",
        "Chalkboard SE",
        "Marker Felt",
        "Noteworthy",
        "Helvetica",
    )


def test_default_resolves_comic_sans_first(font_dir: Path) -> None:
    face = resolve_default()
    assert face.family == "Comic Sans MS"
    assert face.regular.path.parent == font_dir
    assert face.has_bold


def test_regular_and_bold_are_distinct_faces(font_dir: Path) -> None:
    face = resolve_family("Comic Sans MS")
    assert face.bold is not None
    assert (face.bold.path, face.bold.index) != (face.regular.path, face.regular.index)
    assert "bold" in face.bold.style.lower()


def test_a_family_without_a_real_bold_is_refused_not_faked(font_dir: Path) -> None:
    with pytest.raises(FontError, match="no real bold face"):
        resolve_family("Marker Felt")
    # Still resolvable when bold is not required, so the refusal is about bold.
    assert resolve_family("Marker Felt", require_bold=False).bold is None


def test_an_explicitly_named_missing_font_is_an_error_not_a_substitution(
    font_dir: Path,
) -> None:
    with pytest.raises(FontError, match="not found"):
        resolve("Wingdings Deluxe")


def test_a_font_file_path_is_accepted(font_dir: Path) -> None:
    face = resolve_family(str(font_dir / "Comic Sans MS.ttf"), require_bold=False)
    assert face.regular.path == font_dir / "Comic Sans MS.ttf"


def test_faces_load_at_a_requested_size(font_dir: Path) -> None:
    face = resolve_family("Comic Sans MS")
    assert face.regular.load(24).size == 24


def test_nothing_resolvable_reports_every_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("COMICTRANS_FONT_PATH", str(tmp_path / "nowhere"))
    monkeypatch.setattr("comictrans.fonts.SEARCH_DIRS", ())
    with pytest.raises(FontError, match="none of the fallback fonts") as excinfo:
        resolve_default()
    for family in FALLBACK_CHAIN:
        assert family in str(excinfo.value)
