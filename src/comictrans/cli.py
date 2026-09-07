"""``comictrans`` command line entry point.

Exit codes:
  0  everything succeeded
  1  the run completed but something needs your attention
  2  the run could not start (bad arguments, no OCR backend, no font)
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .apply import ApplyReport, apply_plan
from .config import (
    DEFAULT_CONDENSE_MIN,
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_ERASE_STRATEGY,
    DEFAULT_FONT_SIZE_MIN_RATIO,
    DEFAULT_MAX_CONTOUR_AREA_RATIO,
    DEFAULT_MAX_EXTENT_RATIO,
    DEFAULT_MIN_SOLIDITY,
    DEFAULT_SOURCE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
    ApplyConfig,
    DetectConfig,
    EraseConfig,
    ExtractConfig,
    OcrConfig,
    TypesetConfig,
)
from .erase import STRATEGIES
from .errors import ComictransError, FontError
from .extract import ExtractReport, default_plan_path, extract
from .fonts import FONT_PATH_ENV, resolve
from .model import Plan, TextCase
from .ocr import get_recognizer
from .planfile import load_plan, write_plan
from .util import is_within

log = logging.getLogger("comictrans")

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_FATAL = 2


def _verbosity_parser() -> argparse.ArgumentParser:
    """Verbosity flags, shared so they work on either side of the subcommand.

    ``comictrans -v extract pages/`` and ``comictrans extract pages/ -v`` are
    the same thing. The defaults are SUPPRESS so an unset flag on one parser
    cannot clobber the same flag set on the other; ``build_parser`` supplies
    the real defaults once.
    """
    parser = argparse.ArgumentParser(add_help=False)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="log detection detail",
    )
    group.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        default=argparse.SUPPRESS,
        help="warnings and errors only",
    )
    return parser


def build_parser() -> argparse.ArgumentParser:
    verbosity = _verbosity_parser()
    parser = argparse.ArgumentParser(
        prog="comictrans",
        parents=[verbosity],
        description=(
            "Translate scanned comic pages in two passes: 'extract' writes a plan "
            "file of the source-language text, you translate it by hand, "
            "'apply' renders new images. Source images are never modified."
        ),
    )
    parser.add_argument("--version", action="version", version=f"comictrans {__version__}")
    parser.set_defaults(verbose=False, quiet=False)

    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_extract(subparsers, verbosity)
    _add_apply(subparsers, verbosity)
    return parser


def _add_extract(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    verbosity: argparse.ArgumentParser,
) -> None:
    extract_parser = subparsers.add_parser(
        "extract",
        parents=[verbosity],
        help="detect and OCR text regions, write a plan file (produces no images)",
        description=(
            "Reads a single image or a directory of images (non-recursive, natural "
            "filename order) and writes one plan file. Sources are opened read-only."
        ),
    )
    extract_parser.add_argument("input", type=Path, help="source image or directory")
    extract_parser.add_argument(
        "--plan",
        type=Path,
        help="plan file path (default: <dir>/comic-plan.yaml or <stem>-plan.yaml)",
    )
    extract_parser.add_argument(
        "--force", action="store_true", help="overwrite an existing plan file"
    )
    extract_parser.add_argument(
        "--ocr",
        default="auto",
        choices=("auto", "vision", "tesseract"),
        help="OCR backend (default: auto, Apple Vision then Tesseract)",
    )
    extract_parser.add_argument(
        "--source-lang",
        default=DEFAULT_SOURCE_LANGUAGE,
        metavar="CODE",
        help="language the pages are lettered in, recorded in the plan file (default: %(default)s)",
    )
    extract_parser.add_argument(
        "--target-lang",
        default=DEFAULT_TARGET_LANGUAGE,
        metavar="CODE",
        help="language the translations will be written in, recorded in the "
        "plan file and used to pick a hyphenation dictionary "
        "(default: %(default)s)",
    )
    extract_parser.add_argument(
        "--lang",
        action="append",
        metavar="CODE",
        help="OCR language, repeatable. Defaults to --source-lang; give a "
        "region-qualified tag here if the recogniser needs one, e.g. pt-BR",
    )
    extract_parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=DEFAULT_CONFIDENCE_THRESHOLD,
        metavar="F",
        help=f"flag regions below this OCR confidence (default: {DEFAULT_CONFIDENCE_THRESHOLD})",
    )
    extract_parser.add_argument(
        "--font",
        metavar="NAME",
        help="font family or file recorded in the plan header (default: Comic Sans MS, "
        "falling back to Chalkboard SE, Marker Felt, Noteworthy, Helvetica)",
    )
    extract_parser.add_argument(
        "--case",
        default=TextCase.UPPER.value,
        choices=[case.value for case in TextCase],
        help="how apply cases the translation (default: upper)",
    )
    extract_parser.add_argument(
        "--min-font-ratio",
        type=float,
        default=DEFAULT_FONT_SIZE_MIN_RATIO,
        metavar="F",
        help=f"smallest readable glyph height as a fraction of image height "
        f"(default: {DEFAULT_FONT_SIZE_MIN_RATIO})",
    )
    extract_parser.add_argument(
        "--condense-min",
        type=float,
        default=DEFAULT_CONDENSE_MIN,
        metavar="F",
        help=f"horizontal condensing floor for apply (default: {DEFAULT_CONDENSE_MIN})",
    )
    extract_parser.add_argument(
        "--max-region-area",
        type=float,
        default=DEFAULT_MAX_CONTOUR_AREA_RATIO,
        metavar="F",
        help="largest balloon contour as a fraction of the page; lower it when "
        "detection escapes into the artwork (default: %(default)s)",
    )
    extract_parser.add_argument(
        "--min-solidity",
        type=float,
        default=DEFAULT_MIN_SOLIDITY,
        metavar="F",
        help="minimum contour area / hull area; lower it for irregular balloons "
        "(default: %(default)s)",
    )
    extract_parser.add_argument(
        "--max-extent-ratio",
        type=float,
        default=DEFAULT_MAX_EXTENT_RATIO,
        metavar="F",
        help="largest share of the page a region may span in either direction; "
        "lower it when a region swallows a band of artwork (default: %(default)s)",
    )
    extract_parser.add_argument(
        "--debug-dir",
        type=Path,
        metavar="DIR",
        help="dump polygon and threshold-mask overlays here for inspection",
    )


def _add_apply(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    verbosity: argparse.ArgumentParser,
) -> None:
    apply_parser = subparsers.add_parser(
        "apply",
        parents=[verbosity],
        help="render translated pages from a plan file into an output directory",
        description=(
            "Reads a plan file with translations filled in and writes new "
            "images. Runs no detection and no OCR: all geometry comes from the "
            "plan, so re-running after editing a translation changes only that "
            "text. Source images are opened read-only."
        ),
    )
    apply_parser.add_argument("plan", type=Path, help="plan file to render")
    apply_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        metavar="DIR",
        help="directory to write pages into; must be outside the source tree",
    )
    apply_parser.add_argument(
        "--force", action="store_true", help="overwrite existing output files"
    )
    apply_parser.add_argument(
        "--font",
        metavar="NAME",
        help="font family or file, overriding the plan file for every region",
    )
    apply_parser.add_argument(
        "--format",
        dest="image_format",
        choices=("png", "jpeg", "tiff"),
        help="output format (default: match the source, except JPEG becomes PNG)",
    )
    apply_parser.add_argument(
        "--erase",
        default=DEFAULT_ERASE_STRATEGY,
        choices=tuple(sorted(STRATEGIES)),
        help="how to remove the original lettering: 'flat' repaints the glyphs "
        "with the region's fill colour, 'polygon' floods the whole interior, "
        "'inpaint' reconstructs from surrounding pixels (default: %(default)s)",
    )
    apply_parser.add_argument(
        "--min-font-ratio",
        type=float,
        metavar="F",
        help="smallest glyph height as a fraction of image height "
        "(default: the plan file's font_size_min_ratio)",
    )
    apply_parser.add_argument(
        "--condense-min",
        type=float,
        metavar="F",
        help="horizontal condensing floor (default: the plan file's condense_min)",
    )
    apply_parser.add_argument(
        "--no-hyphenation", action="store_true", help="never hyphenate to make a line fit"
    )
    apply_parser.add_argument(
        "--skip-hash-check",
        action="store_true",
        help="render even if a source image no longer matches the plan file. "
        "The polygons were measured against the original pixels, so this is "
        "very likely to put text in the wrong place",
    )


def configure_logging(*, verbose: bool, quiet: bool) -> None:
    """Set the root log level. ``-v`` wins if both somehow arrive set."""
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    # force: main() can be called more than once in a process (tests, the
    # future GUI), and basicConfig is otherwise a no-op after the first call.
    logging.basicConfig(
        level=level, format="%(levelname)s %(message)s", stream=sys.stderr, force=True
    )
    # -v is for detection detail. Pillow's debug log dumps every PNG chunk,
    # which buries exactly what you turned -v on to read.
    for noisy in ("PIL", "matplotlib"):
        logging.getLogger(noisy).setLevel(max(level, logging.INFO))


def _build_config(args: argparse.Namespace) -> ExtractConfig:
    languages = tuple(args.lang) if args.lang else (args.source_lang,)
    return ExtractConfig(
        ocr=OcrConfig(
            languages=languages,
            confidence_threshold=args.confidence_threshold,
            engine=args.ocr,
        ),
        detect=DetectConfig(
            max_contour_area_ratio=args.max_region_area,
            min_solidity=args.min_solidity,
            max_extent_ratio=args.max_extent_ratio,
        ),
        font_size_min_ratio=args.min_font_ratio,
        condense_min=args.condense_min,
    )


def _report_summary(report: ExtractReport, plan_path: Path) -> None:
    print(f"\nplan file: {plan_path}")
    print(f"  pages read:        {report.pages_read}")
    print(f"  regions found:     {report.regions}")
    print(f"  low confidence:    {report.low_confidence}")
    print(f"  approximate shape: {report.approximate}")
    for path, reason in report.skipped_inputs:
        print(f"  skipped input:     {path.name} ({reason})")
    for path in report.empty_pages:
        print(f"  NO REGIONS:        {path.name}")
    for path, reason in report.failures:
        print(f"  FAILED:            {path.name}: {reason}")
    if report.approximate:
        print(
            f"\n{report.approximate} region(s) have approximate geometry; "
            "check them before translating (--debug-dir shows what was found)."
        )


def run_extract(args: argparse.Namespace) -> int:
    target: Path = args.input
    plan_path: Path = args.plan or default_plan_path(target)

    if args.debug_dir is not None:
        source_dir = target if target.is_dir() else target.parent
        if is_within(args.debug_dir, source_dir):
            raise ComictransError(
                f"--debug-dir {args.debug_dir} is inside the source directory "
                f"{source_dir}. Debug dumps are images; keep them away from sources."
            )

    config = _build_config(args)

    try:
        face = resolve(args.font)
    except FontError as exc:
        raise ComictransError(
            f"{exc}\nNo font file ships with comictrans. Add a directory to "
            f"{FONT_PATH_ENV} if your fonts live somewhere non-standard."
        ) from exc
    recognizer = get_recognizer(config.ocr)
    log.info("OCR backend: %s", recognizer.name)

    plan, report = extract(
        target,
        plan_path,
        recognizer,
        face.family,
        config,
        case=TextCase(args.case),
        source_language=args.source_lang,
        target_language=args.target_lang,
        debug_dir=args.debug_dir,
    )
    write_plan(plan, plan_path, force=args.force)
    _report_summary(report, plan_path)
    return EXIT_OK if report.ok else EXIT_PROBLEMS


def _apply_config(args: argparse.Namespace, plan: Plan) -> ApplyConfig:
    """Plan header first, CLI flags on top of it."""
    header = plan.header
    return ApplyConfig(
        typeset=TypesetConfig(
            font_size_min_ratio=args.min_font_ratio or header.font_size_min_ratio,
            condense_min=args.condense_min or header.condense_min,
            hyphenate=not args.no_hyphenation,
            # Hyphenation follows the language being written, not a fixed one.
            hyphenation_language=header.target_language,
        ),
        erase=EraseConfig(strategy=args.erase),
    )


def _apply_summary(report: ApplyReport, output: Path) -> None:
    print(f"\noutput: {output}")
    print(f"  pages written:     {len(report.pages_written)}")
    print(f"  regions rendered:  {report.rendered}")
    print(f"  skipped (no text): {report.skipped_empty}")
    print(f"  skipped (skip:):   {report.skipped_flag}")
    print(f"  failed to fit:     {report.failed}")
    print(f"  same as source:    {len(report.unedited)}")
    for image, outcome in report.condensed:
        print(f"  CONDENSED {outcome.condense:.0%}:  {outcome.region_id} ({image})")
    for image, outcome in report.outcomes:
        if outcome.status == "skipped_empty":
            print(f"  NO TRANSLATION:    {outcome.region_id} ({image})")
        elif outcome.failed:
            print(f"  FAILED:            {outcome.region_id}: {outcome.detail}")
    for image, reason in report.page_failures:
        print(f"  PAGE FAILED:       {image}: {reason}")
    for image, outcome in report.unedited:
        print(f"  NOT TRANSLATED:    {outcome.region_id} ({image})")
    if report.unedited:
        print(
            f"\n{len(report.unedited)} region(s) still hold the extracted "
            "source text and were re-lettered as-is."
        )
    if report.condensed:
        print(
            f"\n{len(report.condensed)} region(s) needed condensing; set a "
            "font_size or widen the polygon in the plan file to hand-tune them."
        )


def run_apply(args: argparse.Namespace) -> int:
    plan_path: Path = args.plan
    plan = load_plan(plan_path, check_images=not args.skip_hash_check)
    if args.skip_hash_check:
        log.warning(
            "--skip-hash-check: polygons are being used against pixels they were not measured from"
        )

    report = apply_plan(
        plan,
        plan_path,
        args.output,
        _apply_config(args, plan),
        font=args.font,
        image_format=args.image_format,
        force=args.force,
    )
    _apply_summary(report, args.output)
    return EXIT_OK if report.ok else EXIT_PROBLEMS


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose, quiet=args.quiet)

    handlers = {"extract": run_extract, "apply": run_apply}
    try:
        return handlers[args.command](args)
    except ComictransError as exc:
        log.error("%s", exc)
        return EXIT_FATAL
    except KeyboardInterrupt:  # pragma: no cover
        log.error("interrupted")
        return EXIT_FATAL


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
