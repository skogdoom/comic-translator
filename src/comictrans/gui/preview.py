"""Rendering a live preview of a page, through the exact apply pipeline.

The polygon overlay the canvas draws is enough to catch a geometry problem,
but only a render shows whether a translation actually fits, what it looks
like typeset, or whether two regions draw over each other. This calls
exactly the functions ``apply`` calls on the same plan, built the same way
``cli._apply_config`` builds them, so a preview and a real ``apply`` run can
never disagree about the same region.

No Qt here either. What this module returns is a Pillow image; turning that
into something a widget can paint is the canvas's job.

**It takes a frozen ``Plan``, not the open document**, and that is not
tidiness. A preview runs on a worker thread, beside a window whose document
is still being edited: a job holding the live ``PlanDocument`` would be
reading regions out from under whoever is typing into them. It is the same
rule ``RenderJob`` already follows, for the same reason — what a job works
from is settled before it starts. ``PreviewRequest.of`` is where the snapshot
is taken, on the window's thread, where the document belongs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ..apply import resolve_styles
from ..config import (
    DEFAULT_FONT_SIZE_FLOOR_RATIO,
    ApplyConfig,
    EraseConfig,
    TypesetConfig,
)
from ..imaging import load_page
from ..model import Plan
from ..progress import CancelCheck
from ..render import RegionOutcome, RegionProgress, render_page
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


def apply_config_for(plan: Plan) -> ApplyConfig:
    """The same defaults ``apply`` uses for this plan, with no overrides.

    Everything here comes from the plan's own header. Nothing the window is
    doing leaks in, which is what makes a preview worth trusting: it matches
    a plain ``comictrans apply`` of the same plan file rather than showing
    you a page rendered under settings that only exist in this session.

    The render dialog starts from this too, and layers on the one setting it
    asks for — the erase strategy, which is ``--erase`` on the command line.
    That is a choice made in front of you, not folded in silently, and it is
    the only way the two differ.
    """
    header = plan.header
    return ApplyConfig(
        typeset=TypesetConfig(
            font_size_min_ratio=header.font_size_min_ratio,
            font_size_floor_ratio=DEFAULT_FONT_SIZE_FLOOR_RATIO,
            condense_min=header.condense_min,
            hyphenation_language=header.target_language,
        ),
        erase=EraseConfig(),
    )


@dataclass(frozen=True, slots=True)
class PreviewRequest:
    """One page to render, settled before the thread starts.

    A frozen ``Plan`` and a path, not the open ``PlanDocument``: the window
    goes on being edited while a preview runs, and a job reading regions out
    of a document somebody is typing into is the race ``RenderJob`` avoids
    the same way.

    Here rather than beside the job that runs it, because this module imports
    no Qt and the tests for what a preview *is* should not need a display to
    build one. ``run_job`` holds the thread; this holds what the thread is
    given.
    """

    plan: Plan
    plan_path: Path
    image: str

    @classmethod
    def of(cls, document: PlanDocument, image: str) -> PreviewRequest:
        """Take the snapshot, on the thread the document belongs to."""
        return cls(document.plan, document.path, image)


def source_path(plan_path: Path, image: str) -> Path:
    """Where an image lives, resolved against the plan's own directory.

    The same one-liner as ``apply.source_for`` and ``PlanDocument``\'s own,
    and here rather than borrowed from either: this module may not import
    ``apply`` (it would pull the whole pipeline in for one join) and must not
    reach into the document it deliberately no longer holds.
    """
    return (plan_path.parent / image).resolve()


def render_preview(
    plan: Plan,
    plan_path: Path,
    image: str,
    *,
    on_region: RegionProgress | None = None,
    should_cancel: CancelCheck | None = None,
) -> Preview:
    """Render one page as ``apply`` would, without writing anything anywhere.

    The source image is opened read-only through the same ``load_page`` apply
    uses; nothing here ever touches it, and nothing here touches the plan
    file either — only ``PlanDocument.save`` writes.

    Runs on a worker thread, so everything it needs arrives frozen: a
    ``Plan``, which is immutable, and the path its images are resolved
    against. Nothing it touches can be edited while it runs.

    Raises :class:`RenderCancelled` if ``should_cancel`` says so part-way
    through. Nothing is written either way, so an abandoned preview leaves
    exactly what a finished one does: nothing.
    """
    page = load_page(source_path(plan_path, image))
    styles = resolve_styles(plan, cli_font=None)
    rendered, outcomes = render_page(
        page,
        plan.regions_for(image),
        styles,
        apply_config_for(plan),
        on_region=on_region,
        should_cancel=should_cancel,
    )
    return Preview(rendered, tuple(outcomes))
