"""Drawing a fitted layout onto a page, and compositing a whole image.

Text is drawn into a transparent layer per line and then composited, which is
what makes horizontal condensing possible: the line is laid out and rendered
at its natural width, then scaled on the x axis alone. Scaling a rendered
line is also the only way to condense without a variable font — and the spec
forbids faking anything about the face.

**A tilted region is lettered level in a frame of its own**, then turned onto
the page. Its polygon is turned level about the middle of its box and handed
to the typesetter unchanged — the band algorithm thinks in horizontal bands
and needs no other way of thinking, only a polygon that is already upright —
and its lines are drawn into one layer the size of that frame, turned back by
the region's angle in a single resample and composited. A region with no
angle takes neither step, so it renders exactly as it always has.

That resample was measured before it was chosen, by giving Tesseract lines
of lettering turned 5, 20 and 45 degrees and turned back. From 8px up it
reads them as well as level text, and as well as text drawn at four times the
size, turned and averaged down — the obvious better route. At 6px it does
not: level text read at 0.77 of the characters, one resample at 0.66 to 0.70,
and drawing at 4x at 0.82 to 0.93. Turning the 1x drawing at 2x or 4x instead
gains nothing (0.53 to 0.69) and costs 6 to 35 times as long. Drawing at 4x
is not taken because glyphs drawn four times the size are not four times as
wide as the hinted ones the layout was measured with, so a line fitted to its
band would no longer be the line drawn in it. 6px is the readable minimum on
a page about 500 pixels tall.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image, ImageDraw

from .config import ApplyConfig
from .erase import erase
from .fonts import FontFace
from .imaging import PageImage
from .markup import MarkupError, tokenize
from .model import (
    Box,
    Color,
    Polygon,
    Region,
    TextCase,
    boxes_overlap,
    polygon_bounds,
    rotate_polygon,
)
from .progress import CancelCheck
from .typeset import FitFailure, Layout, layout_text

log = logging.getLogger(__name__)


# Not RenderCancelledError: N818 wants the suffix on anything raised, and
# this is the one case where it would say the wrong thing. Nothing has gone
# wrong; somebody moved on.
class RenderCancelled(Exception):  # noqa: N818
    """A page was abandoned part-way, because its caller asked.

    Deliberately not a ``ComictransError``. Those are failures with something
    to tell a user, and a run that stopped because somebody moved on is not
    one — the window catches this and drops the half-rendered page, and the
    ``RunJob`` machinery that turns a ``ComictransError`` into a red message
    never sees it.

    Only reachable by passing ``should_cancel``. ``apply`` does not, which is
    what keeps its promise intact: a cancelled ``apply`` stops *between*
    pages, and every page it wrote is one a complete run would have written.
    """


RegionProgress = Callable[[int, int], None]
"""``(done, total)`` in regions erased. See ``render_page``."""


@dataclass(frozen=True, slots=True)
class RegionStyle:
    """The resolved presentation for one region."""

    face: FontFace
    case: TextCase
    size: int | None
    """A per-region override, or None to fit automatically."""


@dataclass(frozen=True, slots=True)
class RegionOutcome:
    """What happened to one region, for the end-of-run report."""

    region_id: str
    status: str
    """``rendered``, ``skipped_empty``, ``skipped_flag`` or ``failed``.

    The two kinds of skip are kept apart because they mean opposite things: an
    empty translation is unfinished work and fails the run, while ``skip:
    true`` is a decision you made and passes quietly.
    """

    detail: str = ""
    condense: float = 1.0
    font_size: int = 0
    undersized: bool = False
    """Rendered below the readable minimum size to make the text fit."""

    unedited: bool = False
    """The translation is still identical to the extracted source text."""

    @property
    def rendered(self) -> bool:
        return self.status == "rendered"

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    @property
    def unfinished(self) -> bool:
        return self.status in {"skipped_empty", "failed"}


FRAME_MARGIN = 4
"""Pixels of room around a tilted region's polygon in its own frame.

