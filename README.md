# comictrans

Translate scanned comic pages from Italian to English in two passes, with the
translation done by hand in between.

```
comictrans extract pages/            # -> pages/comic-plan.yaml, no images written
$EDITOR pages/comic-plan.yaml        # fill in the translation: fields
comictrans apply pages/comic-plan.yaml --output out/
```

**Source images are never modified, moved, or written to.** They are opened
read-only, and nothing but `--debug-dir` and (in milestone 2) `--output` is
ever written.

## Status

Milestones 1 and 2 are implemented: `extract` writes a plan file, `apply`
renders translated pages from it. Milestone 3 (CBZ/PDF input) and milestone 4
(the PySide review GUI) are not started.

## Requirements

- macOS on Apple Silicon, Python 3.12
- Apple Vision for OCR, via pyobjc (installed automatically on macOS)
- Tesseract as an optional fallback: `uv sync --extra tesseract` plus a
  `tesseract` binary with the `ita` language data

Everything runs locally. There are no network calls anywhere in the pipeline —
translation is manual by design.

## Install

```
uv sync --group dev
uv run comictrans --help
```

## extract

```
uv run comictrans extract pages/ --debug-dir /tmp/comictrans-debug
```

Takes a single image or a directory. Directory scans are non-recursive, accept
`.png .jpg .jpeg .tif .tiff`, and sort in natural filename order, so `page2`
comes before `page10`. Anything else is skipped and logged.

The plan file defaults to `<dir>/comic-plan.yaml`, or `<stem>-plan.yaml` beside
a single input file. `extract` refuses to overwrite an existing plan file —
it may hold hours of translation — unless you pass `--force`.

`--debug-dir` writes two images per page: `<stem>-regions.png` with the
polygons (green = traced from a balloon contour, orange = approximate), the
OCR boxes, and the reading-order index; and `<stem>-masks.png` with the two
threshold polarities the contour search actually ran on. It refuses to write
inside the source directory.

Useful when detection misbehaves:

| flag | when |
| --- | --- |
| `--max-region-area` | lower it when a broken balloon outline lets a contour escape into the artwork |
| `--max-extent-ratio` | lower it when a region swallows a band of artwork; balloons measure 0.25-0.45 of page width |
| `--min-solidity` | lower it for irregular or spiky balloons |
| `--confidence-threshold` | raise it to flag more regions for checking |
| `--ocr tesseract` | force the fallback backend |

## apply

```
uv run comictrans apply pages/comic-plan.yaml --output out/
```

Reads the plan file, erases the original lettering, and typesets the
translation into the same balloon. It runs **no detection and no OCR**: every
polygon and colour was measured at extract time and recorded, so the pass is
deterministic. Edit a translation, run it again, and only that text changes.

`--output` is required and must be outside the source tree — apply refuses to
run if it is the source directory or anywhere inside it. Existing output files
are never overwritten without `--force`. Filenames are mirrored flat into the
output directory.

A region with an empty `translation` is left untouched and reported as
skipped, which fails the run: unfinished work should be visible. A region with
`skip: true` is a decision you made, so it passes quietly.

A region that will not fit is reported by id and left **completely** alone —
not erased, not half-drawn. The page stays readable in Italian rather than
becoming a blank balloon.

| flag | what it does |
| --- | --- |
| `--font NAME` | override the font for every region; logged, because the plan file is the record |
| `--format {png,jpeg,tiff}` | override the output format |
| `--erase {flat,polygon,inpaint}` | how to remove the old lettering (see below) |
| `--min-font-ratio` / `--condense-min` | override the plan header's fit limits |
| `--no-hyphenation` | never hyphenate to make a line fit |
| `--skip-hash-check` | render against a changed source; almost always wrong |

### Erasing

- `flat` (default) masks the glyph pixels by colour distance to the region's
  recorded `text_color` and repaints them with `fill_color`. Judged relative to
  the gap between the two colours, so light-on-dark works the same way.
- `polygon` floods the whole polygon interior. Cleanest for a plain balloon,
  wrong for anything flagged `geometry: approximate`, since it paints over art.
- `inpaint` reconstructs the masked pixels from their surroundings, for
  textured balloons and borderless captions where a flat patch would read as a
  hole.

### Fitting

Text is fitted to the **polygon**, not its bounding box: for each line's
vertical band the usable width is the widest horizontal run inside the polygon
on every row of that band. That is what keeps a line from running out through
the curve of a balloon.

