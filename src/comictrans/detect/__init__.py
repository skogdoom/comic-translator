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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from statistics import median
from typing import cast

import cv2
import numpy as np

from ..config import DetectConfig
from ..imaging import PageImage
from ..model import Box, Color, Geometry, Polygon, polygon_area, polygon_bounds
from ..ocr.base import OcrLine
from ..ocr.grouping import sort_lines
from .color import interior_uniformity, sample_colors
from .contour import ContourCandidate, GrayArray, build_candidates, enclosing_candidate
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

    balloon_like = _uniformity_test(page, candidates, cfg)
    tolerance = cfg.containment_tolerance_ratio * page.height
    grouped: dict[int, list[OcrLine]] = {}
    loose: list[OcrLine] = []
    for line in lines:
        index = enclosing_candidate(candidates, line.box, cfg, balloon_like, tolerance)
        if index is None:
            # No balloon holds this line. Better a padded box around the text,
            # flagged approximate, than erasing the panel it happens to sit in.
            loose.append(line)
        else:
            grouped.setdefault(index, []).append(line)

    regions: list[DetectedRegion] = []

    for index, members in grouped.items():
        boxes = tuple(line.box for line in members)
        polygon = candidates[index].polygon(cfg)
        geometry = Geometry.EXACT
        if polygon is None or _text_fills_contour(boxes, polygon, cfg):
            # Either no usable polygon came out of the contour, or the contour
            # hugs the glyphs and is a text blob rather than a balloon.
            polygon = approximate_polygon(boxes, cfg, width=page.width, height=page.height)
            geometry = Geometry.APPROXIMATE
        regions.append(_build(page, polygon, geometry, members))

    for cluster in cluster_lines(loose, cfg, width=page.width, height=page.height):
        boxes = tuple(line.box for line in cluster)
        polygon = approximate_polygon(boxes, cfg, width=page.width, height=page.height)
        regions.append(_build(page, polygon, Geometry.APPROXIMATE, cluster))

    regions = _merge_overlapping(page, regions, cfg)
    regions = _absorb_strays(page, regions)

    approximate = sum(1 for r in regions if r.geometry is Geometry.APPROXIMATE)
    if approximate:
        log.info(
            "%s: %d of %d regions have approximate geometry",
            page.path.name,
            approximate,
            len(regions),
        )
    return reading_order(regions, cfg)


def _box_iou(a: Box, b: Box) -> float:
    overlap = a.intersection(b)
    if overlap is None:
        return 0.0
    union = a.area + b.area - overlap.area
    return overlap.area / union if union > 0 else 0.0


def _merge_overlapping(
    page: PageImage, regions: list[DetectedRegion], cfg: DetectConfig
) -> list[DetectedRegion]:
    """Fold together regions that trace the same shape.

    A balloon shows up on both threshold polarities — as its own light
    interior, and as the hole inside its dark outline — and the two contours
    are near identical. When one line matches the first and the next line
    matches the second, a single utterance arrives split across two regions.

    Merged here rather than by discarding one of the candidates up front,
    because the candidate that looks redundant may be the only one that
    actually encloses a given line of text. Dropping it cost a caption box its
    exact geometry.
    """
    merged: list[DetectedRegion] = []
    for region in regions:
        for index, kept in enumerate(merged):
            if _box_iou(region.bounds, kept.bounds) <= cfg.duplicate_iou:
                continue
            tighter = kept.polygon if kept.bounds.area <= region.bounds.area else region.polygon
            merged[index] = _build(page, tighter, kept.geometry, [*kept.lines, *region.lines])
            break
        else:
            merged.append(region)
    return merged


def _absorb_strays(page: PageImage, regions: list[DetectedRegion]) -> list[DetectedRegion]:
    """Fold an approximate region back into the balloon its text sits in.

    A line whose OCR box overshoots its balloon — a tall box on the top line,
    a box stretched to the balloon's full width — fails the containment test
    and becomes a region of its own. The result is one balloon's speech
    arriving split across two regions, with the opening line separated from
    the rest.

    Balloons do not overlap, so a stray whose every line is centred inside
    another region's polygon belongs to that region and nothing else.
    """
    exact = [r for r in regions if r.geometry is Geometry.EXACT]
    if not exact:
        return regions

    absorbed: dict[int, list[OcrLine]] = {}
    survivors: list[DetectedRegion] = []

    for region in regions:
        if region.geometry is not Geometry.APPROXIMATE:
            survivors.append(region)
            continue
        host = _host_for(region, exact)
        if host is None:
            survivors.append(region)
        else:
            absorbed.setdefault(id(host), []).extend(region.lines)

    if not absorbed:
        return regions

    rebuilt: list[DetectedRegion] = []
    for region in survivors:
        extra = absorbed.get(id(region))
        rebuilt.append(
            _build(page, region.polygon, region.geometry, [*region.lines, *extra])
            if extra
            else region
        )
    return rebuilt


def _host_for(stray: DetectedRegion, hosts: list[DetectedRegion]) -> DetectedRegion | None:
    """The region whose polygon holds the centre of every one of ``stray``'s lines.

    Tested point by point against the polygon rather than by rasterising it:
    a full-page mask per region is tens of megabytes on a large scan, and
    there is one per region on the page.
    """
    for host in hosts:
        if host is stray:
            continue
        if not host.bounds.intersection(stray.bounds):
            continue
        outline = np.array(host.polygon, dtype=np.int32)
        if all(
            cv2.pointPolygonTest(outline, (float(x), float(y)), False) >= 0
            for x, y in (line.box.center for line in stray.lines)
        ):
            return host
    return None


def _uniformity_test(
    page: PageImage, candidates: list[ContourCandidate], cfg: DetectConfig
) -> Callable[[int], bool]:
    """A cached 'is this a balloon and not a panel' test, by candidate index.

    Cached because the search tries candidates in order and the same panel is
    offered for every line inside it.
    """
    verdicts: dict[int, bool] = {}

    def accept(index: int) -> bool:
        cached = verdicts.get(index)
        if cached is None:
            score = interior_uniformity(
                page.rgb,
                candidates[index].raw_polygon(),
                page_height=page.height,
                tolerance=cfg.uniformity_tolerance,
            )
            cached = score >= cfg.min_interior_uniformity
            if not cached:
                log.debug(
                    "%s: rejecting contour at %s, interior only %.2f uniform",
                    page.path.name,
                    candidates[index].bounds,
                    score,
                )
            verdicts[index] = cached
        return cached

    return accept


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
