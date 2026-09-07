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

## Module dependency rules

`model` sits at the bottom and imports nothing of ours. Above it:

- `ocr/` and `detect/` never import `planfile`.
- `planfile` never imports Pillow, OpenCV, or pyobjc.
- `cli` and `extract` are the only modules that know about both sides.

That is not tidiness for its own sake. It means `planfile` can be imported by
the milestone-4 review GUI without dragging in Vision, and it means detection
can be tested with synthetic images and hand-written OCR boxes.

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
single region can be re-rendered in isolation — which is what the GUI's
preview will do.

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

## What apply does not do

It does not re-run detection or OCR, so it cannot disagree with the plan file.
It does not touch a region it cannot render: a fit failure leaves the region
exactly as it was, erased no more than it is drawn, so the page stays readable
in the source language rather than becoming a blank balloon.

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

`extract` refuses to overwrite an existing plan file unless `--force`. That
file may hold hours of hand translation and extract has no way to know.

A `--merge` mode that carries translations across by polygon IoU is the
obvious next step, and deliberately not in milestone 1: matching by region id
would be wrong (ids are positional and shift when detection changes), and a
wrong IoU match silently moves your text into the wrong balloon, which is a
worse failure than refusing.

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
