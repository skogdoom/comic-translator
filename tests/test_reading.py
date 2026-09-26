"""Reading a chapter in place, and how its pages sit side by side."""

from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pytest

from comictrans.errors import InputError
from comictrans.model import ReadingDirection
from comictrans.reading import (
    DETAILS,
    ChapterInfo,
    chapter_info,
    group_of,
    is_spread,
    open_pages,
    pages_to_keep,
    spreads,
)

from .conftest import save_page

PAGE = (650, 1000)
"""A printed page's proportions."""

WIDE = (1300, 1000)
"""Two of them, scanned as one."""


def _png(path: Path, shade: int) -> Path:
    return save_page(np.full((30, 20, 3), shade, dtype=np.uint8), path)


# -- layout -----------------------------------------------------------------


def test_one_at_a_time_is_every_page_alone() -> None:
    assert spreads([PAGE, WIDE, PAGE], two_up=False) == ((0,), (1,), (2,))


def test_two_at_a_time_opens_like_a_printed_comic() -> None:
    """The cover alone, then each left page beside its right, and a last
    page with nothing to face alone."""
    assert spreads([PAGE] * 6, two_up=True) == ((0,), (1, 2), (3, 4), (5,))


def test_a_spread_is_shown_alone_and_the_pairing_starts_again_after_it() -> None:
    assert spreads([PAGE, PAGE, PAGE, WIDE, PAGE, PAGE], two_up=True) == (
        (0,),
        (1, 2),
        (3,),
        (4, 5),
    )


def test_the_page_a_spread_would_have_faced_is_alone_too() -> None:
    assert spreads([PAGE, PAGE, PAGE, PAGE, WIDE, PAGE, PAGE], two_up=True) == (
        (0,),
        (1, 2),
        (3,),
        (4,),
        (5, 6),
    )


def test_a_page_not_yet_measured_is_taken_for_one_page() -> None:
    assert spreads([None, None, None], two_up=True) == ((0,), (1, 2))


def test_a_spread_is_a_page_wider_than_tall() -> None:
    assert [is_spread(size) for size in (WIDE, PAGE, (1000, 1000), (1001, 1000), None)] == [
        True,
        False,
        False,
        True,
        False,
    ]


def test_a_page_is_found_in_whichever_group_shows_it() -> None:
    groups = spreads([PAGE] * 6, two_up=True)

    assert [group_of(groups, page) for page in range(6)] == [0, 1, 1, 2, 2, 3]
    assert group_of(groups, 99) == 3, "past the end is the last group"


def test_what_is_kept_decoded_is_on_screen_then_next_then_previous() -> None:
    groups = spreads([PAGE] * 8, two_up=True)  # (0,) (1,2) (3,4) (5,6) (7,)

    assert pages_to_keep(groups, 2) == (3, 4, 5, 6, 1, 2)
    assert pages_to_keep(groups, 0) == (0, 1, 2), "nothing before the first"
    assert pages_to_keep(groups, 4) == (7, 5, 6), "nothing after the last"


# -- reading in place -------------------------------------------------------


def test_a_folder_is_read_in_the_order_extract_reads_it(tmp_path: Path) -> None:
    for name, shade in (("page10.png", 10), ("page2.png", 2), ("page1.png", 1)):
        _png(tmp_path / name, shade)
    (tmp_path / "notes.txt").write_text("not a page")

    with open_pages(tmp_path) as pages:
        labels = [pages.label(index) for index in range(len(pages))]
        second = pages.read(1)

    assert labels == ["page1.png", "page2.png", "page10.png"]
    assert second == (tmp_path / "page2.png").read_bytes()


def test_one_image_is_a_chapter_of_one_page(tmp_path: Path) -> None:
    page = _png(tmp_path / "page.png", 1)

    with open_pages(page) as pages:
        assert (len(pages), pages.read(0)) == (1, page.read_bytes())


def test_a_chapter_file_is_read_without_being_unpacked(tmp_path: Path) -> None:
    """The ``<stem>-pages`` folder extract makes is exactly what must not appear."""
    first = _png(tmp_path / "a.png", 1).read_bytes()
    second = _png(tmp_path / "b.png", 2).read_bytes()
    chapter = tmp_path / "chapter.cbz"
    with zipfile.ZipFile(chapter, "w") as archive:
        archive.writestr("001.png", first)
        archive.writestr("002.png", second)
    before = sorted(tmp_path.iterdir())

    with open_pages(chapter) as pages:
        read = [pages.read(1), pages.read(0)]

    assert read == [second, first]
    assert sorted(tmp_path.iterdir()) == before


