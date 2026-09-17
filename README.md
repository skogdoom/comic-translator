<img src="src/comictrans/gui/resources/appicon/dog-book-master-1024.svg"
     alt="A dog in glasses, sitting on a book" width="160">

# Comic Translator

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

A chapter that arrives as one file works the same way: `comictrans extract
chapter.cbz` unpacks it into `chapter-pages/` beside it and reads that, so
everything after the first command is the three lines above. CBZ, CBR and
PDF — see **Chapter files**.

**Source images are never modified, moved, or written to.** They are opened
read-only, and the only things ever written are `--debug-dir`, `--output`,
the folder a chapter file is unpacked into, and the plan file itself.

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

**Plugins are a second, separate kind of risk, and unreviewed in a different
sense.** They are off unless Preferences ▸ this window ▸ experimental
features is turned on, and even then only what you install yourself runs —
nothing ships active. A plugin is a plain Python file with no sandbox around
it: it can do anything the interpreter running `review` can do, which is
everything you can do, including reach the network — this project makes no
network call anywhere in its own pipeline, but a plugin you install is not
this project, and that promise is not one this tool can make on a plugin's
behalf. Read one before you drop it into the plugin folder the same way you
would any other script you did not write yourself.

## Status

**Released: 1.0.0. This tree: 1.1.0.dev0**, which is work since that release
rather than a version anybody builds from on purpose. All three passes are
here and in use: `extract` writes a plan file, `apply` renders translated
pages from it, and `review` opens a plan beside the pages it describes.
`CHANGELOG.md` says what a release means here — there is no download, and the
reason is in the first paragraph. `validate` checks a plan without rendering
it, for the moment before a long run, and the window can put the recogniser
back over one balloon.

The window is translated: it follows the system's language, English
otherwise, and Swedish is the translation that ships.

Plugins are experimental and off by default: Preferences ▸ this window ▸
experimental features turns on a Plugins menu that runs a plan-in,
plan-out `.py` file you drop into the plugin folder. One example ships —
Plugins ▸ Install Example Plugin — that adds a note to every region.
Configuring a plugin beyond editing its own source is not built yet.

Chapters can arrive as one file and leave as one: `extract` reads CBZ, CBR
and PDF by unpacking them into a folder of pages first, and `apply --output
chapter.cbz` writes the rendered chapter back as one file. CBR output needs
the `rar` compressor, which cannot ship here; PDF output does not exist.
`docs/ROADMAP.md` covers that and everything else planned, in the order it is
worth building, and `known-bugs.md` records what is known to be wrong and left
alone on purpose.

## Requirements

- macOS on Apple Silicon, Python 3.12 — pinned in `.python-version`, since
  that is the version the suite runs on and the one an application bundle
  would carry
- Apple Vision for OCR, via pyobjc (installed automatically on macOS)
- Tesseract as an optional fallback: `uv sync --extra tesseract` plus a
  `tesseract` binary with language data for your source language
- PySide6 for the `review` GUI, optional: `uv sync --extra gui`
- For CBR input only, a RAR tool already on the machine — `unrar`, `unar`,
  `bsdtar` or `7z`. None of them ships with comictrans, and the reason is in
  **Chapter files**. CBZ and PDF need nothing beyond the install below.

Everything runs locally. There are no network calls anywhere in the
pipeline — translation is manual by design. That covers this project; it
does not cover a plugin you choose to install — see **Disclaimer**.

## Install

```
uv sync --group dev
uv run comictrans --help
```

## Build the application

`review` runs perfectly well from the command line, and on macOS it can also
be a real application with a Dock icon and its own name in the menu bar:

```
git clone https://github.com/skogdoom/comic-translator
cd comic-translator
uv sync --group dev --extra gui --group bundle
uv run --extra gui --group bundle tools/build_app.py
```

That writes `dist/Comic Translator.app`. Drag it to `/Applications` if you
want it there.

**It is unsigned, and that is the decision rather than an omission.** There
is no Developer ID, no notarisation and no stapling, so the bundle is
quarantined by Gatekeeper on any machine but the one that built it. It is
something you build, not something you download — which is the same bargain
the disclaimer above already strikes rather than a weaker one dressed up.

**Clone, do not download the zip.** macOS marks a downloaded archive with a
quarantine attribute and everything unpacked from it inherits the mark,
including whatever you then build. A `git clone` carries no such attribute.
Build from a zip and the application will refuse to open and look broken.

**If System Settings will not give the application its own language**, ask
the bundle what it thinks it has:

```
uv run --extra gui tools/inspect_bundle.py "dist/Comic Translator.app"
```

That prints the Info.plist key, the `.lproj` directories, whether the
signature still matches — and what `NSBundle` answers, which is macOS reading
the bundle with its own eyes. If that last one lists both languages and the
Language & Region panel still does not, the panel is answering from its own
database rather than from the application: check that the copy you added
there is the copy you rebuilt.

