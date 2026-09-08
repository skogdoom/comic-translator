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
from .detect import DetectedRegion, find_regions
from .detect.color import interior_uniformity
from .errors import ComictransError
from .imaging import PageImage, collect_inputs, load_page
from .model import Geometry, Plan, PlanHeader, Region, TextCase
from .ocr import TextRecognizer
from .ocr.grouping import (
    lettering_matches_page,
    looks_like_text,
    median_line_height,
    utterance_confidence,
    utterance_text,
)
from .planfile.schema import PLAN_VERSION
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

    @property
    def ok(self) -> bool:
        """False when anything needs your attention before you start translating."""
        return not self.failures and not self.empty_pages


def default_plan_path(target: Path) -> Path:
    """``<dir>/comic-plan.yaml``, or ``<stem>-plan.yaml`` beside a single file."""
    if target.is_dir():
        return target / "comic-plan.yaml"
    return target.with_name(f"{target.stem}-plan.yaml")


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
        image_sha256=page.sha256,
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
) -> list[Region]:
    """Detect and OCR one page. Order restarts at 1 on every page."""
    page = load_page(path)
    lines = recognizer.recognize(page, config.ocr)
    detected = find_regions(page, lines, config.detect)
    if debug_dir is not None:
        dump_debug(page, detected, config.detect, debug_dir)
    # One yardstick for the whole page, from every line on it, so a region is
    # measured against the page rather than against itself.
    page_median = median_line_height([line for region in detected for line in region.lines])
    return [
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
) -> tuple[Plan, ExtractReport]:
    """Run the extract pass over a file or directory."""
    images, skipped = collect_inputs(target)
    report = ExtractReport(skipped_inputs=list(skipped))
    for path, reason in skipped:
        log.warning("skipping %s: %s", path.name, reason)

    plan_dir = plan_path.parent
    regions: list[Region] = []
    for path in images:
        try:
            page_regions = extract_page(path, recognizer, config, plan_dir, debug_dir)
        except ComictransError as exc:
            log.error("%s: %s", path.name, exc)
            report.failures.append((path, str(exc)))
            continue

        report.pages_read += 1
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
    return Plan(header=header, regions=tuple(regions)), report
