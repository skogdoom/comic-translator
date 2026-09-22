"""Core value types.

This module is deliberately dependency-free: no image library, no YAML, no
OpenCV. Every other stage speaks in these types, which is what lets the stages
be tested and reused in isolation — the review GUI's own view-model layer
included, which is free of numpy and OpenCV and still speaks in these.

Coordinate convention, enforced everywhere past the OCR adapter boundary:
pixels, origin top-left, integers.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Self

Point = tuple[int, int]
Polygon = tuple[Point, ...]


class Geometry(StrEnum):
    """How a region's polygon was arrived at."""

    EXACT = "exact"
    """Traced from an enclosing balloon contour."""

    APPROXIMATE = "approximate"
    """Union of OCR boxes plus a margin; no clean contour was found."""

    MANUAL = "manual"
    """Drawn by hand in the review GUI.

    Neither of the other two: nothing traced it and no OCR box bounded it.
    Kept apart from ``exact`` because "someone drew this deliberately" is
    worth knowing on a second pass, and apart from ``approximate`` because
    that one means "check this", which a hand-drawn polygon does not."""


class Erase(StrEnum):
    """What ``apply`` paints over inside a region before it letters it.

    Per region, because it is a decision about one balloon: the run-wide
    ``--erase`` flag is the default for regions that do not say. ``fill_color``
    is what the painting is done *with*, which is why changing that colour
    does nothing under ``flat`` unless there is lettering to repaint.
    """

    NONE = "none"
    """Paint nothing. The translation is drawn straight onto the artwork —
    for a sound effect, or a caption over art that must not be covered."""

    FLAT = "flat"
    """The original lettering only, repainted in ``fill_color``."""

    POLYGON = "polygon"
    """The whole polygon, flooded with ``fill_color``. What a region drawn by
    hand gets: a person outlined that area meaning all of it."""

    INPAINT = "inpaint"
    """The lettering, reconstructed from the pixels around it."""


class TextCase(StrEnum):
    """How ``translation`` is cased at render time."""

    UPPER = "upper"
    PRESERVE = "preserve"


class ReadingDirection(StrEnum):
    """Which way the pages of this chapter are meant to be read.

    A fact about the comic rather than about the translation, which is why it
    sits in the header beside the rest of them. A plan that does not say holds
    ``None``: unstated is not the same claim as left-to-right, and a reader
    that has to pick one should know it is picking rather than being told.

    Nothing reads this yet — see ``planfile.schema.READABLE_VERSIONS``.
    """

    LEFT_TO_RIGHT = "ltr"
    RIGHT_TO_LEFT = "rtl"


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

    def horizontal_overlap(self, other: Box) -> int:
        return max(0, min(self.right, other.right) - max(self.left, other.left))


OVERLAP_BBOX_RATIO = 0.15
"""Share of the smaller box that counts as two regions drawing over each other.

Deliberately loose, and deliberately about boxes rather than polygons: the
question it answers is "will these two draw over each other", which does not
need the exact shared area, and answering it cheaply is what lets the review
window ask it of every region on a page as you type.
"""