The build only works on macOS: PyInstaller bundles the interpreter and
libraries of the machine it runs on and does not cross-compile. Anywhere else
the script says so and stops.

It bundles the *interpreter* too, which is why `.python-version` pins 3.12:
`requires-python` is only a floor, so on a Mac with a newer Python installed
`uv` will happily build an application running one this project's tests have
never executed a line on. Override the pin if you want to — the build says so
rather than refusing.

## extract

```
uv run comictrans extract pages/ --debug-dir /tmp/comictrans-debug
```

Takes a single image, a directory, or a chapter file (see **Chapter files**
below). Directory scans are non-recursive, accept `.png .jpg .jpeg .tif
.tiff`, and sort in natural filename order, so `page2` comes before `page10`.
Anything else is skipped and logged.

The plan file defaults to `<dir>/comic-plan.yaml`, or `<stem>-plan.yaml` beside
a single input file. `extract` refuses to overwrite an existing plan file —
it may hold hours of translation — unless you pass `--force` to discard it or
`--merge` to keep it.

### Chapter files

```
uv run comictrans extract chapter.cbz
```

`.cbz`, `.cbr` and `.pdf` are unpacked into a folder of pages beside the file
— `chapter.cbz` becomes `chapter-pages/` — and everything from there on reads
that folder: the plan lands in it, `apply` and `review` open it, and the
per-page hash check works the way it does for any other chapter. `--unpack-dir`
puts the pages somewhere else.

Unpacking rather than reading pages out of the file on demand is the decision
everything else here follows from. A plan names its pages relative to itself
and hashes each one to prove nothing has moved since; reading from inside a
container at render time would break that in three places to save a folder
nobody had asked to be rid of. The container itself is a source like any
other: opened read-only, never written to.

Pages come out in the order they go in — archive entries in natural filename
order, PDF pages in page order — each written with a zero-padded index in
front of its name (`001-page1.png`). That way the folder sorts the way the
chapter reads even when the names inside do not, and two pages called
`001.png` in different folders cannot collide.

Unpacking the same chapter twice writes nothing the second time: a page
already there byte for byte is left alone. A file of that name holding
something *else* stops the run rather than being overwritten — it is somebody
else's, or an older chapter's, and picking for you is not this tool's job.
Whatever is not a page — `ComicInfo.xml`, `.DS_Store`, a Mac's `__MACOSX`
resource forks, a symlink stored in the archive — is named in the summary and
left where it is.

**The folder has to hold this chapter and nothing else that looks like a
page**, and the run stops if it does not. Everything downstream reads the
folder rather than a list, so a stray image in it is a page of the comic as
far as the plan is concerned. The way to get one is a re-release: insert a
page at the front and every name after it shifts, so the old files collide
with nothing, stay where they are, and the chapter comes out with half its
pages twice. Unpack a changed chapter into a new folder with `--unpack-dir`
— and look for a plan file before deleting the old one, because your
translation lives in there too.

Some pages are unpacked *and* flagged. A page whose one image cannot be a
photograph of it — too few pixels for the paper it covers, or not the shape
of it — is listed under `LOOK AT` in the summary: that is what a born-digital
PDF looks like from here, where the text is drawn as text and the only image
on the page is a logo sitting on it. Flagged rather than refused, because it
is still the only thing on that page and only you can say what it is.

**What a chapter file is, its first bytes decide, not its name.** A CBR that
is really a zip and a CBZ that is really a RAR are both ordinary out in the
world — both extensions mean "comic book archive" to whoever wrote them, and
which compressor made it is an afterthought — so each is read as what it is,
and a zip called `chapter.dat` is read too. The name is used only for a file
that cannot be opened at all, which is every keystroke of one being typed
into the window. A page that has been renamed `.cbz` is not a chapter, and
says so rather than failing as a broken archive.

**CBZ needs nothing**; it is a zip. **PDF needs nothing either**: a scan is
one photograph per page, so the page's own image is lifted out exactly as the
PDF stores it — a JPEG comes out the JPEG that went in, nothing is rasterised
and nothing is resampled. What that cannot do is invent a page out of drawing
instructions, so a page with several images on it, one with none, and one the
reader is told to turn are each reported and skipped rather than guessed at.

**CBR needs a RAR tool, and comictrans will never ship one.** `unrar`'s
licence is not OSI-free and this is an MIT project, so bundling it would put
somebody else's terms on the whole thing. Instead it drives whatever is
already on your machine — `unrar`, `unar`, `bsdtar` or `7z`, any of which
Homebrew has — and `COMICTRANS_UNRAR` names one that lives somewhere the
`PATH` does not reach (**Preferences ▸ chapter files** is where the window
asks the same thing). With none of them installed, CBR input is unavailable
and says so before anything is written, rather than failing part-way through
a chapter.

