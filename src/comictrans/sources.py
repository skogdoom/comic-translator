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
import stat
import zipfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
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

PAGE_SHAPE_TOLERANCE = 0.10
"""How far a page image's proportions may sit from the page's own before it
is worth mentioning. A scan is placed to fill its page, so it matches within
a percent or two; the slack is for one letterboxed onto paper of a different
shape, which is a real thing and not a reason to doubt anything."""

MIN_PAGE_DPI = 72
"""Below this, an image cannot be the page it sits on. Read as "if this image
did fill the page, what would it have been scanned at" — a real scan is 150
to 1200, and a logo on a letter page works out at about twelve."""


@dataclass(slots=True)
class _Notes:
    """What a reader met on the way through that did not become a page.

    One object rather than three out-parameters, and a list rather than a
    return value, so that each note keeps the position it was met in.
    """

    skipped: list[tuple[str, str]] = field(default_factory=list)
    doubtful: list[tuple[str, str]] = field(default_factory=list)


_Reader = Callable[[Path, _Notes], Iterator[_Page]]


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
    doubtful: tuple[tuple[str, str], ...] = ()
    """``(page, why it may not be the page it should be)`` — unpacked all the
    same. Nothing here is wrong enough to refuse: what it catches is a PDF
    that is not a scan, where the one image on a page is a logo rather than a
    photograph of it, and the answer to that is to say so and let somebody
    look. See :func:`_page_doubt`."""


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


def _zip_is_regular(member: Any) -> bool:
    """Whether a zip entry is a file rather than a link or a device.

    An archive can hold a symlink, and reading one gives the path it points
    at rather than any image — thirteen bytes reading ``/etc/passwd`` written
    out as a page. Nothing escapes the directory either way, because only the
    entry's basename is ever used, but a file that cannot be a page should be
    named rather than written.

    A zip made on Windows carries no mode at all, which is not a reason to
    doubt it: no mode means a file.
    """
    kind = stat.S_IFMT(member.external_attr >> 16)
    return kind in (0, stat.S_IFREG)


def _archive_pages(
    archive: _Archive, notes: _Notes, regular: Callable[[Any], bool]
) -> Iterator[_Page]:
    """The pages of an open zip or rar, in the order a reader would show them.

    One function for both because a CBR is a CBZ with a different compressor
    behind it: same member list, same names, same question about which of
    them is a page. Only opening it differs, which is where the licensing
    lives too — and asking what kind of entry this is, which the two formats
    record in their own ways.
    """
    for info in sorted(archive.infolist(), key=lambda member: natural_key(member.filename)):
        if info.is_dir():
            continue  # ordinary inside a comic archive, and not a page
        reason = _skip_reason(info.filename)
        if reason is None and not regular(info):
            reason = "a link or a device, not a file"
        if reason is not None:
            notes.skipped.append((info.filename, reason))
            continue
        yield PurePosixPath(info.filename).name, archive.read(info)


def _zip_pages(source: Path, notes: _Notes) -> Iterator[_Page]:
    try:
        archive = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError) as exc:
        raise InputError(f"cannot read {source.name}: {exc}") from exc
    with archive:
        yield from _archive_pages(archive, notes, _zip_is_regular)


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


def _rar_pages(source: Path, notes: _Notes) -> Iterator[_Page]:
    import rarfile

    try:
        archive = rarfile.RarFile(source)
    except rarfile.Error as exc:
        raise InputError(f"cannot read {source.name}: {exc}") from exc
    with archive:
        yield from _archive_pages(archive, notes, lambda member: not member.is_symlink())


def _page_doubt(page: Any, image: Any) -> str | None:
    """Whether this image looks like a photograph of that page, or not.

    "Exactly one image on the page" is not the same as "this image is the
    page", and the difference is a born-digital PDF: text drawn as text, with
    one logo on it, which would otherwise be lifted out and called a page. Two
    measurements catch it, and neither refuses anything — a PDF this tool
    cannot read at all is one thing, and a page somebody should look at before
    translating it is another.

    **Shape.** A scan is placed to fill its page, so its proportions are the
    page's. A logo's are its own.

    **Size.** Read the image's pixels against the page's paper size and see
    what it would have been scanned at. A logo on a letter page comes out at
    about twelve dots per inch, which is not a number any scanner produces —
    and this one catches a square logo on a square page, which the shape test
    cannot.
    """
    pixels = getattr(image, "image", None)
    if pixels is None:
        return None
    try:
        width, height = (float(page.mediabox.width), float(page.mediabox.height))
        across, down = (float(pixels.width), float(pixels.height))
    except (AttributeError, TypeError, ValueError):
        return None
    if min(width, height, across, down) <= 0:
        return None

    dpi = min(across / (width / 72), down / (height / 72))
    if dpi < MIN_PAGE_DPI:
        return (
            f"its image is {int(across)}x{int(down)} on {width:.0f}x{height:.0f}pt of paper, "
            f"which is {dpi:.0f} dots per inch — too few for a scan of the page, so this is "
            "more likely a picture sitting on it"
        )
    shape = (across / down) / (width / height)
    if abs(shape - 1) > PAGE_SHAPE_TOLERANCE:
        return (
            f"its image is {across / down:.2f} wide to tall and the page is "
            f"{width / height:.2f}, so it does not cover the page it was taken from"
        )
    return None


