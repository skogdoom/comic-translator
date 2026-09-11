# Architecture

## Two passes, one plan file

```
        ┌──────── extract ────────┐              ┌───────── apply ─────────┐
images ─┤ imaging → ocr → detect  ├─→ plan.yaml ─┤ erase → typeset → render├─→ output/
        └─────────────────────────┘   (you edit) └─────────────────────────┘
             read-only                                        writes only to --output
```

`apply` never re-runs detection or OCR. Every polygon, colour, and font
decision it needs is already in the plan file, which is what makes it
deterministic and re-runnable: editing a translation and running it again
changes the text and nothing else.

## What the plan covers

A plan holds two lists: the pages it was extracted from (`images`, each with
the hash it had at the time) and the regions found on them. A page with no
text on it is in the first and absent from the second, which is a page of the
comic all the same — `apply` copies it through so the output is the whole
chapter, and `review` can show it.

The pages are their own list rather than something derived from the regions
because the two answer different questions. Which files this plan describes is
measured once, by `extract`, and does not change when regions are edited;
which regions exist is what a person spends their time changing. Deleting the
last region on a page, or drawing the first one on a blank one, then means
what it says instead of quietly adding or removing a page.

That also gives the hash exactly one home. In version 1 of the schema every
region carried an `image_sha256`, so a page with three balloons stored it
three times and a page with none stored it nowhere; the reader had to check
that the copies agreed, and a page could only be verified if something had
been found on it. Version 1 files still load — the list is derived from the
regions they do have, and the plan is a version 2 one from then on.

## Module dependency rules

`model` sits at the bottom and imports nothing of ours. Above it:

- `ocr/` and `detect/` never import `planfile`.
- `planfile` never imports Pillow, OpenCV, or pyobjc.
- `cli` and `extract` are the only modules that know about both sides.

That is not tidiness for its own sake. It means the review GUI's
`gui.document` — the module that loads a plan, tracks edits, and saves —
imports only `planfile` and `model` and nothing else of ours, so it carries
none of Pillow, OpenCV, pyobjc, or Qt; see "The review GUI" below. And it
means detection can be tested with synthetic images and hand-written OCR
boxes.

## Stage interfaces

```python
# imaging
load_page(path: Path) -> PageImage                  # read-only, captures dpi/icc
collect_inputs(target: Path) -> (list[Path], list[(Path, reason)])

# ocr — one adapter per backend
class TextRecognizer(Protocol):
    name: str
    def recognize(self, page: PageImage, config: OcrConfig) -> list[OcrLine]

# detect
find_regions(page: PageImage, lines: Sequence[OcrLine], cfg: DetectConfig)
    -> list[DetectedRegion]        # polygon, geometry, lines, fill_color, text_color

# planfile
dumps(plan: Plan) -> str
write_plan(plan: Plan, path: Path, *, force: bool = False) -> None
load_plan(path: Path, *, check_images: bool = True) -> Plan     # raises PlanError(line)
```

Milestone 2 added three more, and nothing above changed:

```python
# erase
erase(rgb: RgbArray, region: Region, cfg, *, page_height: int) -> RgbArray
class FillStrategy(Protocol):        # flat, polygon, inpaint
    name: str
    def fill(self, rgb, mask: MaskArray, region: Region, cfg) -> RgbArray

# typeset
layout_text(tokens, polygon: Polygon, face: FontFace, cfg, *,
            page_width, page_height, fixed_size=None) -> Layout | FitFailure

# render
render_region(rgb, region, style, cfg, *, page_width, page_height)
    -> (RgbArray, RegionOutcome)
render_page(page, regions, styles, cfg) -> (Image, list[RegionOutcome])
```

`erase` and `typeset` both take a `Region`, not a page and an index, so a
single region can be re-rendered in isolation. The review GUI's preview does
not go that far — it renders a whole page at once, through `render_page`
itself, unmodified — but the same fact is what makes that safe to call
straight from a GUI action: nothing about rendering one page reaches back
into detection, OCR, or the plan file on disk.

Milestone 4 added the review GUI, over the same interfaces and one new one:

```python
# gui.document — the GUI's view-model; imports only planfile and model
class PlanDocument:
    path: Path
    plan: Plan
    dirty: bool
    def open(path: Path, *, check_images: bool = True) -> PlanDocument   # classmethod
    def region(region_id: str) -> Region
    def flags(region_id: str) -> RegionFlags
    def set_translation(region_id: str, translation: str) -> Region     # and set_notes,
    def save() -> None                                                  # set_skip, set_font,
    def save_as(path: Path, *, force: bool = False) -> None             # set_font_size

# gui.preview
render_preview(document: PlanDocument, image: str) -> Preview   # .image, .outcomes, .problems
```

Milestones 4.14 and 4.6 put both passes behind the window too, and each
gained the same parameter pair rather than a second copy of its loop:

```python
# progress — imports nothing of ours, like model
@dataclass(frozen=True, slots=True)
class PageProgress:
    index: int; total: int; image: str
ProgressCallback = Callable[[PageProgress], None]
CancelCheck = Callable[[], bool]

# apply
apply_plan(plan, plan_path, output, config, *, font=None, image_format=None,
           force=False, progress=None, should_cancel=None) -> ApplyReport

# extract
extract(target, plan_path, recognizer, font_family, config, *, case=…,
        source_language=…, target_language=…, debug_dir=None,
        progress=None, should_cancel=None) -> (Plan, ExtractReport)
```

Both callbacks are asked once per page, before it is worked on. Each report
gained `cancelled` to say a run stopped early, and `ok` is false when it did.
The two live in `progress.py` rather than in either pass, because both take
them and neither should import the other for a dataclass.

## Fitting text to a polygon

For each line's vertical band, the usable width is the widest horizontal run
that is inside the polygon on *every* row of that band. Taking the widest run
on any row would let a line near the top of an ellipse use the chord from the
middle of it and run out through the curve.

The fit order is fixed and not reorderable: shrink to the minimum readable
size, then condense horizontally, never past the condensing floor, then shrink
below the readable minimum as a last resort, then fail and name the region.

That last stage is a deliberate departure from "then fail": text that will not
fit at the size asked for drops under it rather than being refused, because an
empty balloon is worse than small lettering. It stops at
`font_size_floor_ratio`, and every region that used it is reported with the
size it landed on, so the ones worth hand-tuning are visible rather than
silently tiny.

"The size asked for" is the readable minimum for an automatic fit, or a
region's own `font_size` where one is pinned. A pinned size gets the same
fallbacks: it is where the fit starts, not a wall, so an optimistic
`font_size` still renders. What it does not get is silence — the result is
flagged `undersized` against the pinned size, not against the readable
minimum, so the gap between what the plan asked for and what the page got is
always reported.

Below the size asked for, the search is a binary search at full width rather
than a descending scan, so it settles on the largest size that fits without
condensing and only falls back to condensing at the floor. A pinned size can
be far above what a balloon will take, and scanning down from it one pixel at
a time would be needless work. Size is found by binary search, treating fit as monotonic —
line breaking makes that not quite true at the margins, so the result is the
largest size the search proved rather than the global maximum. That is a pixel
of conservatism, never an overflow.

Condensing is applied by rendering a line to a transparent layer at its natural
width and scaling that layer on the x axis alone. It is the only way to condense
without a variable font, and the spec forbids faking anything about a face.

A word can span a markup boundary — `**SHOUT**,` is one word whose comma is not
bold — so a token carries segments rather than a single weight. Tokenising each
markup run separately puts a space before that comma. Only single-weight words
are hyphenated: splitting across a boundary would have to divide the segments
too, and shrinking the font is the better answer.

## A polygon must cover its own text

Erase clips its glyph mask to the region's polygon, so any lettering outside
the polygon is never removed: the original text stays on the page and the
translation is drawn on top of it. Two earlier fixes create exactly that
situation — the containment tolerance admits a line whose box grazes the
outline, and stray absorption folds in a line whose box overshot the balloon,
measured at 86px past the edge on a real page.

After grouping, every polygon is therefore grown until it covers the boxes of
all the lines assigned to it. The boxes are unioned into the shape rather than
replaced by their hull, so a tail or a burst balloon's spikes survive.
Simplifying the traced union shaves corners, so the union gets a few pixels of
slack and the result is checked; the epsilon is halved twice before falling
back to a convex hull, and the hull is only taken when it is no more than
`max_hull_growth` times the shape it replaces.

What this cannot fix is lettering the OCR never reported. On the six-panel
page Tesseract reads `YOU SEE, IN THE PAST,` as `OU SEE, IN THE PA`: its box
covers what it recognised, so the leading `Y` and trailing `ST` are in no box,
fall outside the polygon, and survive the erase. They also happen to touch the
balloon's ink outline, so no colour-based erase could remove them without
cutting the outline either. A recogniser that reports the full line would
resolve it, since coverage is driven by the boxes.

## Erase before draw

`render_page` works in two passes: it decides what every region will do and
lays out its text without touching a pixel, then erases every region it is
going to render, then draws all of them.

One pass would be simpler and is wrong. Erasing and drawing a region at a
time lets a later region's erase wipe lettering an earlier one already drew,
wherever two polygons overlap — silently, because both regions still report
success. Overlaps are not rare: on the fixture pages a junk region overlaps a
real balloon, and fragments sit entirely inside one.

Erasing first cannot stop two overlapping polygons from drawing over each
other, which is a plan file problem rather than a rendering one, so regions
sharing more than 15% of the smaller one's area are named in a warning.

Planning before touching pixels is also what keeps the promise that a region
which will not fit is left completely alone: it produces no layout, so it is
never erased either.

## What apply does not do

It does not re-run detection or OCR, so it cannot disagree with the plan file.
It does not touch a region it cannot render: a fit failure leaves the region
exactly as it was, erased no more than it is drawn, so the page stays readable
in the source language rather than becoming a blank balloon.