**Extract Pages** in the window reads one too: **File…** opens one panel for
a page and a chapter alike, and it unpacks and reads it the same way, with
the plan going in with the pages. The
pages are counted as they come out rather than up front — counting them in
the dialog would mean opening the chapter on every keystroke — so the panel
says it is unpacking until it knows, and **Cancel** stops it between pages
like any other run. Where `unrar` lives is a preference rather than a
per-run question, because an application opened from the Finder does not
inherit a terminal's `PATH` and cannot see a Homebrew one: **Preferences ▸
chapter files**.

### Re-extracting

`--merge` re-detects the pages and then carries your work across from the
existing plan: edited translations, `notes`, `skip` flags, `font` /
`font_size` overrides, and the order you put the pages in. Everything measured from the page — polygons, colours,
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

**`--output` named `.cbz` or `.cbr` writes the chapter as one file** instead
of a directory of pages — see **Chapter output** below. Any other name is a
directory, which is what it has always been.

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
| `--erase {flat,polygon,inpaint,none}` | how to remove the old lettering (see below) |
| `--min-font-ratio` / `--condense-min` | override the plan header's fit limits |
| `--font-size-floor` | absolute floor below which a region fails instead of shrinking further |
| `--no-hyphenation` | never hyphenate to make a line fit |
| `--skip-hash-check` | render against a changed source; almost always wrong |

### Chapter output

`apply --output chapter.cbz` writes the rendered chapter as one file instead
of a folder of pages, and `.cbr` does the same as a RAR. Anything else named
to `--output` is a directory, exactly as before.

**The name decides, and only the name.** Reading does the opposite — a
chapter file that is there gets read for what it is, whatever it is called —
but an output does not exist yet, so there are no bytes to ask. `.zip` and
`.rar` work as well as `.cbz` and `.cbr`, since chapters arrive under both.

**Pages are numbered in the plan's order**: `001-page-004.png`,
`002-page-007.png`, and so on, keeping each page's own name after the index.
Readers sort entries by name, so the order you set by dragging rows in the
review window has to survive as a name; source names that already sort right
are luck.

**Nothing else goes in.** No ComicInfo.xml, no metadata of any kind. The plan
file knows the two languages and nothing else about your chapter — not the
series, not the volume, not the numbering — and a chapter file that claims
those things because a tool had to fill the fields in is worse than one that
claims nothing.

**A cancelled run into a chapter file leaves no file at all**, which is not
the promise a directory makes and is deliberate. Pages are rendered into a
temporary directory and packed once every one of them is done: a directory
keeps whole pages and running it again finishes the job, while half an
archive is nothing anybody wants. The command line says so when it happens
rather than reporting zero pages.

**CBZ needs nothing. CBR needs the `rar` compressor, which cannot ship here
either** — and for a different reason from the reading side. RAR compression
is proprietary: `unrar` only reads, and its licence forbids using it to
create archives, so the only thing that writes a `.rar` is the `rar` binary
that comes with WinRAR, which is paid and not redistributable. What that
licence restricts is *redistributing* it, not driving a copy you have already
licensed — so comictrans looks for `rar` on `PATH`, takes `COMICTRANS_RAR`
for one somewhere else (**Preferences ▸ chapter files** in the window), and
with neither says which binary would provide it rather than leaving CBR
quietly missing. It says that before rendering a page, not after the chapter.

### Erasing

- `flat` (default) masks the glyph pixels by colour distance to the region's
  recorded `text_color` and repaints them with `fill_color`. Judged relative to
  the gap between the two colours, so light-on-dark works the same way.
- `polygon` floods the whole polygon interior. Cleanest for a plain balloon,
  wrong for anything flagged `geometry: approximate`, since it paints over art.
- `inpaint` reconstructs the masked pixels from their surroundings, for
  textured balloons and borderless captions where a flat patch would read as a
  hole.
- `none` paints nothing: the translation is lettered straight onto the page as
  it is. For a sound effect, or a caption over art that must not be covered.

**Any region can say for itself**, with an `erase` of its own in the plan
file, the way it can override `font`. The flag is the default for regions
that do not. This matters because `fill_color` is the colour the erase paints
*with*, not the colour of the region: under `flat` it reaches only the pixels
that read as lettering, so changing it repaints the old letters and leaves the
balloon as it was. `erase: polygon` on that region is what fills the shape.
Regions drawn by hand in `review` are written with `erase: polygon` for that
reason — a person outlining an area means all of it.

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

![The review window: the plan's pages listed on the left, a comic page in the
middle with its balloons outlined, and one region's source text, translation
and colours on the right](docs/images/review-window.png)

