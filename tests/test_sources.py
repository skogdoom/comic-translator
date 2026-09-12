"""Chapters that arrive as one file, and the directory they become."""

from __future__ import annotations

import logging
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import numpy as np
import pytest
from PIL import Image

from comictrans.errors import InputError
from comictrans.imaging import collect_inputs
from comictrans.sources import (
    UNRAR_ENV,
    default_unpack_dir,
    is_container,
    unpack,
)

from .conftest import save_page


def _page_bytes(tmp_path: Path, name: str, shade: int, size: tuple[int, int] = (200, 300)) -> bytes:
    """A real PNG, as bytes, so an archive holds something Pillow can open."""
    width, height = size
    path = save_page(np.full((height, width, 3), shade, dtype=np.uint8), tmp_path / name)
    data = path.read_bytes()
    path.unlink()
    return data


def _cbz(tmp_path: Path, entries: dict[str, bytes | str], name: str = "chapter.cbz") -> Path:
    archive = tmp_path / name
    with zipfile.ZipFile(archive, "w") as handle:
        for entry, payload in entries.items():
            handle.writestr(entry, payload)
    return archive


def _three_page_cbz(tmp_path: Path, name: str = "chapter.cbz") -> Path:
    # Named so that a plain sort would put page10 second: the archive's own
    # reading order is what has to come out.
    return _cbz(
        tmp_path,
        {
            "Chapter 1/page1.png": _page_bytes(tmp_path, "a.png", 201),
            "Chapter 1/page2.png": _page_bytes(tmp_path, "b.png", 202),
            "Chapter 1/page10.png": _page_bytes(tmp_path, "c.png", 210),
        },
        name,
    )


def test_a_chapter_becomes_a_folder_of_pages_in_reading_order(tmp_path: Path) -> None:
    report = unpack(_three_page_cbz(tmp_path))

    assert report.directory == tmp_path / "chapter-pages"
    assert [path.name for path in report.pages] == [
        "001-page1.png",
        "002-page2.png",
        "003-page10.png",
    ]
    assert [path.name for path in collect_inputs(report.directory)[0]] == [
        "001-page1.png",
        "002-page2.png",
        "003-page10.png",
    ]


def test_the_container_is_not_written_to_and_nothing_else_is_either(tmp_path: Path) -> None:
    archive = _three_page_cbz(tmp_path)
    before = archive.read_bytes()

    report = unpack(archive)

    assert archive.read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["chapter-pages", "chapter.cbz"]
    assert report.directory.is_dir()


def test_unpacking_the_same_chapter_twice_writes_nothing_the_second_time(tmp_path: Path) -> None:
    archive = _three_page_cbz(tmp_path)
    first = unpack(archive)
    stamps = {path: path.stat().st_mtime_ns for path in first.pages}

    second = unpack(archive)

    assert second.reused == 3
    assert second.pages == first.pages
    assert {path: path.stat().st_mtime_ns for path in second.pages} == stamps


def test_a_page_of_somebody_elses_stops_the_run_rather_than_being_overwritten(
    tmp_path: Path,
) -> None:
    archive = _three_page_cbz(tmp_path)
    directory = tmp_path / "chapter-pages"
    directory.mkdir()
    mine = directory / "002-page2.png"
    mine.write_bytes(b"not that page")

    with pytest.raises(InputError, match="is already there and is not the page"):
        unpack(archive)

    assert mine.read_bytes() == b"not that page"


def test_pages_left_from_an_earlier_unpack_stop_the_run(tmp_path: Path) -> None:
    # A re-release with one page inserted at the front renumbers every name
    # after it, so the old files collide with nothing and simply stay. Left
    # alone, extract lists the directory and reads p1 and p2 twice each.
    archive = _cbz(
        tmp_path,
        {
            "p1.png": _page_bytes(tmp_path, "a.png", 201),
            "p2.png": _page_bytes(tmp_path, "b.png", 202),
        },
    )
    unpack(archive)
    _cbz(
        tmp_path,
        {
            "p0.png": _page_bytes(tmp_path, "c.png", 203),
            "p1.png": _page_bytes(tmp_path, "a.png", 201),
            "p2.png": _page_bytes(tmp_path, "b.png", 202),
        },
    )

    with pytest.raises(InputError, match=r"that are not pages of chapter\.cbz"):
        unpack(archive)


