"""Image input.

Source images are read-only, always. Every open here goes through a binary
file handle opened ``"rb"``; nothing in this module writes, moves, or touches
the mtime of a source file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image, UnidentifiedImageError

from .errors import InputError
from .util import natural_key, sha256_file

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff"})
"""Accepted source extensions, lowercased. Anything else is skipped and logged."""

RgbArray = NDArray[np.uint8]
MaskArray = NDArray[np.uint8]


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
    """H x W x 3, uint8, with any transparency already flattened onto white."""

    sha256: str
    meta: PageMeta
    alpha: MaskArray | None = None
    """The source's alpha channel, kept so output can be written back with the
    same transparency rather than silently becoming opaque."""

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
            alpha = (
                np.asarray(image.convert("RGBA").getchannel("A"), dtype=np.uint8)
                if had_alpha
                else None
            )
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
    return PageImage(path=path, rgb=rgb, sha256=sha256_file(path), meta=meta, alpha=alpha)


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


UNSUPPORTED_MODES: frozenset[str] = frozenset({"I", "I;16", "I;16B", "I;16L", "F", "CMYK", "YCbCr"})
"""Modes that cannot round-trip through 8-bit RGB without losing information.

Refused outright rather than silently downconverted: a 16-bit or CMYK scan
quietly rewritten as 8-bit sRGB is exactly the kind of damage the read-only
rule exists to prevent, and it would not be visible until print.
"""

JPEG_FORMATS: frozenset[str] = frozenset({"JPEG", "JPG", "MPO"})

_FORMAT_SUFFIX = {"JPEG": ".jpg", "PNG": ".png", "TIFF": ".tif"}
_SUFFIX_FORMAT = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".tif": "TIFF",
    ".tiff": "TIFF",
}


def output_format_for(meta: PageMeta, override: str | None = None) -> str:
    """Which format an output page is written in.

    Matches the source, except that JPEG sources become PNG: re-encoding a
    lossy source after repainting part of it would add a second generation of
    artefacts to artwork that is not being changed at all.
    """
    if override is not None:
        resolved = override.strip().upper()
        if resolved == "JPG":
            resolved = "JPEG"
        if resolved not in _FORMAT_SUFFIX:
            raise InputError(
                f"unsupported output format {override!r}; expected one of "
                f"{', '.join(sorted(_FORMAT_SUFFIX))}"
            )
        return resolved
    if meta.format.upper() in JPEG_FORMATS:
        return "PNG"
    return _SUFFIX_FORMAT.get(f".{meta.format.lower()}", meta.format.upper())


def output_path(source: Path, out_dir: Path, meta: PageMeta, override: str | None = None) -> Path:
    """Source filename mirrored into ``out_dir``, flat, with the right suffix."""
    image_format = output_format_for(meta, override)
    return out_dir / f"{source.stem}{_FORMAT_SUFFIX[image_format]}"


def check_writable(meta: PageMeta, path: Path) -> None:
    """Refuse a source whose pixels cannot be preserved."""
    if meta.mode in UNSUPPORTED_MODES:
        raise InputError(
            f"{path.name}: {meta.mode} images are not supported. Rewriting one "
            "as 8-bit RGB would silently lose precision; convert it yourself "
            "first if that is what you want."
        )


ALPHA_FORMATS: frozenset[str] = frozenset({"PNG", "TIFF"})


def save_page(
    image: Image.Image,
    path: Path,
    meta: PageMeta,
    override: str | None = None,
    alpha: MaskArray | None = None,
) -> None:
    """Write an output page, carrying the source's metadata across.

    DPI, the ICC profile and the source's transparency are all preserved.
    Rendering happens on RGB with any alpha flattened onto white, so without
    reattaching it here a transparent page would come back silently opaque —
    a change to artwork nobody asked to have changed.
    """
    image_format = output_format_for(meta, override)
    params: dict[str, object] = {}
    if meta.dpi is not None:
        params["dpi"] = meta.dpi
    if meta.icc_profile is not None:
        params["icc_profile"] = meta.icc_profile
    if image_format == "JPEG":
        params["quality"] = 95
        params["subsampling"] = 0

    out = image
    if alpha is not None:
        if image_format in ALPHA_FORMATS:
            out = out.convert("RGBA")
            out.putalpha(Image.fromarray(alpha, mode="L"))
        else:
            log.warning(
                "%s: %s cannot store transparency; writing %s opaque",
                path.name,
                image_format,
                path.name,
            )
    if meta.mode in {"L", "LA"} and image_format != "JPEG":
        # A greyscale scan stays greyscale: the sampled colours came from the
        # page, so nothing is lost, and the file does not triple in size.
        out = out.convert("LA" if alpha is not None else "L")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        out.save(temporary, format=image_format, **params)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
