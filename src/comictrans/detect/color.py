"""Sampling a region's fill and text colours.

Never assume black on white. A white-on-black caption has to round-trip, so
both colours are measured from the pixels and stored in the plan file; the
apply pass then needs no access to the original colours at all.
"""

from __future__ import annotations

from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray

from ..imaging import RgbArray
from ..model import Box, Color, Polygon

MaskArray = NDArray[np.uint8]

_MIN_SAMPLE_PIXELS = 12


def polygon_mask(polygon: Polygon, height: int, width: int) -> MaskArray:
    """Filled 0/255 mask of a polygon."""
    mask: MaskArray = np.zeros((height, width), dtype=np.uint8)
    points = [np.array(polygon, dtype=np.int32)]
    cv2.fillPoly(mask, points, 255)
    return mask


def _kernel(size: int) -> NDArray[np.uint8]:
    odd = max(3, size | 1)
    return cast("NDArray[np.uint8]", cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (odd, odd)))


def glyph_mask(rgb: RgbArray, region_mask: MaskArray, boxes: tuple[Box, ...]) -> MaskArray:
    """Mask of the glyph pixels inside a region.

    Otsu splits the text boxes into two classes; the smaller one is the ink.
    That is what makes this work for light text on a dark balloon as well as
    the usual way round.
    """
    height, width = region_mask.shape
    text_area: MaskArray = np.zeros((height, width), dtype=np.uint8)
    for box in boxes:
        clipped = box.clipped(width, height)
        if clipped.width <= 0 or clipped.height <= 0:
            continue
        text_area[clipped.top : clipped.bottom, clipped.left : clipped.right] = 255
    text_area = cast("MaskArray", cv2.bitwise_and(text_area, region_mask))

    if int(np.count_nonzero(text_area)) < _MIN_SAMPLE_PIXELS:
        return text_area

    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    values = gray[text_area > 0]
    threshold, _ = cv2.threshold(values, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    dark = cast("MaskArray", cv2.bitwise_and((gray <= threshold).astype(np.uint8) * 255, text_area))
    light = cast("MaskArray", cv2.bitwise_and((gray > threshold).astype(np.uint8) * 255, text_area))
    # Ink is the minority class within a line of text.
    return dark if int(np.count_nonzero(dark)) <= int(np.count_nonzero(light)) else light


def _median_color(rgb: RgbArray, mask: MaskArray) -> Color | None:
    if int(np.count_nonzero(mask)) < _MIN_SAMPLE_PIXELS:
        return None
    pixels = rgb[mask > 0]
    median = np.median(pixels.astype(np.float64), axis=0)
    return Color.from_rgb((int(median[0]), int(median[1]), int(median[2])))


def sample_colors(
    rgb: RgbArray, polygon: Polygon, boxes: tuple[Box, ...], *, page_height: int
) -> tuple[Color, Color]:
    """Return ``(fill_color, text_color)`` for a region.

    The fill sample is taken from the balloon interior with the outline eroded
    away and the glyphs (plus their antialiased fringe) dilated out, so a
    stroke or a drop shadow does not drag the fill colour grey.
    """
    height, width = rgb.shape[0], rgb.shape[1]
    region = polygon_mask(polygon, height, width)
    erode_size = max(3, round(page_height * 0.003))
    interior = cast("MaskArray", cv2.erode(region, _kernel(erode_size)))
    if int(np.count_nonzero(interior)) < _MIN_SAMPLE_PIXELS:
        interior = region

    glyphs = glyph_mask(rgb, region, boxes)
    fringe = cv2.dilate(glyphs, _kernel(max(3, erode_size)))
    background = cast("MaskArray", cv2.bitwise_and(interior, cv2.bitwise_not(fringe)))

    fill = _median_color(rgb, background) or _median_color(rgb, interior)
    text = _median_color(rgb, glyphs)

    if fill is None:
        fill = Color(255, 255, 255)
    if text is None:
        # No usable glyph sample: pick whichever end of the scale contrasts.
        luminance = 0.299 * fill.r + 0.587 * fill.g + 0.114 * fill.b
        text = Color(0, 0, 0) if luminance >= 128 else Color(255, 255, 255)
    return fill, text