A region whose OCR reading contains no run of two or more letters is not
seeded. OCR reports "text" in artwork, and once extract began seeding
translations every one of those phantom regions became actionable, so apply
would erase a window frame or a character's eye and letter `(6` onto it. The
region still goes in the plan file — the spec is explicit that suspect regions
are kept and flagged rather than dropped — but with an empty translation, so
apply skips it and the run says so.

The test is deliberately weak: one run of letters is enough to pass. Anything
stricter starts refusing real one-word balloons, and `BASTA!` is ordinary
comic lettering. Measured across the fixture pages it catches 13 phantom
regions with no false positives.

It is not enough on its own, because artwork sometimes spells a word. A window
frame came back as `INA`, read as language, and was seeded; apply then
flat-filled every pixel near the frame's own colour and lettered `INA` across
the hole, destroying 28% of that region's pixels. So a second test asks
whether the lettering is *sized* like lettering. Comic lettering on one page
is consistent, so the page is its own yardstick — nothing absolute would do,
since a scan's resolution is unknown. Every real region across the fixtures
measures 0.94 to 1.10 times its page's median line height; the frame measured
6.06.

Size alone would be wrong too. On the screentoned page, halftone noise drags
the median down to 16px until four genuine captions look oversized at 3.3-3.6.
What separates them from the frame is what they sit on, so an oversized region
is only condemned when its interior is also not a flat ground: those captions
score 0.83 to 0.95 for interior uniformity, the frame 0.68. Neither test would
do by itself — size throws out the captions, a non-flat interior throws out a
borderless caption lettered straight onto artwork, which measures 0.57.

An empty translation and `skip: true` are deliberately different. The first is
unfinished work and fails the run; the second is a decision and passes.

Extract seeds `translation` with `source_text` so it can be edited in place
instead of retyped. That costs the old safety property: a balloon you never got
to used to be left untouched, and now renders its own source text in Comic
Sans. Apply gets the signal back by reporting every region whose
translation is still identical to its source, rather than by skipping them —
a name, a number, or an interjection spelled the same in both languages is a
correct translation of itself, so an identical string is a report line and
never a reason to leave a region unrendered.

## Output files

Filenames are mirrored flat into `--output`, which must be outside the source
tree. Format matches the source, except JPEG becomes PNG: re-encoding a lossy
source after repainting part of it would add a second generation of artefacts
to artwork that is not being changed at all.

DPI, the ICC profile and the source's alpha channel are carried across.
Rendering happens on RGB with any transparency flattened onto white, so
without reattaching the alpha a transparent page would come back silently
opaque. Modes that cannot round-trip through 8-bit RGB — 16-bit, CMYK,
YCbCr — are refused rather than quietly downconverted.

## Languages

The language pair lives in the plan file header as `source_language` and
`target_language`, set by `--source-lang` and `--target-lang` on `extract` and
defaulting to `it` and `en`. Nothing downstream hardcodes a language:

- OCR uses the source language, or whatever `--lang` overrides it with, since
  a recogniser sometimes wants a region-qualified tag (`pt-BR`) where the plan
  file records the plain one.
- Hyphenation uses the target language, because that is the language being
  written. A language pyphen has no dictionary for disables hyphenation with a
  warning rather than failing the run — lines then break only between words.
- The Tesseract adapter maps BCP-47 tags to traineddata names and passes an
  unmapped one through unchanged, so a traineddata name given directly works.

## Coordinate convention

Pixels, origin top-left, integers, everywhere past the OCR adapter boundary.

Vision reports normalised coordinates with a bottom-left origin.
`ocr/vision.py::_to_pixel_box` is the only function in the codebase that knows
that, and there is a test pinning the conversion. Nothing downstream — not
detect, not the plan file, not the future renderer — ever sees a normalised
coordinate.

## Region geometry

Balloons are round. A bounding box erases the balloon outline and lets
typeset overflow into the corners, so regions carry polygons.

Detection is two-step, as specified:

1. Vision's text boxes seed the region.
2. `detect/contour.py` thresholds the page (both polarities, so a white-on-
   black caption works the same way), morphologically closes the result to
   bridge halftone gaps, finds contours, and picks the *tightest* contour
   that fully encloses the text box and is at least 15% larger than it.

The polygon traces the balloon's *interior* — the fill region inside the ink
outline. That is deliberate: erase should not eat the outline.

Contours are rejected when they cover more than `max_contour_area_ratio` of
the page (that is a panel, not a balloon) or when their solidity is below
`min_solidity` (artwork is not convex). If nothing survives, the region falls
back to the union of its text boxes plus a margin and is flagged
`geometry: approximate` in the plan file.

One region per balloon, not per line: several text boxes inside one contour
become one region.

## Reading order

Top to bottom, then left to right, over the whole page. No panel detection.

Sorting on raw `top` would let a few pixels of scan skew swap two side-by-side
balloons, so regions within `order_band_ratio` median heights of each other
count as one row and sort left to right. The index is a hint for the
translator; `apply` does not depend on it.

## Colours

Both the fill and the text colour are measured and stored, so `apply` needs no
access to the original colours and a white-on-black caption round-trips.

Fill is the median of the balloon interior with the outline eroded away and
the glyphs (plus their antialiased fringe) dilated out. Text is the median of
the glyph pixels, where "glyph" is the minority Otsu class inside the text
boxes — the assumption being that ink covers less than half of a line's
bounding box, which is true of text and false of solid blocks.

## Fonts

No font file ships with the tool; everything resolves from the system, with
`COMICTRANS_FONT_PATH` available for non-standard locations.

`extract` resolves the chain once (Comic Sans MS → Chalkboard SE → Marker
Felt → Noteworthy → Helvetica), logs what it got, and writes that family name
into the plan header. `apply` therefore does not re-run the chain and cannot
silently substitute.

Precedence at render time, highest first: `--font` on the command line, then a
per-region `font`, then the header `font`. When `--font` overrides what the
plan file says, that is logged — the plan file is the record, and a CLI flag
quietly beating it would be exactly the silent substitution the spec forbids.

Regular and bold must both resolve. Marker Felt ships Thin and Wide but no
bold; asking for it is an error, not an invitation to synthesise one. Italic
and oblique faces are filtered out during face selection, so emphasis can only
ever render as bold.

## Emphasis and case

`**bold**` in a `translation` marks emphasis. The parser lives in `typeset`
(milestone 2); the convention is documented in the plan file's own header
comment so it is discoverable from the file you are editing.

`case: upper | preserve` in the header. Default `upper`, because comic
lettering is.

## Failure behaviour

Both commands process every page and report at the end. A corrupt scan halfway
through a chapter costs you that page, not the run.

Exit codes: 0 clean, 1 completed with something to look at, 2 could not start.
For `extract`, "something to look at" means a page failed to decode or a page
produced no regions at all. A stray `.txt` file in the directory is logged and
does not affect the exit code.

## Re-running extract

`extract` refuses to overwrite an existing plan file unless told what to do
with it: `--force` discards it, `--merge` keeps the hand work.

Merging matches regions on bounding-box overlap, not on id. Ids are
positional, so the moment detection changes they renumber and stop naming the
same balloon. Matching is greedy by descending overlap and strictly one to
one, so two old regions cannot both claim one new one — the better overlap
wins and the loser is reported rather than silently folded in.

The pages come from the fresh run, not the old plan. Which files exist and
what they hash to is measured, like the polygons and the colours, so a page
added to the directory appears and one deleted from it goes.

Only what a person put there is carried: the translation where it was
actually edited, plus notes, skip and the font overrides. A translation still
equal to its `source_text` is the seed extract wrote, not hand work, so it is
replaced by the fresh OCR — carrying it would pin an old, worse read into the
new plan. A cleared translation is the opposite: blanking one is how you tell
apply to leave a balloon alone, so it counts as a decision and is kept.

The risk the merge cannot remove is a wrong match putting your text in the
wrong balloon, which is why the threshold is a conservative 0.5 and anything
below it is reported as lost rather than guessed at. Lost hand work fails the
run.

## Known weak points on real scans

Rewritten against the fixture pages in `tests/fixtures/`, which is why some
of this contradicts what I expected before running it.

1. **Contour escape — confirmed, now guarded.** Text sitting on a flat band of
   artwork (a strip of sand, a block of sky) selects the whole band: it is
   solid, convex, and under the area cap. Erasing it would wipe the art. Area
   alone does not catch it, because a band is thin. `max_extent_ratio` does:
   measured balloons span 0.25–0.45 of page width, the escape spanned 0.88.
   The same cap applies to fallback clustering, where adjacency is transitive
   and a row of spurious OCR lines could otherwise chain across a page.
2. **Joined balloons — did not reproduce.** Balloons joined by a tail and
   balloons sharing an edge both detect as separate regions. The ink outline
   keeps their white interiors separate as connected components, and the
   interior is what gets traced. Still expect trouble from borderless
   balloons of the same colour that touch.
3. **Balloons breaking the panel border — did not reproduce.** A balloon
   crossing the gutter or hanging out through the panel edge traces cleanly,
   for the same reason. Nor does a balloon clipped by a panel corner, where
   two sides of the shape are the panel's border rather than the balloon's
   own outline: the interior is still one connected region, so the traced
   polygon simply runs along the border and stops there instead of bulging
   past it.
4. **Burst and scalloped balloons — was broken, now fixed.** A shout balloon
   with a spiked outline measures 0.64 solidity, and the old 0.80 threshold
   rejected it, falling back to a box around the text and discarding the very
   shape the polygon exists to capture. `min_solidity` is now 0.55. Sweeping
   it from 0.80 down to 0.50 changed nothing on any other fixture, which says
   solidity was never the guard doing the work — the area cap and the extent
   ratio are. Scalloped thought bubbles pass either way.
5. **A caption box flush against the panel frame — confirmed.** Same colour,
   touching, so they are one connected component and the merged blob is too
   large to be a balloon. Falls back to `approximate` and is flagged. Arguably
   correct: there is no visual boundary between the caption's edge and the
   panel's. Colours still round-trip, which is what matters for erase.
