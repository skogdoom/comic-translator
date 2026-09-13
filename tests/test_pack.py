"""Writing a chapter as one file."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from comictrans.errors import InputError
from comictrans.pack import (
    RAR_ENV,
    archive_kind,
    check_writable,
    entry_names,
    pack,
    rar_compressor,
)


def _pages(tmp_path: Path, *names: str) -> list[Path]:
    folder = tmp_path / "rendered"
    folder.mkdir(exist_ok=True)
    made = []
    for name in names:
        path = folder / name
        path.write_bytes(f"pixels of {name}".encode())
        made.append(path)
    return made


def test_what_an_output_is_called_decides_what_it_is() -> None:
    """The other way round from reading one: there are no bytes to ask yet."""
    assert archive_kind(Path("chapter.cbz")) == "zip"
    assert archive_kind(Path("chapter.ZIP")) == "zip"
    assert archive_kind(Path("chapter.cbr")) == "rar"
    assert archive_kind(Path("chapter.rar")) == "rar"
    assert archive_kind(Path("out")) is None
    assert archive_kind(Path("out/pages")) is None


def test_the_entry_names_carry_the_order_they_are_given_in() -> None:
    """A reader sorts by name, so the reading order has to survive as one.

    Source names that happen to sort right are luck: these are page 10 and
    page 2 of a chapter somebody reordered by hand.
    """
    pages = [Path("/tmp/out/page10.png"), Path("/tmp/out/page2.png")]

    assert entry_names(pages) == ("001-page10.png", "002-page2.png")


def test_pages_go_into_a_cbz_in_the_order_they_were_given(tmp_path: Path) -> None:
    pages = _pages(tmp_path, "b.png", "a.png")

    names = pack(pages, tmp_path / "chapter.cbz")

    with zipfile.ZipFile(tmp_path / "chapter.cbz") as archive:
        assert archive.namelist() == list(names) == ["001-b.png", "002-a.png"]
        assert archive.read("001-b.png") == b"pixels of b.png"


def test_an_archive_already_there_is_not_written_over_without_being_asked(
    tmp_path: Path,
) -> None:
    pages = _pages(tmp_path, "a.png")
    archive = tmp_path / "chapter.cbz"
    archive.write_bytes(b"somebody else's chapter")

    with pytest.raises(InputError, match="already exists"):
        pack(pages, archive)

    assert archive.read_bytes() == b"somebody else's chapter"


def test_writing_over_one_replaces_it_rather_than_adding_to_it(tmp_path: Path) -> None:
    """Both writers add to an archive that is already there, and a chapter
    half of whose pages are last week's is worse than no chapter."""
    pack(_pages(tmp_path, "a.png", "b.png"), tmp_path / "chapter.cbz")

    pack(_pages(tmp_path, "only.png"), tmp_path / "chapter.cbz", force=True)

    with zipfile.ZipFile(tmp_path / "chapter.cbz") as archive:
        assert archive.namelist() == ["001-only.png"]


def test_a_name_that_is_not_an_archive_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="is not an archive name"):
        pack(_pages(tmp_path, "a.png"), tmp_path / "chapter.tar")


def test_no_pages_is_not_an_empty_chapter(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="no pages to write"):
        pack([], tmp_path / "chapter.cbz")
    assert not (tmp_path / "chapter.cbz").exists()


# -- the compressor this project will not ship ---------------------------


def test_asking_for_a_cbz_asks_for_no_tool(tmp_path: Path) -> None:
    check_writable(tmp_path / "chapter.cbz")  # and does not raise
    check_writable(tmp_path / "pages")


def test_with_no_rar_anywhere_cbr_says_which_binary_would_do_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(RAR_ENV, raising=False)
    monkeypatch.setattr("comictrans.pack.shutil.which", lambda name: None)

    with pytest.raises(InputError, match="needs the rar compressor"):
        check_writable(tmp_path / "chapter.cbr")

    with pytest.raises(InputError, match="unrar cannot write archives"):
        pack(_pages(tmp_path, "a.png"), tmp_path / "chapter.cbr")