The lines are fitted inside the polygon, but a glyph's ink can reach a pixel
or two past the advance it was measured by, and a frame cut to the polygon
exactly would clip it."""


@dataclass(frozen=True, slots=True)
class UprightFrame:
    """A tilted region's own frame, in which its lettering is fitted level.

    The region's polygon turned level about the middle of its box — the same
    pivot the window turns a region about — and moved so its box sits
    :data:`FRAME_MARGIN` in from the frame's top left corner.
    """

    angle: float
    """The region's tilt: positive counter-clockwise, as the plan has it."""

    bounds: Box
    """The polygon's box on the page. The lettering is fitted inside the
    polygon, so this, grown by :data:`FRAME_MARGIN`, is all of the page it
    can reach once it is turned back."""

    offset: tuple[int, int]
    """Where the frame's top left corner is in the polygon's level position."""

    size: tuple[int, int]
    polygon: Polygon
    """The polygon, level, in the frame's own coordinates."""

    @property
    def pivot(self) -> tuple[float, float]:
        """The middle of the polygon's box, in continuous coordinates — a
        pixel's middle is half a pixel in from its corner, which is why this
        is not ``rotate_polygon``'s middle but half a pixel on from it."""
        return (
            (self.bounds.left + self.bounds.right) / 2,
            (self.bounds.top + self.bounds.bottom) / 2,
        )


def upright_frame(region: Region) -> UprightFrame | None:
    """The frame a region's lettering is fitted in, or ``None`` for a level one.

    A whole turn is no turn, so a plan that spells level as 360 is lettered
    exactly as one that says 0.
    """
    if region.angle % 360.0 == 0.0:
        return None
    # rotate_polygon turns clockwise for a positive angle, and a region
    # tilted counter-clockwise by its angle is levelled by turning it back.
    level = rotate_polygon(region.polygon, region.angle)
    box = polygon_bounds(level)
    left, top = box.left - FRAME_MARGIN, box.top - FRAME_MARGIN
    return UprightFrame(
        angle=region.angle,
        bounds=region.bounds,
        offset=(left, top),
        size=(box.width + 2 * FRAME_MARGIN, box.height + 2 * FRAME_MARGIN),
        polygon=tuple((x - left, y - top) for x, y in level),
    )


def draw_layout(
    image: Image.Image,
    layout: Layout,
    style: RegionStyle,
    color: Color,
    frame: UprightFrame | None = None,
    outline: Color | None = None,
) -> None:
    """Draw a fitted layout onto an RGB image, in place.

    With a ``frame``, the layout is in that frame's coordinates: it is drawn
    level into a layer the frame's size and turned onto the page in one go.
    With an ``outline``, the lettering is drawn round in that colour, as wide
    as the layout left room for.
    """
    if frame is None:
        _draw_lines(image, layout, style, color, outline)
        return
    layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    _draw_lines(layer, layout, style, color, outline)
    _turn_onto(image, layer, frame)


