# comictrans

Translate scanned comic pages from a source language into a target language
in two passes, with the translation done by hand in between.

Defaults are Italian to English; set `--source-lang` and `--target-lang` on
`extract` for any other pair. Both are recorded in the plan file, and the
target language picks the hyphenation dictionary at render time.

```
comictrans extract pages/            # -> pages/comic-plan.yaml, no images written
$EDITOR pages/comic-plan.yaml        # fill in the translation: fields
comictrans apply pages/comic-plan.yaml --output out/
```

**Source images are never modified, moved, or written to.** They are opened
read-only, and nothing but `--debug-dir` and (in milestone 2) `--output` is
ever written.

## Disclaimer

This project relies heavily on vibe coding. Most of it was written by an AI
assistant from prose descriptions, and it has not had the line-by-line human
review you would want before trusting code with anything you care about.
**Run it at your own risk.**

Two things take the edge off that, and neither is a guarantee. Source images
are opened read-only and never written to, so the worst case is a bad output
file rather than a damaged scan — keep your originals backed up anyway. And
`known-bugs.md` lists the defects found so far that have been left unfixed on
purpose; it is a record of what is known to be wrong, not a claim that
nothing else is.

## Status

Milestones 1, 2 and 4 are implemented: `extract` writes a plan file, `apply`
renders translated pages from it, and `review` opens a plan file in a GUI to
check both. Milestone 3 (CBZ/PDF input) is not started.

`docs/ROADMAP.md` covers what is planned and not yet built, in the order it
is worth building.

## Requirements

- macOS on Apple Silicon, Python 3.12
- Apple Vision for OCR, via pyobjc (installed automatically on macOS)
- Tesseract as an optional fallback: `uv sync --extra tesseract` plus a
  `tesseract` binary with language data for your source language
- PySide6 for the `review` GUI, optional: `uv sync --extra gui`

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
it may hold hours of translation — unless you pass `--force` to discard it or
`--merge` to keep it.

### Re-extracting

`--merge` re-detects the pages and then carries your work across from the
existing plan: edited translations, `notes`, `skip` flags and `font` /
`font_size` overrides. Everything measured from the page — polygons, colours,
confidence, the OCR text — comes from the new run, which is the point of
re-running it.

Regions are matched on geometry, not on id: ids are positional, so any change
to detection renumbers them. A region whose hand work finds no match in the
new detection is named under **LOST HAND WORK** and the run exits non-zero,
so it is never quietly dropped.

A translation still identical to its `source_text` is a seed, not hand work,
so it is replaced by the fresh OCR rather than pinning the old read. A
translation you deliberately *cleared* is hand work and is kept.

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
| `--no-color-segmentation` | stop falling back to colour; use it when OCR reports text that is not there |
| `--color-tolerance` | how far a pixel's colour may sit from a region's fill and still belong to it |
| `--source-lang` / `--target-lang` | the language pair, recorded in the plan file (default `it` / `en`) |
| `--lang` | OCR language if the recogniser needs a region-qualified tag; defaults to `--source-lang` |
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

`extract` seeds `translation` with the source text it read, so a region you
have not edited renders that source text back onto the page rather than being
skipped. Those are counted as **same as source** in the summary and named by
region id.
Clearing a `translation` instead leaves the region untouched and reports it as
skipped, which fails the run. A region with `skip: true` is a decision you
made, so it passes quietly.

A region that will not fit is reported by id and left **completely** alone —
not erased, not half-drawn. The page stays readable in the source language
rather than becoming a blank balloon.

A region's polygon is grown until it covers every line of text assigned to
it, because erase clips to the polygon and lettering outside it would survive
and show through the translation. Characters the OCR never reported are the
exception: they are in no box, so nothing knows to cover them.

Every region is erased before any text is drawn, so two overlapping polygons
cannot cut into each other's lettering. They will still draw over each other,
which no rendering order can fix, so overlapping regions are named in a
warning.

| flag | what it does |
| --- | --- |
| `--font NAME` | override the font for every region; logged, because the plan file is the record |
| `--format {png,jpeg,tiff}` | override the output format |
| `--erase {flat,polygon,inpaint}` | how to remove the old lettering (see below) |
| `--min-font-ratio` / `--condense-min` | override the plan header's fit limits |
| `--font-size-floor` | absolute floor below which a region fails instead of shrinking further |
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

The strategy is fixed and ordered:

1. shrink to the minimum readable size,
2. then condense horizontally, never past the condensing floor,
3. then, as a last resort, shrink *below* the size asked for, down to
   `--font-size-floor` — small lettering beats an empty balloon,
4. then fail and name the region.

"The size asked for" is the readable minimum, or a region's own `font_size`
where one is pinned. Nothing overflows silently: regions that needed
condensing, and regions that had to go smaller than asked, are both listed at
the end of a run with the size actually used, so you can hand-tune them.

A region can pin its own size with `font_size` in the plan file. That is
where the fit starts rather than a wall: if the text will not fit at it, the
same fallbacks apply — condense, then shrink below it — and the region is
listed under **below min size** with the size actually used, so the gap
between what you asked for and what you got is never silent. `font_size`
appears in the plan only when you put it there.

