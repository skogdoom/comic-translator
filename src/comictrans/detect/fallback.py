"""What to do when no clean balloon contour is found.

Borderless captions, balloons whose outline is broken by a panel border, and
text sitting directly on artwork all end up here. The region becomes the union
of its text boxes plus a small margin, and is flagged ``geometry: approximate``
in the plan file so you know the erase step is working blind.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..config import DetectConfig
from ..model import Box, Polygon
from ..ocr.base import OcrLine
from ..ocr.grouping import median_line_height


def cluster_lines(
    lines: Sequence[OcrLine], cfg: DetectConfig, *, width: int, height: int
) -> list[list[OcrLine]]:
    """Group loose lines into utterances by vertical adjacency.

    Two lines belong together when they overlap horizontally and the vertical
    gap between them is under ``line_gap_ratio`` line heights — the same test
    a reader applies to decide whether two lines are one balloon's worth of
    speech.

    A cluster is also capped at ``max_extent_ratio`` of the page. Adjacency is
    transitive, so without the cap a row of spurious OCR lines can chain right
    across a page into one region, and erasing that would wipe out the art —
    the same escape the contour path guards against, arriving by another road.
    """
    if not lines:
        return []

    ordered = sorted(lines, key=lambda line: (line.box.top, line.box.left))
    spacing = median_line_height(ordered) or 1.0
    max_gap = spacing * cfg.line_gap_ratio
    max_width = width * cfg.max_extent_ratio
    max_height = height * cfg.max_extent_ratio

    clusters: list[list[OcrLine]] = []
    unions: list[Box] = []
    for line in ordered:
        for index, cluster in enumerate(clusters):
            previous = cluster[-1]
            gap = line.box.top - previous.box.bottom
            overlaps = line.box.horizontal_overlap(previous.box) > 0
            if not overlaps or not (-previous.box.height <= gap <= max_gap):
                continue
            grown = unions[index].union(line.box)
            if grown.width > max_width or grown.height > max_height:
                continue  # this line would stretch the region across the page
            cluster.append(line)
            unions[index] = grown
            break
        else:
            clusters.append([line])
            unions.append(line.box)
    return clusters


def approximate_polygon(
    boxes: Sequence[Box], cfg: DetectConfig, *, width: int, height: int
) -> Polygon:
    """Union of ``boxes`` plus a margin, clipped to the page."""
    if not boxes:
        raise ValueError("cannot build a polygon from no boxes")
    union = boxes[0]
    for box in boxes[1:]:
        union = union.union(box)
    margin = max(1, round(cfg.fallback_margin_ratio * height))
    return union.expanded(margin).clipped(width, height).as_polygon()
