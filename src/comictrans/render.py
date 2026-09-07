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

import numpy as np
from PIL import Image, ImageDraw

from .config import ApplyConfig
from .erase import erase
from .fonts import FontFace
from .imaging import PageImage, RgbArray
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


def render_region(
    rgb: RgbArray,
    region: Region,
    style: RegionStyle,
    cfg: ApplyConfig,
    *,
    page_width: int,
    page_height: int,
) -> tuple[RgbArray, RegionOutcome]:
    """Erase one region's lettering and typeset its translation in place."""
    if region.skip:
        return rgb, RegionOutcome(region.id, "skipped_flag", "marked skip: true")
    if not region.translation.strip():
        return rgb, RegionOutcome(region.id, "skipped_empty", "no translation")

    try:
        tokens = tokenize(region.translation, case=style.case)
    except MarkupError as exc:
        return rgb, RegionOutcome(region.id, "failed", str(exc))
    if not tokens:
        return rgb, RegionOutcome(region.id, "skipped_empty", "translation is only markup")

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
        # is left exactly as it was, so the page stays readable in Italian
        # rather than becoming a blank balloon.
        return rgb, RegionOutcome(region.id, "failed", result.reason)

    erased = erase(rgb, region, cfg.erase, page_height=page_height)
    image = Image.fromarray(erased)
    draw_layout(image, result, style, region.text_color)
    out = np.asarray(image, dtype=np.uint8)

    if result.condensed:
        log.info(
            "region %s: condensed to %.0f%% at %dpx to fit",
            region.id,
            result.condense * 100,
            result.font_size,
        )
    return out, RegionOutcome(region.id, "rendered", condense=result.condense)


def render_page(
    page: PageImage,
    regions: tuple[Region, ...],
    styles: dict[str, RegionStyle],
    cfg: ApplyConfig,
) -> tuple[Image.Image, list[RegionOutcome]]:
    """Apply every region belonging to one page."""
    rgb = page.rgb
    outcomes: list[RegionOutcome] = []
    for region in sorted(regions, key=lambda r: r.order):
        rgb, outcome = render_region(
            rgb,
            region,
            styles[region.id],
            cfg,
            page_width=page.width,
            page_height=page.height,
        )
        outcomes.append(outcome)
    return Image.fromarray(rgb), outcomes