Opens a plan file in a window — **Comic Translator**, which is what the
application calls itself; `comictrans` remains what you type. Run it without
a path and it opens empty, ready to open a plan or extract one. Needs
PySide6, a separate extra like tesseract — most work on this repository never
opens a window. Without it, `review` fails with a clear message rather than
an import error.

The toolbar carries the commands you reach for while reading a chapter —
open and save, undo and redo, the four region tools, the three ways to step
between regions, the preview toggle, and both whole-chapter runs — as icons
rather than words, because as words they came to more than the window is
wide. Hover any of them for its name; the menus keep the words permanently,
with the shortcuts beside them.

**Pages are reordered by dragging their rows** in the list on the left.
That is one order, not two: it is the order you review in, the order `apply`
works through, and — once a whole chapter can be written into a single file
— the order somebody else will read it in. Regions move with their pages, so
`Ctrl+Down` keeps walking the comic in the order you can see. Re-extracting
does not undo it.

The overlay uses the same colours as `--debug-dir`: green for a traced
contour, orange for approximate, violet for a shape drawn or edited here by
hand. A region with something worth checking — approximate geometry, low
confidence, held back with no translation, still identical to its source
text, or overlapping another region — is outlined dashed red instead,
regardless of its geometry colour, and the reason is named in the inspector.
Click a region to select it; its fields are on the right, editable in place,
written straight into the plan as you type — the source text among them,
since a region you drew by hand has no OCR reading until you ask for one, and
the plan file has always been hand-editable anyway.

**Moving a region** needs no mode: `Ctrl`-drag it (`Cmd` on macOS), or use
the **arrow keys** — one pixel a tap, twenty with `Shift`, and faster the
longer you hold the key down — once the page itself has focus, which one
click on it gives. The hint line under the page names the modifier the way
your own keyboard does. Plain dragging still pans the page,
which is why the modifier is there; the cursor turns into a move cursor while
you hold it over the selected region. A region stops at the edge of the page
rather than going off it, a run of nudges is one undo step, and a moved region
records `geometry: manual` like a reshaped one — it is no longer where
detection put it.

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
how the page is panned. The mode stays on the region you chose it for: while
it is on, clicking another region says so rather than switching to it, so a
stray click cannot quietly move the handles onto a balloon you did not mean
to edit. To work on another one, turn the mode off, select it, and turn the
mode back on. Dragging the page around still works throughout. A region you reshape is recorded as `geometry:
manual`: what it says about itself is no longer that detection traced or
guessed it, and the "check this" flag an approximate region carries clears,
because checking it is exactly what you have just done.

**Edit > Add Region** (`Ctrl+Shift+A`) draws one by hand, for a balloon
detection missed entirely. Click to place each corner; click the first corner
again, double-click, or press Enter to close the outline; Backspace takes a
corner back and `Esc` abandons it. A region drawn here has no OCR reading:
type its **source text** in along with the translation, or use **Extract Text
from Region** below to have the recogniser read it. Until there is text, the
region is flagged as held back. It records `geometry: manual` and
`confidence: 1.0` — there is no recogniser's score to report.

**Edit > Extract Text from Region…** (`Ctrl+Shift+T`) runs the recogniser over
the selected region and puts what it reads into the **source text** — for a
region you drew, which has none, and for one whose lettering `extract` read
badly. It reads that region rather than the page: a crop of the outline with
a margin around it, which costs a fraction of a page and is why this is a
per-balloon command rather than a re-run. The margin is not decoration —
measured across the test pages, a crop cut to the outline agreed with what a
full-page reading found 10 times out of 31, and the same crops with a margin
agreed 27, better than half of them word for word — and anything the margin
lets in from the balloon next door is dropped, because a line belongs to the
outline its middle falls inside.

A region with neither a reading nor a translation — one you have just drawn —
gets both, which is what `extract` does for every region it reads: the text is
then edited into the target language in place rather than retyped. A region
that already has text keeps its translation, and is asked about before its
source text is replaced, since nothing in a plan file says whether that text
was read off the page or typed in by hand. The question does not quote either
text: a balloon's worth of lettering is a paragraph.

It is one undo step like any other edit, it runs in the background with the
window still usable, a reading that comes back empty changes nothing and says
so, and a reading that matches the text already there says that too rather
than appearing to do nothing. Only the text is written: the region's
`confidence` is what detection scored it, and reading one balloon is not
detection.

Its **fill colour** and **text colour** are measured from the page inside the
outline you drew, the same way `extract` measures a detected region's, and it
is written with `erase: polygon` so that colour fills the shape. Both
are editable on any region: the swatch opens a menu with the standard
lettering colours, a full colour dialog, and **Sample from the page…**, which
takes the next click on the page as the colour. That last one is the answer
when an outline strays onto artwork and drags a colour with it.

