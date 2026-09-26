"""Writing a chapter as one file."""

from __future__ import annotations

import json
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


def _refuse_to_link(source: object, target: object) -> None:
    """``os.link`` on a filesystem that has none, or across two of them."""
    raise OSError("cross-device link")


def _archiver(path: Path, *, record: Path | None = None) -> Path:
    """A stand-in for ``rar`` that behaves enough like one to be useful.

    Nothing redistributable writes RAR, so the compressor has to be faked —
    and the shape of the fake is what decides whether these tests can catch
    anything. The three stubs that came before this one recorded their
    arguments or touched the archive, and not one opened a file it was told
    to add; a compressor asked for files that were not there therefore passed
    every test and failed on the first real chapter.

    So this one reads what it is given. It writes a zip, because a zip is
    something a test can open, and it exits non-zero when a named file is
    missing, because that is what ``rar`` does and what the bug needed.
    """
    note = (
        "import json\n"
        f"open({str(record)!r}, 'w').write(json.dumps("
        "{'cwd': os.getcwd(), 'argv': sys.argv[1:], 'listing': sorted(os.listdir('.'))}))\n"
        if record is not None
        else ""
    )
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys, zipfile\n"
        "archive, names = sys.argv[4], sys.argv[5:]\n"
        + note
        + "missing = [n for n in names if not os.path.exists(n)]\n"
        "if missing:\n"
        "    print('cannot find: ' + ', '.join(missing), file=sys.stderr)\n"
        "    sys.exit(10)\n"
        "with zipfile.ZipFile(archive, 'a') as z:\n"
        "    for n in names:\n"
        "        z.write(n, n)\n"
    )
    path.chmod(0o755)
    return path


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


def test_comic_info_goes_in_beside_the_pages_and_is_not_one_of_them(tmp_path: Path) -> None:
    pages = _pages(tmp_path, "b.png", "a.png")

    names = pack(pages, tmp_path / "chapter.cbz", comic_info=b"<ComicInfo/>")

    assert names == ("001-b.png", "002-a.png")
    with zipfile.ZipFile(tmp_path / "chapter.cbz") as archive:
        assert archive.namelist() == [*names, "ComicInfo.xml"]
        assert archive.read("ComicInfo.xml") == b"<ComicInfo/>"


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