6. **Regions a grey threshold cannot separate — now handled by colour.**
   Contour search binarises on luminance, which fails whenever a balloon or
   caption box differs from what surrounds it in hue but not brightness. Two
   measured cases: a caption box of flat tan whose luma a global Otsu puts on
   the same side as the artwork around it, and a whisper balloon of white
   over near-white art, luma 255 against 238 with the threshold at 182 — a
   gap of 17 that closing the mask cannot help with, because there is no edge
   to close over. Both now resolve by seeding a region from the text's own
   background colour and growing it by colour distance. The cost is that on a
   page where OCR reports text that is not there, those phantom lines get
   real-looking shapes instead of padded boxes; `--no-color-segmentation`
   turns it off. A caption box flush against a panel frame still merges with
   it and is refused, which is the honest answer: there is no boundary
   between them to find.
7. **Screentone — confirmed, and the failure is in OCR, not detection.** Both
   real balloons on the halftoned fixture trace perfectly; Tesseract reads the
   dot rows as ~25 phantom lines of text, and each becomes a region. Neither
   confidence nor line height separates the noise (noise confidence reaches
   0.82, real text sits at 0.92; heights overlap), so there is no cheap filter
   worth adding. Measured against Tesseract with `--psm 11`; whether Apple
   Vision does the same is unknown and worth checking on a real screentoned
   page before anyone tunes anything.
8. **Transparency — found by the fixtures.** An RGBA source whose transparent
   pixels carry black colour channels became a solid black band under a plain
   `convert("RGB")`, shifting the Otsu threshold across the whole page.
   `imaging.flatten_to_rgb` composites onto white instead.
9. **Self-intersecting polygons — found by the fixtures.** `approxPolyDP` can
   fold a ragged contour over itself. The plan file reader rejects such a
   polygon, so extract was capable of writing a plan it could not load back.
   `_simplify` now falls back to the convex hull, which cannot self-intersect.
10. **Vision on comic lettering.** Untested here — no Mac in the loop. It is
    trained on prose, so expect I/l/1 confusion, dropped accents, and mangled
    elisions in languages that use them (Italian `dell'uomo` → `dell uomo`),
    often at high confidence, so
    the 0.5 threshold will not catch them.
11. **Skew.** One or two degrees inflates axis-aligned boxes and loosens the
    polygon fit, long before it counts as "rotated text".
12. **Gradient or textured balloon interiors** make a single `fill_color` a
    lie; erase leaves a flat patch. Where the sampled text colour comes back
    within 32 luma of the fill, the text colour is snapped to black or white so
    apply cannot draw invisible text; the value is in the plan file to correct
    by hand.
13. **Metadata round-trip.** ICC and DPI survive Pillow for 8-bit RGB
    PNG/TIFF. PNG stores resolution as integer pixels per metre, so 300 dpi
    round-trips as 299.9994. 16-bit and CMYK TIFF do not round-trip cleanly;
    milestone 2 should detect and refuse rather than silently downconvert.

## Performance

Detection is roughly 0.1–0.3 s per page, and 2.8 s on the screentoned fixture,
which yields 5476 contour candidates against 56–280 for a clean page.

Two things keep that from being much worse. Candidates carry only what is
cheap to compute for every contour — bounds and area — and build their
simplified polygon on demand, since at most a handful are ever chosen.
And `contains_box` rejects on bounding box before running any
`pointPolygonTest`. Simplifying every candidate eagerly cost 13 s on the same
page.

## The review GUI

`src/comictrans/gui/` splits along the same line as everything else: what
decides, and what draws.

```
document.py   the loaded plan, its edits, and where they save — no Qt
preview.py     render_page called on the current document — no Qt
about.py       version, author, licence and installed libraries — no Qt
alerts.py      the two strings an alert has, since macOS drops its title
qimage.py      the one function that turns a Pillow image into a QPixmap
icons.py       the toolbar's drawings, tinted; and the application's own
canvas.py      the page: a pixmap, and clickable region outlines over it
hint_line.py    the line under it: what a click does, elided to fit
busy_bar.py     the status bar's indeterminate bar, while a page renders
sampling.py    colours read off the page for a region drawn by hand
inspector.py   one region's fields, writing straight through to the document
color_box.py    a colour field: a swatch, the standard values, the eyedropper
page_list.py   one row per page, with a region-and-flag-count summary
about_dialog.py  what about.py found, plus the Python and Qt versions
help_dialog.py   the bundled guide, in a window you can leave open
header_dialog.py the settings every region is drawn under
font_box.py     a font field offering only what fonts.py can resolve
run_report.py   what a finished run is worth showing — no Qt
run_job.py      a pipeline pass on a worker thread, reporting by signal
render_dialog.py where to write, in what format, erasing how
extract_dialog.py what to read, where the plan goes, in what languages
run_panel.py    the dock a run reports into, whose rows go where they name
preferences.py  what a new run starts from — no Qt
recent.py       the plans opened lately, and their order — no Qt
preferences_dialog.py  those defaults, and a reminder of what they are not
logfile.py      the application log, and the hooks that fill it — no Qt
crash.py        fatal-signal traces to a second file — no Qt
main_window.py wires the widgets together; the only module that knows
               about all of them at once
app.py         available() / run() — the CLI's entry point
```

`document.py`, `preview.py`, `sampling.py`, `about.py`, `run_report.py`,
`preferences.py`, `recent.py`, `logfile.py` and `crash.py` need no display
and import no Qt;
they are tested directly, the same as any other module. The widget modules do
— `main_window.py` is the only one that knows about more than one other
widget, which is what keeps an edit's ripple effects (the window title's
dirty marker, another region's overlap flag, the page list's flag count) in
one place instead of three widgets each guessing at the other two's state.

**Why a document, not a `Plan` passed around.** `apply` treats a `Plan` as
immutable — load it, render it, done. The GUI cannot: the whole point is
editing one in place before saving it. `PlanDocument` wraps a `Plan` and
funnels every mutation through one of a handful of `set_*` methods, each of
which does exactly one `dataclasses.replace` and sets `dirty = True`. That is
what makes "unsaved changes" one fact the window can trust, rather than
something it would otherwise have to reconstruct by asking every widget
whether it has touched anything.

**Why undo is a stack of plans, not a stack of operations.** `Plan` is
frozen and holds a tuple of frozen `Region`s, and every edit already builds
a new one, so the plan as it stood before an edit *is* the undo entry — a
new tuple of pointers, not a copy of anything. What that buys is what it
costs to extend: an operation that adds, deletes or reshapes a region needs
no undo code of its own, because it passes through the same `_record` and
leaves the same kind of entry behind. A command-per-operation stack would
need a new class for each.

It also lets `dirty` stop being a flag that only ever goes true. It is
identity against the plan last written to disk, so undoing back to the last
save clears the marker and stepping past it sets it again — which is what
the file actually holds. The window shows that through `[*]` and
`setWindowModified` rather than editing the title itself, so the marker is
whatever the platform's own is.

**One undo history, not one per widget.** Every keystroke in the inspector
is already a document edit, so the prose fields have their own histories
switched off. Two stacks would disagree the moment a document-level undo
put text back that the widget never saw leave, and a window-level `Ctrl+Z`
reaches only one of them anyway. Consecutive edits to the same region and
field collapse into one step, or every keystroke would be its own.

**Why zoom is a mode and not just a number.** The canvas either follows the
window or holds a factor the reader chose, and `resizeEvent` refits only in
the first. Refitting unconditionally, which is what it used to do, would
make a chosen zoom vanish the moment the window was resized.

**Why the zoom belongs to the page and not to the canvas.** Pages differ in
size and in how much of one you need to see at once, so a level chosen for a
dense page of captions is the wrong one for the splash opposite it. The
canvas is handed a pixmap and does not know which page it is, so it cannot
answer that; `MainWindow` does, and keeps a `ViewState` per image, captured
on the way out of a page and applied on the way in. `show_page` therefore
fits, always, and whoever knows better follows it with `apply_view_state`.
Restoring a page that was *fitting* refits to the window as it is now rather
than pinning the factor it had, which is the difference between remembering
a decision and remembering a number. Rendering the preview goes through the
same save and restore, so the overlay and the output stay comparable at the
same magnification.

Zoom costs nothing because the scene holds page pixels at their own
coordinates and only the view scales. That is the same property that makes a
polygon drawn on the canvas exactly the polygon apply would erase into.

**Why the font list comes from `fonts.py` and not `QFontDatabase`.** They
answer different questions. Qt lists what Qt can draw with; `fonts.py`
resolves a family by looking for files in the search directories and demands
a real bold face, because emphasis is bold and a bold is never synthesised. A
list built from Qt would offer families `apply` then refuses, turning a
two-click choice into a render failure. So `available_families` enumerates
and then puts every candidate back through `resolve_family` — the same call
`apply` makes — and only what survives is offered.

Enumeration reads each file's own family name first and falls back to the
filename, per file, only when none of the internal names resolve.
`_candidate_files` matches a slug against the *filename*, so a font whose
internal name differs from the file it lives in is real and usable but not
findable under the name inside it. Asking per file rather than globally is
what stops a font being offered twice under two spellings.

The field stays editable and never rewrites a name it does not recognise: a
plan written on another Mac can name a font this one lacks, and silently
swapping it is exactly the substitution the spec forbids. The name stays and
is marked instead.

**Why the header dialog validates and the region setters do not.** A
region's fields carry no invariants of their own, so the schema is enforced
once, at load time, by the reader. The header's fields do carry them — an
empty font, a condensing floor under the schema's — and a plan written with
one would be a plan the GUI cannot reopen. So `_update_header` checks, and
raises rather than recording. The limits themselves live in
`planfile/schema.py` and are used by both the reader and the dialog's spin
boxes, because a widget offering one value more than the reader accepts is
the same defect wearing a different hat.