The **erase** field says how much of a region `apply` paints over before it
letters it: the plan default, the lettering only, the whole region, a
reconstruction of the background, or nothing at all. *Nothing* is the
transparent case — the translation goes straight onto the artwork — and the
fill colour is disabled while it is chosen, because nothing is painted with
it. This is the field to reach for when a fill colour appears to do nothing:
under the default `flat`, it only ever repaints pixels that read as lettering.

**Edit > Merge Region** (`Ctrl+Shift+M`) folds two regions into one, for the
balloon detection traced as two: select one, then click the other. The merged
outline is the convex hull of both, its colours are read from the page inside
that shape, and the texts are joined in reading order. The earlier region
keeps its id and order — a name in a report should stay the name it was — and
the geometry becomes `manual`, because you decided the shape.

It is refused unless the two outlines genuinely overlap, and shared area is
what counts, not shared bounding boxes: two balloons on opposite sides of a
panel have no simple polygon covering both and only both, and a hull across
them would swallow the artwork between, which erase would then paint over.

**Edit > Delete Region** (`Ctrl+Backspace`) removes the selected region. A
delete is a delete, not a `skip: true` in disguise — the region is gone from
the file the next time you save. `Ctrl+Z` puts it back while the session
lasts, the same as every other edit here.

**Render Preview** (`Ctrl+R`) calls the exact `render_page` function `apply`
itself uses, on the plan as it currently stands, unsaved edits included. What
it shows is what `apply` would write for that page — not a mock-up — so a
region that will not fit, needs condensing, or overlaps another one shows up
here before you run `apply` for real. The same command becomes **Back to
Overlay** while the rendered page is up: one button, and its label says which
way it goes. Your place is kept across the swap in both directions — the zoom,
the position, and the region you had selected.

Rendering a large page takes a moment, and the window stays yours while it
happens — the render runs on a worker thread, so you can keep reading, keep
typing, and change page. The status bar says `rendering preview…` with a bar
beside it, and then says what the preview found. The bar waits until the page
has been opened and its regions counted, and then fills as each one is
erased, which is where almost all of the time goes.

Change page while one is rendering and it stops — between regions, so the
balloon it was working on is finished and the rest never start — and nothing
is shown. Nothing is written either way: an abandoned preview leaves exactly
what a finished one leaves, which is nothing. Press the command again before
the first has finished and it renders once more from the plan as it now
stands; press it again with nothing changed and it does not, because that is
the same question.

Looking at the same page twice costs one render. The last rendered page is
kept, so toggling back to it is immediate — and any edit that page's render
would notice throws it away, as does installing a font and rescanning. One
page is kept, not the chapter: a rendered page is tens of megabytes.

`apply` is untouched by any of this. It stops between pages, never inside
one, so every page a cancelled run wrote is one a complete run would have
written.

**File > Extract Pages…** (`Ctrl+Shift+E`) runs the `extract` pass without
leaving the window, and opens the plan it wrote. It is the only thing here
that works with no plan open, because it is how you get one — which is why
`review` with no argument now opens an empty window rather than an Open
dialog. **Folder…** and **File…** open one panel each — a file being one
page, or a whole chapter as a `.cbz`, `.cbr` or `.pdf`; say where the plan
goes, prefilled with the same path `extract` picks with no `--plan`; and give
it the language pair and the recogniser. The two buttons only browse: a path
typed into the field is accepted whichever way it got there, and what a file
turns out to be is read out of it rather than taken from its name.

Those four are the whole dialog. `extract` has around fifteen
detection-tuning flags, and they stay on the command line: they exist for the
page that came out wrong, which is a thing you iterate on in a terminal
against `--debug-dir`. The font is not asked for either — with no `--font`,
extract walks its fallback chain and records what it found, and the header
font is editable here the moment the plan opens.

Reading a page is detection and OCR, 0.1 to 2.8 seconds of it, so this runs
on a worker thread like a render: the panel names the page being read, and
**Cancel** stops after it. **A cancelled extract writes nothing at all.**
That is the opposite of a cancelled render, and for a reason: a render stopped
part-way leaves whole pages, each exactly what a complete run would have
written, while a plan file names the images it covers, so half of one is a
file claiming a chapter it never read. There is no partial form of it that is
still true.

When it finishes, the plan opens in this window and the panel lists what the
plan itself cannot tell you: a page that could not be read, a page with
nothing detected on it, an input that was not an image. Regions that merely
need checking are not listed — the page list counts them and `Ctrl+Shift+Down`
walks them, and a second copy in a panel would go stale the moment you fixed
one.

**There is no `--merge` here.** Re-extracting over a plan you have worked on
is the merge case, with its own reporting for hand work that has nowhere to
go, and it stays on the command line. Now that regions can be drawn, reshaped
and merged by hand, re-detecting a page is destructive against exactly that
work. Extract to a new plan, and open it.