def boxes_overlap(first: Box, second: Box) -> bool:
    """True when two boxes share more than :data:`OVERLAP_BBOX_RATIO` of the smaller.

    One implementation, read from both ends of the pipeline: ``render``
    warns with it at apply time and ``gui.document`` flags with it while you
    review. A region the window flags is therefore exactly one apply would
    also warn about — never a surprise the GUI invented and apply does not
    share, which two copies of this arithmetic could not promise.
    """
    shared = first.intersection(second)
    if shared is None:
        return False
    smaller = min(first.area, second.area)
    return smaller > 0 and shared.area / smaller > OVERLAP_BBOX_RATIO


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
    """Source image path, relative to the plan file, POSIX separators.

    Names an entry in the plan's ``images``, which is where that file's hash
    is recorded. The hash is per image, not per region, so it lives once
    rather than once for every balloon on the page."""

    order: int
    geometry: Geometry
    polygon: Polygon
    fill_color: Color
    text_color: Color
    confidence: float

    # The three fields that hold the comic itself, and the only three kept
    # out of this class's repr. A repr is what ends up in a log line someone
    # wrote as `log.info("...%s", region)`, and in the locals of any
    # traceback that passes through — neither of which is a place the
    # translator's text should turn up. `region.translation` still prints
    # when it is asked for by name; nothing prints it by accident.
    source_text: str = field(repr=False)
    translation: str = field(default="", repr=False)
    notes: str = field(default="", repr=False)

    low_confidence: bool = False
    skip: bool = False

    locked: bool = False
    """Finished, and not to be edited without unlocking it first.

    Not ``skip``, which the labels have to keep apart: ``skip`` means do not
    render this region, ``locked`` means do not change it, and a locked region
    still renders. Nothing enforces it yet."""

    erase: Erase | None = None
    """How this region is painted over. ``None`` follows the run's own flag."""

    stroke_color: Color | None = None
    """Outline drawn around the lettering, or ``None`` for no outline.

    Deliberately not implied by ``erase: none``, so that a plan already
    lettering a caption over artwork does not quietly start outlining it.
    Nothing draws it yet."""

    angle: float = 0.0
    """Tilt of this region, in degrees, positive counter-clockwise.

    ``0.0`` is the upright region every plan holds today. Nothing rotates
    anything by it yet — the typesetter still fits level text inside whatever
    polygon it is given, which is why this is a field rather than a change to
    what a polygon means."""

    font: str | None = None
    font_size: int | None = None

    @property
    def bounds(self) -> Box:
        return polygon_bounds(self.polygon)

    @property
    def is_actionable(self) -> bool:
        """True when ``apply`` should render this region."""
        return not self.skip and bool(self.translation.strip())

    @property
    def is_untranslated(self) -> bool:
        """True when the translation is still the source text extract seeded.

        Extract fills ``translation`` with ``source_text`` so it can be edited
        in place, which means an untouched region renders the original text
        back onto the page instead of being skipped. This is how apply can
        still tell you which balloons you have not got to yet.

        A translation that genuinely matches its source — a name, a number, an
        interjection that is the same word in both languages — reads as
        untranslated too. That is a report line, never a reason to skip the
        region.
        """
        return bool(self.translation.strip()) and (
            self.translation.strip() == self.source_text.strip()
        )

    def with_translation(self, translation: str) -> Region:
        return replace(self, translation=translation)


@dataclass(frozen=True, slots=True)
class PlanHeader:
    """Document-level settings, hand-editable."""

    version: int
    """Schema version of the file this came from.

    Always the current one in memory: the reader upgrades an older file as it
    reads it, and the writer only knows how to write today's shape."""

    generator: str
    created: str
    source_language: str
    target_language: str
    ocr_engine: str
    font: str
    case: TextCase
    font_size_min_ratio: float
    condense_min: float

    # What the comic is. Nothing measures any of it from the pages and nothing
    # reads it back yet: `extract` fills in none of these, and a plan that
    # names none is the plan this wrote before they existed. Empty is a valid
    # answer for every one of them, which is what lets that stay true.
    series: str = ""
    title: str = ""
    volume: str = ""
    number: str = ""
    """Free text, not a number: an issue is as often ``1.5`` or ``Annual`` as
    it is ``7``, and a plan that could not say so would be worse than one that
    does not check."""

    year: int | None = None
    """The year of publication, or ``None`` for a plan that does not say.

    The one of these that is a number rather than text, because a year is one
    and a typo in it is worth catching — see ``schema.YEAR_RANGE``."""

    publisher: str = ""
    writer: str = ""
    reading_direction: ReadingDirection | None = None
    """Which way this chapter's pages read, or ``None`` for a plan that does
    not say. Unstated rather than assumed; see :class:`ReadingDirection`."""


@dataclass(frozen=True, slots=True)
class PlanImage:
    """One source page the plan covers, and the file it was read from."""

    name: str
    """Path relative to the plan file, POSIX separators."""

    sha256: str


@dataclass(frozen=True, slots=True)
class Plan:
    """A parsed plan file."""

    header: PlanHeader
    images: tuple[PlanImage, ...]
    """Every page extract read, in the order it read them.

    Including the ones it found no text on. A page with no regions is still
    part of the comic: apply copies it to the output so a chapter comes out
    whole, and the review GUI can show it so a missed balloon can be drawn on
    it by hand."""

    regions: tuple[Region, ...]

    def image_names(self) -> tuple[str, ...]:
        return tuple(image.name for image in self.images)

    def sha256_for(self, image: str) -> str | None:
        for entry in self.images:
            if entry.name == image:
                return entry.sha256
        return None

    def regions_for(self, image: str) -> tuple[Region, ...]:
        return tuple(r for r in self.regions if r.image == image)