The strategy is fixed and ordered: shrink to the minimum readable size, then
condense horizontally, never past the floor, then fail and name the region.
Nothing overflows silently, and every region that needed condensing is listed
at the end of the run so you can hand-tune it.

Emphasis is `**bold**` — never italic, and the oblique face is never used. A
literal asterisk is `\*`. Line breaks in a translation are advisory; typeset
reflows to fit the shape.

## The plan file

YAML, UTF-8, stable key order, hand-editable. One entry per detected region.
Comments you add are preserved.

```yaml
version: 1
generator: comictrans 0.1.0
created: 2026-09-06T19:22:04Z
source_language: it
target_language: en
ocr_engine: apple-vision
font: Comic Sans MS
case: upper
font_size_min_ratio: 0.012
condense_min: 0.9
regions:
  - id: page-002-003
    image: page-002.png
    image_sha256: 9f2b1c…
    order: 3
    geometry: exact
    polygon: [[120, 88], [186, 71], [244, 96], [230, 168], [131, 160]]
    fill_color: "#fdfdfa"
    text_color: "#1b1b1b"
    confidence: 0.93
    source_text: |-
      NON CI POSSO
      CREDERE!
    translation: ""
    notes: ""
```

- `translation` empty means the region is left untouched and reported as
  skipped. `skip: true` says you meant it, and silences the report.
- `notes` is yours. comictrans never reads or rewrites it.
- `**bold**` marks emphasis. It renders as bold, never italic; the oblique
  face is never used.
- Coordinates are pixels, origin top-left, integers. Vision's normalised
  bottom-left coordinates are converted inside the OCR adapter and never leak
  past it.
- `geometry: approximate` means no clean balloon contour was found and the
  polygon is a padded box around the text. Check those regions.
- `low_confidence: true` appears on regions whose worst OCR line scored below
  `--confidence-threshold` (default 0.5).
- Per-region `font` and `font_size` override the header.

Loading validates: unknown keys, malformed or self-intersecting polygons, bad
colours, duplicate ids, and image-hash mismatches are all errors that name the
offending line.

## Fonts

Resolved from the system at run time. No font file ships with this tool.

Default is Comic Sans MS from `/System/Library/Fonts/Supplemental/`, falling
back to Chalkboard SE, Marker Felt, Noteworthy, then Helvetica. Whichever
resolves is logged and written into the plan header, so apply is deterministic
and never silently substitutes.

Regular and bold must both resolve. A family with no real bold face is
reported, never faked — Marker Felt, for instance, ships Thin and Wide but no
bold.

Precedence at render time: `--font` on the command line, then a per-region
`font`, then the header `font`. When `--font` overrides what the plan file
says, that is logged.

Set `COMICTRANS_FONT_PATH` (colon-separated) to add font directories.

## Exit codes

| code | meaning |
| --- | --- |
| 0 | everything succeeded |
| 1 | the run completed but something needs attention: for `extract`, a page failed or yielded no regions; for `apply`, a region had no translation or would not fit |
| 2 | the run could not start: bad arguments, no OCR backend, no resolvable font |

Both commands process every page and report at the end. Neither aborts on the
first bad region.

## Out of scope

Sound effects in artwork, hand-lettered SFX, rotated or vertical text, CBZ and
PDF input (milestone 3), and preserving italic emphasis from the source.

## Development

```
uv sync --group dev        # once, and after any dependency change
uv run pytest              # the suite
uv run ruff check .        # lint
uv run ruff format .       # format
uv run mypy                # strict, over src/comictrans
```

Useful while working:

```
uv run pytest -rs                     # show why anything skipped
uv run pytest tests/test_detect.py    # one file
uv run pytest -k polygon              # one topic
uv run pytest -x -vv                  # stop at the first failure, verbose
```

Two groups of tests skip rather than fail when the host cannot run them:

- The font tests borrow a regular/bold TTF pair from the system, because no
  font ships with this repository. They skip if none is found.
- `tests/test_fixtures.py` runs the real pipeline over any images in
  `tests/fixtures/`, and skips when that directory is empty or no OCR backend
  is installed.

`-rs` tells you which. On macOS with `uv sync --group dev` you should see
neither skipping except the empty fixtures directory.

Fixture images go in `tests/fixtures/`; see the README there.
