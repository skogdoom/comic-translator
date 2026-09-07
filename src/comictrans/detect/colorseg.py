"""Finding a region by colour when a grey threshold cannot.

Contour search binarises the page on luminance, which fails whenever a
balloon or caption box differs from what surrounds it in hue but not in
brightness. Two measured cases: a caption box of flat tan sitting on artwork
that a global Otsu puts on the same side of the threshold, and a white
balloon over near-white art where the luma gap is 17 but the colour gap is
much wider.

This runs only for text that no contour claimed, and only around that text.
The seed is the text's own background colour — the most common colour inside
its box, which is the fill it is lettered on — so nothing has to be guessed
about what a balloon looks like.
"""

from __future__ import annotations

import logging
from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray

from ..config import DetectConfig
from ..imaging import RgbArray
from ..model import Box, Polygon, polygon_bounds, polygon_is_simple

log = logging.getLogger(__name__)

MaskArray = NDArray[np.uint8]


def _window(box: Box, cfg: DetectConfig, width: int, height: int) -> Box:
    """A generous but bounded search area around a line of text."""
    margin_x = int(box.width * cfg.color_window_ratio)
    margin_y = int(box.height * cfg.color_window_ratio)
    return Box(
        max(0, box.left - margin_x),
        max(0, box.top - margin_y),
        min(width, box.right + margin_x),
        min(height, box.bottom + margin_y),
    )


def background_color(rgb: RgbArray, box: Box) -> NDArray[np.float32] | None:
    """The most common colour inside a text box: the fill it is lettered on.

    Ink is the minority of a line's box, so the mode is the background. Taking
    the mode rather than the mean keeps a stray dark pixel from dragging the
    seed off the fill colour.
    """
    patch = rgb[box.top : box.bottom, box.left : box.right]
    if patch.size == 0:
        return None
    colors, counts = np.unique(patch.reshape(-1, 3), axis=0, return_counts=True)
    return cast("NDArray[np.float32]", colors[counts.argmax()].astype(np.float32))


def segment(rgb: RgbArray, box: Box, cfg: DetectConfig, *, page_height: int) -> Polygon | None:
    """Trace the flat-coloured region a line of text sits on, if there is one.

    Returns None when the region that comes back is not plausibly a balloon —
    too big, spanning the page, or not actually one colour. A caption box
    flush against a panel frame merges with it and is refused here, which is
    the honest outcome: there is no boundary between them to find.
    """
    height, width = rgb.shape[0], rgb.shape[1]
    seed = background_color(rgb, box)
    if seed is None:
        return None

    window = _window(box, cfg, width, height)
    crop = rgb[window.top : window.bottom, window.left : window.right]
    distance = np.linalg.norm(crop.astype(np.float32) - seed, axis=2)
    mask = cast("MaskArray", (distance <= cfg.color_tolerance).astype(np.uint8) * 255)

    size = max(3, round(cfg.close_kernel_ratio * page_height) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    # Closing bridges the gaps in a dashed outline and the antialiased fringe
    # where the fill meets its own border.
    mask = cast("MaskArray", cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel))

    component = _component_under(mask, box, window)
    if component is None:
        return None

    contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    area = float(cv2.contourArea(contour))

    epsilon = cfg.approx_epsilon_ratio * float(cv2.arcLength(contour, True))
    approx = cv2.approxPolyDP(contour, epsilon, True)
    polygon = tuple(
        (int(point[0][0]) + window.left, int(point[0][1]) + window.top) for point in approx
    )
    if len(polygon) < 3 or not polygon_is_simple(polygon):
        return None
    if not _plausible(polygon, area, box, cfg, width=width, height=height):
        return None
    return polygon


def _component_under(mask: MaskArray, box: Box, window: Box) -> MaskArray | None:
    """The connected component covering most of the text box.

    Not the one under the box's centre: that pixel is as likely to be a glyph
    as background, and on a caption box of dark fill with light lettering it
    reliably is.
    """
    count, labels = cv2.connectedComponents(mask)
    if count <= 1:
        return None
    inside = labels[
        box.top - window.top : box.bottom - window.top,
        box.left - window.left : box.right - window.left,
    ]
    covered = inside[inside > 0]
    if covered.size == 0:
        return None
    ids, counts = np.unique(covered, return_counts=True)
    return cast("MaskArray", (labels == ids[counts.argmax()]).astype(np.uint8) * 255)


def _plausible(
    polygon: Polygon,
    area: float,
    box: Box,
    cfg: DetectConfig,
    *,
    width: int,
    height: int,
) -> bool:
    """The same size and shape guards a contour candidate has to pass."""
    if area < box.area * cfg.min_contour_area_slack:
        return False
    if area > width * height * cfg.max_contour_area_ratio:
        return False
    bounds = polygon_bounds(polygon)
    return not (
        bounds.width > width * cfg.max_extent_ratio or bounds.height > height * cfg.max_extent_ratio
    )