def test_somebody_elses_images_where_the_pages_go_stop_the_run(tmp_path: Path) -> None:
    archive = _three_page_cbz(tmp_path)
    elsewhere = tmp_path / "mine"
    elsewhere.mkdir()
    save_page(np.full((10, 10, 3), 7, dtype=np.uint8), elsewhere / "holiday.png")

    with pytest.raises(InputError, match=r"holiday\.png"):
        unpack(archive, elsewhere)


def test_the_plan_file_living_with_the_pages_is_not_one_of_those(tmp_path: Path) -> None:
    # The translation is written into this directory by extract, and deleting
    # it on a re-run would be the worst thing this could do.
    archive = _three_page_cbz(tmp_path)
    report = unpack(archive)
    plan = report.directory / "comic-plan.yaml"
    plan.write_text("hours of work", encoding="utf-8")

    assert unpack(archive).reused == 3
    assert plan.read_text(encoding="utf-8") == "hours of work"


def test_a_link_in_an_archive_is_named_rather_than_written(tmp_path: Path) -> None:
    # Reading a symlink entry gives the path it points at, not an image.
    archive = tmp_path / "chapter.cbz"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("001.png", _page_bytes(tmp_path, "a.png", 201))
        link = zipfile.ZipInfo("002.png")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        handle.writestr(link, "/etc/passwd")

    report = unpack(archive)

    assert [path.name for path in report.pages] == ["001-001.png"]
    assert report.skipped == (("002.png", "a link or a device, not a file"),)


def test_an_archive_that_records_no_mode_at_all_is_still_read(tmp_path: Path) -> None:
    # A zip written on Windows carries no Unix mode, which is not a reason to
    # doubt the entry.
    archive = tmp_path / "chapter.cbz"
    with zipfile.ZipFile(archive, "w") as handle:
        info = zipfile.ZipInfo("001.png")
        info.create_system = 0
        info.external_attr = 0
        handle.writestr(info, _page_bytes(tmp_path, "a.png", 201))

    assert [path.name for path in unpack(archive).pages] == ["001-001.png"]


def test_what_is_not_a_page_is_named_rather_than_unpacked(tmp_path: Path) -> None:
    archive = _cbz(
        tmp_path,
        {
            "page1.png": _page_bytes(tmp_path, "a.png", 201),
            "notes.txt": "a translator's notes",
            ".DS_Store": "junk",
            "__MACOSX/._page1.png": "a resource fork",
        },
    )

    report = unpack(archive)

    assert [path.name for path in report.pages] == ["001-page1.png"]
    assert dict(report.skipped) == {
        "notes.txt": "unsupported extension .txt",
        ".DS_Store": "hidden file",
        "__MACOSX/._page1.png": "resource fork (a Mac wrote it, no reader shows it)",
    }


def test_two_pages_with_the_same_name_in_different_folders_do_not_collide(
    tmp_path: Path,
) -> None:
    archive = _cbz(
        tmp_path,
        {
            "a/001.png": _page_bytes(tmp_path, "one.png", 201),
            "b/001.png": _page_bytes(tmp_path, "two.png", 202),
        },
    )

    report = unpack(archive)

    assert [path.name for path in report.pages] == ["001-001.png", "002-001.png"]
    assert report.pages[0].read_bytes() != report.pages[1].read_bytes()


def test_an_archive_with_no_pages_in_it_is_an_error(tmp_path: Path) -> None:
    archive = _cbz(tmp_path, {"notes.txt": "nothing but this"})

    with pytest.raises(InputError, match=r"no pages found in chapter\.cbz"):
        unpack(archive)


def test_a_file_that_is_not_an_archive_says_so(tmp_path: Path) -> None:
    broken = tmp_path / "chapter.cbz"
    broken.write_bytes(b"not a zip at all")

    with pytest.raises(InputError, match=r"cannot read chapter\.cbz"):
        unpack(broken)
    assert not (tmp_path / "chapter-pages").exists()