**What the header dialog will not let you edit.** `generator`, `created`,
`ocr_engine` and `version` describe what produced the plan rather than what
it should look like. Nothing downstream reads `ocr_engine` at all — it is
provenance — and the reviewer is not the authority on which engine ran. They
are shown, because knowing what made a plan is useful, and they are labels
rather than fields, because there is nothing to decide.

**A plan has one order, and `with_image_order` is what keeps it one.** The
page list shows `images`; walking region by region follows `regions` and
takes for granted that it is grouped by page in page order — which is what
makes `Ctrl+Down` walk the comic rather than walk the file. Those are two
lists that have to agree, and nothing about the schema makes them: a plan
with its pages in one order and its regions in another reads back as
perfectly valid and behaves as though the window were lying.

So reordering is one function in `model.py` that moves both, and everything
that reorders goes through it — the page list's drag, and `merge_plans`,
which carries the previous plan's order across a re-extraction the way it
carries translations and notes. Its sorts are stable, so regions keep their
reading order inside a page and a page nobody named keeps its place at the
end; a name the plan does not have is ignored. Between those two rules a
partial or stale list can reorder what it knows about and cannot lose a
page.

The window holds the other half of that bargain. `_resync_page_rows` puts
the rows back in the plan's order whenever the two have parted — after an
undo, which changes the whole list rather than the single row's label a
refresh repaints, and after a reorder the plan could not carry out
literally. It is the same rule the canvas keeps for a reshape the document
refuses: what is on screen is never something the document does not have.

**Where "the next region" is decided.** In `document.py`, not in the window,
as `adjacent_region` over the plan's own region order. Which region comes
after this one is a question about a plan, not about a widget, and asking it
there means it is tested without a display like everything else in that
layer — and that walking off the end of a page continues onto the next one
falls out of the plan order rather than being a rule the window enforces.

**Why the window is handed its `QSettings` instead of making one.** Three
things are remembered outside a plan file — the window layout, the
preferences a new run starts from, and the plans opened lately — and a
window built without settings, which is every window the tests build, reads
and writes none of them. That keeps the suite from depending on, or
writing into, the configuration of whoever runs it, and keeps one test's
dragged-about docks out of the next one. `gui.app` is the single place that
decides a real session should persist anything.

**Why the recent list is history and the other two are settings.** It is
the only thing stored here that records what someone has been reading
rather than how they like the tool set up, which is why it has its own key
and its own way to be emptied. Clearing it has to clear it, not leave the
paths folded in with the geometry of the docks.

Its entries are never checked against the disk while the menu is being
built. That would be a `stat` per entry every time File is opened, and one
entry on a network volume that is not answering would hang the menu rather
than the click. The check happens when a particular plan is chosen, where
someone is already waiting on that file; a plan that has gone says so and
drops off the list. Paths are canonicalised without touching the disk
either — `normpath`, not `resolve`, so a symlink is never followed to
decide whether two entries are the same file.

**Why the overlay and the preview are two different things, not one.** The
overlay — polygons over the original page — is recomputed from the document
on every edit; it is cheap (nothing is rendered, only restyled) and always
in sync. Rendering a real preview erases and typesets, the actual work
`apply` does, and is comparatively expensive and worth doing deliberately
rather than on every keystroke — `Ctrl+R`, not automatic. Both read from the
same `PlanDocument`, so a preview always reflects the edit you just made,
saved or not.

**A preview runs on a worker thread, and what makes that safe is that it
does not hold the document.** `PreviewRequest.of` takes a snapshot on the
window's thread — a frozen `Plan` and the path its images resolve against —
and that is all the job is given. Editing the document while a render is in
flight builds a new `Plan` and leaves the captured one alone, so there is
nothing to lock and nothing to half-read. It is the rule `RenderJob` already
followed; the preview joins it, which is why `render_preview` stopped taking
a `PlanDocument`.

**A preview stops inside the page; nothing else does.** The other two jobs
stop between pages, because a chapter has more coming. A preview is one page,
so stopping at all means stopping part-way through one — which is safe here
and nowhere else: what is abandoned is thrown away rather than written, where
a half-written page would be a file somebody keeps.

So `render_page` grew two optional hooks, `on_region` and `should_cancel`,
and **both are off by default**. `apply` passes neither, which is the whole
of how its promise survives: a cancelled `apply` goes on stopping between
pages, and every page it wrote is one a complete run would have written. That
is not left to a default — a test watches the call apply makes and asserts
neither keyword is in it, because a default is an easy thing to start
relying on by accident.

Both hooks live in the erase loop, and that is measured. On an
eleven-megapixel page with ten regions the three loops in `render_page` cost
0.099s, 1.280s and 0.002s per region: erasing is 80% of the whole preview,
and the other two are below the granularity anyone could see on a bar.
Cancelling is checked in the planning loop as well, which costs one call per
region and takes the worst case from "the whole page" down to "the region
being erased". Never *inside* an erase: a half-erased region would be a
balloon with part of it repainted, which is the one thing this must not
produce even for something thrown away.

`should_cancel` raises `RenderCancelled` rather than returning a sentinel. A
half-erased page is not a result, and a return value saying so would have to
be handled by every caller including the two that can never see it. It is
deliberately not a `ComictransError`: nothing failed, so the `RunJob`
machinery that turns one of those into a red message never sees it, and
`PreviewJob` completes with `None` instead.

Being unwanted and being stopped are two different things, and the window
does both. What makes a result unwanted is not that the plan has moved on: a
preview answers the plan as it stood when it was asked for, and an edit made
while it rendered makes the answer older than the question rather than wrong
— which is what used to happen anyway, since the edit could not have been
made during a render that blocked. What makes it unwanted is that nobody is
waiting: the page changed, another plan is open, the overlay is back, or a
newer preview was asked for. All four clear or replace `_preview_wanted`, and
`_on_preview_ready` shows only a result whose request still matches it.

The first three also cancel the thread, because there is nothing to wait for.
The fourth does not: a superseded request wants the *next* render, and
stopping the current one only to start another immediately would throw away
whatever it had already erased. Asking is not the same as having stopped
either — the check is between regions, so the thread runs on for up to one
erase and its result lands in a handler that no longer has anything to match
it against.

Matching on the request rather than on a counter buys one thing for free:
two presses with nothing changed in between produce equal requests, so the
render already running *is* the answer to the second one and no second
render happens.

**One preview thread, never two.** A request arriving while one runs is
remembered, not started; the running job's `finished` starts it. That keeps
the peak at one render's worth of memory rather than two, which on the
numbers below is the difference worth having.

**The bar is the first animation this window could honestly have.** While the render blocked, the event loop was not turning: the status
bar had to be repainted by hand to get one message onto the screen, and a
spinner would have been a still picture of a spinner. With the render on a
worker thread the loop turns throughout, so `busy_bar.py` shows a
`QProgressBar` with an empty range — Qt's indeterminate mode, animated from
its own timer — beside the status bar's message for as long as a render is
in flight.

It starts indeterminate and becomes determinate, which is the shape of what
is actually known: a preview has to open the page and plan its regions before
it can say how many there are to erase. Until the first count arrives the
only true statement is *something is happening*; from then on there is a real
fraction and it shows it. `set_busy` puts it back to waiting on the way in,
or the next preview would open on the last one's finished bar.

The fraction counts regions erased, reusing `RunJob.progressed` — the run
panel's own signal, carrying regions here instead of pages. That the unit is
regions-erased is the measurement above, not a guess: a bar following the
80% follows the wait. The run panel's bar counts pages and stays a different
thing for a different job.

It goes down on the thread's `finished`, except on failure, where it goes
down first. A failure opens a modal alert, which sits there for as long as it
takes somebody to read it, and `finished` is delivered only once that box is
dismissed — so a bar left to it would spin behind an alert saying the render
had stopped. A superseded request is the other exception in the other
direction: the bar stays up across the handover from one thread to the next,
because from where anyone is sitting that is one wait.

That it animates is measured rather than assumed, and the measuring took two
attempts. Grabbing the whole window came back byte-identical across half a
second, which reads as a still bar and is not — the bar repaints on its own
timer and the window's cached frame did not follow. Grabbing the bar itself
gives six distinct frames a quarter-second apart, under the offscreen
platform, on this machine. The test polls for the first frame that differs
rather than sleeping for a fixed time, so it usually costs one repaint.

**What a thread does not fix, measured.** A preview of an 11 MP page
(`tests/fixtures/11-complex_six_panel_page.png`, 2840x3880, ten regions)
costs, from a 57MB baseline:

| | |
| --- | --- |
| retained after one preview | +116 MB |
| process peak RSS | 540 MB |
| one RGB copy of that page | 33 MB |

So the peak is about sixteen copies of the page, not the three a
back-of-envelope count of "source, erased, rendered" suggests. A thread makes
the window answer while that happens; it does not make it less, and two
previews at once would double it — which is the other reason only one runs.

Where it goes is not evenly spread, and that is the useful part. Stepping
through the render: `load_page` peaks at 78MB, and `render_page` takes it to
467MB (traced allocations; RSS peaks higher still). Inside `render_page`, the
**first `erase` call alone accounts for 356MB of that**; the second adds one
page copy and calls three through ten add nothing at all, the allocator
reusing what the first freed.

That is not a preview problem and not a GUI problem. `apply` calls the same
`render_page` on every page of every chapter and pays exactly the same peak,
on the command line, where nothing has ever measured it. It belongs to
`erase.py` — see the roadmap.

**Why `render_preview` calls `render_page` directly instead of its own
rendering path.** So it cannot drift. If preview had its own drawing code, a
bug fixed in `render_page` would need fixing twice, and a difference between
what the GUI shows and what `apply` writes would be a second bug on top of
whichever one it was hiding. Calling the same function means there is
exactly one way this tool draws a translated page, and the GUI's "does this
fit" answer is `apply`'s answer, not a separate opinion.

