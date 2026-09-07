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
from dataclasses import dataclass, replace
from statistics import median
from typing import cast

import cv2
import numpy as np

from ..config import DetectConfig
from ..imaging import PageImage
from ..model import (
    Box,
    Color,
    Geometry,
    Polygon,
    polygon_area,
    polygon_bounds,
    polygon_is_simple,
)
from ..ocr.base import OcrLine
from ..ocr.grouping import sort_lines
from .color import MaskArray, interior_uniformity, sample_colors
from .colorseg import segment
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

    if cfg.color_segmentation:
        claimed, loose = _segment_loose(page, loose, cfg)
        regions.extend(claimed)

    for cluster in cluster_lines(loose, cfg, width=page.width, height=page.height):
        boxes = tuple(line.box for line in cluster)
        polygon = approximate_polygon(boxes, cfg, width=page.width, height=page.height)
        regions.append(_build(page, polygon, Geometry.APPROXIMATE, cluster))

    regions = _merge_overlapping(page, regions, cfg)
    regions = _absorb_strays(page, regions)
    regions = [_cover_lines(page, region, cfg) for region in regions]

    approximate = sum(1 for r in regions if r.geometry is Geometry.APPROXIMATE)
    if approximate:
        log.info(
            "%s: %d of %d regions have approximate geometry",
            page.path.name,
            approximate,
            len(regions),
        )
    return reading_order(regions, cfg)


def _segment_loose(
    page: PageImage, loose: list[OcrLine], cfg: DetectConfig
) -> tuple[list[DetectedRegion], list[OcrLine]]:
    """Recover regions for text no contour claimed, by colour.

    One segmentation per region rather than per line: a caption box holds
    several lines, and once its shape is known the other lines standing on it
    belong to it too.
    """
    regions: list[DetectedRegion] = []
    remaining: list[OcrLine] = []
    pending = list(loose)

    while pending:
        line = pending.pop(0)
        polygon = segment(page.rgb, line.box, cfg, page_height=page.height)
        if polygon is None:
            remaining.append(line)
            continue

        outline = np.array(polygon, dtype=np.int32)
        members = [line]
        still: list[OcrLine] = []
        for other in pending:
            x, y = other.box.center
            if cv2.pointPolygonTest(outline, (float(x), float(y)), False) >= 0:
                members.append(other)
            else:
                still.append(other)
        pending = still

        # Judged against every line that turned out to belong to the shape,
        # not just the one it was seeded from: a caption box holds several.
        lettering = sum(member.box.area for member in members)
        if polygon_area(polygon) > lettering * cfg.max_color_text_ratio:
            log.debug(
                "%s: colour region at %s is %.0fx its lettering, not a balloon",
                page.path.name,
                polygon_bounds(polygon),
                polygon_area(polygon) / max(1, lettering),
            )
            remaining.extend(members)
            continue
        if (
            interior_uniformity(
                page.rgb,
                polygon,
                page_height=page.height,
                tolerance=cfg.uniformity_tolerance,
            )
            < cfg.min_interior_uniformity
        ):
            remaining.extend(members)
            continue

        region = _build(page, polygon, Geometry.EXACT, members)
        log.debug("%s: recovered a region by colour at %s", page.path.name, region.bounds)
        regions.append(region)

    return regions, remaining


def _covers(polygon: Polygon, lines: Sequence[OcrLine]) -> bool:
    """True when every line's box sits wholly inside the polygon."""
    outline = np.array(polygon, dtype=np.int32)
    return all(
        cv2.pointPolygonTest(outline, (float(x), float(y)), False) >= 0
        for line in lines
        for x, y in line.box.corners()
    )


def _cover_lines(page: PageImage, region: DetectedRegion, cfg: DetectConfig) -> DetectedRegion:
    """Grow a polygon until it covers every line of text assigned to it.

    Erase clips its glyph mask to the polygon, so lettering outside it is
    never removed: the original text stays on the page and the translation is
    drawn over the top of it. Two earlier fixes create exactly that situation.
    The containment tolerance admits a line whose box grazes the outline, and
    stray absorption folds in a line whose box overshot the balloon
    altogether — measured at 86px past the edge on a real page.

    The polygon is unioned with the offending boxes rather than replaced by
    their hull, so a tail or a burst balloon's spikes are not filled in.
    """
    if _covers(region.polygon, region.lines):
        return region
    outline = np.array(region.polygon, dtype=np.int32)
    stray = [line.box for line in region.lines]

    window = region.bounds
    for box in stray:
        window = window.union(box)
    window = window.expanded(2).clipped(page.width, page.height)

    mask: MaskArray = np.zeros((window.height, window.width), dtype=np.uint8)
    shifted = outline - np.array([window.left, window.top], dtype=np.int32)
    cv2.fillPoly(mask, [shifted], 255)
    for box in stray:
        clipped = box.clipped(page.width, page.height)
        mask[
            clipped.top - window.top : clipped.bottom - window.top,
            clipped.left - window.left : clipped.right - window.left,
        ] = 255

    # A little slack, because simplifying the traced union shaves corners and
    # a box that ends up a pixel outside is a box whose text is not erased.
    slack = max(3, round(0.001 * page.height) | 1)
    mask = cast(
        "MaskArray", cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (slack, slack)))
    )

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return region
    contour = max(contours, key=cv2.contourArea)
    perimeter = float(cv2.arcLength(contour, True))

    polygon: Polygon | None = None
    for factor in (1.0, 0.5, 0.25):
        approx = cv2.approxPolyDP(contour, cfg.approx_epsilon_ratio * perimeter * factor, True)
        candidate = tuple(
            (int(point[0][0]) + window.left, int(point[0][1]) + window.top) for point in approx
        )
        if len(candidate) < 3 or not polygon_is_simple(candidate):
            continue
        if _covers(candidate, region.lines):
            polygon = candidate
            break
    if polygon is None:
        # A contour too ragged to simplify into a covering shape. The convex
        # hull always covers and is always simple, but it fills concavities —
        # a balloon's tail among them — so it is only taken when it is not
        # much bigger than what it replaces.
        points = np.array(
            [*region.polygon, *(corner for box in stray for corner in box.corners())],
            dtype=np.int32,
        )
        hull = tuple((int(p[0][0]), int(p[0][1])) for p in cv2.convexHull(points))
        union = float(cv2.countNonZero(mask))
        if (
            len(hull) >= 3
            and polygon_is_simple(hull)
            and polygon_area(hull) <= union * cfg.max_hull_growth
        ):
            polygon = hull
        else:
            log.debug(
                "%s: could not grow the polygon at %s to cover its text",
                page.path.name,
                region.bounds,
            )
            return region

    log.debug(
        "%s: grew a polygon at %s to cover %d line(s) of its own text",
        page.path.name,
        region.bounds,
        len(stray),
    )
    # Colours stay as measured from the original outline: the strip just
    # added is there to be erased, not to be sampled as balloon fill.
    return replace(region, polygon=polygon)


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
