"""A brush stroke, as the region it paints.

No Qt here, like ``sampling``: the canvas collects where the pointer went and
hands it over, and what comes back is a polygon or nothing.

**The region is the outline of whatever was painted.** A plan's region is one
simple ring, and a stroke can paint a doughnut — a loop drawn round a balloon's
lettering is exactly that — so the hole is filled: the ring is the outer
boundary of the painted area and nothing else is recorded.

**Nothing painted is left out of it, which is the point.** Keeping only the
largest piece of what was painted is what ``detect`` does to a mask, and a
tool that drew what you drew and then threw part of it away would be a
surprise. So the stroke goes through detect's own path — painted into a
scratch mask, traced with ``RETR_EXTERNAL``, which gives outermost contours
and ignores holes, that rule exactly, and simplified by
:func:`detect.contour.simplified_rings`, the same steps and the same
refusals — but at :data:`model.SHAPE_TOLERANCE`, one pixel, rather than at
detect's tolerance.

That last part was measured rather than assumed. Detect's tolerance is a
share of the outline's perimeter, which suits a balloon and not a stroke: a
long thin stroke has a long outline and so a coarse tolerance, which cuts
straight across the band that was painted. Over 3,000 random strokes on a
page the size of the six-panel fixture it left 1.2% of what was painted
outside the ring at the median, 10% at the 95th percentile and 64% at worst.
At one pixel, over 1,500 of them, no painted pixel was more than about a
pixel outside, and none was refused — against two refusals, at the finest
brush, at detect's. It costs corners: a median of 12 to 35 by brush size,
and a few hundred for a long scribble with a fine one.

**One stroke is one region.** A stroke is painted as one connected band — the
points are on the page and each is joined to the next — so tracing it gives
one outline and there is never a second to choose between: none of those
strokes gave two. Strokes that accumulated until some explicit commit would
reopen that question; this does not, and it is the simplest thing that could
work.
"""

from __future__ import annotations

from collections.abc import Sequence

import cv2
import numpy as np

from ..detect.color import MaskArray
from ..detect.contour import simplified_rings
from ..model import SHAPE_TOLERANCE, Point, Polygon

BRUSH_SIZES = (0.01, 0.02, 0.04, 0.08)
"""The brushes on offer, as shares of the page's height: fine to large.

Shares of the page rather than pixels, as every size in ``config`` is, so a
brush covers the same part of a page scanned at any resolution — the
fixtures carry one page at two resolutions for exactly that reason. The
finest is a line of lettering: measured at 0.88% of the page's height on the
six-panel fixture, the one shaped like a printed page (34px of 3880). Each
brush after it doubles, so the largest is about two-thirds of that page's
median balloon across its short side (458px, 11.8%) and covers one in a
pass or two."""

MIN_BRUSH = 3
"""The narrowest a brush is ever painted, in page pixels, on a small page.

At one or two pixels a stroke is a line with no inside, and one that doubles
back on itself traces as an outline that touches itself — which is not a
ring a plan can hold."""


def brush_diameter(share: float, page_height: int) -> int:
    """How wide a brush paints on this page, in page pixels."""
    return max(MIN_BRUSH, round(share * page_height))


def stroke_outline(
    points: Sequence[Point], diameter: int, page_size: tuple[int, int]
) -> Polygon | None:
    """The ring around everything a stroke painted, or ``None`` if it has none.

    ``points`` are where the brush's middle went, in page pixels and on the
    page, at least one of them; one alone is a single dab. Painted
    ``diameter`` pixels wide with round ends and joins, as the canvas shows
    it while it is painted, and clipped to the page, which a brush near the
    edge reaches past.

    ``None`` when no step simplifies the outline into a ring a plan can hold
    — never a ring with part of the stroke left out of it. None of the
    strokes measured came to that; see the module docstring.
    """
    width, height = page_size
    radius = diameter // 2 + 1
    xs = [x for x, _y in points]
    ys = [y for _x, y in points]
    left, top = max(0, min(xs) - radius), max(0, min(ys) - radius)
    right, bottom = min(width, max(xs) + radius + 1), min(height, max(ys) + radius + 1)

    mask: MaskArray = np.zeros((bottom - top, right - left), dtype=np.uint8)
    path = np.array([(x - left, y - top) for x, y in points], dtype=np.int32)
    if len(path) == 1:
        # A lone point is a line of no length, which OpenCV draws as nothing
        # at all; the same point twice is the round dab it should be.
        path = np.concatenate([path, path])
    cv2.polylines(mask, [path], False, 255, thickness=diameter)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    # One band, so one outline: see the module docstring.
    outline = contours[0].astype(np.int32)
    return next(simplified_rings(outline, SHAPE_TOLERANCE, (left, top)), None)


__all__ = ["BRUSH_SIZES", "MIN_BRUSH", "brush_diameter", "stroke_outline"]
