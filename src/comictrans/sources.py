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

**What a chapter file is, its first bytes decide.** A CBR that is really a
zip and a CBZ that is really a RAR are both ordinary: the two extensions say
"comic book archive" to the people who write them, and which compressor made
it is an afterthought. The name is used only for a file whose bytes cannot be
read — one that is not there yet, which is every keystroke of a path being
typed into the window.

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
from .progress import CancelCheck, PageProgress, ProgressCallback
from .util import natural_key

log = logging.getLogger(__name__)

ZIP_SUFFIXES = frozenset({".cbz", ".zip"})
RAR_SUFFIXES = frozenset({".cbr", ".rar"})
PDF_SUFFIXES = frozenset({".pdf"})
CONTAINER_SUFFIXES = ZIP_SUFFIXES | RAR_SUFFIXES | PDF_SUFFIXES
"""Every extension a chapter file is likely to arrive under, for a file
panel's filter and for answering about a path that is not there yet. It is
not what decides how one is read — see :func:`chapter_kind`."""

ZIP, RAR, PDF = "zip", "rar", "pdf"
"""What a chapter file turns out to be. Not the same as what it is called."""

_SUFFIX_KINDS: tuple[tuple[frozenset[str], str], ...] = (
    (ZIP_SUFFIXES, ZIP),
    (RAR_SUFFIXES, RAR),
    (PDF_SUFFIXES, PDF),
)

_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"PK\x03\x04", ZIP),  # and the two below: an empty and a spanned archive
    (b"PK\x05\x06", ZIP),
    (b"PK\x07\x08", ZIP),
    (b"Rar!\x1a\x07", RAR),  # RAR 4 and RAR 5 differ after this
)

HEADER_BYTES = 1024
"""How much of a file is read to tell what it is. The three signatures sit at
the very start; ``%PDF-`` is allowed to sit a little way in, and Acrobat's
own rule is that it must be inside the first kilobyte."""

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

MAX_PAGE_BYTES = 512 * 1024 * 1024
"""How large one page may say it unpacks to before it is not a page.

**A cap on what an archive is allowed to claim, read before anything is
decompressed.** Both formats declare each member's unpacked size in their
own header, and both unpackers hold themselves to it — CPython's
``ZipExtFile`` truncates at ``zinfo.file_size`` (``zipfile/__init__.py``,
``self._left``) and ``rarfile`` counts down ``self._remain`` from
``file_size`` the same way — so the declared number is an upper bound
somebody else is already enforcing, and refusing on it costs no read at all.

Measured on a deflate bomb built for this: 255KB of archive declaring, and
delivering, 256MB on one member, at 1029:1. Deflate tops out near that
ratio, so a 4MB file of the same shape is 4GB, read into memory in one
piece by :func:`_archive_pages` and then written to disk.

Set where no page reaches it. A 600dpi colour scan of a US comic page is
about 3960x6120, which is 72MB uncompressed and around 40MB as PNG; 1200dpi
doubles each side and quadruples that. Half a gigabyte is several times the
largest thing anyone puts in a CBZ and still turns the 4GB case into a line
in the report.
"""


@dataclass(slots=True)
class _Notes:
    """What a reader met on the way through that did not become a page.

    One object rather than three out-parameters, and a list rather than a
    return value, so that each note keeps the position it was met in.
    """

    skipped: list[tuple[str, str]] = field(default_factory=list)
    doubtful: list[tuple[str, str]] = field(default_factory=list)

    total: int = 0
    """How many pages are coming, set before the first one is read.

    Every reader can answer this without reading a page's bytes — an archive
    from its member list, a PDF from the pages that pass the cheap checks —
    and a progress bar with no total is a bar that does not move. It can
    still end up one high: a PDF page whose image turns out to be stored in
    something this tool does not read is only found by looking at it."""


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
    cancelled: bool = False
    """Whether it was stopped part-way. What is on disk is whole pages of
    this chapter either way, and running it again finishes the job."""

    doubtful: tuple[tuple[str, str], ...] = ()
    """``(page, why it may not be the page it should be)`` — unpacked all the
    same. Nothing here is wrong enough to refuse: what it catches is a PDF
    that is not a scan, where the one image on a page is a logo rather than a
    photograph of it, and the answer to that is to say so and let somebody
    look. See :func:`_page_doubt`."""


