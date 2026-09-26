"""Reading a chapter rather than translating it: pages by index, and layout.

What the reader window stands on, kept free of Qt like ``document`` and
``preview`` so what it decides is tested without a display.

**Pages come from where they are.** A folder is read file by file and a
chapter file through :func:`sources.open_chapter`, which reads one page when
asked and writes nothing — not the ``<stem>-pages`` directory ``extract``
leaves, which exists for a plan to keep finding its pages, and a reader has
no plan. Nothing here keeps a page's bytes: what is held, and for how long,
is the window's decision — see :func:`pages_to_keep`.

**Two pages at a time is how a printed comic opens**: the cover alone on the
right, then each left page beside its right. A scan of both halves of a
spread is one image already, and beside its neighbour it would be three
pages wide; it is shown alone and the pairing starts again after it, which
is where the printed pairing starts again too — the spread was two pages of
it.

**A chapter file can say what it is**, in the ComicInfo.xml ``extract`` reads
into a plan's header. The reader shows all of it that a person would read —
:func:`chapter_info` — and opens right to left when it says so. A folder of
pages says nothing here, as it says nothing to ``extract``: the reader reads
what ``extract`` reads.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .comicinfo import ComicInfoError, read_elements, reading_direction
from .errors import InputError
from .imaging import collect_inputs
from .model import ReadingDirection
from .sources import is_container, open_chapter

Size = tuple[int, int]
"""``(width, height)`` in pixels."""


class Pages(Protocol):
    """A chapter, readable a page at a time. Page numbers count from zero."""

    def __len__(self) -> int: ...

    def label(self, index: int) -> str:
        """What the page is called where it came from."""
        ...

    def read(self, index: int) -> bytes:
        """The page's bytes, read now. Raises ``InputError`` for one that
        turns out not to be readable."""
        ...

    @property
    def comic_info(self) -> bytes | None:
        """The chapter's ComicInfo.xml, unparsed, or ``None`` if it has none."""
        ...


@dataclass(frozen=True, slots=True)
class FilePages:
    """A folder of pages, or one image: files read where they lie."""

    paths: tuple[Path, ...]

    def __len__(self) -> int:
        return len(self.paths)

    def label(self, index: int) -> str:
        return self.paths[index].name

    def read(self, index: int) -> bytes:
        path = self.paths[index]
        try:
            with path.open("rb") as handle:
                return handle.read()
        except OSError as exc:
            raise InputError(f"cannot read {path.name}: {exc}") from exc

    @property
    def comic_info(self) -> None:
        """Never: ``extract`` reads a ComicInfo.xml only out of a chapter file."""
        return None


@contextmanager
def open_pages(target: Path, unrar_tool: str = "") -> Iterator[Pages]:
    """What ``extract`` would read from ``target``, open to be read in place.

    A folder of pages, one image, or a chapter file — the last held open
    until the ``with`` ends, which is what reading one without unpacking it
    costs. Refuses what ``extract`` refuses, in its words.
    """
    if target.is_file() and is_container(target):
        with open_chapter(target, unrar_tool) as chapter:
            yield chapter
        return
    images, _skipped = collect_inputs(target)
    yield FilePages(tuple(images))


DETAILS = (
    "series",
    "number",
    "title",
    "volume",
    "year",
    "publisher",
    "writer",
    "penciller",
    "inker",
    "colorist",
    "letterer",
    "coverartist",
    "editor",
    "translator",
    "genre",
    "languageiso",
    "pagecount",
    "summary",
)
"""The ComicInfo.xml elements the reader shows, folded, in the order it
shows them: what the chapter is, who made it, and what it is about. Left out
is what is for a library rather than a reader — sort keys, links, the page
list, ratings."""


@dataclass(frozen=True, slots=True)
class ChapterInfo:
    """What a chapter's ComicInfo.xml says, as the reader shows it."""

    details: tuple[tuple[str, str], ...] = ()
    """``(element, value)`` for each of :data:`DETAILS` the file gives, in
    that order. Every value is one line, but the summary, which keeps its
    paragraphs."""

    reading_direction: ReadingDirection | None = None

    problem: str = ""
    """Why the file could not be read, when it could not. Said rather than
    hidden: the chapter has one, and a window that showed nothing would look
    like a chapter that had none."""


def chapter_info(pages: Pages) -> ChapterInfo | None:
    """What ``pages`` says about itself, or ``None`` if it says nothing."""
    data = pages.comic_info
    if data is None:
        return None
    try:
        elements = read_elements(data)
    except ComicInfoError as exc:
        return ChapterInfo(problem=str(exc))
    details = tuple(
        (name, value if name == "summary" else " ".join(value.split()))
        for name in DETAILS
        if (value := elements.get(name))
    )
    return ChapterInfo(details, reading_direction(elements.get("manga", "")))


def is_spread(size: Size | None) -> bool:
    """Whether a page is two printed pages scanned as one: wider than tall.

    Not measured against the fixtures, because they cannot answer it: twelve
    of the thirteen are synthetic pages drawn wider than tall to hold one
    case each, and only the six-panel page is shaped like a printed one.
    What decides it instead is the shapes involved. A US comic page is
    trimmed to about 6.6 by 10.2 inches, 0.65 wide to tall, and a tankōbon
    page is B6, 128 by 182mm, 0.70; two of either side by side is 1.3 to 1.4.
    Square sits near the middle of those in proportion — about as far from
    one page as from two. A page not yet measured is taken to be one page,
    which is what nearly every page is.
    """
    return size is not None and size[0] > size[1]


def spreads(sizes: Sequence[Size | None], two_up: bool) -> tuple[tuple[int, ...], ...]:
    """The pages grouped the way they are shown, in reading order.

    One at a time, each group is one page. Two at a time, the first page is
    alone and the rest pair up, except that a page wider than tall is always
    alone and so is the page it would have been paired with; pairing starts
    again after it. See the module docstring for why.
    """
    count = len(sizes)
    if not two_up:
        return tuple((index,) for index in range(count))
    groups: list[tuple[int, ...]] = []
    index = 0
    while index < count:
        paired = (
            index > 0
            and index + 1 < count
            and not is_spread(sizes[index])
            and not is_spread(sizes[index + 1])
        )
        if paired:
            groups.append((index, index + 1))
            index += 2
        else:
            groups.append((index,))
            index += 1
    return tuple(groups)


def group_of(groups: Sequence[tuple[int, ...]], page: int) -> int:
    """Which group shows ``page``. A page past the last is in the last."""
    for number, group in enumerate(groups):
        if page in group:
            return number
    return max(0, len(groups) - 1)


def pages_to_keep(groups: Sequence[tuple[int, ...]], current: int) -> tuple[int, ...]:
    """Which pages are worth holding decoded, most wanted first.

    What is on screen, then the next group, then the previous one: the next
    is where reading goes, the previous is where a reader looks back. Any
    other page is decoded again when it is asked for, which costs about 70ms
    for an eleven-megapixel page, measured, against 44MB for holding it —
    so what is kept is at most six pages, however long the chapter.
    """
    kept: list[int] = []
    for number in (current, current + 1, current - 1):
        if 0 <= number < len(groups):
            kept.extend(groups[number])
    return tuple(kept)


__all__ = [
    "DETAILS",
    "ChapterInfo",
    "FilePages",
    "Pages",
    "Size",
    "chapter_info",
    "group_of",
    "is_spread",
    "open_pages",
    "pages_to_keep",
    "spreads",
]
