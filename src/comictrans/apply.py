"""The apply pass: a plan file in, translated pages out.

Runs no detection and no OCR. Every polygon, colour and font decision comes
from the plan file, so the pass is deterministic and re-runnable: change a
translation, run it again, and only that text changes.

Source images are opened read-only and everything is written into the
explicit output directory, which may not be the source directory or anywhere
inside it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .config import ApplyConfig
from .errors import ComictransError, FontError, InputError
from .fonts import FontFace, resolve
from .imaging import check_writable, load_page, output_path, save_page
from .model import Plan, Region
from .render import RegionOutcome, RegionStyle, render_page
from .util import is_within

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ApplyReport:
    """What happened, for the end-of-run summary and the exit code."""

    pages_written: list[Path] = field(default_factory=list)
    outcomes: list[tuple[str, RegionOutcome]] = field(default_factory=list)
    page_failures: list[tuple[str, str]] = field(default_factory=list)

    def _count(self, status: str) -> int:
        return sum(1 for _, outcome in self.outcomes if outcome.status == status)

    @property
    def rendered(self) -> int:
        return self._count("rendered")

    @property
    def skipped_empty(self) -> int:
        return self._count("skipped_empty")

    @property
    def skipped_flag(self) -> int:
        return self._count("skipped_flag")

    @property
    def failed(self) -> int:
        return self._count("failed")

    @property
    def undersized(self) -> list[tuple[str, RegionOutcome]]:
        """Regions rendered below the readable minimum to make the text fit."""
        return [(i, o) for i, o in self.outcomes if o.undersized]

    @property
    def unedited(self) -> list[tuple[str, RegionOutcome]]:
        """Rendered regions whose translation is still the extracted source."""
        return [(i, o) for i, o in self.outcomes if o.unedited]

    @property
    def condensed(self) -> list[tuple[str, RegionOutcome]]:
        return [(i, o) for i, o in self.outcomes if o.rendered and o.condense < 1.0]

    @property
    def ok(self) -> bool:
        """A deliberate ``skip: true`` passes; an unfinished translation does not."""
        return not self.page_failures and not any(o.unfinished for _, o in self.outcomes)


def source_for(plan_path: Path, region_image: str) -> Path:
    """Absolute path of a plan file's source image."""
    return (plan_path.parent / region_image).resolve()


def check_output_dir(output: Path, sources: list[Path]) -> None:
    """Refuse an output directory that would write into the source tree."""
    for source in sources:
        if is_within(output, source):
            raise InputError(
                f"--output {output} is inside the source directory {source}. "
                "Source images are never written to; choose a directory "
                "outside the source tree."
            )


def resolve_styles(
    plan: Plan, cli_font: str | None, *, require_bold: bool = True
) -> dict[str, RegionStyle]:
    """Resolve the font for every region, honouring the precedence rules.

    Highest first: ``--font`` on the command line, then a region's own
    ``font``, then the header's. A CLI override of what the plan file says is
    logged, because the plan is the record and silently beating it is the
    substitution the spec forbids.
    """
    faces: dict[str, FontFace] = {}
    styles: dict[str, RegionStyle] = {}
    announced = False

    for region in plan.regions:
        planned = region.font or plan.header.font
        family = cli_font or planned
        if cli_font is not None and cli_font != planned and not announced:
            log.warning("--font %r overrides the plan file's %r", cli_font, planned)
            announced = True
        if family not in faces:
            faces[family] = resolve(family, require_bold=require_bold)
        styles[region.id] = RegionStyle(
            face=faces[family], case=plan.header.case, size=region.font_size
        )
    return styles


def apply_plan(
    plan: Plan,
    plan_path: Path,
    output: Path,
    config: ApplyConfig,
    *,
    font: str | None = None,
    image_format: str | None = None,
    force: bool = False,
) -> ApplyReport:
    """Render every page the plan refers to into ``output``."""
    images = plan.images()
    sources = [source_for(plan_path, image) for image in images]
    check_output_dir(output, sorted({path.parent for path in sources}))

    try:
        styles = resolve_styles(plan, font)
    except FontError as exc:
        raise ComictransError(str(exc)) from exc

    report = ApplyReport()
    for image, source in zip(images, sources, strict=True):
        regions: tuple[Region, ...] = plan.regions_for(image)
        try:
            page = load_page(source)
            check_writable(page.meta, source)
            destination = output_path(source, output, page.meta, image_format)
            if destination.exists() and not force:
                raise InputError(f"{destination} already exists; pass --force to overwrite it")
            rendered, outcomes = render_page(page, regions, styles, config)
            save_page(rendered, destination, page.meta, image_format, page.alpha)
        except ComictransError as exc:
            log.error("%s: %s", image, exc)
            report.page_failures.append((image, str(exc)))
            continue

        report.pages_written.append(destination)
        report.outcomes.extend((image, outcome) for outcome in outcomes)
        rendered_count = sum(1 for outcome in outcomes if outcome.rendered)
        log.info("%s -> %s (%d region(s))", image, destination.name, rendered_count)
        for outcome in outcomes:
            if outcome.status == "skipped_empty":
                log.warning("region %s skipped: %s", outcome.region_id, outcome.detail)
            elif outcome.failed:
                log.error("region %s failed: %s", outcome.region_id, outcome.detail)

    return report
