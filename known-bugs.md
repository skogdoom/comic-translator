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

## 5. macOS will not give the application its own language

System Settings > General > Language & Region > Applications answers
"Comic Translator.app doesn't support additional languages" for a bundle that
declares two, and no amount of declaring them differently has moved it.

What the built bundle has, verified on the Mac that built it by the build's
own read-back, which printed no warning:

| declaration | state |
| --- | --- |
| `CFBundleLocalizations` in `Info.plist` | `["en", "sv"]` |
| `CFBundleDevelopmentRegion` | `en` |
| `Contents/Resources/en.lproj/InfoPlist.strings` | present, UTF-16 |
| `Contents/Resources/sv.lproj/InfoPlist.strings` | present, UTF-16 |

Four things were tried, in this order, each rebuilt and tested:

1. The Info.plist key alone. No change.
2. The key plus `.lproj` directories written into the bundle after the
   build. No change — and wrong for a second reason: PyInstaller signs the
   bundle and then verifies it, so anything added afterwards breaks the seal
   it just made. That is why they are collected by the spec now.
3. The directories collected before signing, sealed with everything else,
   and the `.strings` files written as UTF-16 rather than UTF-8. No change.
4. `lsregister -f` on the bundle, in case macOS was answering from the Launch
   Services database rather than the bundle. No change.

Where the `.lproj` directories land was measured rather than assumed:
PyInstaller's own bundle-layout logic, run over a synthetic table of contents,
puts a data entry destined for `sv.lproj` at `Contents/Resources/sv.lproj`
with a cross-link in `Contents/Frameworks`. That is where macOS looks.

**The one measurement not taken is the one that would split the problem.**
`NSBundle.bundleWithPath_(…).localizations()` is macOS reading the bundle
through the call everything else goes through: if it lists both languages,
the bundle is right and the panel is answering from somewhere else; if it
lists one, something in the bundle is not being seen after all.
`tools/inspect_bundle.py` takes that reading, along with the plist, the
directories and the signature. **Run it first when picking this up.**

Untested leads, in the order they seem worth trying: that the panel wants a
signed bundle rather than PyInstaller's ad-hoc one; that the Launch Services
record needs more than `-f` (`lsregister -kill -r -domain local -domain
user`); that an application outside `/Applications` is treated differently;
that the panel wants something in the `.lproj` beyond `InfoPlist.strings`.

**Impact.** One route to a setting that has two others. The window's own
Preferences > this window > language works, and so does
`COMICTRANS_LANGUAGE`; what is unavailable is macOS's per-application
language, which is where somebody would look first on a Mac. Nothing about
the translation itself is affected — the catalogues load, and a Mac set to
Swedish throughout gets a Swedish window.