def _pdf_pages(source: Path, notes: _Notes) -> Iterator[_Page]:
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
            notes.skipped.append((label, f"unreadable: {exc}"))
            continue
        if rotation:
            notes.skipped.append(
                (label, f"the page is turned {rotation}°; its image is not upright")
            )
            continue
        if count != 1:
            what = "no image on it" if count == 0 else f"{count} images on it"
            notes.skipped.append((label, f"{what}, so there is no one page image to lift out"))
            continue
        try:
            image = page.images[0]
        except (PyPdfError, ValueError, NotImplementedError) as exc:
            notes.skipped.append((label, f"its image could not be read: {exc}"))
            continue
        suffix = PurePosixPath(image.name).suffix.lower()
        if suffix not in IMAGE_SUFFIXES:
            notes.skipped.append(
                (
                    label,
                    f"its image is {suffix or 'of an unknown kind'}, which is not"
                    f" one of {', '.join(sorted(IMAGE_SUFFIXES))}",
                )
            )
            continue
        doubt = _page_doubt(page, image)
        if doubt is not None:
            notes.doubtful.append((label, doubt))
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


def _stray_pages(directory: Path, pages: Sequence[Path]) -> list[Path]:
    """Images in the unpacked directory that this chapter did not put there.

    They matter because nothing downstream reads the list this function's
    caller returns: ``extract`` is handed the directory and lists it again,
    so anything image-shaped in there is a page of the chapter as far as the
    plan is concerned. The way to get some is to unpack a re-release over an
    older unpack — one page inserted at the front renumbers every name after
    it, so the old files collide with nothing and simply stay, and the
    chapter comes out with half its pages twice.

    A plan file is not an image, so the translation living in this directory
    is never one of these.
    """
    kept = {page.resolve() for page in pages}
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in IMAGE_SUFFIXES
        and path.resolve() not in kept
    )


def unpack(source: Path, into: Path | None = None) -> UnpackReport:
    """Write every page of a container into a directory, and say what it did.

    Unpacking the same container twice is the same directory: a page already
    there byte for byte is left alone and counted as reused, so re-running
    extract over a chapter costs the reads and not the writes. A file of the
    same name holding something else is not overwritten — it is somebody
    else's, or an older chapter's, and the run stops rather than deciding
    which.

    The directory has to hold this chapter and nothing else that looks like a
    page, for the reason :func:`_stray_pages` gives. When it does not, the
    run stops there: the pages written are this chapter's own, so nothing is
    lost by it, and the run would otherwise end in a plan describing somebody
    else's images as pages of this comic.
    """
    if not source.is_file():
        raise InputError(f"input path does not exist: {source}")
    reader = _reader_for(source)

    directory = into if into is not None else default_unpack_dir(source)
    notes = _Notes()
    pages: list[Path] = []
    reused = 0
    for index, (name, data) in enumerate(reader(source, notes), start=1):
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

    for name, reason in notes.skipped:
        log.warning("%s: skipping %s: %s", source.name, name, reason)
    for name, reason in notes.doubtful:
        log.warning("%s: %s: %s", source.name, name, reason)
    if not pages:
        raise InputError(f"no pages found in {source.name}")

    strays = _stray_pages(directory, pages)
    if strays:
        named = ", ".join(path.name for path in strays[:4])
        more = f" and {len(strays) - 4} more" if len(strays) > 4 else ""
        raise InputError(
            f"{directory} holds {len(strays)} image(s) that are not pages of "
            f"{source.name}: {named}{more}. extract reads the whole directory, "
            "so they would go into the plan as pages of this chapter. Unpack "
            "somewhere else with --unpack-dir, or clear that directory out — "
            "check it for a plan file of your own first."
        )

    log.info("unpacked %d page(s) from %s into %s", len(pages), source.name, directory)
    return UnpackReport(
        source=source,
        directory=directory,
        pages=tuple(pages),
        reused=reused,
        skipped=tuple(notes.skipped),
        doubtful=tuple(notes.doubtful),
    )


__all__ = [
    "CONTAINER_SUFFIXES",
    "UNRAR_ENV",
    "UnpackReport",
    "default_unpack_dir",
    "is_container",
    "unpack",
]
