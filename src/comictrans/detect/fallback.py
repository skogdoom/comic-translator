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


def cluster_lines(lines: Sequence[OcrLine], cfg: DetectConfig) -> list[list[OcrLine]]:
    """Group loose lines into utterances by vertical adjacency.

    Two lines belong together when they overlap horizontally and the vertical
    gap between them is under ``line_gap_ratio`` line heights — the same test
    a reader applies to decide whether two lines are one balloon's worth of
    speech.
    """
    if not lines:
        return []

    ordered = sorted(lines, key=lambda line: (line.box.top, line.box.left))
    spacing = median_line_height(ordered) or 1.0
    max_gap = spacing * cfg.line_gap_ratio

    clusters: list[list[OcrLine]] = []
    for line in ordered:
        for cluster in clusters:
            previous = cluster[-1]
            gap = line.box.top - previous.box.bottom
            overlaps = line.box.horizontal_overlap(previous.box) > 0
            if overlaps and -previous.box.height <= gap <= max_gap:
                cluster.append(line)
                break
        else:
            clusters.append([line])
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
