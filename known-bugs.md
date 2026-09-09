# Known bugs

Defects that have been found, reproduced and measured, and then deliberately
left alone. Each one is here because fixing it needs a decision rather than a
patch.

## Rules for agents

**Nothing in this file is a work item.** Do not fix, refactor, work around, or
"improve" anything recorded here unless the current request explicitly names
the bug. Finding one of these while working on something else is not an
instruction to fix it, and neither is being asked to review, audit, or clean
up nearby code. If a change you are already making would otherwise alter one
of these behaviours, stop and say so rather than folding the fix in quietly.

When you do fix one on request, delete its entry in the same commit.

To add an entry: record the measurement and the reason it was left, not just
the symptom. An entry without evidence is a guess and belongs in an issue
tracker instead.

---

## 1. Re-wrapping turns the source's soft hyphens into word breaks

`extract` keeps the original lettering's line breaks in `source_text`, soft
hyphens included, and seeds `translation` from it. `apply` reflows the text
from scratch, and `markup.tokenize` splits on whitespace, so a hyphenated
break becomes two words:

```
source_text  "SARAH, MY\nSECRE-\nTARY, HAS..."
tokens       [..., 'SECRE-', 'TARY,', ...]
rendered     "SECRE- TARY,"
```

Measured on a real Apple Vision plan for `tests/fixtures/11-complex_six_panel_page.png`:
4 hyphenated breaks across 3 of 11 regions — `SECRE-`/`TARY`, `DAY OF VA-`/`CATION`,
`MISSING PEO-`/`PLE`, `AND SOME-`/`TIMES`. All four render as two words.

Keeping the breaks in the plan file is deliberate — see the module docstring
in `src/comictrans/ocr/grouping.py`. They are OCR truth and they show the
shape of the original balloon while translating.

**Why it is not a one-line fix.** Joining on a trailing hyphen turns
`SELF-`/`AWARE` into `SELFAWARE`. The usual cue for telling a soft break from
a real hyphen is a lowercase continuation, which ALL-CAPS comic lettering does
not give you. Real options are to validate the joined word with pyphen, to
strip the hyphen only at render time, or to treat it as the translator's job
and leave it.

**Impact.** Cosmetic, and only while the seeded source text is still in place:
a real translation replaces the text and the hyphen with it. It is most
visible when re-lettering a page as-is to check the pipeline.

## 2. Erase only reaches ink inside the polygon

`glyph_mask` (`src/comictrans/erase.py:59`) intersects the ink mask with the
region's polygon, at lines 80 and 87. Original lettering outside the polygon
is therefore never repainted, and the translation is drawn on top of it.

Measured on `tests/fixtures/11-complex_six_panel_page.png`, last panel, first
line of the balloon: **2,971 of 7,097 ink pixels (42%) fall outside the
polygon** and survive — the visible `Y` and `ST` of "YOU SEE, IN THE PAST".

`_cover_lines` in `src/comictrans/detect/__init__.py` grows a polygon until it
covers its own OCR line boxes, which fixes the case where a traced interior
cuts through recognised text. It cannot help when the engine under-reads a
line's *extent*: Tesseract returned `OU SEE, IN THE PA` for that line, so the
box it was grown to never included the missing glyphs.

**Engine-weighted.** Apple Vision read the same line in full. This shows up
mainly on the Tesseract fallback.

**Why it is not a one-line fix.** The polygon is the only thing bounding the
erase; widening it is what destroys artwork. `apply` also may not re-run OCR
or detection — that is what makes it deterministic and re-runnable — so it
cannot re-measure the lettering at render time. A fix has to come from
detection producing a polygon that covers the balloon's whole interior, and
that has to stay safe for regions whose polygon is a padded box over artwork.

## 3. A solid caption box is detected as a padded text box, not as the box

Caption boxes drawn as a filled rectangle come back `geometry: approximate` —
a padded box around the lettering rather than the box itself. Measured on an
Apple Vision run over the fixtures, the polygon claims this much of the real
box's width:

| region | width claimed |
| --- | --- |
| `2-white-on-black-caption-box-001` | 60% |
| `3-screentoned-halftone-comic-page-002` | 66% |
| `7-1-combined-box-balloon-and-bare-caption-001` | 63% |

**Impact is on fitting, not on the artwork.** The lettering is inside the
padded box, so erase still removes it and destroys nothing. But the typesetter
fits to the polygon, so it works with about 60% of the width the art actually
offers, and a translation longer than its source shrinks or condenses when
there was room to spare.

**Why it is not a one-line fix.** The obvious cause does not survive
measurement. The box's fill merges with the panel frame it touches into one
colour component spanning 0.89 of the page, well past the `max_extent_ratio`
cap of 0.75 — but the *same box at 0.3x resolution* measures 0.88 and traces
exactly, covering 101% of the box (`7_2-...-001` against `7_1-...-001`, the
same artwork at two sizes). So the extent cap alone does not explain it and
the real trigger is still unidentified.