def source_path(plan_path: Path, image: str) -> Path:
    """Where a plan's page lives on disk, resolved against the plan's own directory.

    A plan names its images relative to itself — see :attr:`Region.image` —
    so this join is the one rule turning what the file says into a path, and
    `apply`, `review` and `validate` all have to follow it identically or
    they would disagree about which file a region is about.

    Here, at the bottom, because each of them would otherwise write it out
    again: it is a line of code, and three copies of a line of code is still
    three places for one rule to live. Nothing but ``pathlib`` is needed for
    it, so every layer that speaks in these types can reach it without
    reaching for a layer above.
    """
    return (plan_path.parent / image).resolve()


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


def point_in_polygon(point: Point, polygon: Polygon) -> bool:
    """True when a point is inside a polygon or on its edge.

    Ray casting: count how many edges a ray to the right crosses, and an odd
    count means inside. Kept here rather than taken from OpenCV — which has
    ``pointPolygonTest`` and is already a dependency of ``detect`` — because
    the review GUI's document layer needs this and is deliberately free of
    numpy and OpenCV both.
    """
    x, y = point
    count = len(polygon)
    inside = False
    for index in range(count):
        first, second = polygon[index], polygon[(index + 1) % count]
        if _orientation(first, second, point) == 0 and _on_segment(first, second, point):
            return True  # on the boundary, which counts as in
        if (first[1] > y) != (second[1] > y):
            crossing = first[0] + (y - first[1]) * (second[0] - first[0]) / (second[1] - first[1])
            if crossing > x:
                inside = not inside
    return inside


def polygons_overlap(first: Polygon, second: Polygon) -> bool:
    """True when two polygons share any area, or touch.

    The real test, not the bounding-box ratio the review GUI uses to warn
    about regions drawing over each other: that one is deliberately loose,
    and this decides whether two regions may be merged into one shape.

    Two cases, because an outline can share area without crossing: edges that
    meet, and one polygon wholly inside the other.
    """
    if polygon_bounds(first).intersection(polygon_bounds(second)) is None:
        return False
    for index, start in enumerate(first):
        end = first[(index + 1) % len(first)]
        for other_index, other_start in enumerate(second):
            other_end = second[(other_index + 1) % len(second)]
            if segments_intersect(start, end, other_start, other_end):
                return True
    return point_in_polygon(first[0], second) or point_in_polygon(second[0], first)


def with_image_order(plan: Plan, names: Sequence[str]) -> Plan:
    """The same plan with its pages in ``names`` order, and regions following.

    **Both lists move together, and that is the point.** ``images`` is the
    order pages are reviewed and rendered in; ``regions`` is the order
    walking region by region follows, and it takes for granted that it is
    grouped by page in page order — which is what makes stepping through a
    plan step through the comic. Reordering one without the other would
    leave the page list and the next-region key disagreeing about what comes
    after what, on a plan that still reads as valid.

    Sorting is stable throughout, so regions keep their order within a page
    and pages the caller did not name keep theirs at the end. A name that is
    not in the plan is ignored. Between them those two rules mean a partial
    or stale list can reorder what it knows about and cannot lose a page.
    """
    wanted = {name: index for index, name in enumerate(names)}
    after_the_named = len(wanted)
    images = tuple(sorted(plan.images, key=lambda image: wanted.get(image.name, after_the_named)))

    settled = {image.name: index for index, image in enumerate(images)}
    unknown_page = len(settled)
    regions = tuple(sorted(plan.regions, key=lambda r: settled.get(r.image, unknown_page)))
    return replace(plan, images=images, regions=regions)


def convex_hull(points: Sequence[Point]) -> Polygon:
    """The smallest convex ring containing every point, in whole pixels.

    Andrew's monotone chain, dropping collinear points so the ring has no
    redundant corners. What merging two regions produces: the hull of both
    outlines is the smallest convex shape that covers what either of them
    covered.
    """
    unique = sorted(set(points))
    if len(unique) < 3:
        return tuple(unique)

    def chain(sequence: Sequence[Point]) -> list[Point]:
        built: list[Point] = []
        for point in sequence:
            while len(built) >= 2 and _orientation(built[-2], built[-1], point) <= 0:
                built.pop()
            built.append(point)
        return built

    lower = chain(unique)
    upper = chain(list(reversed(unique)))
    return tuple(lower[:-1] + upper[:-1])


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
            if (j + 1) % count == i or j == (i + 1) % count:
                continue  # adjacent edges legitimately share an endpoint
            b1, b2 = polygon[j], polygon[(j + 1) % count]
            if segments_intersect(a1, a2, b1, b2):
                return False
    return True
