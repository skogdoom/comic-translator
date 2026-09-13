"""The extract pass: images in, one plan file out.

Produces no images. Reads every source file read-only. Processes every page
even when one of them fails, and reports at the end — a bad scan halfway
through a chapter should not cost you the other forty pages.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .config import (
    DEFAULT_SOURCE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
    ExtractConfig,
)
from .debug import dump as dump_debug
from .detect import DetectedRegion, find_regions, lines_inside
from .detect.color import interior_uniformity
from .errors import ComictransError
from .imaging import PageImage, collect_inputs, crop_page, load_page
from .model import (
    Box,
    Geometry,
    Plan,
    PlanHeader,
    PlanImage,
    Polygon,
    Region,
    TextCase,
    polygon_bounds,
)
from .ocr import TextRecognizer
from .ocr.grouping import (
    lettering_matches_page,
    looks_like_text,
    median_line_height,
    utterance_confidence,
    utterance_text,
)
from .planfile.schema import PLAN_VERSION
from .progress import CancelCheck, PageProgress, ProgressCallback
from .util import relative_posix, slugify

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ExtractReport:
    """What happened, for the end-of-run summary and the exit code."""

    pages_read: int = 0
    regions: int = 0
    low_confidence: int = 0
    approximate: int = 0
    artefacts: int = 0
    """Regions whose OCR text does not read as language, left unseeded."""
    on_artwork: int = 0
    """Regions that look like artwork OCR read as text, left unseeded."""
    empty_pages: list[Path] = field(default_factory=list)
    skipped_inputs: list[tuple[Path, str]] = field(default_factory=list)
    failures: list[tuple[Path, str]] = field(default_factory=list)
    cancelled: bool = False
    """Whether the run was stopped part-way rather than reaching the last page."""

    @property
    def ok(self) -> bool:
        """False when anything needs your attention before you start translating.

        A cancelled run is not ok either: the plan it would describe covers
        only the pages it got to, which is not the chapter it was asked for.
        """
        return not self.cancelled and not self.failures and not self.empty_pages


PLAN_NAME = "comic-plan.yaml"
"""What a plan is called when it goes in a directory of pages.