Emphasis is `**bold**` — never italic, and the oblique face is never used. A
literal asterisk is `\*`. Line breaks in a translation are advisory; typeset
reflows to fit the shape.

## review

```
uv sync --extra gui
uv run comictrans review pages/comic-plan.yaml
```

Opens a plan file in a window: pages on the left, the current page in the
middle with its region polygons overlaid, one region's fields on the right.
Run it without a path and it asks for a plan file to open. Needs PySide6, a
separate extra like tesseract — most work on this repository never opens a
window. Without it, `review` fails with a clear message rather than an
import error.

The overlay uses the same colours as `--debug-dir`: green for a traced
contour, orange for approximate, violet for a shape edited here by hand. A
region with something worth checking —
approximate geometry, low confidence, held back with no translation, still
identical to its source text, or overlapping another region — is outlined
dashed red instead, regardless of its geometry colour, and the reason is
named in the inspector. Click a region to select it; its fields are on the
right, editable in place, written straight into the plan as you type.

**Edit > Edit Region Shape** (`Ctrl+E`) puts a handle on every corner of the
selected region. Drag a corner to move it, drag anywhere inside the outline
to move the whole shape, and double-click an edge to add a corner or a corner
to remove it — four corners is what detection produces for a caption box and
never enough to trace a balloon. `Esc` abandons a drag in progress. Each drag
is one undo step, and a shape a plan file could not hold — fewer than three
corners, off the top or left of the page, an outline that crosses itself — is
refused with a message and the region left as it was, rather than saved and
discovered the next time the file is opened.

It is a mode rather than always on, because dragging inside a region is also
how the page is panned. A region you reshape is recorded as `geometry:
manual`: what it says about itself is no longer that detection traced or
guessed it, and the "check this" flag an approximate region carries clears,
because checking it is exactly what you have just done.

**Render Preview** (`Ctrl+R`) calls the exact `render_page` function `apply`
itself uses, on the plan as it currently stands, unsaved edits included. What
it shows is what `apply` would write for that page — not a mock-up — so a
region that will not fit, needs condensing, or overlaps another one shows up
here before you run `apply` for real. **Back to Overlay** returns to the
polygon view.

The page starts fitted to the window. `Ctrl+=` and `Ctrl+-` zoom, `Ctrl+0`
fits again and `Ctrl+1` is actual size; Ctrl and the wheel zooms about the
pointer, and on a Mac trackpad pinch works too. The status bar shows the
current level.

**The zoom is per page.** Each page keeps the level and position you left it
at and comes back to them, so reading one page of dense captions close up
does not decide how the splash opposite it opens. A page you have not opened
yet starts fitted, and a page you left fitted comes back fitted to the window
as it is now rather than to the factor it happened to have. Switching to the
rendered preview and back holds your place, which is what makes the overlay
and the output comparable at the same magnification.

Reviewing a chapter means walking every balloon on every page, so the
regions are walkable: `Ctrl+Down` and `Ctrl+Up` step to the next and
previous region, carrying on to the following page rather than stopping at
the end of this one, and `Ctrl+Shift+Down` skips ahead to the next region
with something worth checking. All three are on the toolbar, and switch off
when there is nowhere left to go.

The **Window** menu closes and reopens the two panels, and **Reset Layout**
puts them back where they started. The layout is remembered between
sessions — the only thing this tool stores outside a plan file.

**Help > About** reports the version, the author, the licence, and every
declared dependency that is actually installed, with its version — read from
the installed package at the time you open it, not written down anywhere. It
does not check for updates, and nothing else here does either.

The **font** fields — a region's override and the plan header's — list the
families installed here that this tool can actually render with: found on
disk, and each one checked to have a real bold, since emphasis is bold and a
bold is never synthesised. Qt knows a wider set than that, so the list is
built from the same resolver `apply` uses rather than from Qt, and everything
it offers will render. The fields stay typeable, and a name this machine does
not have is kept exactly as written and marked rather than swapped for
something installed — you find out before you render, not after. The list is
read once per session; **Edit > Rescan Fonts** looks again after you install
one.

**Edit > Plan Header…** changes the settings every region is drawn under:
the font, the case, the two fit limits, and the language pair. It reports how
many regions actually follow the header font, since any of them can override
it. Fields write through as you type, like the region fields, and each one is
its own undo step. What the tool *recorded* — which OCR engine ran, what wrote
the plan and when — is shown there but cannot be edited: a plan that names an
engine that never ran is a plan that lies about where its text came from. The
dialog only offers values the plan file reader will accept, so a header you
edit here always loads again.

`Ctrl+Z` and `Ctrl+Shift+Z` step back and forward through your edits, one
change at a time — a run of typing in one field counts as one change, not
one per keystroke. There is a single history covering everything, so undo
means the same thing wherever the cursor is. It lives in memory only:
undoing past a save does not un-write the file, and the title's modified
marker says so.

