"""Chapters that arrive as one file: CBZ, CBR and PDF.

**Unpacked to a directory beside the file, never read on demand.** A plan
file names its pages relative to itself, and `apply`, `review` and
`validate` all resolve them that way and hash them to prove nothing has
moved since. Reading pages out of a container at render time would break
that in three modules at once and take the per-page hash check with it, to
save a directory nobody had asked to be rid of. Unpacking first costs the
disk twice and changes nothing downstream: what comes out is a folder of
images, which is what this tool has always read.

The unpacked pages are **outputs of this stage, not sources being
modified**. The container itself is opened read-only and never written to,
like every other source this tool touches.

**The order pages come out in is the order they go in.** Archive entries
sort in natural filename order, PDF pages come in page order, and each page
is written with a zero-padded index in front of its name — so the directory
sorts the way the chapter reads even when the names inside the container do
not, and two pages with the same name in different folders cannot collide.

**CBR needs a binary this project will not ship.** `unrar`'s licence is not
OSI-free and this is an MIT project, so bundling it would put somebody
else's terms on the whole thing. `rarfile` drives whichever tool is already
on the machine — `unrar`, `unar`, `bsdtar` or `7z` — and
``COMICTRANS_UNRAR`` names one that lives somewhere unusual. With none of
them installed, CBR input is unavailable and says so before anything is
written, rather than failing part-way through a chapter.
"""

from __future__ import annotations

import logging
import os
import zipfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from .errors import InputError
from .imaging import IMAGE_SUFFIXES
from .util import natural_key

log = logging.getLogger(__name__)

ZIP_SUFFIXES = frozenset({".cbz", ".zip"})
RAR_SUFFIXES = frozenset({".cbr", ".rar"})
PDF_SUFFIXES = frozenset({".pdf"})
CONTAINER_SUFFIXES = ZIP_SUFFIXES | RAR_SUFFIXES | PDF_SUFFIXES
"""Every extension :func:`unpack` reads. A plain ``.zip`` and ``.rar`` are in
there because a chapter is regularly saved under the general extension, and
what is inside decides whether it is a chapter, not what it is called."""

UNRAR_ENV = "COMICTRANS_UNRAR"
"""Names the RAR tool when it is not on ``PATH``; see the module docstring."""

_Page = tuple[str, bytes]
"""What a reader yields: the name the page should take on disk, before the
index prefix, and the bytes to write under it."""

_Reader = Callable[[Path, list[tuple[str, str]]], Iterator[_Page]]
"""Readers append to the skipped list rather than returning one, so that a
page they refuse is reported in the same order it was met."""


@dataclass(frozen=True, slots=True)
class UnpackReport:
    """What came out of one container, and what did not."""

    source: Path
    directory: Path
    pages: tuple[Path, ...] = ()
    reused: int = 0
    """Pages already in the directory, byte for byte, from an earlier run.
    Unpacking the same chapter twice writes nothing the second time."""
    skipped: tuple[tuple[str, str], ...] = ()
    """``(what it was called inside the container, why it was not a page)``."""


def is_container(path: Path) -> bool:
    """Whether ``extract`` would unpack this before reading it."""
    return path.suffix.lower() in CONTAINER_SUFFIXES


def default_unpack_dir(source: Path) -> Path:
    """``<stem>-pages`` beside the container.

    Beside it rather than in a cache, because the plan file written next to
    these pages refers to them for as long as it exists, and a directory the
    system may sweep is no place for something a translation points at.
    """
    return source.with_name(f"{source.stem}-pages")


def _skip_reason(name: str) -> str | None:
    """Why this container entry is not a page, or ``None`` if it is one."""
    entry = PurePosixPath(name)
    if name.startswith("__MACOSX/"):
        return "resource fork (a Mac wrote it, no reader shows it)"
    if entry.name.startswith("."):
        return "hidden file"
    if entry.suffix.lower() not in IMAGE_SUFFIXES:
        return f"unsupported extension {entry.suffix or '(none)'}"
    return None


class _Member(Protocol):
    """What a zip entry and a rar entry have in common, which is enough."""

    filename: str

    def is_dir(self) -> bool: ...


class _Archive(Protocol):
    def infolist(self) -> Sequence[_Member]: ...

    def read(self, member: Any) -> bytes: ...


def _archive_pages(archive: _Archive, skipped: list[tuple[str, str]]) -> Iterator[_Page]:
    """The pages of an open zip or rar, in the order a reader would show them.

    One function for both because a CBR is a CBZ with a different compressor
    behind it: same member list, same names, same question about which of
    them is a page. Only opening it differs, which is where the licensing
    lives too.
    """
    for info in sorted(archive.infolist(), key=lambda member: natural_key(member.filename)):
        if info.is_dir():
            continue  # ordinary inside a comic archive, and not a page
        reason = _skip_reason(info.filename)
        if reason is not None:
            skipped.append((info.filename, reason))
            continue
        yield PurePosixPath(info.filename).name, archive.read(info)


def _zip_pages(source: Path, skipped: list[tuple[str, str]]) -> Iterator[_Page]:
    try:
        archive = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError) as exc:
        raise InputError(f"cannot read {source.name}: {exc}") from exc
    with archive:
        yield from _archive_pages(archive, skipped)