Named rather than spelled out twice: the extract dialog has to answer where
a chapter file's plan will go before the directory it goes in exists, so it
cannot ask :func:`default_plan_path`, which decides by looking.
"""


def default_plan_path(target: Path) -> Path:
    """``<dir>/comic-plan.yaml``, or ``<stem>-plan.yaml`` beside a single file."""
    if target.is_dir():
        return target / PLAN_NAME
    return target.with_name(f"{target.stem}-plan.yaml")


def region_box(polygon: Polygon, config: ExtractConfig) -> Box:
    """A region's own box with a margin round it, before any clamping.

    The margin is what makes reading one region worth doing at all: measured
    over the fixture regions that read as language, a crop cut to the
    polygon's own box agreed with what full-page detection read 10 times out
    of 31, and the same crops with a margin agreed 27. Recognisers read a
    line by what surrounds it, and a box drawn to the ink is a line with
    nothing around it.

    A fraction of the box's shorter side, not a number of pixels: nothing
    here knows a scan's resolution. Too much is its own mistake — at a fifth
    of the side the agreement starts to fall back again.
    """
    box = polygon_bounds(polygon)
    pad = round(min(box.width, box.height) * config.region_padding_ratio)
    return Box(box.left - pad, box.top - pad, box.right + pad, box.bottom + pad)


def read_region(
    page: PageImage,
    polygon: Polygon,
    recognizer: TextRecognizer,
    config: ExtractConfig,
) -> str:
    """What the recogniser reads inside one region, as extract would write it.

    For a region drawn by hand, which has no reading at all, and for one
    where detection read the lettering badly. The same recogniser, the same
    grouping, the same text: what comes back is what ``source_text`` would
    have held if this region had come out of a full run.

    **One region, not the page.** Recognising the whole page and keeping the
    lines inside the polygon would need almost no new code and would spend a
    full-page recognition on one balloon — seconds, per balloon, in a window
    where this is a per-region action rather than an occasional one.

    **Lines whose centre falls outside the polygon are dropped.** The margin
    that makes the crop readable is also what lets a neighbour into it, and
    on a dense page the next balloon starts a few pixels away. Measured, it
    is worth a region: 26 of 31 exact without the check and 27 with it, and
    at a fifth of a side — a margin wide enough to reach the neighbour — 22
    against 25. Asked of the centre rather than the whole box, which is how
    ``detect`` asks the same question: lettering grazing an outline still
    belongs to it.
    """
    padded = region_box(polygon, config)
    piece = crop_page(page, padded)
    # Where the crop starts on the page, after crop_page has clamped it to
    # the page's own edges: the lines come back in the crop's coordinates and
    # the polygon is in the page's.
    origin = (max(0, padded.left), max(0, padded.top))
    lines = lines_inside(polygon, recognizer.recognize(piece, config.ocr), origin)
    return utterance_text(lines)


def _region_id(page: PageImage, order: int) -> str:
    """Deterministic for a given page and detection result, and readable in logs."""
    return f"{slugify(page.path.stem)}-{order:03d}"


def _letters_on_artwork(
    page: PageImage, detected: DetectedRegion, page_median: float, config: ExtractConfig
) -> bool:
    """True when a region is artwork that OCR read as text.

    Two tests, because either one alone refuses something real. Oversized
    lettering by itself would throw out a genuine display caption — four of
    them on the screentoned fixture, where halftone noise drags the page
    median down. A non-flat interior by itself would throw out a borderless
    caption lettered straight onto the art, which measures 0.57 there.

    Together they are specific: text far larger than anything else on the
    page, sitting on something that is not a flat ground. That is a window
    frame or a doorway, not lettering.
    """
    if lettering_matches_page(detected.lines, page_median):
        return False
    uniformity = interior_uniformity(
        page.rgb,
        detected.polygon,
        page_height=page.height,
        tolerance=config.detect.uniformity_tolerance,
    )
    return uniformity < config.artefact_uniformity


def _to_region(
    page: PageImage,
    detected: DetectedRegion,
    order: int,
    plan_dir: Path,
    config: ExtractConfig,
    page_median: float,
) -> Region:
    # Rounded here rather than at write time so the in-memory plan and the
    # file on disk are the same thing.
    confidence = round(utterance_confidence(detected.lines), 3)
    source_text = utterance_text(detected.lines)
    # A region whose "text" is an artefact — a window frame, an eye, halftone
    # dots — is kept so it can be checked, but not seeded: seeding would make
    # it actionable, and apply would erase the artwork to letter nonsense onto
    # it. Left empty, apply leaves it alone and the run says so.
    #
    # Two tests, because artwork can read as a perfectly good word. A window
    # frame came back as "INA", passed for language, and was lettered back
    # onto the page six times the size of the real text around it — erasing
    # the frame it was read from on the way.
    readable = looks_like_text(source_text) and not _letters_on_artwork(
        page, detected, page_median, config
    )
    return Region(
        id=_region_id(page, order),
        image=relative_posix(page.path, plan_dir),
        order=order,
        geometry=detected.geometry,
        polygon=detected.polygon,
        fill_color=detected.fill_color,
        text_color=detected.text_color,
        confidence=confidence,
        source_text=source_text,
        # Seeded with the source so the text is edited into the target
        # language in place rather than retyped into a blank field. Until it
        # is, the region still reads as untranslated: apply reports every
        # translation that is still identical to its source_text.
        translation=source_text if readable else "",
        notes="",
        low_confidence=confidence < config.ocr.confidence_threshold,
    )


def extract_page(
    path: Path,
    recognizer: TextRecognizer,
    config: ExtractConfig,
    plan_dir: Path,
    debug_dir: Path | None = None,
) -> tuple[PlanImage, list[Region]]:
    """Detect and OCR one page. Order restarts at 1 on every page.

    Returns the page's identity as well as its regions, because a page
    belongs in the plan whether or not anything was found on it.
    """
    page = load_page(path)
    lines = recognizer.recognize(page, config.ocr)
    detected = find_regions(page, lines, config.detect)
    if debug_dir is not None:
        dump_debug(page, detected, config.detect, debug_dir)
    # One yardstick for the whole page, from every line on it, so a region is
    # measured against the page rather than against itself.
    page_median = median_line_height([line for region in detected for line in region.lines])
    identity = PlanImage(name=relative_posix(page.path, plan_dir), sha256=page.sha256)
    return identity, [
        _to_region(page, region, order, plan_dir, config, page_median)
        for order, region in enumerate(detected, start=1)
    ]


def extract(
    target: Path,
    plan_path: Path,
    recognizer: TextRecognizer,
    font_family: str,
    config: ExtractConfig,
    *,
    case: TextCase = TextCase.UPPER,
    source_language: str = DEFAULT_SOURCE_LANGUAGE,
    target_language: str = DEFAULT_TARGET_LANGUAGE,
    debug_dir: Path | None = None,
    progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> tuple[Plan, ExtractReport]:
    """Run the extract pass over a file or directory.

    ``progress`` is called once per page, before it is read, and
    ``should_cancel`` is asked at the same moment; see
    :mod:`comictrans.progress`. Neither changes what is detected.

    A cancelled run still returns a plan, because this function does not
    write one — the caller does, and the report says not to. Half a chapter
    written as a whole plan file would be a plan that lies about what it
    covers, and unlike a page of output there is no partial form of it that
    is still true.
    """
    images, skipped = collect_inputs(target)
    report = ExtractReport(skipped_inputs=list(skipped))
    for path, reason in skipped:
        log.warning("skipping %s: %s", path.name, reason)

    plan_dir = plan_path.parent
    pages: list[PlanImage] = []
    regions: list[Region] = []
    for index, path in enumerate(images):
        if should_cancel is not None and should_cancel():
            report.cancelled = True
            log.warning("cancelled after %d page(s)", report.pages_read)
            break
        if progress is not None:
            progress(PageProgress(index=index, total=len(images), image=path.name))

        try:
            identity, page_regions = extract_page(path, recognizer, config, plan_dir, debug_dir)
        except ComictransError as exc:
            log.error("%s: %s", path.name, exc)
            report.failures.append((path, str(exc)))
            continue

        report.pages_read += 1
        # Listed whether or not it holds text. A page with no balloons is
        # still a page of the comic: apply copies it through so the output is
        # the whole chapter, and review can show it to have one drawn on.
        pages.append(identity)
        if not page_regions:
            log.warning("%s: no text regions found", path.name)
            report.empty_pages.append(path)
            continue

        regions.extend(page_regions)
        report.regions += len(page_regions)
        report.low_confidence += sum(1 for r in page_regions if r.low_confidence)
        report.approximate += sum(1 for r in page_regions if r.geometry is Geometry.APPROXIMATE)
        unseeded = [r for r in page_regions if not r.translation]
        report.artefacts += sum(1 for r in unseeded if not looks_like_text(r.source_text))
        report.on_artwork += sum(1 for r in unseeded if looks_like_text(r.source_text))
        log.info("%s: %d region(s)", path.name, len(page_regions))

    header = PlanHeader(
        version=PLAN_VERSION,
        generator=f"comictrans {__version__}",
        created=datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        source_language=source_language,
        target_language=target_language,
        ocr_engine=recognizer.name,
        font=font_family,
        case=case,
        font_size_min_ratio=config.font_size_min_ratio,
        condense_min=config.condense_min,
    )
    return Plan(header=header, images=tuple(pages), regions=tuple(regions)), report
