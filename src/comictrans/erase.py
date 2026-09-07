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


def glyph_mask(rgb: RgbArray, region: Region, cfg: EraseConfig, *, page_height: int) -> MaskArray:
    """Mask of the original lettering inside a region.

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
    return cast("MaskArray", cv2.bitwise_and(mask, inside))


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
    strategy.name: strategy for strategy in (FlatFill(), PolygonFill(), InpaintFill())
}


def get_strategy(name: str) -> FillStrategy:
    try:
        return STRATEGIES[name]
    except KeyError:
        raise ValueError(
            f"unknown erase strategy {name!r}; expected one of {', '.join(sorted(STRATEGIES))}"
        ) from None


def erase(rgb: RgbArray, region: Region, cfg: EraseConfig, *, page_height: int) -> RgbArray:
    """Remove a region's original lettering, leaving the rest of the page alone."""
    strategy = get_strategy(cfg.strategy)
    mask = glyph_mask(rgb, region, cfg, page_height=page_height)
    if int(np.count_nonzero(mask)) == 0 and strategy.name != "polygon":
        log.debug("region %s: nothing matched the recorded text colour", region.id)
    return strategy.fill(rgb, mask, region, cfg)
