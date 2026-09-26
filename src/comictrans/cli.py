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
from .comicinfo import FILENAME as COMIC_INFO
from .comicinfo import ComicInfo, differs
from .config import (
    DEFAULT_COLOR_TOLERANCE,
    DEFAULT_CONDENSE_MIN,
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_ERASE_STRATEGY,
    DEFAULT_FONT_SIZE_FLOOR_RATIO,
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
from .planfile import MergeReport, load_plan, merge_plans, write_plan
from .sources import UnpackReport, default_unpack_dir, is_container, unpack
from .util import is_within
from .validate import ValidateReport, validate_plan

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
    _add_review(subparsers, verbosity)
    _add_read(subparsers, verbosity)
    _add_validate(subparsers, verbosity)
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
            "Reads a single image, a directory of images (non-recursive, natural "
            "filename order), or a chapter file — .cbz, .cbr or .pdf, which is "
            "unpacked into a folder of pages beside it first — and writes one "
            "plan file. Sources are opened read-only."
        ),
    )
    extract_parser.add_argument(
        "input", type=Path, help="source image, directory, or chapter (.cbz, .cbr, .pdf)"
    )
    extract_parser.add_argument(
        "--plan",
        type=Path,
        help="plan file path (default: <dir>/comic-plan.yaml or <stem>-plan.yaml)",
    )
    extract_parser.add_argument(
        "--unpack-dir",
        type=Path,
        help="where to unpack a chapter file (default: <stem>-pages beside it). "
        "Pages already there from an earlier run are left alone",
    )
    existing = extract_parser.add_mutually_exclusive_group()
    existing.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing plan file, discarding everything in it",
    )
    existing.add_argument(
        "--merge",
        action="store_true",
        help="re-detect, then carry translations, notes, skip flags and font "
        "overrides across from the existing plan file by matching regions on "
        "geometry. Hand work with no match in the new detection is reported "
        "and lost, and the run exits non-zero",
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
        "--no-color-segmentation",
        action="store_true",
        help="do not fall back to colour when a grey threshold finds no region. "
        "Recovers balloons and caption boxes that differ from their "
        "surroundings in hue but not brightness; turn it off on a page where "
        "OCR reports text that is not there, since it gives those phantom "
        "regions real-looking shapes",
    )
    extract_parser.add_argument(
        "--color-tolerance",
        type=float,
        default=DEFAULT_COLOR_TOLERANCE,
        metavar="F",
        help="how far a pixel's colour may sit from a region's fill and still "
        "belong to it (default: %(default)s)",
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
        help="render translated pages from a plan file into a directory or a chapter file",
        description=(
            "Reads a plan file with translations filled in and writes new "
            "images. Runs no detection and no OCR: all geometry comes from the "
            "plan, so re-running after editing a translation changes only that "
            "text. Source images are opened read-only. --output named .cbz or "
            ".cbr writes the chapter as one file instead of a directory of "
            "pages; CBR needs the rar compressor, which does not ship with "
            "comictrans."
        ),
    )
    apply_parser.add_argument("plan", type=Path, help="plan file to render")
    apply_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        metavar="DEST",
        help="directory to write pages into, or a .cbz/.cbr to write the whole "
        "chapter as one file; either way it must be outside the source tree",
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
        "'inpaint' reconstructs from surrounding pixels, 'none' paints nothing "
        "at all. A region's own 'erase' overrides this (default: %(default)s)",
    )
    apply_parser.add_argument(
        "--min-font-ratio",
        type=float,
        metavar="F",
        help="smallest glyph height as a fraction of image height "
        "(default: the plan file's font_size_min_ratio)",
    )
    apply_parser.add_argument(
        "--font-size-floor",
        type=float,
        metavar="F",
        help="absolute floor on glyph height as a fraction of image height, "
        "below which a region fails instead of shrinking further "
        f"(default: {DEFAULT_FONT_SIZE_FLOOR_RATIO})",
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


def _add_review(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    verbosity: argparse.ArgumentParser,
) -> None:
    review_parser = subparsers.add_parser(
        "review",
        parents=[verbosity],
        help="open a plan file in the review GUI (needs the 'gui' extra)",
        description=(
            "Opens a plan file for visual review: pages with their region "
            "polygons overlaid, translations and other fields editable in "
            "place, and a live preview through the same render_page apply "
            "itself uses. Needs PySide6: uv sync --extra gui."
        ),
    )
    review_parser.add_argument(
        "plan", type=Path, nargs="?", help="plan file to open (optional; asks for one if omitted)"
    )


def _add_read(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    verbosity: argparse.ArgumentParser,
) -> None:
    read_parser = subparsers.add_parser(
        "read",
        parents=[verbosity],
        help="read a chapter in a window, without translating it (needs the 'gui' extra)",
        description=(
            "Opens a chapter to read: arrow keys page through it, one page or "
            "two at a time, left to right or right to left. Reads what extract "
            "reads — a folder of pages, one image, or a .cbz, .cbr, .zip, .rar "
            "or .pdf — and a chapter file is read where it is, never unpacked: "
            "nothing is written beside it. Needs PySide6: uv sync --extra gui."
        ),
    )
    read_parser.add_argument(
        "input", type=Path, help="folder of pages, one image, or chapter file to read"
    )


def _add_validate(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    verbosity: argparse.ArgumentParser,
) -> None:
    validate_parser = subparsers.add_parser(
        "validate",
        parents=[verbosity],
        help="check a plan file without rendering it, and list everything wrong",
        description=(
            "Answers 'would apply get through this?' in the time it takes to "
            "hash the images: the schema, every page still being the one that "
            "was extracted, every polygon fitting on its own page, and every "
            "font named resolving on this machine. Reports every problem it "
            "finds rather than the first, renders nothing, and writes nothing. "
            "Exits 1 if anything is wrong, including a file that is not a "
            "plan, so a script can stop on it."
        ),
    )
    validate_parser.add_argument("plan", type=Path, help="plan file to check")


def configure_logging(*, verbose: bool, quiet: bool) -> None:
    """Set the root log level. ``-v`` wins if both somehow arrive set."""
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    # force: main() can be called more than once in a process (tests, and
    # `review`, which configures logging and then hands over to the window),
    # and basicConfig is otherwise a no-op after the first call.
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
            color_segmentation=not args.no_color_segmentation,
            color_tolerance=args.color_tolerance,
        ),
        font_size_min_ratio=args.min_font_ratio,
        condense_min=args.condense_min,
    )


