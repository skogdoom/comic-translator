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
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .errors import InputError
from .imaging import collect_inputs
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
    "FilePages",
    "Pages",
    "Size",
    "group_of",
    "is_spread",
    "open_pages",
    "pages_to_keep",
    "spreads",
]