def test_unpack_dir_overrides_where_the_pages_go(tmp_path: Path) -> None:
    archive = _three_page_cbz(tmp_path)
    elsewhere = tmp_path / "somewhere" / "else"

    report = unpack(archive, elsewhere)

    assert report.directory == elsewhere
    assert [path.parent for path in report.pages] == [elsewhere] * 3


def test_a_missing_chapter_file_is_reported_before_anything_is_made(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="input path does not exist"):
        unpack(tmp_path / "nothing.cbz")
    assert list(tmp_path.iterdir()) == []


def test_what_counts_as_a_chapter_file() -> None:
    for name in ("c.cbz", "c.CBZ", "c.zip", "c.cbr", "c.rar", "c.pdf", "c.PDF"):
        assert is_container(Path(name)), name
    for name in ("page.png", "page.jpg", "plan.yaml", "pages"):
        assert not is_container(Path(name)), name


def test_the_default_directory_sits_beside_the_chapter(tmp_path: Path) -> None:
    assert default_unpack_dir(tmp_path / "Vol 1" / "chapter.cbz") == tmp_path / "Vol 1" / (
        "chapter-pages"
    )


def test_a_chapter_file_handed_to_the_rest_of_the_tool_says_what_to_do_with_it(
    tmp_path: Path,
) -> None:
    archive = _three_page_cbz(tmp_path)

    with pytest.raises(InputError, match="is a chapter file, not an image"):
        collect_inputs(archive)


# -- PDF ----------------------------------------------------------------


def _pdf(
    tmp_path: Path, pages: list[Image.Image], name: str = "chapter.pdf", resolution: int = 150
) -> Path:
    """Pages saved the way a scanner would: pixels, and the paper they cover.

    The resolution matters to more than the file size — it is what decides
    the page's size in points, which is half of what tells a scan of a page
    from a picture sitting on one.
    """
    path = tmp_path / name
    pages[0].save(path, save_all=True, append_images=pages[1:], resolution=resolution)
    return path


def _scan(shade: int, size: tuple[int, int] = (200, 300)) -> Image.Image:
    width, height = size
    return Image.fromarray(np.full((height, width, 3), shade, dtype=np.uint8))


def test_a_pdf_gives_up_the_image_on_each_page(tmp_path: Path) -> None:
    report = unpack(_pdf(tmp_path, [_scan(201), _scan(120)]))

    assert [path.name for path in report.pages] == ["001-page.jpg", "002-page.jpg"]
    assert report.skipped == ()
    assert [Image.open(path).size for path in report.pages] == [(200, 300), (200, 300)]


def test_the_pdfs_own_bytes_come_out_rather_than_a_rendering(tmp_path: Path) -> None:
    # Lifting the page's image out is lossless; rasterising it would resample
    # the scan the polygons are about to be measured against.
    source = _pdf(tmp_path, [_scan(201)])
    from pypdf import PdfReader

    embedded = PdfReader(source).pages[0].images[0].data

    report = unpack(source)

    assert report.pages[0].read_bytes() == embedded


def test_a_page_that_is_not_one_photograph_is_named_rather_than_guessed_at(
    tmp_path: Path,
) -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.append(_pdf(tmp_path, [_scan(201)]))
    writer.add_blank_page(width=200, height=300)
    turned = writer.pages[0]
    path = tmp_path / "mixed.pdf"
    with path.open("wb") as handle:
        writer.write(handle)
    assert turned is not None

    report = unpack(path)

    assert [path.name for path in report.pages] == ["001-page.jpg"]
    assert report.skipped == (
        ("page 2", "no image on it, so there is no one page image to lift out"),
    )


def test_a_turned_page_is_skipped_because_its_image_is_not_upright(tmp_path: Path) -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.append(_pdf(tmp_path, [_scan(201), _scan(120)]))
    writer.pages[1].rotate(90)
    path = tmp_path / "turned.pdf"
    with path.open("wb") as handle:
        writer.write(handle)

    report = unpack(path)

    assert [page.name for page in report.pages] == ["001-page.jpg"]
    assert report.skipped == (("page 2", "the page is turned 90°; its image is not upright"),)