def test_the_environment_names_the_compressor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = tmp_path / "rar"
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o755)
    monkeypatch.setenv(RAR_ENV, str(tool))

    assert rar_compressor() == str(tool)
    assert rar_compressor("  ") == str(tool), "whitespace is not an answer"


def test_a_named_tool_that_is_not_a_program_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(RAR_ENV, raising=False)
    folder = tmp_path / "bin"
    folder.mkdir()

    with pytest.raises(InputError, match="is not a program this can run"):
        rar_compressor(str(folder))

    unreadable = tmp_path / "rar.txt"
    unreadable.write_text("not executable")
    with pytest.raises(InputError, match="is not a program this can run"):
        rar_compressor(str(unreadable))


def test_the_compressor_is_run_where_the_pages_are_with_bare_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real rar cannot be had here — nothing redistributable writes RAR —
    so this holds the command instead: what it is asked to do, and where.

    Bare names run from the pages' own directory, because an archive that
    holds a temporary directory's path inside it is one somebody unpacks
    into a folder called comictrans-8fh2k.
    """
    recorder = tmp_path / "rar"
    recorder.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$PWD" "$@" > "{tmp_path}/asked.txt"\nshift 3\ntouch "$1"\n'
    )
    recorder.chmod(0o755)
    monkeypatch.setenv(RAR_ENV, str(recorder))
    pages = _pages(tmp_path, "b.png", "a.png")

    names = pack(pages, tmp_path / "chapter.cbr")

    asked = (tmp_path / "asked.txt").read_text().splitlines()
    assert asked[0] == str(pages[0].parent), "run where the pages are"
    assert asked[1:4] == ["a", "-ep", "-o+"]
    assert asked[4] == str((tmp_path / "chapter.cbr").resolve())
    assert asked[5:] == list(names) == ["001-b.png", "002-a.png"]


def test_the_old_archive_is_gone_before_the_compressor_is_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``rar a`` adds to an archive that is there; it does not replace it.

    A zip hides this — ``ZipFile(..., "w")`` truncates — so the promise that
    ``--force`` replaces a chapter rather than merging last week's pages into
    it is only observable from the RAR side, which is where it is tested.
    """
    recorder = tmp_path / "rar"
    archive = tmp_path / "chapter.cbr"
    recorder.write_text(
        f'#!/bin/sh\ntest -e "{archive}" && echo still-there > "{tmp_path}/found.txt"\n'
        f'shift 3\ntouch "$1"\n'
    )
    recorder.chmod(0o755)
    monkeypatch.setenv(RAR_ENV, str(recorder))
    archive.write_bytes(b"last week's chapter")

    pack(_pages(tmp_path, "a.png"), archive, force=True)

    assert not (tmp_path / "found.txt").exists(), "it was cleared, not added to"


def test_a_compressor_that_fails_says_what_it_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failing = tmp_path / "rar"
    failing.write_text("#!/bin/sh\necho 'no space left on device' >&2\nexit 5\n")
    failing.chmod(0o755)
    monkeypatch.setenv(RAR_ENV, str(failing))

    with pytest.raises(InputError, match="no space left on device"):
        pack(_pages(tmp_path, "a.png"), tmp_path / "chapter.cbr")


def test_a_compressor_that_cannot_be_run_at_all_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Executable to look at and not to run: the bit is set, the file is not
    # a program. What os.access can answer and what exec can are different.
    broken = tmp_path / "rar"
    broken.write_bytes(b"\x7fELF not really\n")
    broken.chmod(0o755)
    monkeypatch.setenv(RAR_ENV, str(broken))
    assert os.access(broken, os.X_OK)

    with pytest.raises(InputError, match="could not be run"):
        pack(_pages(tmp_path, "a.png"), tmp_path / "chapter.cbr")
