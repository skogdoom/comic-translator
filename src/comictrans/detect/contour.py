"""Finding the balloon that encloses a run of text.

Vision gives axis-aligned text boxes. Balloons are round. Erasing or
typesetting against a box eats the balloon outline and overflows the corners,
so the box is only ever used as a seed: the region's real geometry is the
enclosing contour found here.

Both threshold polarities are searched, so a white-on-black caption is found
the same way a black-on-white balloon is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray

from ..config import DetectConfig
from ..model import Box, Polygon

GrayArray = NDArray[np.uint8]


# eq=False: the raw contour is a numpy array, and dataclass equality on it
# returns an array rather than a bool. Candidates are compared by identity.
@dataclass(frozen=True, slots=True, eq=False)
class ContourCandidate:
    """A blob that might be a balloon.

    ``points`` is the raw contour, kept for exact containment tests;
    ``polygon`` is the simplified version that ends up in the plan file.
    """

    points: NDArray[np.int32]
    polygon: Polygon
    area: float
    inverted: bool
    """True when found on the inverted threshold, i.e. a dark balloon."""

    def contains_box(self, box: Box) -> bool:
        """True when every corner of ``box`` is inside the contour."""
        return all(
            cv2.pointPolygonTest(self.points, (float(x), float(y)), False) >= 0
            for x, y in box.corners()
        )


def binarise(gray: GrayArray, cfg: DetectConfig, *, inverted: bool) -> GrayArray:
    """Otsu threshold plus a morphological close.

    The close is what bridges the gaps halftone screening and JPEG ringing
    punch in a balloon outline; without it a screentoned page produces
    confetti instead of contours.
    """
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    mode = cv2.THRESH_BINARY_INV if inverted else cv2.THRESH_BINARY
    _, binary = cv2.threshold(blurred, 0, 255, mode + cv2.THRESH_OTSU)

    size = max(3, round(cfg.close_kernel_ratio * gray.shape[0]) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    return cast(GrayArray, cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel))


def _simplify(contour: NDArray[np.int32], cfg: DetectConfig) -> Polygon:
    """approxPolyDP down to a hand-editable number of integer pixel vertices."""
    perimeter = float(cv2.arcLength(contour, True))
    epsilon = cfg.approx_epsilon_ratio * perimeter
    approx: NDArray[np.int32] = contour
    for _ in range(8):
        approx = cast("NDArray[np.int32]", cv2.approxPolyDP(contour, epsilon, True))
        if len(approx) <= cfg.max_polygon_points:
            break
        epsilon *= 1.6
    return tuple((int(point[0][0]), int(point[0][1])) for point in approx)


def build_candidates(gray: GrayArray, cfg: DetectConfig) -> list[ContourCandidate]:
    """Every blob on the page that could plausibly be a balloon or caption box.

    Filtered by area (a contour covering a quarter of the page is a panel, not
    a balloon) and by solidity (balloons are convex-ish; artwork is not).
    """
    page_area = float(gray.shape[0] * gray.shape[1])
    max_area = page_area * cfg.max_contour_area_ratio
    candidates: list[ContourCandidate] = []

    for inverted in (False, True):
        binary = binarise(gray, cfg, inverted=inverted)
        contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            if len(contour) < 3:
                continue
            area = float(cv2.contourArea(contour))
            if area <= 0.0 or area > max_area:
                continue
            hull_area = float(cv2.contourArea(cv2.convexHull(contour)))
            if hull_area <= 0.0 or area / hull_area < cfg.min_solidity:
                continue
            polygon = _simplify(cast("NDArray[np.int32]", contour), cfg)
            if len(polygon) < 3:
                continue
            candidates.append(
                ContourCandidate(
                    points=contour.astype(np.int32),
                    polygon=polygon,
                    area=area,
                    inverted=inverted,
                )
            )

    # Smallest first: the tightest contour containing the text is the balloon,
    # anything larger is the panel it sits in.
    candidates.sort(key=lambda candidate: candidate.area)
    return candidates


def enclosing_candidate(
    candidates: list[ContourCandidate], box: Box, cfg: DetectConfig
) -> int | None:
    """Index of the tightest candidate that properly encloses ``box``.

    ``min_contour_area_slack`` rejects a contour that merely traces the text
    itself — a filled caption blob rather than a balloon around it. An index
    is returned rather than the candidate so callers can group lines by it.
    """
    minimum_area = box.area * cfg.min_contour_area_slack
    for index, candidate in enumerate(candidates):  # already sorted smallest first
        if candidate.area < minimum_area:
            continue
        if candidate.contains_box(box):
            return index
    return None