**The colour convention matches `--debug-dir`.** Green for a region traced
from a contour, orange for one approximated from a padded box around its
text — the same two colours `extract --debug-dir` has used since milestone
1. A polygon shaped by hand in the window is a third colour (violet),
because it is a third thing: `manual` geometry, neither traced nor guessed.
A region with something to check goes dashed red instead, regardless of
which of those it would otherwise be, because the geometry colour and
"look at this" are two different facts and only one dashed style was needed
to say the second one. Selection is a separate colour again (blue), since it
can coincide with any of them.

**Why the toolbar's icons are drawn here and tinted at load time.** Three
choices, each forced by the one above it. The bar is icons because words
did not fit: measured, fourteen commands cost 1344px of a 1200px window as
text and 513px as icons, which is why Extract and Render Pages could go on
it at all — as text the twelve already there came to 1138px. The icons are
files because there is nothing to ask for: `QIcon.fromTheme` returns
nothing on macOS, and half of these commands — reshape a region, merge two,
walk to the next flagged one — have no standard pixmap in any Qt style. And
they are drawn for this tool rather than taken from a set because a set
carries a licence, which would have to be recorded in `LICENSE`, honoured
in the About dialog, and carried by anyone redistributing this; fifteen line
drawings are a smaller thing to make than a licence is to keep. They are
this project's own work under its own licence, so there is nothing extra to
record.

`icons.py` paints the tint through the drawing's alpha
(`CompositionMode_SourceIn`), so the files are shape only and the colour
comes from the palette when the icon is built. That is what lets one set
follow a light window and a dark one: `main_window.changeEvent` drops the
cache and rebuilds on `PaletteChange`, rather than a second set of files
being kept in step by hand. A name with no file behind it costs the picture
and nothing else — a warning in the log, an empty `QIcon`, and an action
that keeps its text, its shortcut and its tooltip — because a window that
refused to open over a missing asset would be the worse failure. Every
icon-only button carries its menu label as a tooltip, since a picture on
its own is not a word.

**The application icon is a different kind of drawing, and is kept
differently.** It is full colour and meant to be looked at, where the
toolbar's are line art meant to be tinted, so it never goes through the
tinting path and does not follow the palette. It lives in
`resources/appicon/` as two files and a script: a 1024 master, a 512 icon,
and `recreate-icons.py`, which derives the second from the first by hiding
three detail groups and raising the stroke weight. That is what keeps a
drawing that has to read at 16px from being a second drawing to maintain —
the fur lines and page edges that make it at 512 are what turn it to mud at
16, and they are removed by hiding groups rather than by redrawing.

The script never touches geometry, and proves it rather than promising it:
it parses both files and compares every element and every attribute except
the two the derive is allowed to change, so a moved pupil is caught even
though a pupil is a `<circle>` with no path data. Every substitution it
makes is counted, because `re.sub` and `str.replace` both answer "not
found" by handing back what they were given — a master re-saved with its
stroke width spelled `4.50` would otherwise have produced a hairline icon,
reported as written. Neither SVG carries provenance metadata, and the
derive strips `<metadata>` so that neither starts to. The pair arrived with
a signed C2PA manifest each — 63% of the bytes, and the two different,
having been signed separately — which made `--check` impossible to pass,
since the derive carries the master's forward and that is never the icon's.
The deeper problem is that a manifest signs a file's bytes, so regenerating
the geometry invalidates whichever one is carried: a credential the script
cannot keep true is worse than none. Stripping both cost nothing visible —
the rendered pixels are identical at every size — and took each file from
12KB to 4.6KB.

Two different guards, which is worth keeping straight. The parsed
comparison above is about the *script*: the derive must not alter the
drawing on its way through. `--check`, which `tests/test_gui_appicon.py`
runs as part of the suite, is about the *files*: the committed icon must
still be what the current master derives to, so forgetting to regenerate is
a test failure rather than a shipped stale icon.

**Reshaping: the canvas drags, the window decides, the document validates.**
Dragging a corner changes only what is drawn; the polygon reaches the
document once, when the mouse comes up, which is what makes one drag one
undo step. The canvas cannot judge the result — it draws shapes and knows
nothing about what a plan file will hold — so it reports the new outline and
`main_window` puts it to `PlanDocument.set_polygon`, which validates by the
reader's own rules and raises rather than accept a shape that could not be
read back. A refused edit is put back on the canvas from the document, so
what is on screen is never something the document does not have.

That validation is the one place this could have gone wrong quietly.
`write_plan` performs no schema checking — the reader does — which was
harmless while the GUI could only edit text, and stops being harmless the
moment a polygon can move: a self-intersecting outline would save fine and
fail to load, taking the rest of the file's translations with it. The rules
live in `planfile.schema` where both the reader and `gui.document` can reach
them, rather than being written out twice and drifting.

**`show_page` lets go of every item before it clears the scene, and that
order is load-bearing.** `QGraphicsScene.clear()` destroys the C++ objects
without telling shiboken, so any Python wrapper still holding one is left
pointing into freed memory — `Shiboken.isValid` on it returns False the
instant `clear()` returns. Nothing has to *read* such a wrapper for this to
be fatal: dropping it is enough, because shiboken frees what it wrapped as
the last reference goes and there is nothing left to free. That is a
segfault, not an exception, so it takes the window with it.

It was reported as the review window vanishing, and the fatal trace named
the line that installs the *next* page rather than the clear — because
rebinding `_pixmap_item` was what dropped the previous, already-invalidated
one. Anything that reaches `show_page` can trigger it: toggling the
preview, or stepping to a region on another page. The fix is to null the
item attributes and empty the item containers first, so the wrappers are
gone before the objects are, and a test asserts exactly that by watching
what is still held at the moment `clear()` is called.

`clear()` is the only call with this hazard. `removeItem` hands ownership
back to Python and leaves the wrapper valid, which is why `refresh_regions`
and `_refresh_handles` can use it freely.

**The same shape turned up again at teardown, and it is why no signal here is
connected to a `partial` or a lambda over `self`.** Quitting a built
application segfaulted once: the `QApplication` destructor deletes the
window, which deletes its child `QAction`s, and destroying an action cleans
its connections — which freed a `functools.partial` holding a bound method of
the window, dropping the last reference to a wrapper whose C++ object was
part-way through the destructor the whole chain was running inside. Again the
*drop* is what kills the process, not a read.

A bound method connected on its own does not do it: PySide gives such a
connection the receiving `QObject` as its context, so Qt breaks it when that
object goes rather than leaving a Python callable to be freed during
teardown. So the recent menu's per-path argument, which is what the partial
existed to carry, rides on the action instead — `QAction.setData` holds it as
a plain string, and nothing holds a reference to anything.

Recorded as reasoning rather than as a measurement: it has been seen once, on
a Mac, on Python 3.14, and it does not reproduce here — six runs of the real
window left alive at interpreter exit under the offscreen platform exit
cleanly. What was done removes the object the trace died on. It does not
prove the race is gone.

**One canvas mode at a time.** A click on the page means different things —
select a region, take hold of a corner, place a corner, take a colour — and
they contradict each other, so the canvas holds a single `CanvasMode` rather
than a set of switches that could be on together. The window's two checkable
actions follow the canvas through `mode_changed` rather than driving it,
because the canvas leaves a mode on its own: an outline that closes and a
pixel that is picked both end the mode that produced them.

**A mode's gestures are shown, not announced.** The line under the canvas
carries them, keyed off the same `mode_changed` the toolbar follows, and its
text lives in `canvas.MODE_HINTS` beside the modes themselves — a mode that
gains a gesture gains its line in the same edit. It replaced a status bar
message shown once on entering a mode, which meant the gestures were
readable for exactly as long as it took the next message to arrive. The
label (`hint_line.py`) takes an `Ignored` horizontal size policy so that a
line wide enough to read cannot set a floor under the window's width:
measured at 405px of text against a central widget whose minimum stays 70px,
and a window minimum of 478px either way. What will not fit is elided with
the whole line in the tooltip — the same bargain `font_box` strikes with a
long family name, and the reason the hints are ordered with the gesture you
need most at the front.

**`review` may measure the page; `apply` may not.** The invariant is about
the second pass: everything `apply` needs is in the plan file, which is what
makes it deterministic. A region drawn by hand needs a `fill_color` and a
`text_color` before it can be in that file at all, and measuring them beats
guessing — a white-on-black caption is a page turn away. `gui/sampling.py`
is where that dependency lives, kept out of `gui/document.py` so the
view-model stays free of numpy and OpenCV.

