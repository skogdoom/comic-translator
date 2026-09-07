"""Image input.

Source images are read-only, always. Every open here goes through a binary
file handle opened ``"rb"``; nothing in this module writes, moves, or touches
the mtime of a source file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image, UnidentifiedImageError

from .errors import InputError
from .util import natural_key, sha256_file

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff"})
"""Accepted source extensions, lowercased. Anything else is skipped and logged."""

RgbArray = NDArray[np.uint8]


@dataclass(frozen=True, slots=True)
class PageMeta:
    """Everything ``apply`` must carry over to the output file.

    Captured at read time so the apply pass never has to reopen the source for
    metadata it forgot to keep.
    """

    format: str
    mode: str
    dpi: tuple[float, float] | None
    icc_profile: bytes | None
    had_alpha: bool = False
    """The source carried transparency, which was flattened onto white."""


@dataclass(frozen=True, slots=True)
class PageImage:
    """A decoded source page."""

    path: Path
    rgb: RgbArray
    """H x W x 3, uint8."""

    sha256: str
    meta: PageMeta

    @property
    def height(self) -> int:
        return int(self.rgb.shape[0])

    @property
    def width(self) -> int:
        return int(self.rgb.shape[1])

    @property
    def area(self) -> int:
        return self.width * self.height


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in {"RGBA", "LA", "PA"} or "transparency" in image.info


def flatten_to_rgb(image: Image.Image) -> Image.Image:
    """Convert to RGB, compositing any transparency onto white.

    A transparent pixel's colour channels are undefined and in practice are
    often zero, so ``convert("RGB")`` alone turns whatever the artist left
    transparent into solid black. A black band across a page shifts the Otsu
    threshold and poisons colour sampling. Pages are paper: white is the
    right ground to flatten onto.
    """
    if not _has_alpha(image):
        return image.convert("RGB")
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    return Image.alpha_composite(background, rgba).convert("RGB")


def load_page(path: Path) -> PageImage:
    """Decode a source image read-only, capturing metadata for the apply pass."""
    try:
        with path.open("rb") as handle, Image.open(handle) as image:
            image_format = image.format or path.suffix.lstrip(".").upper()
            original_mode = image.mode
            dpi_raw = image.info.get("dpi")
            icc = image.info.get("icc_profile")
            had_alpha = _has_alpha(image)
            rgb = np.asarray(flatten_to_rgb(image), dtype=np.uint8)
    except (UnidentifiedImageError, OSError) as exc:
        raise InputError(f"cannot read image {path}: {exc}") from exc

    dpi: tuple[float, float] | None = None
    if isinstance(dpi_raw, tuple) and len(dpi_raw) == 2:
        try:
            dpi = (float(dpi_raw[0]), float(dpi_raw[1]))
        except (TypeError, ValueError):
            dpi = None

    meta = PageMeta(
        format=image_format,
        mode=original_mode,
        dpi=dpi,
        icc_profile=icc if isinstance(icc, bytes) else None,
        had_alpha=had_alpha,
    )
    return PageImage(path=path, rgb=rgb, sha256=sha256_file(path), meta=meta)


def collect_inputs(target: Path) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Resolve a file or directory into a sorted list of source images.

    Returns ``(accepted, skipped)`` where each skipped entry carries a reason.
    Directory scans are non-recursive and sorted by natural filename order, so
    ``page2`` comes before ``page10``.
    """
    if not target.exists():
        raise InputError(f"input path does not exist: {target}")

    if target.is_file():
        if target.suffix.lower() not in IMAGE_SUFFIXES:
            raise InputError(
                f"unsupported input file type {target.suffix!r}; "
                f"expected one of {', '.join(sorted(IMAGE_SUFFIXES))}"
            )
        return [target], []

    if not target.is_dir():
        raise InputError(f"input path is neither a file nor a directory: {target}")

    accepted: list[Path] = []
    skipped: list[tuple[Path, str]] = []
    for entry in sorted(target.iterdir(), key=lambda p: natural_key(p.name)):
        if entry.is_dir():
            skipped.append((entry, "directory (scan is non-recursive)"))
        elif entry.name.startswith("."):
            skipped.append((entry, "hidden file"))
        elif entry.suffix.lower() not in IMAGE_SUFFIXES:
            skipped.append((entry, f"unsupported extension {entry.suffix or '(none)'}"))
        else:
            accepted.append(entry)

    if not accepted:
        raise InputError(f"no supported images found in {target}")
    return accepted, skipped
