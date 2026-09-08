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