def test_the_pages_are_there_under_the_names_the_compressor_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect this milestone is, stated as the thing that was not true.

    ``rar a`` adds a file under the name it already has — there is no flag
    for "add this one, call it that", which is what ``ZipFile.write`` takes
    as a second argument and why the zip half needs no staging. So the entry
    names, which carry the reading order, have to be the names on disk before
    the compressor runs. They were not: it was handed ``001-b.png`` while
    ``b.png`` sat in the directory it ran in, and every .cbr ever asked for
    failed.

    Bare names run from a directory of their own, because an archive holding
    a temporary directory's path inside it is one somebody unpacks into a
    folder called comictrans-8fh2k.
    """
    recorder = _archiver(tmp_path / "rar", record=tmp_path / "asked.txt")
    monkeypatch.setenv(RAR_ENV, str(recorder))
    pages = _pages(tmp_path, "b.png", "a.png")

    names = pack(pages, tmp_path / "chapter.cbr")

    asked = json.loads((tmp_path / "asked.txt").read_text())
    command = asked["argv"]
    assert command[:3] == ["a", "-ep", "-o+"]
    assert command[3] == str((tmp_path / "chapter.cbr").resolve())
    assert command[4:] == list(names) == ["001-b.png", "002-a.png"]
    assert asked["listing"] == ["001-b.png", "002-a.png"], (
        "the names it was given are the names that were there when it ran"
    )
    assert Path(asked["cwd"]) != pages[0].parent, "and not by renaming the caller's pages"
    assert sorted(path.name for path in pages[0].parent.iterdir()) == ["a.png", "b.png"], (
        "which are left exactly as they were"
    )


def test_a_cbr_is_handed_comic_info_by_the_name_readers_look_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _archiver(tmp_path / "rar", record=tmp_path / "asked.txt")
    monkeypatch.setenv(RAR_ENV, str(recorder))
    archive = tmp_path / "chapter.cbr"

    names = pack(_pages(tmp_path, "a.png"), archive, comic_info=b"<ComicInfo/>")

    asked = json.loads((tmp_path / "asked.txt").read_text())
    assert asked["argv"][4:] == [*names, "ComicInfo.xml"]
    assert asked["listing"] == ["001-a.png", "ComicInfo.xml"]
    with zipfile.ZipFile(archive) as packed:
        assert packed.read("ComicInfo.xml") == b"<ComicInfo/>"


def test_the_staging_directory_does_not_outlive_the_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _archiver(tmp_path / "rar", record=tmp_path / "asked.txt")
    monkeypatch.setenv(RAR_ENV, str(recorder))

    pack(_pages(tmp_path, "a.png"), tmp_path / "chapter.cbr")

    staged = Path(json.loads((tmp_path / "asked.txt").read_text())["cwd"])
    assert not staged.exists(), "thrown away on the way out"


def test_the_pages_it_was_given_are_the_bytes_that_come_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end, through an archive that is opened rather than believed.

    The command shape was asserted for a whole milestone while being wrong.
    What settles it is reading back what the compressor actually wrote.
    """
    monkeypatch.setenv(RAR_ENV, str(_archiver(tmp_path / "rar")))
    pages = _pages(tmp_path, "b.png", "a.png")
    archive = tmp_path / "chapter.cbr"

    names = pack(pages, archive)

    with zipfile.ZipFile(archive) as packed:
        assert packed.namelist() == list(names) == ["001-b.png", "002-a.png"]
        assert packed.read("001-b.png") == pages[0].read_bytes()
        assert packed.read("002-a.png") == pages[1].read_bytes()


def test_pages_from_two_directories_do_not_collide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Staging settles this too: one filename, two folders, two entries.

    The old shape ran the compressor in ``pages[0].parent`` and could not
    have seen the second one at all.
    """
    monkeypatch.setenv(RAR_ENV, str(_archiver(tmp_path / "rar")))
    pages = []
    for folder in ("left", "right"):
        (tmp_path / folder).mkdir()
        page = tmp_path / folder / "page.png"
        page.write_bytes(f"pixels from {folder}".encode())
        pages.append(page)
    archive = tmp_path / "chapter.cbr"

    pack(pages, archive)

    with zipfile.ZipFile(archive) as packed:
        assert packed.namelist() == ["001-page.png", "002-page.png"]
        assert packed.read("001-page.png") == b"pixels from left"
        assert packed.read("002-page.png") == b"pixels from right"


def test_a_page_that_cannot_be_linked_is_copied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hard link is free and is not always available; a copy always is."""
    monkeypatch.setattr("comictrans.pack.os.link", _refuse_to_link)
    monkeypatch.setenv(RAR_ENV, str(_archiver(tmp_path / "rar")))
    pages = _pages(tmp_path, "a.png")
    archive = tmp_path / "chapter.cbr"

    pack(pages, archive)

    with zipfile.ZipFile(archive) as packed:
        assert packed.read("001-a.png") == pages[0].read_bytes()


def test_the_old_archive_is_gone_before_the_compressor_is_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``rar a`` adds to an archive that is there; it does not replace it.

    A zip hides this — ``ZipFile(..., "w")`` truncates — so the promise that
    ``--force`` replaces a chapter rather than merging last week's pages into
    it is only observable from the RAR side, which is where it is tested. The
    stub appends, as rar does, so an archive left in place would show.
    """
    monkeypatch.setenv(RAR_ENV, str(_archiver(tmp_path / "rar")))
    archive = tmp_path / "chapter.cbr"
    with zipfile.ZipFile(archive, "w") as stale:
        stale.writestr("001-last-week.png", b"last week's chapter")

    pack(_pages(tmp_path, "a.png"), archive, force=True)

    with zipfile.ZipFile(archive) as packed:
        assert packed.namelist() == ["001-a.png"], "cleared, not added to"


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
