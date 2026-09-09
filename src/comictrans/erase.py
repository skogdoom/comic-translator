"""Removing the original lettering.

Everything here works from the plan file alone: the polygon, ``text_color``
and ``fill_color`` were all measured at extract time, so erase re-runs neither
detection nor OCR and gives the same result every time.

Glyph pixels are found by colour distance to ``text_color``, judged relative
to how far that sits from ``fill_color``. That ratio is scale-free, so a
white-on-black caption masks exactly as well as black-on-white.
"""

from __future__ import annotations

import logging
from typing import Protocol, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from .config import EraseConfig
from .imaging import RgbArray
from .model import Color, Region

log = logging.getLogger(__name__)

MaskArray = NDArray[np.uint8]


class FillStrategy(Protocol):
    """How masked pixels get repainted.

    The hook the spec asks for: a cheap flat fill covers plain balloons, and
    anything heavier for textured art or borderless captions plugs in here
    without touching the masking or the caller.
    """

    name: str

    def fill(
        self, rgb: RgbArray, mask: MaskArray, region: Region, cfg: EraseConfig
    ) -> RgbArray: ...


def polygon_mask(region: Region, height: int, width: int) -> MaskArray:
    mask: MaskArray = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [np.array(region.polygon, dtype=np.int32)], 255)
    return mask


def _distance(rgb: RgbArray, color: Color) -> NDArray[np.float32]:
    target = np.array(color.as_tuple(), dtype=np.float32)
    return cast(
        "NDArray[np.float32]",
        np.linalg.norm(rgb.astype(np.float32) - target, axis=2),
    )