**File > Render Pages…** (`Ctrl+Shift+R`) runs the whole `apply` pass without
leaving the window. The dialog asks what the command line asks for as flags —
where to write, what holds the pages, what each page is encoded as, and how to
erase the original lettering — plus whether to overwrite what is already
there, which is `--force` as a checkbox rather than a refusal you rerun the
command to get past. Everything else comes from the plan's own header, so a
chapter rendered from here and the same chapter rendered by `comictrans apply`
are made the same way.

**A folder or one chapter file** is one decision shown twice, because `apply`
reads it off the output's name and nothing else: choose `.cbz` in the box and
the path is renamed, type a `.cbz` path and the box follows. There is no third
state for the two of them to disagree in. Choosing `.cbr` says up front
whether there is anything on this machine to write one with, rather than
finding out after the chapter is drawn; **Preferences ▸ chapter files** is
where you say where `rar` is.

The output is prefilled with a directory beside the pages, and one inside the
source tree is refused with the button disabled and the reason underneath it.
There is no override, no confirmation and nothing to hold down: it is the same
check `--output` gets, and the only refusal in this tool that cannot be argued
with. Source images are never written to. A chapter file is asked the same
question about the folder it would go in.

**A plan with unsaved edits is saved first**, and the button says so — it
reads *Save and Render* rather than *Render*. Pages rendered from a plan that
exists only in a window are pages that cannot be regenerated, which is the
property the two passes exist to have. The live preview renders unsaved edits
happily, because a preview is ephemeral and output files are not.

There is no equivalent of `--skip-hash-check`, and that is deliberate. The
window verifies every page's hash when it opens a plan and refuses one whose
images have changed, so a render started from an open plan has already passed
the check that flag exists to skip. **File > Revert to Saved** checks again.

The run happens on a worker thread, so the window stays usable while a
chapter renders: a panel opens along the bottom, naming the page being
worked on and how far along the run is. **Cancel** stops it after that page rather than
part-way through one, so what is on disk is always whole pages and re-running
finishes the job.

When it is done the panel lists what wants a second look — a page that failed
to render at all, a region whose text would not fit, one with no translation,
one lettered below the readable minimum or condensed to make it fit, one
still holding its source text. Click a row and the window goes to that
region, changing page if it is on another one. It is a panel rather than a
dialog because that list is a thing to work through, not a thing to dismiss;
it opens itself when a run finishes and closes from the **Window** menu.

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

A line under the canvas says what a click does in whatever mode the window is
in — placing corners, reshaping, merging, taking a colour — and stays there
for as long as the mode does, rather than scrolling past in the status bar.

The **Window** menu opens with **Minimise** (`Ctrl+M`) and **Zoom**, the two
every window has, then closes and reopens the three panels; **Reset Layout**
puts them back where they started. The layout is remembered between
sessions, with one exception: the Run panel always starts closed, because a
new session has nothing to report yet. A run opens it, and it comes back
wherever you left it.

**File > Open Recent** lists the last ten plans opened, most recent first,
labelled by their directory since plans written by `extract` are all called
`comic-plan.yaml`. Choosing one that has since been moved or deleted says
so and takes it off the list. **Clear Menu**, at the bottom, empties it.

Three things are remembered between sessions, then, and nothing else: the
layout, the preferences below, and that list. All three live in the
application's own settings rather than in your plans, and each can be
cleared without disturbing the others.

**Preferences…** (`Cmd+,` on macOS, `Ctrl+,` elsewhere — in the application
menu on macOS, under Edit elsewhere) sets what a
new run starts from: the language pair, OCR languages and recogniser a new
plan is written with, a font to record in its header, and the output
directory, erase strategy and format the render dialog opens holding. The
Extract and Render dialogs are prefilled from these, and every one of them is
still editable there — a preference is a starting point, never a decision.

**A preference never changes a plan you already have.** That is the whole
rule, and the dialog says so at the top. Every field either seeds a *new*
plan's header or picks a run-wide setting that is not part of a plan at all;
none of them is written into a plan that exists. A default font that quietly
won over a plan's header would mean the same plan renders differently on two
machines, and re-runnability — edit a translation, run it again, and only
that text changes — is the property the two passes exist to have. Edit >
Plan Header changes *this* plan; Preferences decides what a *new* one starts
from.

Fields write through as you edit them, so there is nothing to apply and
nothing to cancel. A preferred output directory gets no more trust than a
path typed by hand: if it is inside the source tree the render dialog refuses
it, exactly as it would either way. Leaving a field empty means the behaviour
you would get without it — no default output directory, the source language
for OCR, the source image's own format, whatever font `extract` finds for
itself. The window also remembers which directory you last opened a plan or
read pages from, and starts the file dialogs there.