It calls the same `detect.color.sample_colors` extract calls. Detection hands
it the OCR line boxes to find ink in; a hand-drawn region has none, so the
middle 60% of the outline's bounding box stands in for them — lettering sits
in the middle of a balloon, and the edges of a hand-drawn outline are where
it strays onto the artwork. Measured: offering the *whole* bounding box of a
rectangle drawn around the synthetic ellipse fixture returns the dark art in
its corners (90, 90, 90) as the text colour instead of the lettering's
(20, 20, 20). With the middle box, re-sampling all 21 detected regions across
four real fixture pages reproduces exactly what extract recorded in 20 of
them; the twenty-first differs by a near-white tint on a whisper balloon
(#ebf8f4 against #ffffff).

**A merge is refused unless the outlines share area.** The bounding-box
ratio `overlapping_region_ids` warns with is deliberately loose — it exists to
say "these two will draw over each other", where a false positive costs a
glance. Merging cannot use it: the merged polygon is the convex hull of both
outlines, and a hull across two balloons on opposite sides of a panel covers
the artwork between them, which erase then paints over. So `model` grew a real
test — `polygons_overlap`, built from the edge-crossing check that was already
there plus a ray-cast `point_in_polygon` for the case where one outline is
wholly inside the other. Both are kept free of OpenCV, which has
`pointPolygonTest` and is already a dependency of `detect`, because
`gui.document` needs them and is deliberately free of numpy and OpenCV.

The hull, rather than a union: a `Region` holds one simple polygon, and the
union of two overlapping outlines is not always one. A hull is, it covers
everything both covered, and with the overlap gate in front of it the extra
area it claims is the notch between two tracings of the same balloon.

**Region ids go forward, never back.** A new region is numbered past the
highest its page has used, and the document keeps that high-water mark for
the session so that deleting the last region on a page and drawing another
does not hand the old one's name to the new one. An id is how a region is
named in a report, in a note, in a commit message; reusing one makes those
quietly wrong. The mark cannot outlive the session, because a plan file has
no way to record the ids that are no longer in it.

**How much to paint over is a fact about one balloon.** `fill_color` is the
colour an erase paints *with*, not the colour of the region, and under the
default `flat` strategy it reaches only the pixels that read as the original
lettering. Measured on the synthetic fixture: recolouring a balloon's
`fill_color` to red repaints 10.4% of it — the glyphs and their fringe — and
leaves the rest white; a region drawn on plain artwork, where nothing matches
its `text_color` at all, has 0% painted. Neither is a bug in the fill, and
neither is what someone changing a colour expects to see.

So a region carries an optional `erase` of its own, the way it carries
`font`: `none`, `flat`, `polygon` or `inpaint`, with the run's `--erase` flag
as the default for regions that say nothing. `none` is the transparent case —
nothing is painted and the translation is lettered onto the artwork as it is
— and exists as a run-wide flag too, since a strategy that paints nothing is
still a strategy. A region drawn in `review` is written with `erase: polygon`,
because a person outlining an area means all of it, and because the colours
sampled for it would otherwise reach nothing.

**The hint line names the modifier the way the platform does.** Qt maps
`ControlModifier` to Command on macOS, so a line hard-coded to "Ctrl" would
be wrong on the machine this tool is written for. `canvas.move_modifier_name`
asks Qt what it renders the modifier as — by pairing it with a key and taking
that key's text back off, since a bare modifier renders as an empty string —
and `mode_hint` fills it into the copy. Whatever a menu would print is what
the line prints.

**A tap is a pixel; a held key accelerates.** The keys exist for the
correction no drag can land — no pointer lands on an exact pixel — so the tap
has to stay one pixel. Holding one at that rate is a crawl, so the step grows
every three auto-repeats up to a ceiling: measured at 26px over the first ten
repeats and about 360px in the first second at a typical repeat rate, against
40px and 30px before. The count resets on the next fresh press, so precision
is always one tap away, and `Shift` is a flat stride that starts above the
ceiling.

**A gesture is an undo step; a run of keys is one too.** A drag or a
double-click ends the edit run on both sides of itself, so it can neither
join what came before nor collect what comes after: one gesture, one step.
Arrow-key nudges deliberately do not, so consecutive taps coalesce on the
`(region_id, "polygon")` run key the way typing into a field does — forty
taps are one thing done. The run then breaks where typing runs break: a
different region selected, a mode entered, a gesture finished. This is the
one place where two routes to the same `set_polygon` call are meant to
record differently, which is why the canvas reports them on two signals
rather than one.

**Editing a polygon makes it `manual`.** The value describes how the outline
was arrived at, and once someone has dragged it, "traced from a contour" and
"a padded box around the OCR" are both false. It is also the useful thing to
know on a second pass — which regions have already been fixed by hand — and
it clears the `approximate` flag, which means "check this" and has by then
been done. Moving a shape without reshaping it counts the same: a polygon
that has been put somewhere by hand is a polygon someone decided on.

**What counts as "something to check" is computed once, in
`gui.document.RegionFlags`, and nowhere else.** Overlap uses the exact
threshold `render._warn_about_overlaps` warns at, over actionable regions
only, so a region the GUI flags as overlapping is exactly one `apply` would
also warn about — never a surprise the GUI invented on its own reading of
the plan.

**Both passes run off the UI thread, and both loops stay where they are.** A
chapter is a second or so a page — apply in Pillow, numpy and OpenCV, extract
in detection and OCR on top of them — and either would freeze the window for
the length of the run, with no progress and no way out. So `gui.run_job` runs
`apply_plan` and `extract` on a worker thread, but it runs *them*, not loops
of its own. The alternative was for the window to iterate the pages itself so
it could report between them, and that is a second implementation of "every
page of a chapter" to keep in step with the first, per pass. Two optional
callbacks on each existing loop cost less and cannot drift.

`RunJob` subclasses `QThread` rather than moving a worker object onto one.
The usual advice is the other way round, and it is right when the worker has
slots to be called while it runs, because a thread that only executes `run()`
has no event loop to deliver them to. These workers have nothing to receive:
they are stopped through a `threading.Event`, not a slot. Overriding `run()`
then costs nothing and fixes the thing the other shape gets wrong here — with
an idling event loop, `wait()` blocks until somebody remembers to quit it,
which made closing the window during a render hang for the full timeout.

The plan a render job works from is the frozen `Plan` it was handed at the
start, so editing the document while it runs cannot change what lands on
disk, and no lock is needed for that. An extract job shares even less: the
plan it writes did not exist when it started. Nothing else is shared either —
`render_page` allocates its own images and `FontFile.load` builds a new
FreeType font per call — so a live preview on the main thread and a run on
the worker do not meet.

**Apple Vision on a worker thread — confirmed.** `performRequests_error_` is
synchronous and Apple's own guidance is to run it off the main queue, so the
request side is what the API is for. What a Python thread does not get for
free is an autorelease pool: PyObjC does not install one per thread, and
without it every Objective-C object autoreleased in the adapter leaks for the
life of the process — forty pages of CGImages and Vision observations. The
adapter therefore opens one around each page, which is the right granularity
anyway: the pool drains when the page is done rather than when the chapter is.

This was written on Linux, where pyobjc will not install, so it shipped as an
argument rather than a measurement. It has since run on a Mac: `Extract
Pages…` over `tests/fixtures/11-complex_six_panel_page.png` wrote a plan whose
header says `ocr_engine: apple-vision`, with ten regions, all `geometry:
exact`, tails traced and text read correctly. Vision executes on the worker
thread and comes back with usable results — that half is settled.

**The leak is not.** One page cannot show a pool that never drains; that
would take a long chapter and a memory profile. So the pool stays because the
argument for it is sound, not because it has been seen to work, and anyone
with a forty-page run and Instruments to hand can close the second half.

The harness around it was measured on Linux from the start: a real Tesseract
run through `ExtractJob` executed on a different thread id from the window's,
which turned its event loop 272,000 times while two pages were read.

**Cancelling happens between pages, never inside one** — and what that leaves
behind is each pass's own business. A page takes about a second, so waiting
for the one in flight costs nothing. For `apply` it buys the guarantee worth
having: what a cancelled run leaves on disk is whole pages, byte-identical to
the ones a complete run would have written, and re-running finishes the job.

`extract` is the opposite, and deliberately so: a cancelled extract writes
**nothing**. Its output is one file that names the images it covers, so half
of one is a plan claiming a chapter it never read — there is no partial form
of it that is still true, the way a rendered page is. `extract` itself still
returns the plan it built; refusing to write it is the caller's decision, and
`ExtractJob` makes it. That is also why the window opens the plan only on a
run that finished.

The Cancel button disables itself on the first press and says "Cancelling…",
because a button that still looks pressable invites the assumption that the
press did not land.

**Extract asks for four things, and the other fifteen stay on the command
line.** Input, plan path, the language pair and the recogniser are what you
decide every time. The detection-tuning flags — contour area, solidity,
extent, colour segmentation — exist for the page that came out wrong, which
is a thing you iterate on in a terminal against `--debug-dir`; putting them in
a dialog would be putting a debugging session in one. `--merge` is left out
for a different reason: re-detecting over hand-drawn, reshaped and merged
regions is destructive against exactly that work, and carrying translations
across by geometry is a second feature with its own failure mode. Extract to a
new plan, and open it.

**One button for the input, and a radio pair saying what it will open.** The
extract dialog asks one question — which pages? — and a folder and a single
image are the same answer at two granularities, so they belong on one
control. There are two panels behind it rather than one because Qt has no
file dialog that takes either: measured, `FileMode.Directory` refuses a file
and `ExistingFile` refuses a directory, both returning `result=0` from
`accept()`. The only route to a panel that accepts both is subclassing
`QFileDialog` and overriding `accept()`, which forces `DontUseNativeDialog` —
a Qt-drawn Open panel on macOS, and the only non-native file dialog in an
application whose Open Plan, Save As and Render Into are all the system's.

The choice is radio buttons rather than a menu on the button so that the mode
is visible without clicking anything and browsing stays one click. They steer
that button and nothing else: what the field accepts is decided by looking at
the path, so a folder typed in under "a single image" still works. Folder is
preselected because it is the usual case by a long way — a single image is
what `default_plan_path` has its own special case for.

**A render saves first; a preview does not.** `apply_plan` takes a `Plan`
object and would happily render what is in the window, which is exactly what
the live preview does. The difference is what survives: a preview is
ephemeral, and output files are not. Pages rendered from a plan that exists
only in a window are pages nobody can regenerate — which is the property the
two passes exist to have — so the render dialog offers "Save and Render"
rather than rendering an unsaved document, and its button says which of the
two it is about to do.

There is no equivalent of `--skip-hash-check` in the window, and none is
needed: `PlanDocument.open` hash-checks every page, so a plan whose images
have changed does not open at all. A render started from an open plan has
already passed the check that flag exists to skip.

**The output-directory refusal is the one thing here with no override.**
`check_output_dir` is where "source images are never written to" is actually
enforced, and it is asked on every keystroke rather than after the dialog
closes: the button stays disabled and the reason sits under the field. No
checkbox, no confirmation, no modifier. Every other refusal in this tool has
a `--force`; this one must not grow one, in either surface. Its message
avoids naming `--output` for that reason — the same sentence has to read
right in a dialog.

**The report is a dock, not a dialog.** What a finished run leaves behind is
a list of regions to go and look at — text that would not fit, a translation
still holding its source, a page that failed outright. That is a thing to
work through, not a thing to dismiss, so its rows select the region they name
and the window follows onto the right page. A modal would also have stopped
you reading the plan while a chapter rendered, which is the one thing there
is to do while waiting for it.

Which outcomes are worth listing is decided in `gui.run_report`, which
imports no Qt and is tested on its own. It is the same judgement each pass's
end-of-run summary makes on the command line, kept out of the widget so that
it can be.

**One dock for both passes**, because the two are never both current: an
extract ends by opening the plan it wrote, at which point the last render's
report describes a plan that is no longer open. The two reports are different
lists — a render's rows are regions to go and look at, an extract's are pages
the plan cannot speak for — but they are the same shape, so `RunRow` carries
both and a row knows whether there is anywhere to go. An extract does *not*
list the regions that merely need checking: the plan it just wrote flags every
one of them, the page list counts them, and Next Flagged Region walks them, so
a second copy in a panel would go stale the moment one was fixed.

**Two files, because a failure comes in two kinds.** `review` configured
logging the way the CLI does — a stream handler onto stderr — and a window
launched from Finder has no stderr anyone will ever read, so everything it
said was written and thrown away. `gui.logfile` adds a rotating file
alongside; `gui.crash` keeps a separate one for a process that dies. They
stay separate because the crash file is written from a signal handler and
must not contend with the locks the logging module takes.

The file takes DEBUG while the stream handler keeps whatever level the CLI
chose, which is what makes "log the risky thing before doing it" worth doing:
a log that ends mid-page names the page even when nothing else can. `install`
pins the existing handlers at the root level before opening the root up, or
`-q` would quietly start printing INFO to a terminal it was told to keep
quiet.

**The log must not contain the comic, so a `Region` cannot say it.**
`source_text`, `translation` and `notes` carry `repr=False`. The easy way to
write a log line is `log.info("region %s", region)`, and the easy way for a
traceback to carry text is a local variable in a frame — neither is a place
the translator's work should turn up, and neither is defended by remembering
to be careful. `region.translation` still prints when asked for by name;
nothing prints it by accident. Two tests hold the line, one per route.

**One hook covers the whole swallowed-exception case.** Measured: PySide6
calls `sys.excepthook` for an exception raised inside a slot, whether the
slot was invoked from C++ or queued through the event loop. So the window
does not need per-slot wrapping to stop those vanishing — the hook logs the
traceback and the window puts a line in the status bar naming the type. That
matters because PySide6 does not abort on one: without this the event loop
carries on with the document possibly half-edited and the only evidence on a
terminal nobody had open.

`threading.excepthook` catches what a worker thread's own handler misses, and
`qInstallMessageHandler` routes Qt's own warnings — the most informative of
the three, since Qt says a good deal about layouts and dangling objects that
nobody was seeing.

**Guard what touches the world; let the hook catch the rest.** The audit that
came with this found fewer holes than expected: the handlers that read a
page, render a preview, merge two regions or write a plan already caught
`ComictransError`. What was left were the ones that read the filesystem
outside a plan — rescanning fonts, and the two dialogs whose font box does it
on construction — plus deleting a region that could have gone underneath the
window. Those are guarded; everything else relies on the hook, which is the
honest trade, since wrapping forty slots that cannot fail would be forty
places to keep in step.

`_report_failure` decides how loudly by what kind of error it is. A
`ComictransError` is an expected failure carrying something to act on, so the
message *is* the message. Anything else is a bug: the status bar gets its
type and a pointer to the log, and the log gets the traceback, because a type
and a line number are what a bug report needs and neither belongs in a status
bar.

**A hard crash gets a file, because it gets nothing else.** An exception in a
Qt slot is printed and the event loop carries on — survivable. A segfault, or
the abort `qFatal` raises when Qt gives up, is not: the process dies, and a
window launched from Finder has no stderr for any of it to have gone to.
`gui.crash` turns `faulthandler` on before the `QApplication` exists, so the
fatal signals write a Python traceback — the exact line, on every thread —
into `~/Library/Logs/comictrans/` on macOS. Measured: `SIGSEGV` (exit 139)
and `SIGABRT` (exit 134) both caught, and there is a test that segfaults a
subprocess and reads the function name back out of the file.

Three deliberate choices. The path is worked out by hand rather than from
`QStandardPaths`, whose `AppDataLocation` is `Application Support` — the wrong
answer on the one platform this tool targets, where logs live in
`~/Library/Logs` and Console.app reads them. The file object is held for the
life of the process, because `faulthandler` writes to the *descriptor* and
letting the object be collected would close it out from under a handler that
only runs when things are already bad. And `enable` never raises: an
unwritable home costs the traces and nothing else, since a diagnostic that
stops the window from opening is worse than no diagnostic.

It is not the application log, and 4.17 will not make it one. This file is
written from a signal handler and must not contend with the logging module's
locks; it holds a banner per launch and the last thing the process did.

**A preference never overrides a plan value.** It fills in a blank when
something is created, and does that only. This is the whole of milestone
4.13 and the one way it could have gone wrong: a default font that quietly
won over a plan's header would mean the same plan renders differently on two
machines, and re-runnability is the property the two passes exist to have.
The header dialog edits *this* plan; preferences decide what a *new* one
starts from.

What keeps them apart is not vigilance but where the values can reach. Every
field either seeds a new plan's header — the language pair, the OCR settings,
the font `extract` records — or picks a run-wide setting that is not part of
a plan at all: the erase strategy, which a region's own `erase` still beats,
and the output directory and format, which no plan has ever held. There is no
code path from `Preferences` to an open `PlanDocument`, and a test asserts
the header, the dirty flag and the file on disk are all untouched by editing
one.

A preference also gets no more trust than anything typed by hand. A stored
output directory inside the source tree is refused by `check_output_dir` on
the way through the render dialog, the same as one typed there.

**`preferences.py` imports no Qt**, because `QSettings` satisfies its store
protocol structurally: `value` and `setValue` and nothing else. The tests
pass a dictionary, so they never build a `QSettings` — which would want a
real application name and write into the config of whoever runs the suite.
Every field is a string, and an empty one always means "the behaviour you
would get without this setting". One rule instead of a scattering of
sentinels, and it round-trips through an INI file without the type guessing
that makes `QSettings` booleans a trap.

Loading falls back field by field rather than all at once. A settings file is
hand-editable and outlives the version that wrote it, so a stale engine name
costs that one field and leaves the rest of the file standing.

**The guide is a document, not strings in the source.** `review` ships a
short guide — what the outline colours mean, what each flag means, what the
preview does and does not tell you — as one HTML file per language under
`gui/resources/help/`, opened in a `QTextBrowser`. Strings in the source
would have to be found and re-found by whoever translates them; a file is
handed over whole. `document_path` picks by name and falls back to English,
because a partial translation is the normal state of one of these and a
missing language is not worth a missing Help menu.

It is not modal. Help you cannot keep open beside the thing it describes is
help you memorise a paragraph at a time, so it is a window, and asking for
it twice raises the one already up rather than stacking a second and losing
the place it was scrolled to.

Nothing in it is generated, including the swatch colours, which are written
into the document as hex. That is the deliberate half of the bargain: a
translator receives prose with no machinery in it. The drift that would
otherwise buy is caught by tests instead, which read the shipped file and
hold it to the code — the swatches against `canvas.COLOR_*`, the flag names
against `inspector._FLAG_LABELS`, and every `href="#…"` against every
`<a name>`. They run over every file in the directory, so a translation
dropped in is held to the same rules.

That last one is not a nicety. Measured: `scrollToAnchor` on a name the
document does not have scrolls to the *top* — not an exception, not a no-op
— so a renamed section silently turns its contents entry into a link back to
the contents.

The browser fetches nothing. `setOpenLinks(False)` turns navigation off
outright and `_on_anchor` follows a bare fragment and nothing else, so a URL
that finds its way into a translated document is ignored rather than opened
— this pipeline makes no network calls, and a rich text widget is the one
place a document could have made one on its behalf.

### Interface conventions

Settled once, so that the next label added does not have to be argued about
from first principles. Apple's Human Interface Guidelines decide where
something is; the rules below decide what it is called.

**Two cases, told apart by where the words are, not by what they mean.**
Anything you invoke — a menu item, a push button, a window or dock title —
is Title Case, which is what the HIG asks for. Anything inside a form — a
field label, a group heading, a checkbox, the line of help under a control —
is lowercase: `fill colour`, `pages are lettered in`, `a new plan starts as`,
`overwrite pages already in that directory`. So are the status bar's
messages and the hint line under the canvas.

That second half is not the HIG, which wants sentence case there, and it is
kept deliberately. The inspector is a dense column of field names read at a
glance rather than sentences read in order, and a capital on each would give
twelve of them the weight the section headings are carrying. The rule is
positional so it can be applied without judgement: the two checkboxes that
had drifted into sentence case were the only places it was ambiguous, and
they are lowercase now.

**British in the window, American in the plan.** `colour`, `licence`,
`recogniser`, `minimise`, `cancelling` — and `fill_color`, `text_color`,
`ocr_engine`. One is prose and the other is a data format: a key cannot be
respelled without a `PLAN_VERSION` bump and a reader that accepts both
spellings forever, which is a real cost for no gain. `Preferences` survives
in the code as the dataclass and as the `preferences/` settings prefix for
the same reason — renaming the key would lose everyone's stored settings —
while the window that edits it is called Settings.

**An alert has two strings, because macOS shows two.** There is no title bar
on one, and `QMessageBox` knows it: Qt overrides `setWindowTitle` on that
class for the single purpose of making it do nothing on macOS. Every static
helper takes a title as its second argument, so an alert written the obvious
way arrives on the target platform as the bare detail — an exception's own
words with nothing saying which action produced them. `gui/alerts.py` is the
one way this window opens one, and it puts the sentence in the message and
the detail under it.

The unsaved-changes alert offers Save, Don't Save and Cancel rather than the
Discard and Cancel it started with. Two buttons made Cancel the only way to
keep the work, which put the answer people want most often behind backing
out, saving, and asking for the same thing again. The buttons are Qt's
standard ones so that the row is laid out in the platform's order and
`Discard` is titled "Don't Save" on macOS — measured in Qt's own translation
catalogue, where that string carries the context `QCocoaTheme`.

**The three items macOS moves carry their roles explicitly.** About,
Settings and Quit are merged into the application menu by `QAction.MenuRole`,
and Qt will guess the role from the label if none is set — `detectMenuRole`
looks for "about", "quit", "exit", "preference" and friends in the item's own
text. That guess stops working the first time one of those labels is
translated, and what it costs is the item disappearing from the menu it
belongs in. A test asserts that the set of actions carrying a merge role is
exactly those three.

What the merged items are *titled* is Qt's business, not ours, and that has
been measured on a real bundle. The About item reads "About ⟨application
name⟩" whatever the action says, which is why `gui.app` passes the name as
`argv[0]`. Settings is the same: an action whose text was "&Settings…"
produced a menu item reading "Preferences". So the HIG name — macOS has
called it Settings since 13 — is not reachable by naming the action, and the
action is called Preferences again, along with the window it opens. A command
whose name does not match the window it opens is the thing this section
exists to prevent; reaching the newer name needs a translator over Qt's own
catalogue, which belongs with localisation.

**Cmd+M belongs to the window, and the Window menu was missing.** Qt adds no
Minimise or Zoom of its own, so a window that does not define them has no
Cmd+M at all — which is how Merge Region came to hold the shortcut every Mac
window uses to minimise. The menu now opens with Minimise and Zoom, then a
separator, then this window's own panels, which is the order every other one
has. There is no Bring All to Front because there is nothing to bring: one
window, and no way to open a second. Merge Region moved to Cmd+Shift+M, which
pairs it with Add Region's Cmd+Shift+A.

**An ellipsis means the command stops to ask; a tick means you are in a
mode.** Merge Region wore one because a second click follows it — which is
equally true of Edit Region Shape and Add Region, and neither of those wore
one. All three are checkable, so all three are modes, and a test holds the
line that nothing checkable ends in an ellipsis.

**Reload is called Revert to Saved**, which is what macOS calls re-reading
the file and throwing away what is unsaved, and what it does. Reload is
browser and editor vocabulary.

Four things were looked at and deliberately left alone. Delete Region keeps
Ctrl+Backspace rather than a bare Delete, for the reason the region
navigation keys are modified too: the inspector's text fields hold the focus
for most of a session and would swallow an unmodified key. Walking to the
previous, next and next flagged region stays in View rather than earning a Go
menu of its own for three items. The dock toggles keep Qt's
`toggleViewAction`, a ticked item named for the panel, rather than a
hand-wired "Show Pages"/"Hide Pages" pair that would have to be kept in step
with the dock's actual state. And Save As still asks before replacing a file
even though the Save panel has already asked: the cost of the extra
confirmation is one click in a rare case, and the cost of being wrong about
what every file panel on every platform does is somebody's plan file.

### The application bundle

`tools/build_app.py` and `tools/comictrans.spec` build `Comic Translator.app`
on the machine that will run it. Neither is part of the package: the bundler
lives in its own `bundle` dependency group so that installing `comictrans`
for the command line does not pull one, and `tools/` is outside
`src/comictrans` so nothing ships in the wheel.

**Unsigned is the decision.** No Developer ID, no notarisation, no stapling —
so the bundle is quarantined by Gatekeeper anywhere but where it was built,
which makes it a thing you build rather than a thing you download. That is
the same bargain the README's disclaimer already strikes. The consequence
worth writing down is the one nobody expects: macOS marks a downloaded zip
with a quarantine attribute and everything built from its contents inherits
it, while a `git clone` does not — so the instructions say clone, and say
why, rather than leaving someone to meet Gatekeeper and conclude the build
is broken.

**`CFBundleName` is the whole point.** macOS titles "About X", "Hide X" and
"Quit X" from `qt_mac_applicationName()`, which reads `CFBundleName` out of
`Info.plist` and falls back to the `argv[0]`-derived name only when there is
no bundle. `gui.app` passes the name as `argv[0]`, which is what makes the
unbundled case right; the moment a bundle exists that file outranks it, and
one that omitted the key would put `comictrans` back in that menu.

**Three settings that fail silently, and are therefore tested.** The spec is
a Python file PyInstaller `exec`s with five names injected, so the tests
inject recorders instead and read back what it would have asked for. That
reaches `CFBundleName`; it reaches `console=False`, which PyInstaller turns
into `LSBackgroundOnly` — an application with no Dock icon and no menu bar —
and which COLLECT and BUNDLE each inherit from the object below them; and it
reaches the two collections the analysis cannot find on its own.

Those two are `collect_data_files`, which carries `gui/resources/` — nothing
imports the toolbar drawings, the application icon or the guide, and all
three fail as a warning in a log — and `copy_metadata`, which carries the
distribution metadata the About dialog reads itself from. `recursive=True`
walks the *required* dependencies, which is not all of them: PySide6 is an
extra, so it is required by nothing and its metadata was simply absent from
a build until it was named. Extras are asked for one at a time and skipped
when missing, because which of them are installed is the builder's choice.

**The icon is rendered per slot, from both drawings.** `.icns` holds one
image per size rather than one scalable drawing, which is the distinction
`resources/appicon/` was built around. The slot rule is keyed on **points**,
not pixels: a 16x16@2x slot is 32 pixels shown at 16 points, physically the
same size as a 16x16 on a display without Retina, and it wants the same
drawing. Keyed on pixels it would get the detailed master while the 32-point
slot beside it got the simplified one — the two swapped, at exactly the
sizes the second drawing exists for. So the simplified drawing covers 16 and
32 points (the menu bar, the Finder list, the sidebar) and the master covers
128 and up (the Dock, Finder's icon view). Measured, the choice is not
cosmetic: at 32 pixels 105 of 1024 pixels differ between the two by more
than 8/255.

The PNGs are rendered here and assembled by `iconutil`. `.icns` is a typed
container whose codes are not guessable — a 32-pixel image in the 16-point
slot and in the 32-point one differ by four bytes, with no error if you get
it wrong — so Apple's own tool does that part.

**What none of this can check, and what checked it.** No `.app` is produced
anywhere but on a Mac, because PyInstaller bundles the interpreter and
libraries of the machine it runs on. What runs on Linux is the whole
analysis: the spec builds, a frozen binary launches, opens the review window
under the offscreen platform and writes its log, and the resources and
metadata are all in the tree. `BUNDLE` returns immediately off macOS, so it,
`iconutil` and the icon itself were only ever going to be answered by
building one.

They have been. The application opens from Finder, the application menu
carries the bundle's name, and the icon reads at every size the Dock and the
Finder ask for — which is what the ten slots and the two drawings were for,
and the only evidence that the points-not-pixels rule was the right way round.
The toolbar in real dark-mode chrome was checked in the same pass. Gatekeeper
still has not been: an unsigned bundle is quarantined on a machine other than
the one that built it, and nobody has carried one to a second Mac. The advice
to clone rather than download a zip rests on how macOS marks archives, not on
having watched it happen.

**The interpreter is pinned because the bundle carries it.** `requires-python`
is a floor, so on a Mac with a newer Python installed `uv` will build an
application running one the suite has never executed a line on — measured, on
a bundle that shipped Python 3.14 against a suite that has only ever run 3.12.
`.python-version` makes the tested version the default and the build warns,
rather than refuses, when something overrides it.

**Testing.** `gui.document` and `gui.preview` are tested like any other
module, no different setup. The widget tests build a real `QApplication`
under `QT_QPA_PLATFORM=offscreen` and skip — rather than fail — on a machine
with no PySide6 installed or no windowing libraries available to construct
one; see the `qapp` fixture and the third bullet under Development in the
README. One thing they found worth recording here: closing (or reloading, or
opening a different plan over) a *dirty* `MainWindow` without first stubbing
the alert hangs the test suite rather than failing it — a real `QMessageBox`
opens a native modal event loop even under `offscreen`, and nothing will
ever click its button. Every test that leaves a document dirty either saves
or discards it, or patches the alert, before the test ends.

The stub patches `QMessageBox.exec`, not the static helpers, because
`gui.alerts` builds the box itself and execs it from Python. That is worth
more than the seam it replaced: the box is really constructed, so a test
reads back both strings it carries rather than the arguments it was called
with. It also means reintroducing `QMessageBox.critical` anywhere would hang
the suite rather than fail it, since the C++ static execs its own box out of
Python's reach.

A run is waited for rather than polled: the test starts it, calls
`QThread.wait`, and then turns the event loop over, because the report is a
queued signal and arrives only once the main thread processes events. The
things that depend on timing rather than on a rule — that the action is
disabled while a run is going, that Cancel reaches the job — are tested on a
job that is never started, so they check the rule instead of racing a
three-page run. The extract tests replace `get_recognizer` where `run_job`
looks it up, so they neither need Apple Vision nor depend on whether the
machine running them has Tesseract; what is under test there is the window,
and detection has its own tests.

A second one, found while testing that the page still pans in reshape mode:
a synthetic press-move-release that reaches `QGraphicsView`'s own
`ScrollHandDrag` — one that starts where nothing on the canvas intercepts it
— **segfaults the offscreen platform**, taking the whole run with it. Drags
that the canvas handles itself are fine, which is every drag the suite makes
on a region or a corner handle. Panning is therefore checked by hand rather
than in the suite; the press falls through to the view in one line, and what
is worth testing about it — that the selection does not follow the click — is
tested without dragging at all.
