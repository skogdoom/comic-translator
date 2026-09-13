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
from tempfile import TemporaryDirectory

from .config import ApplyConfig
from .errors import ComictransError, FontError, InputError
from .fonts import FontFace, resolve
from .imaging import check_writable, load_page, output_path, save_page
from .model import Plan, Region

# Aliased: imaging.check_writable asks whether a source page can be written
# back out at all, which is a different question about a different file.
from .pack import archive_kind, pack
from .pack import check_writable as check_can_pack
from .progress import CancelCheck, PageProgress, ProgressCallback
from .render import RegionOutcome, RegionStyle, render_page
from .util import is_within

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ApplyReport:
    """What happened, for the end-of-run summary and the exit code."""

    pages_written: list[Path] = field(default_factory=list)
    """The pages this run put on disk: files in the output directory, or —
    when the output was an archive — what each page is called inside it.
    Empty after a cancelled archive run, because nothing survives it."""

    outcomes: list[tuple[str, RegionOutcome]] = field(default_factory=list)
    page_failures: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False
    """Whether the run was stopped part-way rather than reaching the last page."""

    archive: Path | None = None
    """The one file the pages were packed into, when the output was one."""

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
        """A deliberate ``skip: true`` passes; an unfinished translation does not.

        A cancelled run is not ok either. Nothing it wrote is wrong — pages
        are written whole — but pages the plan named are missing, and a caller
        that treated that as success would be reporting a chapter it did not
        render.
        """
        return (
            not self.cancelled
            and not self.page_failures
            and not any(o.unfinished for _, o in self.outcomes)
        )


def source_for(plan_path: Path, region_image: str) -> Path:
    """Absolute path of a plan file's source image."""
    return (plan_path.parent / region_image).resolve()


def check_output_dir(output: Path, sources: list[Path]) -> None:
    """Refuse an output directory that would write into the source tree.

    This is where the invariant that source images are never written to is
    actually enforced, so it is the one refusal in this tool with no
    ``--force`` and no equivalent anywhere else: the command line has no flag
    for it and the review window's render dialog offers no checkbox. The
    wording avoids naming ``--output`` because the window shows it too.
    """
    for source in sources:
        if is_within(output, source):
            raise InputError(
                f"the output directory {output} is inside the source directory "
                f"{source}. Source images are never written to; choose a "
                "directory outside the source tree."
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
    rar_tool: str = "",
    force: bool = False,
    progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> ApplyReport:
    """Render every page the plan refers to, into a directory or an archive.

    ``output`` named ``chapter.cbz`` or ``chapter.cbr`` is a chapter written
    as one file; anything else is a directory of pages. One entry point for
    both, so that neither caller has to decide which loop to run — see
    :mod:`comictrans.pack` for what the name decides and why it is the name
    that decides it.

    **An archive is packed only once every page is rendered.** The pages go
    somewhere temporary and are packed at the end, so cancelling leaves no
    archive at all rather than a truncated one — and nothing to re-run
    *from*, which is the difference from a directory, where a cancelled run
    leaves whole pages and running it again finishes the job. Both promises
    are worth knowing, so both are written down.
    """
    if archive_kind(output) is None:
        return _render_pages(
            plan,
            plan_path,
            output,
            config,
            font=font,
            image_format=image_format,
            force=force,
            progress=progress,
            should_cancel=should_cancel,
        )

    # Asked before a page is rendered rather than after the last one: a
    # chapter is minutes of work, and "there is nothing here to write a CBR
    # with" is an answer that costs nothing to give first.
    check_can_pack(output, rar_tool)
    sources = sorted({source_for(plan_path, image).parent for image in plan.image_names()})
    check_output_dir(output.parent, sources)
    if output.exists() and not force:
        raise InputError(f"{output} already exists; pass --force to overwrite it")

    with TemporaryDirectory(prefix="comictrans-") as workspace:
        report = _render_pages(
            plan,
            plan_path,
            Path(workspace),
            config,
            font=font,
            image_format=image_format,
            # Not ``force``: that answered whether to replace the archive,
            # and was settled above. Nothing pre-exists a directory made a
            # moment ago, so the only thing this can refuse is the run
            # colliding with itself — two plan entries whose filenames are
            # the same, in different folders, flattened onto one name. The
            # directory path reports the second as a page failure; without
            # this the archive would quietly hold the same page twice.
            force=False,
            progress=progress,
            should_cancel=should_cancel,
        )
        if report.cancelled or not report.pages_written:
            # Nothing survives the workspace, so the report must not claim
            # pages were written: what is on disk is what was there before.
            report.pages_written.clear()
            return report
        report.pages_written = [
            Path(name) for name in pack(report.pages_written, output, rar_tool=rar_tool, force=True)
        ]
        report.archive = output
        return report


def _render_pages(
    plan: Plan,
    plan_path: Path,
    output: Path,
    config: ApplyConfig,
    *,
    font: str | None = None,
    image_format: str | None = None,
    force: bool = False,
    progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> ApplyReport:
    """Render every page the plan refers to into the directory ``output``.

    ``progress`` is called once per page, before it is rendered, so a caller
    driving this from a window can say where it has got to. ``should_cancel``
    is asked at the same moment and stops the run when it answers true. Both
    are optional and neither changes what is rendered: this loop is the only
    implementation of "render every page of a plan", and a second copy of it
    outside this module would be a second place for the two to drift.

    Cancelling takes effect between pages, never inside one. What is on disk
    when a run stops is therefore always whole pages, the same files a
    complete run would have written for them, and re-running finishes the job.
    """
    images = plan.image_names()
    sources = [source_for(plan_path, image) for image in images]
    check_output_dir(output, sorted({path.parent for path in sources}))

    try:
        styles = resolve_styles(plan, font)
    except FontError as exc:
        raise ComictransError(str(exc)) from exc

    report = ApplyReport()
    for index, (image, source) in enumerate(zip(images, sources, strict=True)):
        if should_cancel is not None and should_cancel():
            report.cancelled = True
            log.warning("cancelled after %d page(s)", len(report.pages_written))
            break
        if progress is not None:
            progress(PageProgress(index=index, total=len(images), image=image))

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