**Help > Comic Translator Help** (`Cmd+?` on macOS, `F1` elsewhere) opens a
short guide to this window: what the outline colours mean, what each flag
means, what the preview does and does not tell you, and how the two passes
fit together. It is a window rather than a dialog, so it stays open beside
the page while you work. This README covers the command line; that guide
covers the window.

**The window speaks your language if it has been translated into it**, and
English otherwise. Swedish ships. Three places say which, and they are asked
in this order:

- `COMICTRANS_LANGUAGE`, which forces one whatever anything else says
  (`COMICTRANS_LANGUAGE=sv comictrans review`) — how to look at a
  translation on a machine that is not set to it.
- **Preferences > this window > language**, which is the one to use: it is
  remembered, and it takes effect the next time the application starts — the
  window says so when you choose one, and again under the field.
- The machine's own language settings — a Mac set to Swedish throughout
  gets a Swedish window.

**macOS's per-application language does not work yet.** System Settings >
General > Language & Region says the application supports no additional
languages, for a bundle that declares them every way Apple documents. It is
recorded, with everything tried and measured, as entry 5 in `known-bugs.md`.
Use Preferences instead; nothing about the translation itself is affected.

A language named in the first two and not translated falls back to English
rather than to the next place down: naming one is an answer, and answering a
different question would be worse than answering in English.

The guide the Help menu opens is picked by the language the window actually
got, with English behind it, so a window in a language the guide has not been
written in yet still has help.

This is the window only. The command line is not translated, neither is a
plan file's content — `source_text` and `translation` are the comic, not the
interface — and neither is what the pipeline says when something goes wrong,
since those sentences are the same ones the command line prints.

**When something goes wrong**, **Help > Open Log Folder** shows you where it
was written down. Two files live there —
`~/Library/Logs/comictrans/` on macOS, `~/.local/state/comictrans/`
elsewhere, or wherever `COMICTRANS_LOG_DIR` points:

- `review.log` is everything a running window has to say: every page skipped,
  every region that would not fit, and the full traceback of anything that
  went wrong without stopping the window. It keeps more detail than the
  terminal shows, and rotates at a megabyte.
- `review-crash.log` is for the case where there is no window left to say
  anything. A segfault, or the abort Qt raises when it gives up, kills the
  process outright — no dialog, no message, and no stderr at all when it was
  not started from a terminal. That file gets a line every time `review`
  starts and a Python traceback naming the exact line if the process dies, on
  every thread.

Both are written to your own disk and sent nowhere. This tool makes no
network calls, and a stack trace is not an exception to that. Neither file
ever contains the comic: `source_text`, `translation` and `notes` are kept
out of a region's own repr, so no log line and no traceback can carry them by
accident.

**An error that does not stop the window still says so.** An exception in a
menu action or a mouse handler used to be printed to a terminal nobody had
open, leaving a button that appeared to do nothing; now the status bar names
it and points at the log. Failures you can act on — an unwritable directory,
a font that will not resolve — say what happened instead; the traceback goes
to the file, where a bug report can pick it up.

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
Closing the window, reverting, or opening a different plan while there are
unsaved changes asks first, and offers to save rather than only to discard.
Like every other pass, `review` never writes to a source image — only ever
to the plan file you explicitly save to.

## validate

```
comictrans validate pages/comic-plan.yaml
```

Answers "would `apply` get through this?" without rendering anything. A
chapter takes minutes to render and can fail in the first second on a font
that will not resolve, so this checks what is checkable in the time it takes
to hash the images:

- the plan parses and every field is one the reader accepts
- every page named is still there and still hashes as it did at `extract`
- every polygon fits on the page it is drawn on
- every font the plan names resolves on this machine, bold face included

It prints **every** problem it finds rather than stopping at the first, which
is the whole difference between this and opening the plan to render it: a
chapter with three missing pages and two unresolvable fonts says so once
instead of over five runs. It renders nothing and writes nothing.

```
pages/comic-plan.yaml
  images:   12
  regions:  87
  fonts:    Comic Sans MS, Chalkboard SE

2 problem(s):
  page-004.png has changed since extract (expected 3ac70d0f1b2c…, found 91ee4c7a0d51…). Re-run extract, or restore the original image.
  page-007-002: polygon reaches past page-007.png (1650x2550): (1650, 402)
```

What it accepts is what `apply` accepts, because it calls the same functions
rather than agreeing with them: the schema is the plan reader's, the page
check is the one `apply` runs before it renders, and the fonts go through
`fonts.resolve` with the precedence `apply` uses. It takes no `--font`, so
what it checks is what the plan file says.