def is_container(path: Path) -> bool:
    """Whether ``extract`` would unpack this before reading it.

    What it holds decides; see :func:`chapter_kind`. A chapter saved as
    ``chapter.dat`` is still a chapter, and a page saved as ``page.cbz`` is
    still a page.
    """
    return chapter_kind(path) is not None


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
    file_size: int
    """What the archive's own header says this unpacks to. A claim, not a
    measurement — but one the unpacker holds itself to, which is what makes
    it worth reading. See :data:`MAX_PAGE_BYTES`."""

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
    members = []
    for info in sorted(archive.infolist(), key=lambda member: natural_key(member.filename)):
        if info.is_dir():
            continue  # ordinary inside a comic archive, and not a page
        reason = _skip_reason(info.filename)
        if reason is None and not regular(info):
            reason = "a link or a device, not a file"
        if reason is None and info.file_size > MAX_PAGE_BYTES:
            reason = (
                f"it says it unpacks to {info.file_size / 1024 / 1024:,.0f}MB, past the "
                f"{MAX_PAGE_BYTES // 1024 // 1024}MB a page may be — nothing scanned "
                "off paper is this size, so this is a compression bomb or a mistake"
            )
        if reason is not None:
            notes.skipped.append((info.filename, reason))
            continue
        members.append(info)
    # Decided in full before anything is read: an archive's member list costs
    # nothing to walk, and knowing how many pages are coming is what lets a
    # progress bar mean something.
    notes.total = len(members)
    for info in members:
        yield PurePosixPath(info.filename).name, archive.read(info)


def _zip_pages(source: Path, notes: _Notes) -> Iterator[_Page]:
    try:
        archive = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError) as exc:
        raise InputError(f"cannot read {source.name}: {exc}") from exc
    with archive:
        yield from _archive_pages(archive, notes, _zip_is_regular)


