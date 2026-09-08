from __future__ import annotations

from pathlib import Path

import pytest

from comictrans import fonts
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


def test_available_families_lists_only_what_resolves(font_dir: Path) -> None:
    """The fixture directory is exactly the awkward case.

    Its files carry Apple filenames but DejaVu's internal family names, so a
    family enumerated from inside a file does not resolve, and one taken from
    the filename does. Only the second kind is any use.
    """
    fonts.forget_available_families()
    assert fonts.available_families() == ("Comic Sans MS",)


def test_available_families_refuses_a_family_with_no_bold(font_dir: Path) -> None:
    """Marker Felt is copied regular-only, so emphasis could not be drawn."""
    fonts.forget_available_families()
    assert "Marker Felt" not in fonts.available_families()
    assert "Marker Felt" in fonts.available_families(require_bold=False)


def test_every_family_offered_actually_resolves(font_dir: Path) -> None:
    """The promise the dropdown makes: nothing offered can fail at render."""
    fonts.forget_available_families()
    for family in fonts.available_families():
        fonts.resolve_family(family)  # raises FontError if it cannot


def test_available_families_is_cached_per_search_path(
    font_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keyed on the directories, so a changed path is not answered from cache."""
    fonts.forget_available_families()
    assert fonts.available_families() == ("Comic Sans MS",)

    empty = tmp_path / "no-fonts"
    empty.mkdir()
    monkeypatch.setenv(fonts.FONT_PATH_ENV, str(empty))
    assert fonts.available_families() == ()


def test_forgetting_makes_it_look_again(font_dir: Path) -> None:
    fonts.forget_available_families()
    assert fonts.available_families() == ("Comic Sans MS",)

    (font_dir / "Comic Sans MS.ttf").unlink()
    assert fonts.available_families() == ("Comic Sans MS",), "still the cached answer"

    fonts.forget_available_families()
    assert fonts.available_families() == ()
