from __future__ import annotations

from pathlib import Path

from comictrans.util import is_within, natural_key, relative_posix, sha256_file, slugify


def test_natural_order_puts_page2_before_page10() -> None:
    names = ["page10.png", "page2.png", "page1.png", "Page3.png"]
    assert sorted(names, key=natural_key) == [
        "page1.png",
        "page2.png",
        "Page3.png",
        "page10.png",
    ]


def test_natural_key_is_case_insensitive() -> None:
    assert natural_key("Page2") == natural_key("page2")


def test_slugify_strips_accents_and_punctuation() -> None:
    assert slugify("Pagina 03 — Città!") == "pagina-03-citta"
    assert slugify("???") == "page"


def test_sha256_matches_known_value(tmp_path: Path) -> None:
    path = tmp_path / "x.bin"
    path.write_bytes(b"comic")
    assert sha256_file(path) == ("7e002e1ba02e628e7422bd3017520effa8cc1638662efa78a78b6af1548eb575")


def test_is_within_catches_nested_and_identical_paths(tmp_path: Path) -> None:
    source = tmp_path / "pages"
    (source / "out").mkdir(parents=True)
    assert is_within(source, source)
    assert is_within(source / "out", source)
    assert not is_within(tmp_path / "elsewhere", source)


def test_is_within_follows_symlinks(tmp_path: Path) -> None:
    source = tmp_path / "pages"
    source.mkdir()
    link = tmp_path / "link"
    link.symlink_to(source)
    assert is_within(link, source)


def test_relative_posix_uses_forward_slashes(tmp_path: Path) -> None:
    plan_dir = tmp_path / "plans"
    plan_dir.mkdir()
    image = tmp_path / "pages" / "page-001.png"
    image.parent.mkdir()
    image.touch()
    assert relative_posix(image, plan_dir) == "../pages/page-001.png"