def _unrar_tool(named: str = "") -> None:
    """Point ``rarfile`` at a tool, or say which ones would have done.

    Called before the directory is made, so a machine with no RAR tool gets
    the same nothing-happened it started with.

    ``named`` is for a caller that has been told where the tool is — the
    window, which has a Preferences field for it, because an application
    opened from the Finder does not inherit the shell's ``PATH`` and so
    cannot see a Homebrew ``unrar`` at all. The environment variable is the
    command line's way of saying the same thing, and is the fallback.
    """
    import rarfile

    configured = named.strip() or os.environ.get(UNRAR_ENV, "").strip()
    if configured:
        rarfile.UNRAR_TOOL = configured
    try:
        rarfile.tool_setup(force=True)
    except rarfile.RarCannotExec as exc:
        tried = f" {configured!r} was named and did not work." if configured else ""
        raise InputError(
            f"CBR needs a RAR tool and none was found: {exc}.{tried} Install one "
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

    # Which pages are pages is settled first. Every question asked here is
    # answered from the page's own dictionary — how it is turned, how many
    # images are on it — so none of it decodes anything, and the run knows
    # how many pages are coming before it reads the first.
    readable: list[tuple[str, Any]] = []
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
        readable.append((label, page))
    notes.total = len(readable)

    for label, page in readable:
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


_READERS: dict[str, _Reader] = {ZIP: _zip_pages, RAR: _rar_pages, PDF: _pdf_pages}


def _kind_from_header(header: bytes) -> str | None:
    for signature, kind in _MAGIC:
        if header.startswith(signature):
            return kind
    return PDF if b"%PDF-" in header else None


def _kind_from_name(source: Path) -> str | None:
    suffix = source.suffix.lower()
    for suffixes, kind in _SUFFIX_KINDS:
        if suffix in suffixes:
            return kind
    return None


def chapter_kind(source: Path) -> str | None:
    """What this chapter file is — ``"zip"``, ``"rar"``, ``"pdf"`` — or ``None``.

    **Its first bytes decide, not its name.** A CBR that is really a zip and
    a CBZ that is really a RAR are both ordinary out in the world: the two
    extensions say "comic book archive" to the people who write them, and
    which compressor made it is an afterthought. Reading one by its name
    would refuse a file every comic reader opens.

    The name is the fallback, and only for a file whose bytes cannot be had
    — one that is not there yet, which is every keystroke of a path being
    typed. A directory is neither, and is nothing.
    """
    try:
        with source.open("rb") as handle:
            kind = _kind_from_header(handle.read(HEADER_BYTES))
    except OSError:
        return _kind_from_name(source)
    return kind


def _reader_for(source: Path, unrar_tool: str = "") -> _Reader:
    """The reader for what this file is. Callers check it exists first."""
    kind = chapter_kind(source)
    if kind is None:
        named = _kind_from_name(source)
        if named is None:
            raise InputError(
                f"{source.name} is not a chapter file: it is not a zip, a rar "
                f"or a PDF, and it is not named like one either. Chapters are "
                f"{', '.join(sorted(CONTAINER_SUFFIXES))}."
            )
        raise InputError(
            f"{source.name} is named like a chapter file but is not one: it "
            "begins as neither a zip, a rar nor a PDF. Something renamed, or "
            "a download that did not finish."
        )
    if kind is RAR:
        _unrar_tool(unrar_tool)
    return _READERS[kind]


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


def check_readable(source: Path, unrar_tool: str = "") -> None:
    """Raise unless :func:`unpack` could read this, reading nothing itself.

    For asking before a run rather than during one — the window asks on every
    keystroke, so this reads a file's first kilobyte and never a page of it.
    What it catches is a file that is not a chapter at all and, for a RAR,
    the tool that is not there: the two refusals worth making before somebody
    has waited. What it cannot catch is an archive whose header is fine and
    whose contents are not.
    """
    if not source.is_file():
        raise InputError(f"input path does not exist: {source}")
    _reader_for(source, unrar_tool)


def unpack(
    source: Path,
    into: Path | None = None,
    *,
    unrar_tool: str = "",
    progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> UnpackReport:
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

    ``progress`` is called once per page, before it is written, and
    ``should_cancel`` is asked at the same moment — the same two hooks the
    passes take, see :mod:`comictrans.progress`. Stopping leaves whole pages
    of this chapter on disk, which is what running it again continues from,
    and the report says so.
    """
    if not source.is_file():
        raise InputError(f"input path does not exist: {source}")
    reader = _reader_for(source, unrar_tool)

    directory = into if into is not None else default_unpack_dir(source)
    notes = _Notes()
    pages: list[Path] = []
    reused = 0
    cancelled = False
    for index, (name, data) in enumerate(reader(source, notes), start=1):
        if should_cancel is not None and should_cancel():
            cancelled = True
            log.warning("cancelled after %d page(s) of %s", len(pages), source.name)
            break
        if progress is not None:
            progress(PageProgress(index=index - 1, total=notes.total, image=name))
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
    if cancelled:
        # Before the empty check below, and ahead of the stray check: a run
        # stopped before its first page has no pages for the reason it was
        # asked for, which is not the same as a file with nothing in it. And
        # the directory is half a chapter by design, so what is not in it yet
        # is nobody else's.
        return UnpackReport(
            source=source,
            directory=directory,
            pages=tuple(pages),
            reused=reused,
            cancelled=True,
            skipped=tuple(notes.skipped),
            doubtful=tuple(notes.doubtful),
        )

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
    "PDF",
    "PDF_SUFFIXES",
    "RAR",
    "RAR_SUFFIXES",
    "UNRAR_ENV",
    "ZIP",
    "ZIP_SUFFIXES",
    "UnpackReport",
    "chapter_kind",
    "check_readable",
    "default_unpack_dir",
    "is_container",
    "unpack",
]
