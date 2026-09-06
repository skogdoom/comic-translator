"""Core value types.

This module is deliberately dependency-free: no image library, no YAML, no
OpenCV. Every other stage speaks in these types, which is what lets the stages
be tested and reused (including by the future review GUI) in isolation.

Coordinate convention, enforced everywhere past the OCR adapter boundary:
pixels, origin top-left, integers.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Self

Point = tuple[int, int]
Polygon = tuple[Point, ...]


class Geometry(StrEnum):
    """How a region's polygon was arrived at."""

    EXACT = "exact"
    """Traced from an enclosing balloon contour."""

    APPROXIMATE = "approximate"
    """Union of OCR boxes plus a margin; no clean contour was found."""


class TextCase(StrEnum):
    """How ``translation`` is cased at render time."""

    UPPER = "upper"
    PRESERVE = "preserve"


@dataclass(frozen=True, slots=True)
class Color:
    """An 8-bit RGB colour."""

    r: int
    g: int
    b: int

    @classmethod
    def from_hex(cls, value: str) -> Self:
        text = value.strip().lstrip("#")
        if len(text) != 6:
            raise ValueError(f"expected a 6-digit hex colour like '#rrggbb', got {value!r}")
        try:
            return cls(int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
        except ValueError:
            raise ValueError(f"not a hex colour: {value!r}") from None

    @classmethod
    def from_rgb(cls, rgb: tuple[int, int, int]) -> Self:
        return cls(*(max(0, min(255, int(c))) for c in rgb))

    def to_hex(self) -> str:
        return f"#{self.r:02x}{self.g:02x}{self.b:02x}"

    def as_tuple(self) -> tuple[int, int, int]:
        return (self.r, self.g, self.b)


@dataclass(frozen=True, slots=True)
class Box:
    """An axis-aligned pixel box. ``right``/``bottom`` are exclusive."""

    left: int
    top: int
    right: int
    bottom: int

    def __post_init__(self) -> None:
        if self.right < self.left or self.bottom < self.top:
            raise ValueError(f"inverted box: {self}")

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return ((self.left + self.right) / 2.0, (self.top + self.bottom) / 2.0)

    def corners(self) -> tuple[Point, Point, Point, Point]:
        """The four corner pixels, inclusive, for containment tests."""
        return (
            (self.left, self.top),
            (self.right - 1, self.top),
            (self.right - 1, self.bottom - 1),
            (self.left, self.bottom - 1),
        )

    def as_polygon(self) -> Polygon:
        return self.corners()

    def expanded(self, margin: int) -> Box:
        return Box(self.left - margin, self.top - margin, self.right + margin, self.bottom + margin)

    def clipped(self, width: int, height: int) -> Box:
        return Box(
            max(0, min(self.left, width)),
            max(0, min(self.top, height)),
            max(0, min(self.right, width)),
            max(0, min(self.bottom, height)),
        )

    def union(self, other: Box) -> Box:
        return Box(
            min(self.left, other.left),
            min(self.top, other.top),
            max(self.right, other.right),
            max(self.bottom, other.bottom),
        )

    def intersection(self, other: Box) -> Box | None:
        left, top = max(self.left, other.left), max(self.top, other.top)
        right, bottom = min(self.right, other.right), min(self.bottom, other.bottom)
        if right <= left or bottom <= top:
            return None
        return Box(left, top, right, bottom)

    def vertical_overlap(self, other: Box) -> int:
        return max(0, min(self.bottom, other.bottom) - max(self.top, other.top))

    def horizontal_overlap(self, other: Box) -> int:
        return max(0, min(self.right, other.right) - max(self.left, other.left))


def polygon_bounds(polygon: Polygon) -> Box:
    """Bounding box of a polygon. ``right``/``bottom`` stay exclusive."""
    if not polygon:
        raise ValueError("empty polygon")
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return Box(min(xs), min(ys), max(xs) + 1, max(ys) + 1)


def polygon_area(polygon: Polygon) -> float:
    """Absolute shoelace area."""
    if len(polygon) < 3:
        return 0.0
    total = 0.0
    for i, (x0, y0) in enumerate(polygon):
        x1, y1 = polygon[(i + 1) % len(polygon)]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


@dataclass(frozen=True, slots=True)
class Region:
    """One balloon or caption box: everything ``apply`` needs to redraw it.

    ``apply`` must never need to re-run detection or OCR, so every piece of
    geometry and colour it uses lives here.
    """

    id: str
    image: str
    """Source image path, relative to the plan file, POSIX separators."""

    image_sha256: str
    order: int
    geometry: Geometry
    polygon: Polygon
    fill_color: Color
    text_color: Color
    confidence: float
    source_text: str
    translation: str = ""
    notes: str = ""
    low_confidence: bool = False
    skip: bool = False
    font: str | None = None
    font_size: int | None = None

    @property
    def bounds(self) -> Box:
        return polygon_bounds(self.polygon)

    @property
    def is_actionable(self) -> bool:
        """True when ``apply`` should render this region."""
        return not self.skip and bool(self.translation.strip())

    def with_translation(self, translation: str) -> Region:
        return replace(self, translation=translation)


@dataclass(frozen=True, slots=True)
class PlanHeader:
    """Document-level settings, hand-editable."""

    version: int
    generator: str
    created: str
    source_language: str
    target_language: str
    ocr_engine: str
    font: str
    case: TextCase
    font_size_min_ratio: float
    condense_min: float


@dataclass(frozen=True, slots=True)
class Plan:
    """A parsed plan file."""

    header: PlanHeader
    regions: tuple[Region, ...]

    def images(self) -> tuple[str, ...]:
        """Distinct source images, in first-seen order."""
        seen: dict[str, None] = {}
        for region in self.regions:
            seen.setdefault(region.image, None)
        return tuple(seen)

    def regions_for(self, image: str) -> tuple[Region, ...]:
        return tuple(r for r in self.regions if r.image == image)


def _orientation(a: Point, b: Point, c: Point) -> int:
    """Sign of the cross product (b-a) x (c-a). 0 means collinear."""
    value = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return (value > 0) - (value < 0)


def _on_segment(a: Point, b: Point, point: Point) -> bool:
    """True when a collinear ``point`` lies within the a-b span."""
    return min(a[0], b[0]) <= point[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= point[1] <= max(
        a[1], b[1]
    )


def segments_intersect(p1: Point, p2: Point, p3: Point, p4: Point) -> bool:
    """Proper or improper intersection of two closed segments."""
    d1, d2 = _orientation(p3, p4, p1), _orientation(p3, p4, p2)
    d3, d4 = _orientation(p1, p2, p3), _orientation(p1, p2, p4)
    if d1 != d2 and d3 != d4:
        return True
    return (
        (d1 == 0 and _on_segment(p3, p4, p1))
        or (d2 == 0 and _on_segment(p3, p4, p2))
        or (d3 == 0 and _on_segment(p1, p2, p3))
        or (d4 == 0 and _on_segment(p1, p2, p4))
    )


def polygon_is_simple(polygon: Polygon) -> bool:
    """True when no two non-adjacent edges of the polygon touch or cross.

    A hand-edited polygon that crosses itself renders as nonsense — the fill
    and the text area disagree — so it is worth catching at load time. Fine to
    brute-force: polygons are capped at a few dozen vertices.
    """
    count = len(polygon)
    if count < 3:
        return False
    for i in range(count):
        a1, a2 = polygon[i], polygon[(i + 1) % count]
        for j in range(i + 1, count):
            if j == i or (j + 1) % count == i or j == (i + 1) % count:
                continue  # adjacent edges legitimately share an endpoint
            b1, b2 = polygon[j], polygon[(j + 1) % count]
            if segments_intersect(a1, a2, b1, b2):
                return False
    return True
