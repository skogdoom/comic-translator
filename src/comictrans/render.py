"""Drawing a fitted layout onto a page, and compositing a whole image.

Text is drawn into a transparent layer per line and then composited, which is
what makes horizontal condensing possible: the line is laid out and rendered
at its natural width, then scaled on the x axis alone. Scaling a rendered
line is also the only way to condense without a variable font — and the spec
forbids faking anything about the face.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from PIL import Image, ImageDraw

from .config import ApplyConfig
from .erase import erase
from .fonts import FontFace
from .imaging import PageImage
from .markup import MarkupError, tokenize
from .model import Color, Region, TextCase
from .typeset import FitFailure, Layout, layout_text

log = logging.getLogger(__name__)


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


def draw_layout(image: Image.Image, layout: Layout, style: RegionStyle, color: Color) -> None:
    """Draw a fitted layout onto an RGB image, in place."""
    regular = style.face.regular.load(layout.font_size)
    bold = style.face.bold.load(layout.font_size) if style.face.bold else regular
    ascent, descent = regular.getmetrics()
    baseline = max(0, (layout.line_height - (ascent + descent)) // 2)

    for line in layout.lines:
        if not line.runs:
            continue
        width = max(1, line.width)
        layer = Image.new("RGBA", (width, layout.line_height), (0, 0, 0, 0))
        pen = ImageDraw.Draw(layer)
        for run in line.runs:
            pen.text(
                (run.x, baseline),
                run.text,
                font=bold if run.bold else regular,
                fill=(*color.as_tuple(), 255),
                anchor="la",
            )

        if layout.condense < 1.0:
            layer = layer.resize(
                (max(1, round(width * layout.condense)), layout.line_height),
                Image.Resampling.LANCZOS,
            )
        left = line.band_left + (line.band_width - layer.width) // 2
        image.paste(layer, (left, line.top), layer)


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

    result = layout_text(
        tokens,
        region.polygon,
        style.face,
        cfg.typeset,
        page_width=page_width,
        page_height=page_height,
        fixed_size=style.size,
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
            shared = first.region.bounds.intersection(second.region.bounds)
            if shared is None:
                continue
            smaller = min(first.region.bounds.area, second.region.bounds.area)
            if smaller > 0 and shared.area / smaller > 0.15:
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
) -> tuple[Image.Image, list[RegionOutcome]]:
    """Apply every region belonging to one page.

    Two passes on purpose. Erasing and drawing one region at a time lets a
    later region's erase wipe lettering an earlier one already drew, wherever
    two polygons overlap — silently, since both regions still report success.
    Every erase therefore happens first, against pixels that hold only the
    original artwork, and only then is any text drawn.
    """
    planned: list[PlannedRegion] = []
    outcomes: list[RegionOutcome] = []
    for region in sorted(regions, key=lambda r: r.order):
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
    for entry in planned:
        rgb = erase(rgb, entry.region, cfg.erase, page_height=page.height)

    image = Image.fromarray(rgb)
    for entry in planned:
        draw_layout(image, entry.layout, entry.style, entry.region.text_color)
    return image, outcomes