What it does not check is anything that needs pixels drawn: whether a
translation fits its balloon, whether a region is still holding the source
text, whether two polygons overlap. Those are answers `apply` and `review`
give, and they need the render this exists to run before.

It exits 1 if anything is wrong — including a file that is not a plan at all,
which it reports rather than raising — so a script can stop on it.

## The plan file

YAML, UTF-8, stable key order, hand-editable. One entry per detected region.
Comments you add are preserved.

```yaml
version: 3
generator: comictrans 1.0.0
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
  manual` is a polygon shaped or drawn by hand in `review`, which is neither
  of the other two: nothing traced it and no OCR box bounded it. A region
  drawn by hand carries `confidence: 1.0` beside it — not a measurement, but
  the absence of one: nothing read it, so there is no score to doubt.
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
- Per-region `font` and `font_size` override the header, and a per-region
  `erase` (`none`, `flat`, `polygon`, `inpaint`) overrides the `--erase` flag.
  All four are omitted unless set.

Loading validates: unknown keys, malformed or self-intersecting polygons, bad
colours, duplicate ids, a region naming an image the `images` list does not
have, and image-hash mismatches are all errors that name the offending line.

Older plans still load. Version 1 carried an `image_sha256` on every region
and had no `images` list; the list is derived from the regions it does have.
Version 2 is version 3 without the optional `erase` key. Either way the plan
is a current one from then on, and saving it writes the current shape.

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
| 1 | the run completed but something needs attention: for `extract`, a page failed or yielded no regions; for `apply`, a region had no translation or would not fit; for `validate`, anything at all was wrong with the plan |
| 2 | the run could not start: bad arguments, no OCR backend, no resolvable font |

`extract`, `apply` and `validate` all process every page and report at the
end. None of them aborts on the first bad region — and `validate` goes
furthest: a plan it cannot even parse is a problem it prints, not an error it
raises, so the exit code is 1 there too. `review` has no code 1: it is
interactive, not a batch run, so there is nothing to report at the end beyond
what is already on screen — it exits 0 when the window closes normally, 2 if
it could not open one at all (PySide6 missing, or the platform's own
windowing libraries).

## Out of scope

Sound effects in artwork, hand-lettered SFX, rotated or vertical text, PDF
*output* (milestone 6 — reading one works), ComicInfo.xml or any other
metadata inside a chapter file, and preserving italic emphasis from the
source.

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
  skip — the plan a window edits, and the render it previews, are decided
  without Qt. `test_gui_run.py` and `test_gui_translations.py` sit in
  between: they build no widget and need no display, but the words they
  check are translated ones, so they need PySide6.

`-rs` tells you which. On macOS with `uv sync --group dev --extra gui` you
should see none of these skip except the empty fixtures directory.

Fixture images go in `tests/fixtures/`; see the README there.

**The application icon is derived, not drawn twice.**
`src/comictrans/gui/resources/appicon/` holds a 1024 master, the 512 icon,
and the script that makes the second from the first — the same drawing with
its finest detail hidden and a heavier line, so it still reads at 16px. Edit
the master, then:

```
src/comictrans/gui/resources/appicon/recreate-icons.py
```

The suite runs that script's `--check` mode, so a master edited without
regenerating fails `pytest` rather than shipping a stale icon.

**The window's words are translated from catalogues, not from the source.**
`src/comictrans/gui/resources/translations/` holds one `.ts` per language,
the `.qm` each compiles to, and the script that does both. After adding or
rewording anything the window says:

```
src/comictrans/gui/resources/translations/recompile.py --extract   # into every .ts
src/comictrans/gui/resources/translations/recompile.py             # .ts -> .qm
```

Translate the new entries in the `.ts` — by hand or in Qt Linguist — and
recompile. The suite runs the script's `--check` mode, so a `.ts` edited
without recompiling fails `pytest` rather than shipping a window still
speaking the last translation, exactly as with the icon above.

English has a catalogue too, and it holds nothing but plurals: Qt counts for
you but cannot inflect the noun beside the number, so `%n page(s)` would
otherwise reach an English window as `1 page(s)`. It is extracted with
`-pluralonly` and everything left unfinished in it falls through to the
English in the source.

Both drawings go into the `.icns` the application bundle carries, at
different sizes: the simplified one in the 16- and 32-point slots, where the
master's fur lines and page edges are the mud it exists to avoid, and the
master from 128 points up, where that detail is the drawing. `tools/` holds
the build script and the PyInstaller spec; the slot rule and the Info.plist
keys are tested on any platform, because none of them fails loudly enough to
notice on a Mac.

## Licence

MIT. The full text is in [LICENSE](LICENSE), and `pyproject.toml` declares
the same, so the package metadata and the repository agree.

No font is bundled and none ever will be: comictrans resolves the fonts
already installed on your Mac, so nothing here redistributes one.
