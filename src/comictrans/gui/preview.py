"""Rendering a live preview of a page, through the exact apply pipeline.

The polygon overlay the canvas draws is enough to catch a geometry problem,
but only a render shows whether a translation actually fits, what it looks
like typeset, or whether two regions draw over each other. This calls
exactly the functions ``apply`` calls on the same plan, built the same way
``cli._apply_config`` builds them, so a preview and a real ``apply`` run can
never disagree about the same region.

No Qt here either. What this module returns is a Pillow image; turning that
into something a widget can paint is the canvas's job.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from ..apply import resolve_styles
from ..config import (
    DEFAULT_FONT_SIZE_FLOOR_RATIO,
    ApplyConfig,
    EraseConfig,
    TypesetConfig,
)
from ..imaging import load_page
from ..render import RegionOutcome, render_page
from .document import PlanDocument


@dataclass(frozen=True, slots=True)
class Preview:
    """One page, rendered exactly as ``apply`` would write it."""

    image: Image.Image
    outcomes: tuple[RegionOutcome, ...]

    @property
    def problems(self) -> tuple[RegionOutcome, ...]:
        """Outcomes worth a second look: failed, undersized, or condensed."""
        return tuple(
            o
            for o in self.outcomes
            if o.failed or o.undersized or (o.rendered and o.condense < 1.0)
        )


def apply_config_for(document: PlanDocument) -> ApplyConfig:
    """The same defaults ``apply`` uses for this plan, with no CLI overrides.

    Deliberately not configurable from here: the preview's whole value is
    that it matches a plain ``comictrans apply`` of the same plan file. A
    font or erase-strategy override belongs on the command line, not folded
    silently into what the GUI happens to show you.
    """
    header = document.plan.header
    return ApplyConfig(
        typeset=TypesetConfig(
            font_size_min_ratio=header.font_size_min_ratio,
            font_size_floor_ratio=DEFAULT_FONT_SIZE_FLOOR_RATIO,
            condense_min=header.condense_min,
            hyphenation_language=header.target_language,
        ),
        erase=EraseConfig(),
    )


def render_preview(document: PlanDocument, image: str) -> Preview:
    """Render one page as ``apply`` would, without writing anything anywhere.

    The source image is opened read-only through the same ``load_page`` apply
    uses; nothing here ever touches it, and nothing here touches the plan
    file either — only ``PlanDocument.save`` writes.
    """
    page = load_page(document.source_path(image))
    styles = resolve_styles(document.plan, cli_font=None)
    rendered, outcomes = render_page(
        page, document.regions_for(image), styles, apply_config_for(document)
    )
    return Preview(rendered, tuple(outcomes))
