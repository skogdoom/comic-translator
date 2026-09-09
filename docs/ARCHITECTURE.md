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
qimage.py      the one function that turns a Pillow image into a QPixmap
canvas.py      the page: a pixmap, and clickable region outlines over it
hint_line.py    the line under it: what a click does, elided to fit
sampling.py    colours read off the page for a region drawn by hand
inspector.py   one region's fields, writing straight through to the document
color_box.py    a colour field: a swatch, the standard values, the eyedropper
page_list.py   one row per page, with a region-and-flag-count summary
about_dialog.py  what about.py found, plus the Python and Qt versions
header_dialog.py the settings every region is drawn under
font_box.py     a font field offering only what fonts.py can resolve
main_window.py wires the widgets together; the only module that knows
               about all of them at once
app.py         available() / run() — the CLI's entry point
```

`document.py`, `preview.py`, `sampling.py` and `about.py` need no display and
import no Qt;
they are tested directly, the same as any other module. The widget modules do
— `main_window.py` is the only one that imports more than one of the others,
which is what keeps an edit's ripple effects (the window title's dirty
marker, another region's overlap flag, the page list's flag count) in one
place instead of three widgets each guessing at the other two's state.

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

**Where "the next region" is decided.** In `document.py`, not in the window,
as `adjacent_region` over the plan's own region order. Which region comes
after this one is a question about a plan, not about a widget, and asking it
there means it is tested without a display like everything else in that
layer — and that walking off the end of a page continues onto the next one
falls out of the plan order rather than being a rule the window enforces.

**Why the window is handed its `QSettings` instead of making one.** The
layout is the only thing this tool remembers outside a plan file, and a
window built without settings — which is every window the tests build —
reads and writes nothing. That keeps the suite from depending on, or
writing into, the configuration of whoever runs it, and keeps one test's
dragged-about docks out of the next one. `gui.app` is the single place that
decides a real session should persist anything.

**Why the overlay and the preview are two different things, not one.** The
overlay — polygons over the original page — is recomputed from the document
on every edit; it is cheap (nothing is rendered, only restyled) and always
in sync. Rendering a real preview erases and typesets, the actual work
`apply` does, and is comparatively expensive and worth doing deliberately
rather than on every keystroke — `Ctrl+R`, not automatic. Both read from the
same `PlanDocument`, so a preview always reflects the edit you just made,
saved or not.

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

**Testing.** `gui.document` and `gui.preview` are tested like any other
module, no different setup. The widget tests build a real `QApplication`
under `QT_QPA_PLATFORM=offscreen` and skip — rather than fail — on a machine
with no PySide6 installed or no windowing libraries available to construct
one; see the `qapp` fixture and the third bullet under Development in the
README. One thing they found worth recording here: closing (or reloading, or
opening a different plan over) a *dirty* `MainWindow` without first
stubbing `QMessageBox.question` hangs the test suite rather than failing
it — a real `QMessageBox` opens a native modal event loop even under
`offscreen`, and nothing will ever click its button. Every test that leaves
a document dirty either saves or discards it, or patches the dialog, before
the test ends.

A second one, found while testing that the page still pans in reshape mode:
a synthetic press-move-release that reaches `QGraphicsView`'s own
`ScrollHandDrag` — one that starts where nothing on the canvas intercepts it
— **segfaults the offscreen platform**, taking the whole run with it. Drags
that the canvas handles itself are fine, which is every drag the suite makes
on a region or a corner handle. Panning is therefore checked by hand rather
than in the suite; the press falls through to the view in one line, and what
is worth testing about it — that the selection does not follow the click — is
tested without dragging at all.