def _report_summary(
    report: ExtractReport, plan_path: Path, merged: MergeReport | None = None
) -> None:
    print(f"\nplan file: {plan_path}")
    print(f"  pages read:        {report.pages_read}")
    print(f"  regions found:     {report.regions}")
    print(f"  low confidence:    {report.low_confidence}")
    print(f"  approximate shape: {report.approximate}")
    print(f"  not text-like:     {report.artefacts}")
    print(f"  artwork as text:   {report.on_artwork}")
    for path, reason in report.skipped_inputs:
        print(f"  skipped input:     {path.name} ({reason})")
    for path in report.empty_pages:
        print(f"  NO REGIONS:        {path.name}")
    for path, reason in report.failures:
        print(f"  FAILED:            {path.name}: {reason}")
    if report.unread_languages:
        print(f"  NOT READ BY OCR:   {', '.join(report.unread_languages)}")
    if merged is not None:
        print(f"  carried over:      {len(merged.carried)}")
        print(f"  newly detected:    {len(merged.added)}")
        for region_id in merged.dropped:
            print(f"  LOST HAND WORK:    {region_id} (no match in this detection)")
    if merged is not None and merged.dropped:
        print(
            f"\n{len(merged.dropped)} region(s) from the previous plan had "
            "translations or notes with nowhere to go. Recover them from your "
            "backup or version control before re-running."
        )
    if report.unread_languages:
        print(
            f"\nApple Vision cannot read {', '.join(report.unread_languages)}. It "
            "does not refuse a language it lacks; it reads with its own defaults "
            "instead, so the text above was recognised without it."
        )
    if report.artefacts:
        print(
            f"\n{report.artefacts} region(s) hold something that does not read "
            "as text; their translation was left empty so apply leaves the art "
            "alone. Check them, then delete them or set skip: true."
        )
    if report.on_artwork:
        print(
            f"\n{report.on_artwork} region(s) look like artwork read as text: "
            "lettering far larger than the rest of the page, on a background "
            "that is not a flat ground. Their translation was left empty so "
            "apply leaves the art alone. Check them, then delete them or set "
            "skip: true."
        )
    if report.approximate:
        print(
            f"\n{report.approximate} region(s) have approximate geometry; "
            "check them before translating (--debug-dir shows what was found)."
        )


def _unpack_summary(report: UnpackReport, languages: tuple[str, ...]) -> None:
    print(f"\nunpacked {report.source.name} into {report.directory}")
    print(f"  pages:             {len(report.pages)}")
    if report.reused:
        print(f"  already there:     {report.reused}")
    info = report.comic_info
    if info is not None:
        stated = ", ".join(name.replace("_", " ") for name in info.stated())
        print(f"  {COMIC_INFO + ':':<19}{stated or 'nothing the plan can hold'}")
    for name, reason in report.skipped:
        print(f"  SKIPPED:           {name}: {reason}")
    for name, reason in report.doubtful:
        print(f"  LOOK AT:           {name}: {reason}")
    if report.doubtful:
        print(
            f"\n{len(report.doubtful)} unpacked page(s) may not be a scan of the "
            "page they came from. They are in the directory like any other; look "
            "at them before translating them."
        )
    # Said here, before a page is read, rather than in the summary at the
    # end: the whole chapter is about to be read in the other language, and
    # this is the moment stopping it costs nothing.
    if info is not None and differs(info.language, languages):
        print(
            f"\n{COMIC_INFO} says this chapter is in {info.language}, and it is "
            f"about to be read in {', '.join(languages)}. If the file is right, "
            "stop here and run this again in that language: --source-lang, or "
            "--lang if you gave one."
        )


