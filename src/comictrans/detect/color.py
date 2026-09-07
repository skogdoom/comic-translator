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
from ..model import Box, Color, Polygon, polygon_bounds

MaskArray = NDArray[np.uint8]

_MIN_SAMPLE_PIXELS = 12

_MIN_CONTRAST = 32.0
"""Minimum luminance gap between a region's fill and text colours. Below this
the two are indistinguishable and apply would draw invisible text."""


def luminance(color: Color) -> float:
    """Rec. 601 luma, 0-255."""
    return 0.299 * color.r + 0.587 * color.g + 0.114 * color.b


def _luminance_gap(a: Color, b: Color) -> float:
    return abs(luminance(a) - luminance(b))


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


def interior_uniformity(
    rgb: RgbArray, polygon: Polygon, *, page_height: int, tolerance: float
) -> float:
    """How single-coloured a polygon's interior is, from 0.0 to 1.0.

    A speech balloon is a flat fill; a comic panel is artwork. Every other
    guard in detection is a fraction of the page, so on a page of six small
    panels a whole panel passes them all. This one does not care how big the
    page is.

    Measured inside the polygon's bounding box rather than across the page: a
    screentoned page offers thousands of candidates, and a full-page mask and
    erosion for each of them costs seconds. The outline is eroded away first,
    so a balloon's own ink border does not count against its interior.
    """
    bounds = polygon_bounds(polygon)
    left, top = max(0, bounds.left), max(0, bounds.top)
    right = min(rgb.shape[1], bounds.right)
    bottom = min(rgb.shape[0], bounds.bottom)
    if right <= left or bottom <= top:
        return 0.0

    window = rgb[top:bottom, left:right]
    local = tuple((x - left, y - top) for x, y in polygon)
    region = polygon_mask(local, window.shape[0], window.shape[1])

    erode_size = max(3, round(page_height * 0.004))
    interior = cv2.erode(region, _kernel(erode_size))
    if int(np.count_nonzero(interior)) < _MIN_SAMPLE_PIXELS:
        interior = region

    pixels = window[interior > 0].astype(np.float32)
    if len(pixels) < _MIN_SAMPLE_PIXELS:
        return 0.0
    median = np.median(pixels, axis=0)
    distance = np.linalg.norm(pixels - median, axis=1)
    return float((distance <= tolerance).mean())


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
    if text is None or _luminance_gap(fill, text) < _MIN_CONTRAST:
        # Either no usable glyph sample, or one so close to the fill that
        # apply would render the translation invisible. Fall back to whichever
        # end of the scale contrasts; the value is in the plan file to edit.
        text = Color(0, 0, 0) if luminance(fill) >= 128 else Color(255, 255, 255)
    return fill, text