def _draw_lines(
    target: Image.Image,
    layout: Layout,
    style: RegionStyle,
    color: Color,
    outline: Color | None,
) -> None:
    """Each line of a layout, drawn where the layout put it on ``target``.

    ``target`` is the page, or a tilted region's own layer; on a layer the
    lines are composited over it, since pasting through a mask would multiply
    their coverage into the layer's alpha a second time.

    An outline is drawn for the whole line before any of its letters, so a
    run's outline never lies over the letters of the run beside it — a word
    half in bold is two runs touching. Each line's layer is grown by the
    outline on every side and placed back by as much, which puts the letters
    where they would be without it and the outline in the room the fit left.
    """
    regular = style.face.regular.load(layout.font_size)
    bold = style.face.bold.load(layout.font_size) if style.face.bold else regular
    ascent, descent = regular.getmetrics()
    baseline = max(0, (layout.line_height - (ascent + descent)) // 2)
    edge = layout.outline if outline is not None else 0

    for line in layout.lines:
        if not line.runs:
            continue
        width = max(1, line.width)
        layer = Image.new("RGBA", (width + 2 * edge, layout.line_height + 2 * edge), (0, 0, 0, 0))
        pen = ImageDraw.Draw(layer)
        if outline is not None:
            for run in line.runs:
                pen.text(
                    (run.x + edge, baseline + edge),
                    run.text,
                    font=bold if run.bold else regular,
                    fill=(*outline.as_tuple(), 255),
                    anchor="la",
                    stroke_width=edge,
                    stroke_fill=(*outline.as_tuple(), 255),
                )
        for run in line.runs:
            pen.text(
                (run.x + edge, baseline + edge),
                run.text,
                font=bold if run.bold else regular,
                fill=(*color.as_tuple(), 255),
                anchor="la",
            )

        if layout.condense < 1.0:
            layer = layer.resize(
                (max(1, round(layer.width * layout.condense)), layer.height),
                Image.Resampling.LANCZOS,
            )
        left = line.band_left + (line.band_width - layer.width) // 2
        if target.mode == "RGBA":
            target.alpha_composite(layer, (left, line.top - edge))
        else:
            target.paste(layer, (left, line.top - edge), layer)


def _turn_onto(image: Image.Image, layer: Image.Image, frame: UprightFrame) -> None:
    """Turn a region's level layer by its angle and composite it onto the page.

    One resample, measured: see the module docstring. Only the polygon's own
    box is resampled, grown by the frame's margin: nothing drawn in the frame
    lies outside the polygon, and a box turned back lies inside it. The
    frame itself can be longer than that box — a long region at a steep angle
    — and what of it lies beyond is empty.
    """
    turn = math.radians(frame.angle)
    cos, sin = math.cos(turn), math.sin(turn)
    pivot_x, pivot_y = frame.pivot
    offset_x, offset_y = frame.offset
    reach = frame.bounds.expanded(FRAME_MARGIN)
    left, top = reach.left, reach.top

    # Pillow's affine transform asks, for each pixel of what it makes, where
    # to read from in the layer: the page point turned clockwise by the angle
    # about the pivot, back into the frame.
    turned = layer.transform(
        (reach.width, reach.height),
        Image.Transform.AFFINE,
        (
            cos,
            -sin,
            cos * (left - pivot_x) - sin * (top - pivot_y) + pivot_x - offset_x,
            sin,
            cos,
            sin * (left - pivot_x) + cos * (top - pivot_y) + pivot_y - offset_y,
        ),
        resample=Image.Resampling.BICUBIC,
    )
    image.paste(turned, (left, top), turned)


@dataclass(frozen=True, slots=True)
class PlannedRegion:
    """A region that will be rendered, and the layout it will be rendered with."""

    region: Region
    style: RegionStyle
    layout: Layout


def plan_region(
    region: Region,
    style: RegionStyle,
    cfg: ApplyConfig,
    *,
    page_width: int,
    page_height: int,
) -> tuple[Layout | None, RegionOutcome]:
    """Decide what happens to a region, without touching a single pixel.

    Separating the decision from the drawing is what lets every erase happen
    before any text is drawn. A region that will not fit returns no layout, so
    it is never erased either.
    """
    if region.skip:
        return None, RegionOutcome(region.id, "skipped_flag", "marked skip: true")
    if not region.translation.strip():
        return None, RegionOutcome(region.id, "skipped_empty", "no translation")

    try:
        tokens = tokenize(region.translation, case=style.case)
    except MarkupError as exc:
        return None, RegionOutcome(region.id, "failed", str(exc))
    if not tokens:
        return None, RegionOutcome(region.id, "skipped_empty", "translation is only markup")

    frame = upright_frame(region)
    result = layout_text(
        tokens,
        region.polygon if frame is None else frame.polygon,
        style.face,
        cfg.typeset,
        page_width=page_width,
        page_height=page_height,
        fixed_size=style.size,
        canvas=None if frame is None else frame.size,
        outlined=region.stroke_color is not None,
    )
    if isinstance(result, FitFailure):
        # Nothing is drawn and nothing is erased: a region that will not fit
        # is left exactly as it was, so the page stays readable in the source
        # language rather than becoming a blank balloon.
        return None, RegionOutcome(region.id, "failed", result.reason)

    if region.is_untranslated:
        log.warning(
            "region %s: translation is still the extracted source text; "
            "the original text is being re-lettered",
            region.id,
        )
    if result.condensed:
        log.info(
            "region %s: condensed to %.0f%% at %dpx to fit",
            region.id,
            result.condense * 100,
            result.font_size,
        )
    if result.undersized:
        asked = (
            f"the {region.font_size}px it pins"
            if region.font_size is not None
            else "the readable minimum"
        )
        log.warning("region %s: rendered at %dpx, below %s", region.id, result.font_size, asked)

    return result, RegionOutcome(
        region.id,
        "rendered",
        condense=result.condense,
        font_size=result.font_size,
        undersized=result.undersized,
        unedited=region.is_untranslated,
    )


def _warn_about_overlaps(planned: list[PlannedRegion]) -> None:
    """Note regions that share pixels, since their text will overlap.

    Erasing every region before drawing any of them stops one region's erase
    from cutting into another's lettering. It cannot stop two overlapping
    polygons from drawing over each other, and that is a plan file problem —
    so say which ones rather than let it be a surprise on the page.
    """
    for index, first in enumerate(planned):
        for second in planned[index + 1 :]:
            if boxes_overlap(first.region.bounds, second.region.bounds):
                log.warning(
                    "regions %s and %s overlap; their text will be drawn over each other",
                    first.region.id,
                    second.region.id,
                )


def render_page(
    page: PageImage,
    regions: tuple[Region, ...],
    styles: dict[str, RegionStyle],
    cfg: ApplyConfig,
    *,
    on_region: RegionProgress | None = None,
    should_cancel: CancelCheck | None = None,
) -> tuple[Image.Image, list[RegionOutcome]]:
    """Apply every region belonging to one page.

    Two passes on purpose. Erasing and drawing one region at a time lets a
    later region's erase wipe lettering an earlier one already drew, wherever
    two polygons overlap — silently, since both regions still report success.
    Every erase therefore happens first, against pixels that hold only the
    original artwork, and only then is any text drawn.

    **Both hooks are optional and both are off by default**, which is how
    ``apply`` keeps its promise that a cancelled run leaves whole pages: it
    passes neither, so this can neither report from inside a page nor stop
    part-way through one. They exist for ``review``'s preview, which is one
    page, ephemeral, and thrown away rather than written if it is abandoned.

    **Both live in the erase loop, and that is measured rather than assumed.**
    On an eleven-megapixel page with ten regions the three loops here cost
    0.099s, 1.280s and 0.002s per region — erasing is 80% of the whole
    preview, and the other two are below the granularity anybody could see
    on a progress bar. Cancelling is checked in the planning loop too, which
    costs one call per region and takes the worst case from "the whole page"
    down to "the region being erased".

    ``should_cancel`` raises :class:`RenderCancelled` rather than returning
    something. A half-erased page is not a result, and a return value saying
    so would have to be handled by every caller including the two that can
    never see it.
    """
    stop = should_cancel or (lambda: False)
    planned: list[PlannedRegion] = []
    outcomes: list[RegionOutcome] = []
    for region in sorted(regions, key=lambda r: r.order):
        if stop():
            raise RenderCancelled
        layout, outcome = plan_region(
            region,
            styles[region.id],
            cfg,
            page_width=page.width,
            page_height=page.height,
        )
        outcomes.append(outcome)
        if layout is not None:
            planned.append(PlannedRegion(region, styles[region.id], layout))

    _warn_about_overlaps(planned)

    rgb = page.rgb
    for done, entry in enumerate(planned):
        if stop():
            raise RenderCancelled
        if on_region is not None:
            on_region(done, len(planned))
        rgb = erase(rgb, entry.region, cfg.erase, page_height=page.height)
    if on_region is not None:
        on_region(len(planned), len(planned))

    image = Image.fromarray(rgb)
    for entry in planned:
        draw_layout(
            image,
            entry.layout,
            entry.style,
            entry.region.text_color,
            upright_frame(entry.region),
            entry.region.stroke_color,
        )
    return image, outcomes