@pytest.mark.parametrize(
    ("name", "refusal"),
    [("missing", "does not exist"), ("empty", "no supported images found")],
)
def test_nothing_to_read_is_refused(tmp_path: Path, name: str, refusal: str) -> None:
    (tmp_path / "empty").mkdir()

    with pytest.raises(InputError, match=refusal), open_pages(tmp_path / name):
        pass


def test_a_page_that_has_gone_is_named_when_it_is_read(tmp_path: Path) -> None:
    page = _png(tmp_path / "page.png", 1)

    with open_pages(tmp_path) as pages:
        page.unlink()
        with pytest.raises(InputError, match=r"cannot read page\.png"):
            pages.read(0)


# -- what a chapter says about itself -----------------------------------------

DESCRIBED = b"""<?xml version="1.0"?>
<ComicInfo>
  <Summary>
    The first paragraph.

    The second.
  </Summary>
  <Web>https://example.invalid/tex</Web>
  <Title>La mano  rossa</Title>
  <Series>Tex</Series>
  <Number>7</Number>
  <Penciller>Aurelio
    Galleppini</Penciller>
  <LanguageISO>it</LanguageISO>
  <Manga>YesAndRightToLeft</Manga>
  <Pages><Page Image="0" /></Pages>
</ComicInfo>
"""


def _described_chapter(tmp_path: Path, comic_info: bytes | None) -> Path:
    chapter = tmp_path / "chapter.cbz"
    with zipfile.ZipFile(chapter, "w") as archive:
        archive.writestr("001.png", _png(tmp_path / "a.png", 1).read_bytes())
        if comic_info is not None:
            archive.writestr("ComicInfo.xml", comic_info)
    return chapter


def test_what_a_chapter_says_is_shown_in_the_readers_order(tmp_path: Path) -> None:
    with open_pages(_described_chapter(tmp_path, DESCRIBED)) as pages:
        info = chapter_info(pages)

    assert info == ChapterInfo(
        details=(
            ("series", "Tex"),
            ("number", "7"),
            ("title", "La mano rossa"),
            ("penciller", "Aurelio Galleppini"),
            ("languageiso", "it"),
            ("summary", "The first paragraph.\n\n    The second."),
        ),
        reading_direction=ReadingDirection.RIGHT_TO_LEFT,
    )


def test_what_is_for_a_library_rather_than_a_reader_is_left_out() -> None:
    """A link, the page list: nothing in them is what a chapter is."""
    assert "web" not in DETAILS and "pages" not in DETAILS


def test_a_chapter_with_nothing_to_say_says_nothing(tmp_path: Path) -> None:
    with open_pages(_described_chapter(tmp_path, None)) as pages:
        assert chapter_info(pages) is None


def test_a_folder_says_nothing_even_with_a_comic_info_in_it(tmp_path: Path) -> None:
    """The reader reads what extract reads, and extract reads it only out of
    a chapter file."""
    _png(tmp_path / "001.png", 1)
    (tmp_path / "ComicInfo.xml").write_bytes(DESCRIBED)

    with open_pages(tmp_path) as pages:
        assert chapter_info(pages) is None


def test_one_that_cannot_be_read_says_why_rather_than_nothing(tmp_path: Path) -> None:
    with open_pages(_described_chapter(tmp_path, b"<ComicInfo><Series>")) as pages:
        info = chapter_info(pages)

    assert info is not None
    assert info.details == ()
    assert info.problem.startswith("it is not well-formed XML")


def test_one_the_archive_cannot_give_up_leaves_the_pages_readable(tmp_path: Path) -> None:
    """A damaged entry fails to come out of the zip at all — a bad CRC here."""
    chapter = _described_chapter(tmp_path, b"<ComicInfo><Series>Tex</Series></ComicInfo>")
    raw = bytearray(chapter.read_bytes())
    at = raw.index(b"<Series>Tex")
    raw[at + len(b"<Series>")] = ord("X")
    chapter.write_bytes(bytes(raw))

    with open_pages(chapter) as pages:
        assert chapter_info(pages) is None
        assert pages.read(0), "and the chapter still reads"
