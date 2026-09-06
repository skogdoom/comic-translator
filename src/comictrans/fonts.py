"""System font resolution.

No font file ships with this tool. Everything is resolved from the system at
run time, from the standard macOS locations (extra directories can be added
via ``COMICTRANS_FONT_PATH``, which is also how the tests run off a Mac).

Two rules from the spec drive the shape of this module:

* Regular *and* bold must both resolve. A face with no real bold is reported,
  never synthesised — Pillow will happily fake a bold by stroking glyphs and
  that is exactly the silent substitution we refuse to make.
* Emphasis is bold, never italic. Oblique and italic faces are filtered out
  during face selection so they can never be picked by accident.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import ImageFont

from .errors import FontError
from .util import slugify

log = logging.getLogger(__name__)

DEFAULT_FAMILY = "Comic Sans MS"

FALLBACK_CHAIN: tuple[str, ...] = (
    DEFAULT_FAMILY,
    "Chalkboard SE",
    "Marker Felt",
    "Noteworthy",
    "Helvetica",
)
"""Tried in order when no font was explicitly requested."""

SEARCH_DIRS: tuple[Path, ...] = (
    Path("/System/Library/Fonts/Supplemental"),
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
)

FONT_PATH_ENV = "COMICTRANS_FONT_PATH"

FONT_SUFFIXES = (".ttf", ".ttc", ".otf", ".otc")

# Filenames Apple actually ships, in preference order. The generic
# slug-matching search below covers anything not listed here.
KNOWN_FILES: dict[str, tuple[str, ...]] = {
    "comic sans ms": ("Comic Sans MS.ttf", "Comic Sans MS Bold.ttf"),
    "chalkboard se": ("ChalkboardSE.ttc",),
    "marker felt": ("MarkerFelt.ttc",),
    "noteworthy": ("Noteworthy.ttc",),
    "helvetica": ("Helvetica.ttc", "Helvetica.dfont"),
}

_ITALIC_WORDS = ("italic", "oblique")
_BOLD_WORDS = ("bold", "heavy", "black")
_REGULAR_STYLES = ("regular", "roman", "book", "plain", "medium", "normal")


@dataclass(frozen=True, slots=True)
class FontFile:
    """One face inside a font file. ``index`` selects a face within a .ttc."""

    path: Path
    index: int
    style: str

    def load(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(self.path), size=size, index=self.index)


@dataclass(frozen=True, slots=True)
class FontFace:
    """A resolved family: the regular face, and its real bold if one exists."""

    family: str
    """The name as requested, which is what gets written to the plan file."""

    regular: FontFile
    bold: FontFile | None

    @property
    def has_bold(self) -> bool:
        return self.bold is not None


def search_dirs() -> tuple[Path, ...]:
    """Font directories, ``COMICTRANS_FONT_PATH`` entries first."""
    extra = os.environ.get(FONT_PATH_ENV, "")
    prefix = tuple(Path(p) for p in extra.split(os.pathsep) if p)
    return prefix + SEARCH_DIRS


def _candidate_files(family: str) -> list[Path]:
    """Font files that might contain ``family``, best guess first."""
    slug = slugify(family)
    dirs = [d for d in search_dirs() if d.is_dir()]
    named: list[Path] = []
    for filename in KNOWN_FILES.get(family.strip().lower(), ()):
        named.extend(d / filename for d in dirs if (d / filename).is_file())

    scanned: list[Path] = []
    for directory in dirs:
        for entry in sorted(directory.iterdir()):
            if entry.suffix.lower() not in FONT_SUFFIXES or not entry.is_file():
                continue
            if slugify(entry.stem).startswith(slug):
                scanned.append(entry)

    ordered: list[Path] = []
    for path in [*named, *scanned]:
        if path not in ordered:
            ordered.append(path)
    return ordered


def _faces(path: Path) -> list[tuple[int, str, str]]:
    """Enumerate ``(index, family, style)`` for every face in a font file."""
    found: list[tuple[int, str, str]] = []
    for index in range(32):  # .ttc collections on macOS top out well below this
        try:
            font = ImageFont.truetype(str(path), size=10, index=index)
            name, style = font.getname()
        except OSError:
            break
        found.append((index, name or path.stem, style or "Regular"))
    return found


def _is_italic(style: str) -> bool:
    lowered = style.lower()
    return any(word in lowered for word in _ITALIC_WORDS)


def _is_bold(style: str) -> bool:
    lowered = style.lower()
    return any(word in lowered for word in _BOLD_WORDS)


def _pick_faces(family: str, paths: list[Path]) -> tuple[FontFile | None, FontFile | None]:
    """Choose the regular and bold faces for ``family`` across candidate files."""
    slug = slugify(family)
    regular: FontFile | None = None
    bold: FontFile | None = None

    for path in paths:
        for index, face_family, style in _faces(path):
            matches_family = slugify(face_family).startswith(slug)
            if not matches_family and not slugify(path.stem).startswith(slug):
                continue
            if _is_italic(style):
                continue  # emphasis is bold, never italic
            candidate = FontFile(path=path, index=index, style=style)
            if _is_bold(style):
                if bold is None:
                    bold = candidate
            elif regular is None or (
                style.lower() in _REGULAR_STYLES and regular.style.lower() not in _REGULAR_STYLES
            ):
                regular = candidate
    return regular, bold


def resolve_family(family: str, *, require_bold: bool = True) -> FontFace:
    """Resolve one named family, or raise.

    A direct path to a font file is accepted too, which is how you use a font
    that lives somewhere non-standard.
    """
    path = Path(family).expanduser()
    if path.suffix.lower() in FONT_SUFFIXES and path.is_file():
        faces = _faces(path)
        if not faces:
            raise FontError(f"no usable faces in font file {path}")
        name = faces[0][1]
        regular, bold = _pick_faces(name, [path])
        if regular is None:
            regular = FontFile(path=path, index=faces[0][0], style=faces[0][2])
    else:
        candidates = _candidate_files(family)
        if not candidates:
            raise FontError(
                f"font {family!r} not found in {', '.join(str(d) for d in search_dirs())}"
            )
        regular, bold = _pick_faces(family, candidates)
        if regular is None:
            raise FontError(f"font {family!r}: no regular (non-italic) face found")

    if require_bold and bold is None:
        raise FontError(
            f"font {family!r} has no real bold face. Emphasis renders as bold and "
            "synthesising one is not allowed; pick another font or drop emphasis."
        )
    return FontFace(family=family, regular=regular, bold=bold)


def resolve_default(*, require_bold: bool = True) -> FontFace:
    """Walk the fallback chain and return the first family that fully resolves.

    Logs which font was actually used, and why each earlier one was passed over.
    """
    problems: list[str] = []
    for family in FALLBACK_CHAIN:
        try:
            face = resolve_family(family, require_bold=require_bold)
        except FontError as exc:
            problems.append(str(exc))
            log.info("font %r unavailable: %s", family, exc)
            continue
        if family != DEFAULT_FAMILY:
            log.warning("font %r unavailable; falling back to %r", DEFAULT_FAMILY, family)
        log.info("using font %r (%s)", face.family, face.regular.path)
        return face

    detail = "\n  ".join(problems)
    raise FontError(
        f"none of the fallback fonts could be resolved ({', '.join(FALLBACK_CHAIN)}):\n  {detail}"
    )


def resolve(family: str | None, *, require_bold: bool = True) -> FontFace:
    """Resolve ``family``, or the fallback chain when it is ``None``.

    An explicitly named font is never silently substituted: if it will not
    resolve, that is an error, not a reason to try the next one.
    """
    if family is None:
        return resolve_default(require_bold=require_bold)
    face = resolve_family(family, require_bold=require_bold)
    log.info("using font %r (%s)", face.family, face.regular.path)
    return face