def _rar_tool() -> None:
    """Point ``rarfile`` at a tool, or say which ones would have done.

    Called before the directory is made, so a machine with no RAR tool gets
    the same nothing-happened it started with.
    """
    import rarfile

    configured = os.environ.get(UNRAR_ENV, "").strip()
    if configured:
        rarfile.UNRAR_TOOL = configured
    try:
        rarfile.tool_setup(force=True)
    except rarfile.RarCannotExec as exc:
        named = f" {UNRAR_ENV} names {configured!r}, which did not work." if configured else ""
        raise InputError(
            f"CBR needs a RAR tool and none was found: {exc}.{named} Install one "
            "of unrar, unar, bsdtar or 7z — none of them ships with comictrans, "
            "because unrar's licence is not one an MIT project can redistribute "
            f"— or set {UNRAR_ENV} to the one you have. CBZ and PDF need nothing."
        ) from exc


def _rar_pages(source: Path, skipped: list[tuple[str, str]]) -> Iterator[_Page]:
    import rarfile

    try:
        archive = rarfile.RarFile(source)
    except rarfile.Error as exc:
        raise InputError(f"cannot read {source.name}: {exc}") from exc
    with archive:
        yield from _archive_pages(archive, skipped)


def _pdf_pages(source: Path, skipped: list[tuple[str, str]]) -> Iterator[_Page]:
    """The image on each page of a scan, exactly as the PDF stores it.

    A scanned comic is one photograph per page, so the page's own image is
    the page and lifting it out is lossless — nothing is rasterised, nothing
    is resampled, and a JPEG comes out the JPEG that went in. What that
    cannot do is invent a page out of drawing instructions: a born-digital
    PDF, a page with several images on it, or one the reader is told to turn
    is reported rather than guessed at, because every one of those would
    come out as something other than the page somebody is looking at.
    """
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(source)
        pages = list(reader.pages)
    except (PyPdfError, OSError, ValueError) as exc:
        raise InputError(f"cannot read {source.name}: {exc}") from exc

    for number, page in enumerate(pages, start=1):
        label = f"page {number}"
        try:
            rotation = page.rotation % 360
            count = len(page.images)
        except (PyPdfError, ValueError) as exc:
            skipped.append((label, f"unreadable: {exc}"))
            continue
        if rotation:
            skipped.append((label, f"the page is turned {rotation}°; its image is not upright"))
            continue
        if count != 1:
            what = "no image on it" if count == 0 else f"{count} images on it"
            skipped.append((label, f"{what}, so there is no one page image to lift out"))
            continue
        try:
            image = page.images[0]
        except (PyPdfError, ValueError, NotImplementedError) as exc:
            skipped.append((label, f"its image could not be read: {exc}"))
            continue
        suffix = PurePosixPath(image.name).suffix.lower()
        if suffix not in IMAGE_SUFFIXES:
            skipped.append(
                (
                    label,
                    f"its image is {suffix or 'of an unknown kind'}, which is not"
                    f" one of {', '.join(sorted(IMAGE_SUFFIXES))}",
                )
            )
            continue
        yield f"page{suffix}", image.data


_READERS: tuple[tuple[frozenset[str], _Reader], ...] = (
    (ZIP_SUFFIXES, _zip_pages),
    (RAR_SUFFIXES, _rar_pages),
    (PDF_SUFFIXES, _pdf_pages),
)


def _reader_for(source: Path) -> _Reader:
    suffix = source.suffix.lower()
    for suffixes, reader in _READERS:
        if suffix in suffixes:
            if suffixes is RAR_SUFFIXES:
                _rar_tool()
            return reader
    raise InputError(
        f"unsupported container {source.suffix!r}; "
        f"expected one of {', '.join(sorted(CONTAINER_SUFFIXES))}"
    )


def unpack(source: Path, into: Path | None = None) -> UnpackReport:
    """Write every page of a container into a directory, and say what it did.

    Unpacking the same container twice is the same directory: a page already
    there byte for byte is left alone and counted as reused, so re-running
    extract over a chapter costs the reads and not the writes. A file of the
    same name holding something else is not overwritten — it is somebody
    else's, or an older chapter's, and the run stops rather than deciding
    which.
    """
    if not source.is_file():
        raise InputError(f"input path does not exist: {source}")
    reader = _reader_for(source)

    directory = into if into is not None else default_unpack_dir(source)
    skipped: list[tuple[str, str]] = []
    pages: list[Path] = []
    reused = 0
    for index, (name, data) in enumerate(reader(source, skipped), start=1):
        if not pages:
            # Made when there is a page to put in it, so that a file which
            # turns out not to be a chapter at all leaves the directory it
            # would have had uncreated rather than empty.
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise InputError(f"cannot unpack into {directory}: {exc}") from exc
        target = directory / f"{index:03d}-{name}"
        if target.exists():
            if target.read_bytes() == data:
                reused += 1
                pages.append(target)
                continue
            raise InputError(
                f"{target} is already there and is not the page {source.name} "
                "holds for it. Unpack somewhere else with --unpack-dir, or "
                "delete that directory and run this again."
            )
        target.write_bytes(data)
        pages.append(target)

    for name, reason in skipped:
        log.warning("%s: skipping %s: %s", source.name, name, reason)
    if not pages:
        raise InputError(f"no pages found in {source.name}")
    log.info("unpacked %d page(s) from %s into %s", len(pages), source.name, directory)
    return UnpackReport(
        source=source,
        directory=directory,
        pages=tuple(pages),
        reused=reused,
        skipped=tuple(skipped),
    )


__all__ = [
    "CONTAINER_SUFFIXES",
    "UNRAR_ENV",
    "UnpackReport",
    "default_unpack_dir",
    "is_container",
    "unpack",
]