`Ctrl+S` saves back to the file it was opened from; **Save As** writes
elsewhere and refuses to overwrite an existing file without confirming.
Closing the window, reloading, or opening a different plan while there are
unsaved changes asks first. Like every other pass, `review` never writes to
a source image — only ever to the plan file you explicitly save to.

## The plan file

YAML, UTF-8, stable key order, hand-editable. One entry per detected region.
Comments you add are preserved.

```yaml
version: 2
generator: comictrans 0.1.0
created: 2026-09-06T19:22:04Z
source_language: it
target_language: en
ocr_engine: apple-vision
font: Comic Sans MS
case: upper
font_size_min_ratio: 0.012
condense_min: 0.9
images:
  - name: page-001.png
    sha256: 3ac70d…
  - name: page-002.png
    sha256: 9f2b1c…
regions:
  - id: page-002-003
    image: page-002.png
    order: 3
    geometry: exact
    polygon: [[120, 88], [186, 71], [244, 96], [230, 168], [131, 160]]
    fill_color: "#fdfdfa"
    text_color: "#1b1b1b"
    confidence: 0.93
    source_text: |-
      NON CI POSSO
      CREDERE!
    translation: |-
      NON CI POSSO
      CREDERE!
    notes: ""
```

- `images` lists every page the plan covers, in the order they were read,
  with the hash each one had at extract time. A page with no text on it is
  listed too: `apply` copies it through so the output is the whole chapter,
  and `review` can show it. `regions` may name only some of them.
- `translation` is seeded with the extracted source text so you can edit it
  in place rather than retyping into a blank field — unless the reading does
  not look like text at all, in which case it is left empty (see below).
- Clearing `translation` leaves the region untouched and reports it as
  skipped, which fails the run. `skip: true` says you meant it, and passes
  quietly.
- A region whose `translation` is still identical to its `source_text` is
  rendered as-is — the source text gets re-lettered — and listed under
  **same as source** at the end of an `apply` run, so a balloon you never got
  to is visible rather than silent.
- `notes` is yours. comictrans never reads or rewrites it.
- `**bold**` marks emphasis. It renders as bold, never italic; the oblique
  face is never used.
- Coordinates are pixels, origin top-left, integers. Vision's normalised
  bottom-left coordinates are converted inside the OCR adapter and never leak
  past it.
- `geometry: approximate` means no clean balloon contour was found and the
  polygon is a padded box around the text. Check those regions. `geometry:
  manual` is a polygon shaped by hand in `review`, which is neither of the
  other two: nothing traced it and no OCR box bounded it.
- Regions are found by tracing a contour on a luminance threshold. When that
  finds nothing — a caption box the same brightness as the art behind it, a
  white balloon over near-white art — a second pass seeds a region from the
  text's own background colour and grows it by colour distance.
- `low_confidence: true` appears on regions whose worst OCR line scored below
  `--confidence-threshold` (default 0.5).
- OCR finds "text" in artwork — a window frame, an eye, the dots of a halftone
  screen — and reports things like `(6` or `o ©`. Those regions are kept so you
  can see them, but their `translation` is left empty, so `apply` leaves the
  art alone instead of erasing it to letter nonsense on top. They are counted
  under **not text-like** at the end of an extract run. Delete them, or set
  `skip: true`.
- Per-region `font` and `font_size` override the header.

Loading validates: unknown keys, malformed or self-intersecting polygons, bad
colours, duplicate ids, a region naming an image the `images` list does not
have, and image-hash mismatches are all errors that name the offending line.

Version 1 plans, which carried an `image_sha256` on every region and had no
`images` list, still load: the list is derived from the regions and the plan
is a version 2 one from then on. Saving it writes the new shape.

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
first bad region. `review` has no code 1: it is interactive, not a batch run,
so there is nothing to report at the end beyond what is already on screen —
it exits 0 when the window closes normally, 2 if it could not open one at all
(PySide6 missing, or the platform's own windowing libraries).

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

Three groups of tests skip rather than fail when the host cannot run them:

- The font tests borrow a regular/bold TTF pair from the system, because no
  font ships with this repository. They skip if none is found.
- `tests/test_fixtures.py` runs the real pipeline over any images in
  `tests/fixtures/`, and skips when that directory is empty or no OCR backend
  is installed.
- `test_gui_widgets.py` skips when PySide6 is not installed (`uv sync
  --extra gui`), or when it is but no display could actually be opened.
  `test_gui_document.py` and `test_gui_preview.py` need neither and never
  skip — nothing under `comictrans.gui` besides the widgets themselves
  touches Qt.

`-rs` tells you which. On macOS with `uv sync --group dev --extra gui` you
should see none of these skip except the empty fixtures directory.

Fixture images go in `tests/fixtures/`; see the README there.

## Licence

MIT. The full text is in [LICENSE](LICENSE), and `pyproject.toml` declares
the same, so the package metadata and the repository agree.

No font is bundled and none ever will be: comictrans resolves the fonts
already installed on your Mac, so nothing here redistributes one.
