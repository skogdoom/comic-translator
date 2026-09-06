"""Detection stage: OCR boxes in, balloon polygons out.

One region per balloon or caption box, never per line: multiple text boxes
inside one contour merge into a single region, because that is the unit you
translate and the unit apply redraws.

This module knows nothing about plan files. It takes a page and a list of OCR
lines and returns geometry, which is what makes it testable with synthetic
images and hand-written boxes.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median
from typing import cast

import cv2

from ..config import DetectConfig
from ..imaging import PageImage
from ..model import Box, Color, Geometry, Polygon, polygon_area, polygon_bounds
from ..ocr.base import OcrLine
from ..ocr.grouping import sort_lines
from .color import sample_colors
from .contour import GrayArray, build_candidates, enclosing_candidate
from .fallback import approximate_polygon, cluster_lines

log = logging.getLogger(__name__)

__all__ = ["DetectedRegion", "find_regions"]


@dataclass(frozen=True, slots=True)
class DetectedRegion:
    """One balloon: its outline, the lines inside it, and its colours."""

    polygon: Polygon
    geometry: Geometry
    lines: tuple[OcrLine, ...]
    fill_color: Color
    text_color: Color

    @property
    def bounds(self) -> Box:
        return polygon_bounds(self.polygon)


def _text_fills_contour(boxes: Sequence[Box], polygon: Polygon, cfg: DetectConfig) -> bool:
    """True when the contour is really just the text, not a balloon around it."""
    area = polygon_area(polygon)
    if area <= 0:
        return True
    return sum(box.area for box in boxes) / area > cfg.max_fill_ratio


def reading_order(regions: Sequence[DetectedRegion], cfg: DetectConfig) -> list[DetectedRegion]:
    """Sort top-to-bottom, then left-to-right, across the whole page.

    No panel detection — that is deliberately out of scope, and the resulting
    index is a hint for the translator, not something apply depends on. The
    row tolerance keeps two side-by-side balloons from swapping places on a
    few pixels of scan skew.
    """
    if not regions:
        return []
    heights = [region.bounds.height for region in regions] or [1]
    band = max(1.0, float(median(heights)) * cfg.order_band_ratio)
    return sorted(
        regions,
        key=lambda region: (round(region.bounds.top / band), region.bounds.left),
    )


def find_regions(
    page: PageImage, lines: Sequence[OcrLine], cfg: DetectConfig
) -> list[DetectedRegion]:
    """Group OCR lines into balloon-shaped regions, in reading order."""
    if not lines:
        return []

    gray = cast("GrayArray", cv2.cvtColor(page.rgb, cv2.COLOR_RGB2GRAY))
    candidates = build_candidates(gray, cfg)
    log.debug("%s: %d contour candidates", page.path.name, len(candidates))

    grouped: dict[int, list[OcrLine]] = {}
    loose: list[OcrLine] = []
    for line in lines:
        index = enclosing_candidate(candidates, line.box, cfg)
        if index is None:
            loose.append(line)
        else:
            grouped.setdefault(index, []).append(line)

    regions: list[DetectedRegion] = []

    for index, members in grouped.items():
        polygon = candidates[index].polygon
        geometry = Geometry.EXACT
        boxes = tuple(line.box for line in members)
        if _text_fills_contour(boxes, polygon, cfg):
            # The contour hugs the glyphs; it is a text blob, not a balloon.
            polygon = approximate_polygon(boxes, cfg, width=page.width, height=page.height)
            geometry = Geometry.APPROXIMATE
        regions.append(_build(page, polygon, geometry, members))

    for cluster in cluster_lines(loose, cfg):
        boxes = tuple(line.box for line in cluster)
        polygon = approximate_polygon(boxes, cfg, width=page.width, height=page.height)
        regions.append(_build(page, polygon, Geometry.APPROXIMATE, cluster))

    approximate = sum(1 for r in regions if r.geometry is Geometry.APPROXIMATE)
    if approximate:
        log.info(
            "%s: %d of %d regions have approximate geometry",
            page.path.name,
            approximate,
            len(regions),
        )
    return reading_order(regions, cfg)


def _build(
    page: PageImage, polygon: Polygon, geometry: Geometry, lines: Sequence[OcrLine]
) -> DetectedRegion:
    boxes = tuple(line.box for line in lines)
    fill, text = sample_colors(page.rgb, polygon, boxes, page_height=page.height)
    return DetectedRegion(
        polygon=polygon,
        geometry=geometry,
        lines=tuple(sort_lines(lines)),
        fill_color=fill,
        text_color=text,
    )