def test_a_page_stored_in_a_format_this_tool_does_not_read_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Scanners do emit JPEG 2000, which nothing downstream here opens. The
    # reader says which kind it met rather than writing a file the next pass
    # would refuse.
    monkeypatch.setattr("comictrans.sources.IMAGE_SUFFIXES", frozenset({".png"}))
    source = _pdf(tmp_path, [_scan(201)])

    with caplog.at_level(logging.WARNING), pytest.raises(InputError, match="no pages found"):
        unpack(source, tmp_path / "out")

    assert "page 1: its image is .jpg, which is not one of .png" in caplog.text
    assert not (tmp_path / "out").exists()


def _page_carrying(tmp_path: Path, image: Image.Image, resolution: int, name: str) -> Path:
    """A letter-sized page with ``image`` dropped onto it and nothing else.

    Which is what a born-digital PDF looks like from here: the text is drawn
    as text, so the only image on the page is whatever picture is sitting on
    it.
    """
    from pypdf import PdfWriter, Transformation

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    from pypdf import PdfReader

    carried = PdfReader(_pdf(tmp_path, [image], "carried.pdf", resolution)).pages[0]
    writer.pages[0].merge_transformed_page(carried, Transformation().translate(40, 650))
    path = tmp_path / name
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def test_a_scan_that_fills_its_page_is_doubted_by_nothing(tmp_path: Path) -> None:
    report = unpack(_pdf(tmp_path, [_scan(201), _scan(120)]))

    assert report.doubtful == ()


def test_a_picture_too_small_to_be_the_page_is_unpacked_and_flagged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    source = _page_carrying(tmp_path, _scan(30, (100, 100)), 72, "born-digital.pdf")

    with caplog.at_level(logging.WARNING):
        report = unpack(source, tmp_path / "out")

    assert [path.name for path in report.pages] == ["001-page.jpg"], (
        "flagged, not refused: it is still the only thing on the page"
    )
    assert len(report.doubtful) == 1
    page, reason = report.doubtful[0]
    assert page == "page 1"
    assert "dots per inch" in reason
    assert reason in caplog.text


def test_a_scan_letterboxed_onto_other_paper_is_within_the_slack(tmp_path: Path) -> None:
    # 1700x2100 on letter paper is 5% off the page's own proportions, which
    # is what the tolerance is for: paper of a different shape is ordinary,
    # and is not what the test is looking for.
    source = _page_carrying(tmp_path, _scan(30, (1700, 2100)), 200, "letterboxed.pdf")

    assert unpack(source, tmp_path / "out").doubtful == ()


def test_a_picture_shaped_unlike_the_page_is_flagged_too(tmp_path: Path) -> None:
    # Big enough to be a scan of something, but square on letter paper.
    source = _page_carrying(tmp_path, _scan(30, (1700, 1700)), 200, "square.pdf")

    report = unpack(source, tmp_path / "out")

    assert len(report.doubtful) == 1
    assert "does not cover the page" in report.doubtful[0][1]


def test_a_pdf_that_is_not_a_pdf_says_so(tmp_path: Path) -> None:
    broken = tmp_path / "chapter.pdf"
    broken.write_bytes(b"%PDF-1.4 and then nothing")

    with pytest.raises(InputError, match=r"cannot read chapter\.pdf"):
        unpack(broken)


# -- CBR ----------------------------------------------------------------


class _FakeRarInfo:
    def __init__(self, filename: str, directory: bool = False, link: bool = False) -> None:
        self.filename = filename
        self._directory = directory
        self._link = link

    def is_dir(self) -> bool:
        return self._directory

    def is_symlink(self) -> bool:
        return self._link


class _FakeRarFile:
    """Enough of rarfile.RarFile to read a chapter, backed by a dict.

    A real one cannot be built here: nothing that can be redistributed
    writes RAR, which is the same licensing fact that keeps the reader out
    of the bundle.
    """

    opened: ClassVar[list[Path]] = []

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        type(self).opened.append(self.path)
        self.entries: dict[str, bytes] = _FAKE_RAR_CONTENT

    def __enter__(self) -> _FakeRarFile:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def infolist(self) -> list[_FakeRarInfo]:
        return [_FakeRarInfo(name, link=name.endswith(".link.png")) for name in self.entries]

    def read(self, info: _FakeRarInfo) -> bytes:
        return self.entries[info.filename]