def _ground_mask(
    rgb: RgbArray, region: Region, separation: float, cfg: EraseConfig, *, page_height: int
) -> MaskArray:
    """Where the region's own flat background is, with the lettering filled in.

    A pixel belongs to the ground when it is nearer ``fill_color`` than
    ``glyph_threshold_ratio`` of the way to ``text_color`` — the same
    scale-free test the ink mask uses, read from the other end. Lettering
    leaves holes, and closing by more than a stroke width fills them back in,
    so what comes out is the flat area *including* the text sitting on it.

    What it excludes is the balloon's own ink outline, which is the point. The
    polygon can reach past that outline: a polygon is grown until it covers
    every line of text assigned to it, and it grows by union with the lines'
    bounding boxes, whose corners stick out beyond the glyphs they were added
    for. Where such a corner crosses the outline, a mask bounded only by the
    polygon treats the outline as lettering and the flat fill repaints it,
    cutting a notch out of the balloon.
    """
    near_fill = _distance(rgb, region.fill_color) < separation * cfg.glyph_threshold_ratio
    ground = near_fill.astype(np.uint8) * 255
    size = max(3, round(cfg.ground_close_ratio * page_height) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    return cast("MaskArray", cv2.morphologyEx(ground, cv2.MORPH_CLOSE, kernel))


def glyph_mask(rgb: RgbArray, region: Region, cfg: EraseConfig, *, page_height: int) -> MaskArray:
    """Mask of the original lettering inside a region.

    Bounded by two things, not one: the region's polygon, and the flat ground
    the lettering sits on. The polygon alone is not enough — see
    :func:`_ground_mask`.

    Dilated by ``dilate_ratio`` to take the antialiased fringe with it — a
    pure colour test leaves a halo of half-ink pixels that reads as a ghost of
    the old lettering once the new text is drawn over it.
    """
    height, width = rgb.shape[0], rgb.shape[1]
    inside = polygon_mask(region, height, width)

    separation = float(
        np.linalg.norm(
            np.array(region.text_color.as_tuple(), dtype=np.float32)
            - np.array(region.fill_color.as_tuple(), dtype=np.float32)
        )
    )
    if separation <= 0.0:
        # Extract guarantees the two colours differ; belt and braces.
        return np.zeros((height, width), dtype=np.uint8)

    ink = _distance(rgb, region.text_color) < separation * cfg.glyph_threshold_ratio
    mask = cv2.bitwise_and((ink.astype(np.uint8) * 255), inside)

    grow = round(cfg.dilate_ratio * page_height)
    if grow >= 1:
        size = max(3, (grow * 2 + 1) | 1)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        mask = cv2.dilate(mask, kernel)
    mask = cv2.bitwise_and(mask, inside)

    # A floor, not a judgement call. Closing the lettering back into the
    # ground needs a kernel wider than a glyph's stroke; if it is not, the
    # lettering stays a hole, the ground excludes all of it, and the region
    # would come back unerased with the translation drawn over the top. That
    # is a worse failure than the notched outline this guards against, so a
    # result this empty means the ground could not be read and the mask
    # stands unconstrained.
    #
    # Measured over the thirteen fixtures the constraint keeps 95% of a
    # region's mask, and half of it where a polygon steps out over the
    # outline. A ground that failed to close keeps none at all.
    ground = _ground_mask(rgb, region, separation, cfg, page_height=page_height)
    constrained = cast("MaskArray", cv2.bitwise_and(mask, ground))
    if int(np.count_nonzero(constrained)) < int(np.count_nonzero(mask)) // 20:
        log.debug("%s: ground could not be read; erasing without it", region.id)
        return cast("MaskArray", mask)
    return constrained


class FlatFill:
    """Repaint masked pixels with the region's recorded fill colour."""

    name = "flat"

    def fill(self, rgb: RgbArray, mask: MaskArray, region: Region, cfg: EraseConfig) -> RgbArray:
        out = rgb.copy()
        out[mask > 0] = np.array(region.fill_color.as_tuple(), dtype=np.uint8)
        return out


class PolygonFill:
    """Flood the whole polygon interior with the fill colour.

    Guaranteed to leave no trace of the old lettering, and the right choice
    for a plain balloon. It also destroys anything else inside the polygon, so
    it is wrong for a region flagged ``geometry: approximate``.
    """

    name = "polygon"

    def fill(self, rgb: RgbArray, mask: MaskArray, region: Region, cfg: EraseConfig) -> RgbArray:
        out = rgb.copy()
        interior = polygon_mask(region, rgb.shape[0], rgb.shape[1])
        out[interior > 0] = np.array(region.fill_color.as_tuple(), dtype=np.uint8)
        return out


class NoFill:
    """Paint nothing at all: the translation goes straight onto the page.

    For a region whose background is the artwork and has to stay that way — a
    sound effect, a caption lettered over a panel. There is nothing to
    reconstruct and nothing to flatten, so ``fill_color`` goes unused.
    """

    name = "none"

    def fill(self, rgb: RgbArray, mask: MaskArray, region: Region, cfg: EraseConfig) -> RgbArray:
        return rgb.copy()


class InpaintFill:
    """Reconstruct masked pixels from their surroundings.

    For textured balloons and borderless captions, where a flat patch of
    ``fill_color`` would read as a hole in the artwork.
    """

    name = "inpaint"

    def fill(self, rgb: RgbArray, mask: MaskArray, region: Region, cfg: EraseConfig) -> RgbArray:
        radius = max(1, round(cfg.inpaint_radius_ratio * rgb.shape[0]))
        return cast(
            "RgbArray",
            cv2.inpaint(rgb.copy(), mask, radius, cv2.INPAINT_TELEA),
        )


STRATEGIES: dict[str, FillStrategy] = {
    strategy.name: strategy for strategy in (FlatFill(), PolygonFill(), InpaintFill(), NoFill())
}

_NEEDS_NO_MASK = frozenset({PolygonFill.name, NoFill.name})
"""Strategies that ignore the glyph mask, so nothing is amiss when it is empty."""


def get_strategy(name: str) -> FillStrategy:
    try:
        return STRATEGIES[name]
    except KeyError:
        raise ValueError(
            f"unknown erase strategy {name!r}; expected one of {', '.join(sorted(STRATEGIES))}"
        ) from None


def erase(rgb: RgbArray, region: Region, cfg: EraseConfig, *, page_height: int) -> RgbArray:
    """Remove a region's original lettering, leaving the rest of the page alone.

    The region's own ``erase`` wins over the run's ``--erase`` flag, the same
    way its ``font`` wins over the header's: which of these is right is a fact
    about one balloon — a flat one, a textured one, a sound effect that must
    keep the art behind it — not about a chapter.
    """
    strategy = get_strategy(str(region.erase) if region.erase is not None else cfg.strategy)
    if strategy.name == NoFill.name:
        return rgb.copy()  # no mask to compute; nothing is painted
    mask = glyph_mask(rgb, region, cfg, page_height=page_height)
    if int(np.count_nonzero(mask)) == 0 and strategy.name not in _NEEDS_NO_MASK:
        log.debug("region %s: nothing matched the recorded text colour", region.id)
    return strategy.fill(rgb, mask, region, cfg)
