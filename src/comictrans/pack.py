"""A chapter written as one file: CBZ and CBR.

The mirror of :mod:`comictrans.sources`, and not quite its reflection. What
a chapter file *is* can be read out of its first bytes; what one is *to be*
cannot, because it does not exist yet. So an output is decided by its name —
``chapter.cbz`` is a zip, ``chapter.cbr`` is a RAR — and that is the whole
rule.

**The pages are named for the order they are read in, not for where they
came from.** A reader sorts entries by name, so the order somebody set by
dragging rows in the review window has to survive as a name: every entry
gets a zero-padded index in front of the page's own filename. Source names
that happen to sort correctly are luck, and a reordered chapter would
otherwise come out in the order it was scanned in.

**A cancelled run leaves no archive at all**, which is a different promise
from the directory case and a deliberate one. Cancelling a render into a
directory leaves whole pages and re-running finishes the job; an archive has
no half-way state worth keeping, so the pages are rendered somewhere
temporary and packed only once every one of them is done. See ``apply_plan``.

**CBR needs a compressor this project cannot ship, which is not the same as
cannot use.** RAR compression is proprietary: ``unrar`` only reads, and its
licence forbids using it to create archives, so the only thing that writes a
``.rar`` is the ``rar`` binary from WinRAR — paid, and not redistributable.
What that licence restricts is *redistributing* the compressor; it says
nothing about somebody driving the copy they have already licensed. So the
tool is never bundled: it is looked for on ``PATH``, ``COMICTRANS_RAR``
names one that lives somewhere else, and with neither, CBR output is
unavailable and says which binary would provide it rather than quietly not
being offered.

That configured path is an executable this module then runs, which is worth
a hard look when the security audit comes: it is validated here only as far
as "something executable is there".
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import zipfile
from collections.abc import Sequence
from pathlib import Path

from .errors import InputError
from .sources import RAR, ZIP

log = logging.getLogger(__name__)

CBZ_SUFFIXES = frozenset({".cbz", ".zip"})
CBR_SUFFIXES = frozenset({".cbr", ".rar"})
ARCHIVE_SUFFIXES = CBZ_SUFFIXES | CBR_SUFFIXES
"""What ``--output`` has to be called for a chapter to come out as one file.
The plain extensions are here because a chapter is regularly saved under
them, the same way they are read."""

RAR_ENV = "COMICTRANS_RAR"
"""Names the RAR compressor when it is not on ``PATH``."""

RAR_MISSING = (
    "CBR output needs the rar compressor, which does not ship with comictrans: "
    "its licence is not one an MIT project can pass on, and unrar cannot write "
    "archives whatever it is asked to. Install the rar binary from WinRAR, which "
    f"you need a licence for, or set {RAR_ENV} to the one you have. CBZ needs "
    "nothing and every reader opens it."
)
"""Said when CBR is asked for and there is nothing to write it with. It names
the binary rather than leaving the format quietly missing."""


def archive_kind(path: Path) -> str | None:
    """``"zip"``, ``"rar"``, or ``None`` for an output that is a directory.

    By name, and only by name: an output file does not exist yet, so there
    are no bytes to ask. The reading side does the opposite — see
    :func:`comictrans.sources.chapter_kind` — because there the file is
    there and what it is called is only a claim.
    """
    suffix = path.suffix.lower()
    if suffix in CBZ_SUFFIXES:
        return ZIP
    if suffix in CBR_SUFFIXES:
        return RAR
    return None


def rar_compressor(named: str = "") -> str:
    """Where the RAR compressor is, or why there is not one.

    ``named`` is a caller that has been told — the window's Preferences, or
    ``COMICTRANS_RAR`` for the command line, which is the fallback. A path
    given here is checked for being there and runnable and no further; what
    it turns out to be when it runs is the audit's question.
    """
    configured = named.strip() or os.environ.get(RAR_ENV, "").strip()
    if configured:
        tool = Path(configured).expanduser()
        if not (tool.is_file() and os.access(tool, os.X_OK)):
            raise InputError(
                f"{configured} is not a program this can run — name the rar "
                "binary itself, not the folder it is in."
            )
        return str(tool)
    found = shutil.which("rar")
    if found is None:
        raise InputError(RAR_MISSING)
    return found


def check_writable(output: Path, rar_tool: str = "") -> None:
    """Raise unless an archive of this kind could be written, writing nothing.

    For asking before a render rather than after one: the window asks on
    every keystroke, and a chapter that takes minutes to render should not
    reach the end and then find there is nothing to pack it with.
    """
    if archive_kind(output) == RAR:
        rar_compressor(rar_tool)


def entry_names(pages: Sequence[Path]) -> tuple[str, ...]:
    """What each page is called inside the archive, in the order given.

    The index rather than the source name carries the order; the source name
    is kept after it so a page can still be recognised. Three digits, and
    four past a thousand pages — a reader sorts by name, and so does
    everything else that opens one.
    """
    return tuple(f"{index:03d}-{page.name}" for index, page in enumerate(pages, start=1))


def _pack_zip(pages: Sequence[Path], names: Sequence[str], into: Path) -> None:
    # Deflated rather than stored. Measured on the fixture pages: 29% off a
    # folder of PNGs and 56% off the same pages as JPEG, for a tenth of a
    # second on 3MB — though those pages are synthetic and flat, and a real
    # scan's noise will give up far less. The cost stays proportionally that
    # small either way, and every reader handles both.
    with zipfile.ZipFile(into, "w", zipfile.ZIP_DEFLATED) as archive:
        for page, name in zip(pages, names, strict=True):
            archive.write(page, name)


def _pack_rar(pages: Sequence[Path], names: Sequence[str], into: Path, rar_tool: str) -> None:
    tool = rar_compressor(rar_tool)
    folder = pages[0].parent
    # Run where the pages are and name them bare, so the archive holds names
    # rather than paths — with -ep as well, since a rar that disagrees about
    # the default would otherwise put a temporary directory inside somebody's
    # chapter. -o+ because the archive was already cleared or refused above.
    command = [tool, "a", "-ep", "-o+", str(into.resolve()), *names]
    log.info("packing %d page(s) with %s", len(names), tool)
    try:
        # The tool is the one the caller named, run with arguments this
        # module built: no shell, and nothing from the plan file in them.
        done = subprocess.run(command, cwd=folder, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise InputError(f"{tool} could not be run: {exc}") from exc
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        raise InputError(
            f"{Path(tool).name} could not write {into.name} "
            f"(it stopped with {done.returncode}): {detail[-1] if detail else 'no reason given'}"
        )


def pack(
    pages: Sequence[Path], into: Path, *, rar_tool: str = "", force: bool = False
) -> tuple[str, ...]:
    """Write every page into one archive, and say what they are called in it.

    The pages are taken in the order given, which is the order the plan
    names them in — the reading order, which is the thing being preserved.
    """
    kind = archive_kind(into)
    if kind is None:
        raise InputError(
            f"{into.name} is not an archive name; expected one of "
            f"{', '.join(sorted(ARCHIVE_SUFFIXES))}"
        )
    if not pages:
        raise InputError(f"there are no pages to write into {into.name}")
    if into.exists() and not force:
        raise InputError(f"{into} already exists; pass --force to overwrite it")
    if into.exists():
        # Cleared rather than written over: both writers add to an archive
        # that is already there, and a chapter half of whose pages are last
        # week's is worse than no chapter.
        into.unlink()
    into.parent.mkdir(parents=True, exist_ok=True)

    names = entry_names(pages)
    if kind == ZIP:
        _pack_zip(pages, names, into)
    else:
        _pack_rar(pages, names, into, rar_tool)
    log.info("wrote %d page(s) into %s", len(names), into)
    return names


__all__ = [
    "ARCHIVE_SUFFIXES",
    "CBR_SUFFIXES",
    "CBZ_SUFFIXES",
    "RAR_ENV",
    "RAR_MISSING",
    "archive_kind",
    "check_writable",
    "entry_names",
    "pack",
    "rar_compressor",
]