_FAKE_RAR_CONTENT: dict[str, bytes] = {}


class _RarCannotExecError(Exception):
    pass


def _fake_rarfile(*, works: bool) -> Any:
    def tool_setup(force: bool = False) -> None:
        if not works:
            raise _RarCannotExecError("Cannot find working tool")

    return SimpleNamespace(
        RarFile=_FakeRarFile,
        Error=Exception,
        RarCannotExec=_RarCannotExecError,
        tool_setup=tool_setup,
        UNRAR_TOOL="unrar",
    )


@pytest.fixture
def fake_rarfile(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    module = _fake_rarfile(works=True)
    monkeypatch.setitem(sys.modules, "rarfile", module)
    _FakeRarFile.opened = []
    yield module


def test_a_cbr_is_read_like_any_other_archive(
    tmp_path: Path, fake_rarfile: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(_FAKE_RAR_CONTENT, "Ch/page2.png", _page_bytes(tmp_path, "b.png", 202))
    monkeypatch.setitem(_FAKE_RAR_CONTENT, "Ch/page1.png", _page_bytes(tmp_path, "a.png", 201))
    monkeypatch.setitem(_FAKE_RAR_CONTENT, "Ch/notes.txt", b"not a page")
    archive = tmp_path / "chapter.cbr"
    archive.write_bytes(b"Rar!\x1a\x07\x00 pretend")

    report = unpack(archive)

    assert [path.name for path in report.pages] == ["001-page1.png", "002-page2.png"]
    assert report.skipped == (("Ch/notes.txt", "unsupported extension .txt"),)
    assert _FakeRarFile.opened == [archive]


def test_a_link_in_a_cbr_is_named_as_well(
    tmp_path: Path, fake_rarfile: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(_FAKE_RAR_CONTENT, "page1.png", _page_bytes(tmp_path, "a.png", 201))
    monkeypatch.setitem(_FAKE_RAR_CONTENT, "page2.link.png", b"/etc/passwd")
    archive = tmp_path / "chapter.cbr"
    archive.write_bytes(b"Rar!\x1a\x07\x00 pretend")

    report = unpack(archive)

    assert [path.name for path in report.pages] == ["001-page1.png"]
    assert report.skipped == (("page2.link.png", "a link or a device, not a file"),)


def test_without_a_rar_tool_cbr_says_so_and_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "rarfile", _fake_rarfile(works=False))
    monkeypatch.delenv(UNRAR_ENV, raising=False)
    archive = tmp_path / "chapter.cbr"
    archive.write_bytes(b"Rar!\x1a\x07\x00 pretend")

    with pytest.raises(InputError, match="CBR needs a RAR tool and none was found"):
        unpack(archive)

    assert not (tmp_path / "chapter-pages").exists()


def test_the_environment_can_name_the_rar_tool(
    tmp_path: Path, fake_rarfile: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(UNRAR_ENV, "/opt/homebrew/bin/unrar")
    monkeypatch.setitem(_FAKE_RAR_CONTENT, "page1.png", _page_bytes(tmp_path, "a.png", 201))
    archive = tmp_path / "chapter.cbr"
    archive.write_bytes(b"Rar!\x1a\x07\x00 pretend")

    unpack(archive)

    assert fake_rarfile.UNRAR_TOOL == "/opt/homebrew/bin/unrar"


def test_a_named_rar_tool_that_does_not_work_is_named_in_the_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "rarfile", _fake_rarfile(works=False))
    monkeypatch.setenv(UNRAR_ENV, "/nowhere/unrar")
    archive = tmp_path / "chapter.cbr"
    archive.write_bytes(b"Rar!\x1a\x07\x00 pretend")

    with pytest.raises(InputError, match="'/nowhere/unrar', which did not work"):
        unpack(archive)
