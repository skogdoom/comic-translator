"""Finding the balloon that encloses a run of text.

Vision gives axis-aligned text boxes. Balloons are round. Erasing or
typesetting against a box eats the balloon outline and overflows the corners,
so the box is only ever used as a seed: the region's real geometry is the
enclosing contour found here.

Both threshold polarities are searched, so a white-on-black caption is found
the same way a black-on-white balloon is.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray

from ..config import DetectConfig
from ..model import Box, Polygon, polygon_is_simple

GrayArray = NDArray[np.uint8]


# eq=False: the raw contour is a numpy array, and dataclass equality on it
# returns an array rather than a bool. Candidates are compared by identity.
@dataclass(frozen=True, slots=True, eq=False)
class ContourCandidate:
    """A blob that might be a balloon.

    Holds only what is cheap to compute for every contour on the page. The
    simplified polygon is built on demand by :meth:`polygon`, because a
    screentoned page produces thousands of candidates and at most a handful
    are ever chosen — simplifying all of them costs seconds per page.
    """

    points: NDArray[np.int32]
    bounds: Box
    area: float
    inverted: bool
    """True when found on the inverted threshold, i.e. a dark balloon."""

    def contains_box(self, box: Box, tolerance: float = 0.0) -> bool:
        """True when every corner of ``box`` is inside the contour.

        ``tolerance`` lets a corner sit that many pixels outside and still
        count, because lettering grazes the balloon outline and the polygon
        traces the interior within it.

        The bounding-box test first: a screentoned page yields thousands of
        candidates and pointPolygonTest is far too expensive to run on all of
        them when a coordinate comparison rejects most.
        """
        margin = int(tolerance) + 1
        if (
            box.left < self.bounds.left - margin
            or box.top < self.bounds.top - margin
            or box.right > self.bounds.right + margin
            or box.bottom > self.bounds.bottom + margin
        ):
            return False
        corners = box.corners()
        if all(
            cv2.pointPolygonTest(self.points, (float(x), float(y)), False) >= 0 for x, y in corners
        ):
            return True
        if tolerance <= 0:
            return False
        # Measuring the distance costs far more than the inside/outside test,
        # so it only runs for the near misses the tolerance exists for.
        return all(
            cv2.pointPolygonTest(self.points, (float(x), float(y)), True) >= -tolerance
            for x, y in corners
        )

    def polygon(self, cfg: DetectConfig) -> Polygon | None:
        """The plan-file polygon for this contour, or None if none is usable."""
        return _simplify(self.points, cfg)

    def raw_polygon(self) -> Polygon:
        """The unsimplified contour, for measurements that do not need tidying."""
        return _points_to_polygon(self.points)


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


def _points_to_polygon(points: NDArray[np.int32]) -> Polygon:
    return tuple((int(point[0][0]), int(point[0][1])) for point in points)


def _simplify(contour: NDArray[np.int32], cfg: DetectConfig) -> Polygon | None:
    """approxPolyDP down to a hand-editable number of integer pixel vertices.

    Simplifying a ragged contour can fold it over itself, and a
    self-intersecting polygon is both meaningless to erase against and
    rejected by the plan file reader — extract must never write one. When that
    happens we fall back to the convex hull, which cannot self-intersect and
    is a fair stand-in for a shape that already passed the solidity test.
    """
    perimeter = float(cv2.arcLength(contour, True))
    epsilon = cfg.approx_epsilon_ratio * perimeter
    approx: NDArray[np.int32] = contour
    for _ in range(8):
        approx = cast("NDArray[np.int32]", cv2.approxPolyDP(contour, epsilon, True))
        if len(approx) <= cfg.max_polygon_points:
            break
        epsilon *= 1.6

    polygon = _points_to_polygon(approx)
    if len(polygon) >= 3 and polygon_is_simple(polygon):
        return polygon

    hull = cast("NDArray[np.int32]", cv2.convexHull(contour))
    hull_polygon = _points_to_polygon(hull)
    if len(hull_polygon) >= 3 and polygon_is_simple(hull_polygon):
        return hull_polygon
    return None


def build_candidates(gray: GrayArray, cfg: DetectConfig) -> list[ContourCandidate]:
    """Every blob on the page that could plausibly be a balloon or caption box.

    Filtered by area (a contour covering a quarter of the page is a panel, not
    a balloon) and by solidity (balloons are convex-ish; artwork is not).
    """
    height, width = gray.shape[0], gray.shape[1]
    max_area = float(height * width) * cfg.max_contour_area_ratio
    max_width = width * cfg.max_extent_ratio
    max_height = height * cfg.max_extent_ratio
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
            left, top, box_width, box_height = cv2.boundingRect(contour)
            if box_width > max_width or box_height > max_height:
                continue  # a band of artwork spanning the page, not a balloon
            candidates.append(
                ContourCandidate(
                    points=contour.astype(np.int32),
                    bounds=Box(left, top, left + box_width, top + box_height),
                    area=area,
                    inverted=inverted,
                )
            )

    # Smallest first: the tightest contour containing the text is the balloon,
    # anything larger is the panel it sits in.
    candidates.sort(key=lambda candidate: candidate.area)
    return candidates


def enclosing_candidate(
    candidates: list[ContourCandidate],
    box: Box,
    cfg: DetectConfig,
    accept: Callable[[int], bool] | None = None,
    tolerance: float = 0.0,
) -> int | None:
    """Index of the tightest candidate that properly encloses ``box``.

    ``min_contour_area_slack`` rejects a contour that merely traces the text
    itself — a filled caption blob rather than a balloon around it. An index
    is returned rather than the candidate so callers can group lines by it.

    ``accept`` gets the last word on a candidate that fits geometrically; the
    search carries on past one it rejects. That is how a panel is turned down
    in favour of nothing, rather than swallowing the text inside it.
    """
    minimum_area = box.area * cfg.min_contour_area_slack
    for index, candidate in enumerate(candidates):  # already sorted smallest first
        if candidate.area < minimum_area:
            continue
        if not candidate.contains_box(box, tolerance):
            continue
        if accept is not None and not accept(index):
            continue
        return index
    return None