def run_extract(args: argparse.Namespace) -> int:
    target: Path = args.input
    container = is_container(target)
    if args.unpack_dir is not None and not container:
        raise ComictransError(
            f"--unpack-dir is for a chapter file (.cbz, .cbr, .pdf); {target} is not one."
        )

    # Where the pages will be, before there are any: a chapter file's pages
    # end up in the directory it unpacks into, and that is the source tree
    # --debug-dir has to stay out of.
    source_dir = (
        (args.unpack_dir or default_unpack_dir(target))
        if container
        else (target if target.is_dir() else target.parent)
    )
    if args.debug_dir is not None and is_within(args.debug_dir, source_dir):
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

    # After the font and the recogniser, deliberately: unpacking is the first
    # thing here that writes anything, and a run that was going to fail on a
    # missing font should fail before it has left a directory behind.
    comic_info: ComicInfo | None = None
    if container:
        unpacked = unpack(target, args.unpack_dir)
        _unpack_summary(unpacked, config.ocr.languages)
        target = unpacked.directory
        comic_info = unpacked.comic_info
    plan_path: Path = args.plan or default_plan_path(target)

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
        comic_info=comic_info,
    )
    merged: MergeReport | None = None
    if args.merge:
        if plan_path.is_file():
            # Geometry and text are all that matter here, so a source image
            # that has changed since the old plan is not a reason to refuse.
            previous = load_plan(plan_path, check_images=False)
            plan, merged = merge_plans(previous, plan)
        else:
            log.info("--merge: no existing plan at %s, writing a fresh one", plan_path)

    write_plan(plan, plan_path, force=args.force or args.merge)
    _report_summary(report, plan_path, merged)
    return EXIT_OK if report.ok and (merged is None or not merged.dropped) else EXIT_PROBLEMS


def _apply_config(args: argparse.Namespace, plan: Plan) -> ApplyConfig:
    """Plan header first, CLI flags on top of it."""
    header = plan.header
    return ApplyConfig(
        typeset=TypesetConfig(
            font_size_min_ratio=args.min_font_ratio or header.font_size_min_ratio,
            font_size_floor_ratio=args.font_size_floor or DEFAULT_FONT_SIZE_FLOOR_RATIO,
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
    print(f"  below min size:    {len(report.undersized)}")
    for image, outcome in report.undersized:
        print(f"  SMALL {outcome.font_size:>3}px:      {outcome.region_id} ({image})")
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
    if report.undersized:
        print(
            f"\n{len(report.undersized)} region(s) were rendered below the "
            "readable minimum to make the text fit. Pin a size with font_size "
            "in the plan file, or shorten the translation."
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


def run_review(args: argparse.Namespace) -> int:
    # Imported here, not at module level, so `comictrans` stays importable
    # without PySide6 — the same reason get_recognizer imports the OCR
    # backends from inside itself rather than at the top of ocr/__init__.py.
    from .gui import app as gui_app

    # end: see gui.app.end_process. Nothing here comes after the window.
    return gui_app.run(args.plan, end=True)


def run_read(args: argparse.Namespace) -> int:
    # Lazily, for the reason run_review gives.
    from .gui import app as gui_app

    return gui_app.read(args.input, end=True)


def _validate_summary(report: ValidateReport) -> None:
    print(f"\n{report.plan_path}")
    if report.parsed:
        print(f"  images:   {report.images}")
        print(f"  regions:  {report.regions}")
        print(f"  fonts:    {', '.join(report.fonts) if report.fonts else '(none named)'}")
    if report.ok:
        print("\nno problems: apply would get through this plan.")
        return
    print(f"\n{len(report.problems)} problem(s):")
    for problem in report.problems:
        print(f"  {problem}")


def run_validate(args: argparse.Namespace) -> int:
    report = validate_plan(args.plan)
    _validate_summary(report)
    return EXIT_OK if report.ok else EXIT_PROBLEMS


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose, quiet=args.quiet)

    handlers = {
        "extract": run_extract,
        "apply": run_apply,
        "review": run_review,
        "read": run_read,
        "validate": run_validate,
    }
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