Raising the cap is not the answer either: it is what catches a contour
escaping onto a flat band of artwork, measured at 0.88 against real balloons
at 0.25–0.45. A caption box fused to its frame sits in the same place on that
scale, so no threshold separates them. Telling the box from the frame needs a
different signal.

## 4. A long region id sets a floor under the Region panel's width

The inspector's id line (`_id_label` in `src/comictrans/gui/inspector.py`) is
a plain `QLabel` with no wrapping, so its minimum size is the full width of
its text. That propagates up: the panel cannot be made narrower than the
longest id it has shown, and neither can the dock holding it.

Measured with the review window's own inspector:

| id shown | id width | panel minimum |
| --- | --- | --- |
| *(none)* | — | 330px |
| `page-001-001  (exact, order 1)` | 185px | 330px |
| `5-borderless-caption-on-artwork-001  (approximate, order 1)` | 364px | 471px |
| `7-1-combined-box-balloon-and-bare-caption-001  (approximate, order 12)` | 443px | 549px |

The panel's own floor is 330px, so the id starts deciding the width somewhere
past 185px of text and grows the floor one-for-one after that. Region ids are
built from the source image's filename, so in practice this is set by the
longest filename in the plan — the fixtures alone reach 549px, a fifth of a
1440-wide laptop screen given over to a dock that holds a few short fields.

Not the same defect as the flags field, which clipped: this one never hides
anything, and it does not depend on the platform's form-layout policy —
measured identical under `FieldsStayAtSizeHint` and `AllNonFixedFieldsGrow`.

**Why it is not a one-line fix.** Every obvious remedy trades away something:

- Letting the label shrink makes `QLabel` clip without an ellipsis, so a
  truncated id is indistinguishable from a complete one — and the id's tail
  is the part that identifies the region.
- Eliding needs to elide the *middle* to keep that tail, and needs redoing on
  every resize.
- Wrapping reintroduces exactly the height-depends-on-width problem that the
  flags field was just moved off a `QLabel` to escape.
- Moving the id out of the form to a full-width line above it buys room once
  and then grows the same way.

The decision is which to give up: the whole id being readable at a glance, or
the panel being narrowable. That is a judgement about how the panel is used,
not a patch.

**Impact.** Space only. Nothing is hidden, nothing is misreported, and no
plan data is affected.

## 5. The review window has been seen to vanish during preview

Reported after a session on a Mac: the window disappeared outright while
using Render Preview. Not a dialog, not a frozen window — the process died.

**Unlike every other entry here, this one has not been reproduced.** It is
recorded anyway because the eliminations cost real time and would otherwise
have to be paid for twice, and because there is now a tool that will name it
the next time it happens. Read the rest of this entry as an open hunt rather
than a decision.

**What it is not.** PySide6 6.11.2 does not abort on an exception in a slot —
measured; it prints the traceback and the event loop carries on. So a window
that vanishes is a segfault or an abort, and `qFatal` (which Qt raises when it
gives up, on a `QThread` destroyed while running among other things) takes the
same route. From outside the two are indistinguishable.

Preview eliminates the whole thread-related branch: `render_preview` is
synchronous on the main thread and touches no OCR, no recogniser and no
worker thread. Apple Vision on a `QThread`, a `QThread` destroyed while
running, and the job read after `deleteLater` are all out unless it turns out
to happen elsewhere too.

**A reproduction was attempted and did not crash.** The reported plan and its
page — `tests/fixtures/11-complex_six_panel_page.png`, byte-identical to the
one in the report — were run through `render_preview`, `to_pixmap` and a real
paint into a `QGraphicsScene`, on Linux under the offscreen platform with
PySide6 6.11.2. It rendered 2840×3880 RGB, converted to a non-null pixmap and
painted. So the preview path is not unconditionally broken on that input, and
whatever this is depends on the platform, the display, or state accumulated
across a session.

**What is left, in the order worth checking:**

- `to_pixmap`, which is `PIL.ImageQt`. It wraps the image's memory rather than
  copying it; `QPixmap.fromImage` copying is the only reason it survives, and
  that margin is one function call wide. Converting through an explicit
  `QImage.copy()`, or through raw bytes with an explicit `bytesPerLine`, would
  remove the question entirely.
- Three copies of an eleven-megapixel page per preview: about 33MB as PIL RGB,
  44MB as ARGB32 inside `ImageQt`, 44MB again as the `QPixmap`. `Ctrl+R`
  toggles, so comparing overlay against render does that repeatedly, and a
  graphics allocation that fails on macOS need not come back as a
  `MemoryError`.
- The Retina backing store, which none of the testing has ever run on.
- `QGraphicsScene.clear()` with a stale wrapper. `show_page` clears the scene
  and every dict that held its items, so this looks handled — but it is the
  other classic, and preview is the path that calls it most.

**What closes this.** `gui.crash` now writes a Python traceback naming the
exact line to `~/Library/Logs/comictrans/review-crash.log` on `SIGSEGV` and
`SIGABRT` alike. One captured trace picks from the list above; until there is
one, picking by argument is what the failed reproduction already showed does
not work.
