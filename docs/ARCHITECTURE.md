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

Milestone 2 adds three more, and nothing above changes:

```python
# erase
erase(page: RgbArray, region: Region, cfg) -> RgbArray
class InpaintStrategy(Protocol):     # cheap flat fill now, heavier work later
    def fill(self, page: RgbArray, mask: MaskArray, region: Region) -> RgbArray

# typeset
layout(text: str, polygon: Polygon, style: TextStyle, cfg) -> Layout | FitFailure
    # size down to font_size_min_ratio, then condense to condense_min, then fail

# render
composite(page: RgbArray, layouts: Sequence[Layout], meta: PageMeta) -> Image
```

`erase` and `typeset` both take a `Region`, not a page and an index, so a
single region can be re-rendered in isolation — which is what the GUI's
preview will do.

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

In rough order of how often they will bite:

1. **Contour escape.** A balloon whose outline is broken, open, or touching
   the panel frame gives a contour that is the whole panel. `--max-region-area`
   and `--min-solidity` guard it; tight crops will still misfire.
2. **Joined balloons.** Two balloons sharing an edge trace as one contour and
   merge into one region, contrary to the two-balloons-two-regions rule.
   Detectable (two well-separated line clusters in one contour), not reliably
   splittable.
3. **Screentone.** Otsu on halftoned art fragments contours into confetti. The
   morphological close helps; it is not a fix.
4. **Borderless captions.** No contour, so `geometry: approximate`, and the
   cheap fill erase will smear artwork. This is what the inpainting hook is
   for.
5. **Vision on comic lettering.** It is trained on prose. Expect I/l/1
   confusion, dropped accents, and mangled Italian elisions
   (`dell'uomo` → `dell uomo`), often at high confidence — so the 0.5 threshold
   will not catch them.
6. **Skew.** One or two degrees inflates axis-aligned boxes and loosens the
   polygon fit, long before it counts as "rotated text".
7. **Gradient or textured balloon interiors** make a single `fill_color` a
   lie; erase leaves a flat patch.
8. **Metadata round-trip.** ICC and DPI survive Pillow for 8-bit RGB
   PNG/TIFF. PNG stores resolution as integer pixels per metre, so 300 dpi
   round-trips as 299.9994. 16-bit and CMYK TIFF do not round-trip cleanly;
   milestone 2 should detect and refuse rather than silently downconvert.
9. **Bleed-through** from thin paper reads as faint text and produces phantom
   low-confidence regions.
