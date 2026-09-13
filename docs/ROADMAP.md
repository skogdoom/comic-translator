# Roadmap

What is planned and not yet built, in the order it is worth building.

This is a different list from the two that already exist. `known-bugs.md`
records defects that are understood and deliberately left alone;
**Known weak points on real scans** in `ARCHITECTURE.md` records what
detection does badly on real pages. Both describe the tool as it stands.
This file describes what it is not yet.

Nothing here is a commitment, and nothing here is a work item an agent
should pick up on its own — the same rule `known-bugs.md` carries. Work a
milestone when the request names it.

The numbered sections are the plan. **Ideas, not milestones** at the end is
a step further back: things worth looking into that have not been decided
on, kept unnumbered so that nothing can refer to one as though it were
scheduled.

**A milestone includes its own documentation.** A milestone that changes
what the window does updates the in-application guide in the same change —
that is live now, since the guide has shipped — and runs
`resources/translations/recompile.py --extract`, then translates what it
added, which is live now too. Where that turns out to be a body of work
rather than a paragraph it becomes its own milestone — but it is never
simply left for later, because help describing the previous version is worse
than no help at all.

Sizes are relative effort, not estimates. When a milestone ships, delete
its section and its row from the table, and say so in the Status section of
`README.md`. Now that 1.0.0 is out, it also earns a line in `CHANGELOG.md`,
under the heading of the version being worked towards — `1.1.0 — unreleased`
while this tree is `1.1.0.dev0`. The changelog is a record of releases, and
milestones accumulate into one; a test holds that heading to the version, so
there is always somewhere to write the line at the time rather than however
many merges later.

**The numbers are names, not positions.** They were allocated in the order
the milestones were thought of, and the order worth building them in has
moved since. The table below is the plan; a number is only there so that
one section can refer to another without ambiguity.

## Order

| # | Milestone | Size |
|---|-----------|------|
| 13 | Why the review window crashed | S |
| 14 | Undo and redo inside a text field | S |
| 15 | The translation field takes focus with its region | S |
| 4.28 | Region context menu | S |
| 4.27 | Lock a region | M |
| 16 | Languages by name, not by code | M |
| 17 | A shape palette for drawing a region | M |
| 18 | Chapter metadata in the plan | M |
| 19 | ComicInfo.xml, read and written | M |
| 20 | EPUB output | L |
| 6 | PDF output | L |
| 21 | Plugins: the runtime, and one example | L |
| 22 | Plugins: configuring them | M |
| 23 | An empty plan, and pages added by hand | M |
| 24 | The guide, checked against the window | S |
| 10 | Code quality review | M |
| 7 | Security audit | M |

**1.0.0 is out.** Everything a first release needed has shipped, and the
macOS pass that could only happen on a built application has been done.
Everything in this table is what comes after a release.

The 4.x numbering says these follow milestone 4, the review GUI.

**Why this order.** A crash outranks everything, so 13 is first whatever else
is wanted.

Then the window irritations — 14, 15 and 4.28, each an S, with 4.27 behind
them because it is the same corner of the same files even though it is
larger. They are met every few minutes by the one person using this, they are
cheap, and doing them apart means reading the inspector and the canvas four
times over. 15 makes 14 matter more rather than less, which is why 14 is
first of the four.

18 is the one piece of sequencing worth insisting on. **19 and 20 both need
the plan to know what chapter it is**, and neither can be done without adding
header fields, which is a `PLAN_VERSION` bump and a reader that accepts the
old shape. Done separately that is two bumps and two migrations for one idea;
done once first, 19 and 20 are each a file format wrapped around a header
that already holds the answers. 6 sits with them because it is the third
member of the same family and the least urgent of the three.

The plugin pair can be started any time after 13, because the experimental
flag means an unfinished plugin runtime is invisible to anyone who has not
switched it on — the one milestone here that cannot destabilise what already
works. It is last in the table only because it is the largest.

24 is late on purpose. The guide goes stale against window changes, and
checking it before 14, 15, 4.27, 4.28, 16 and 17 land would mean checking it
twice. Each of those milestones still updates the guide for its own change,
as every milestone here does; 24 is the sweep for what that misses.

**Asked for, and half of it was already there.** Rendering a chapter as JPEG
works for a folder of pages and for a `.cbz`, from the command line and from
the render window alike — measured both ways, `001-page-001.jpg` and the rest,
real JPEG bytes. It earns a regression test over the four combinations rather
than a milestone.

`.cbr` is the other half, and asking that question found that no `.cbr` this
tool had ever been asked for was written — `rar` was handed the entry names as
the files to add. Fixed since, with a stub that reads what it is given so the
next one of those cannot pass.

Three things decide this order.

**Cheap and immediately felt comes first.** 4.1 to 4.3 were small and
visible the moment the window opened, which is why they went first, and
4.11, 4.12 and 4.15 were the same shape: felt on every page of every
review, and waiting on nothing, as was moving a region after them.

**Cross-cutting comes last.** Localisation touched every user-visible
string, so it went after the milestones that added strings; packaging
bundled whatever the application was by then. Both are why the rename went
first rather than being filed with the other small things: renaming after
either one would have meant doing that work a second time, and the interface
review was filed just ahead of them for exactly that reason — it decided
what the labels say, and deciding that after they have been translated is
the same mistake twice. That paid out as expected: the catalogues were
extracted once, from labels nobody had to argue about again. Help text
describes the UI, so it went after the UI stopped moving — after that review
and before the release froze it, which is where it landed.

**Foundations come before what stands on them.** 4.4 and zoom went early for
that reason, and region editing — the largest of the minor milestones, now
shipped — stood on both: undo built for five text fields would have needed
rewriting the moment a polygon could move, so it snapshots whole plans
instead, and dragging a polygon vertex accurately means being able to see
it.

**A release changed what "first" meant.** The milestones above the line were
there because a release needed them, not because they were cheap or ready.
That is why the region tools, the archive formats and the validate command
sat below a line they would otherwise have been well up: none of them was
what made this releasable, and each is easier to get right against a released
version. That line has now been crossed, and what is left is in the order it
is worth building rather than in the order a release forced.

The test suite was the exception that proved it, and it went first for
that reason: not a release blocker, but free, invisible to users, and paid
for by every milestone above the line. It came in at 78 seconds and leaves
at 56.

**Measurement comes before the thing it would justify.** The suite's
saving was found by timing it rather than by guessing which tests looked
slow, and then confirmed by counting the calls rather than by trusting the
clock. Preview on a thread paid that out: the guess in its own section was
"three copies of an eleven-megapixel page, about 120MB", and measuring found
116MB retained and a 540MB peak, most of it inside a single `erase` call —
which became milestone 12, and would not have been found by shipping the
thread and moving on. That milestone then did the same thing to itself: 540MB
was a number rather than a diagnosis, and walking one `erase` call line by
line turned it into "a colour distance computed for the whole page to decide
a mask a balloon wide", which is a one-paragraph fix worth 15x the time and
all of the memory. 4.26 owed the same debt and has now paid it: what a crop
does to recognition accuracy was a number somebody had to produce before that
design could be settled, and it turned out to settle it — a crop cut to the
outline agrees with a full-page reading 10 times out of 31, the same crops
with a margin 27, and masking everything outside the outline, which looked
like the obvious refinement, is worse than either.

One ordering was a judgement call rather than a dependency, and it paid out:
**rendering (4.14) went before extract (4.6)**. Both run a pipeline pass from
the window and both needed the same worker thread, progress and cancel.
Rendering was the simpler of the two — no OCR, so no question about what is
safe to call off the main thread — and the more valuable, because reviewing a
plan and then leaving for a terminal to render it was the obvious hole in the
window. The threading was built on the easy case, and extract reused it: by
the time it landed, the harness was a base class and one `work()` method.

**Two things were deliberately held back from 1.0.0.** Putting preview on a
thread would have replaced a working wait cursor with threading, in the one
code path that has produced a segfault in this project — risk taken on
immediately before a release, for a benefit 4.21 already largely delivered.
It has since shipped, and found milestone 12 on the way, which has now
shipped too. And 4.9 would have
shipped translation machinery for a single language while freezing every
string just as the milestones below started adding more, which under the
documentation rule above makes every one of them run a translation pass too.
It has since shipped as well — so that rule is live, and every milestone
below now ends with an extraction pass and whatever it added translated.
Neither was what made 1.0 releasable; both were simply next.

## 13 Why the review window crashed

A segmentation fault, caught by `gui/crash.py` and written to
`review-crash.log`:

```
--- comictrans 1.1.0.dev0 review started 2026-09-13T17:07:33Z ---
Fatal Python error: Segmentation fault

Current thread 0x0000000204c8a2c0 (most recent call first):
  <no Python frame>

Extension modules: PIL._imaging, PIL._imagingft, numpy._core._multiarray_umath,
numpy.linalg._umath_linalg, shiboken6.Shiboken, PySide6.QtCore, PySide6.QtGui,
PySide6.QtWidgets (total: 8)
```

The diagnostics did their job: this is exactly the file that module exists to
leave behind, and without it there would be nothing at all. What follows is
what the file does and does not support, measured rather than assumed,
because the wrong reading of it is the easier one to reach.

**The extension module list is not evidence about what had run.** The
tempting inference is that OpenCV and Vision are missing from it, so nothing
had been rendered or recognised, so the crash is in the startup path. That
inference is wrong. `faulthandler` lists a module only when the module object
itself came from an extension loader, and `cv2` is a Python package wrapping
its own `.so`, so it never appears. Measured here: a process that had imported
`cv2`, numpy and Pillow and then crashed listed four modules and `cv2` was not
among them. `gui/preferences` imports `..erase`, which imports `cv2`, so
OpenCV is loaded in **every** window this tool has ever opened. The list tells
us Qt was up and FreeType was in use, and nothing else.

**`<no Python frame>` is the evidence.** `faulthandler` prints the Python
stack of the crashing thread, and prints frames whenever there are any —
measured, a crash inside a `ctypes` call from Python shows the `ctypes` frame
and its caller. An empty stack on the main thread therefore means the main
thread was not running Python at all: either before the interpreter had got
going, or **after it had finished** — after `app.exec()` returned and the
process was tearing down. The second is far the more likely, because Qt and
FreeType were both already loaded.

**The leading candidate, and where to look first.** `MainWindow.closeEvent`
stops and waits for two of the three worker threads it can have running:

```
self._job          cancel(), wait()   ✓
self._preview_job  cancel(), wait()   ✓
self._read_job     —                  ✗
```

`_read_job` is the `RegionTextJob` added by milestone 4.26, and it is the one
with nothing to cancel — a recogniser is handed a crop and either comes back
or does not, which on Apple Vision is seconds. Close the window while one is
in flight and a running `QThread` is destroyed, which the comments beside the
other two say aborts the process — and, from the other end, the interpreter
finalises while a worker thread is still executing Python bytecode, which is
a segfault with no Python frame left on the main thread to print. Both halves
of that match this log.

It is a candidate and not a conclusion. Against it: a `QThread` destroyed
while running raises `qFatal`, which is `SIGABRT`, and this was `SIGSEGV`.
For it: the finalisation race is a segfault, it was introduced by the most
recent window change, and it is the only worker this window does not wait
for.

**What would settle it is the half this log cannot hold.** `faulthandler`
writes the *Python* stack; the crash was in native code, so the native stack
is the answer and macOS already wrote it down. It is in
`~/Library/Logs/DiagnosticReports/`, named for the application and the time,
ending `.ips`, and its crashing-thread backtrace names the frames — Qt,
Python, or something else entirely. **Ask for that file before changing
anything.** Worth knowing alongside it: whether the window was being quit or
closed at the time, and whether **Extract Text from Region** had been used in
that session.

Where it lands if the candidate holds: `closeEvent` waits for `_read_job` as
it waits for the other two, and a test closes a window with a read in flight
and asserts the thread is finished before the event is accepted — the shape
the other two already have. If it does not hold, the `.ips` says where to
look instead, and this section is rewritten rather than guessed at twice.

## 14 Undo and redo inside a text field

**Not a bug, which is why it needs a decision rather than a patch.** The
inspector switches the text widgets' own undo off on purpose —
`setUndoRedoEnabled(False)`, with the reason written beside it: every
keystroke is already a document edit, so a per-widget stack would be a second,
invisible history that disagrees with the visible one about what the last
change was. Undo everywhere in this window means one history over the whole
plan, and that is what makes a plugin rewriting every region one step, and a
drag one step.

What is actually wrong is the **granularity**, not the ownership. Typing is
coalesced into a run, so `Cmd+Z` in the middle of a translation throws away
everything typed since the field was entered. That is right for a drag and
much too coarse for prose.

Three ways out, and the middle one is the recommendation:

- **Turn the widget histories back on.** Cheap, and it reintroduces exactly
  the disagreement the comment describes: undo in the field and undo in the
  menu would be two different stacks over the same text, and a document-level
  undo would put back text the widget never saw.
- **Keep one history and break the run up.** A run ends at a word boundary,
  or after a pause, rather than only when focus leaves — so `Cmd+Z` while
  typing behaves the way typing expects, and it is still the plan's history
  doing it. `document.end_edit_run` is already the seam; this changes when it
  is called, not who owns what.
- **Leave it and say so in the guide.** Honest, and it is the answer this
  milestone exists to avoid.

Whichever is chosen, `Cmd+Shift+Z` has to mean redo in a field as well, and
the menu items must stay enabled to match — today they follow
`document.can_undo` only.

## 15 The translation field takes focus when a region is selected

Select a balloon, start typing. Small, wanted, and with one conflict that has
to be settled first rather than discovered.

**Arrow keys already mean something on the canvas.** They nudge the selected
region a pixel, `Shift` twenty, and they accelerate while held. Move focus to
the translation field on selection and the arrow keys become cursor movement,
so nudging is gone for anybody who selects a region the ordinary way. That is
a real loss, not a hypothetical: nudging is how a polygon gets lined up.

Ways to have both, in the order they are worth trying:

- **Focus follows a click, not a keyboard walk.** Clicking a region on the
  canvas means "I am about to write"; `Tab`, `Next Flagged Region` and the
  page list mean "I am moving about". Nudging survives where it is used.
- **Escape returns focus to the canvas**, which it already half does — it
  cancels a shape — so there is one key that always gets you back.
- **A preference**, which is the answer if the first two do not settle it,
  and the worst of the three: a window that behaves two ways is a window with
  two sets of instructions in its guide.

Also worth deciding here: whether the field is focused with its text selected
(so typing replaces a seeded source text, which is usually what is wanted) or
with the caret at the end (so typing appends). The seeded-translation
behaviour from 4.26 argues for selected.

## 16 Languages by name, not by code

`it` and `en` in the header dialog and in preferences should read `Italian`
and `English`, chosen from a list rather than typed.

**There are three vocabularies here and they are not the same.** This is the
whole of the work; the dropdown is the easy half.

| Field | What it holds today | Who reads it |
| --- | --- | --- |
| `source_language`, `target_language` | a short code — `it`, `en` | the plan, and pyphen for hyphenation |
| `ocr_languages` | comma-separated recogniser codes — `ita` for Tesseract | `get_recognizer` |
| the window's own `language` | a catalogue name — `sv` | `gui/translations` |

The third is already a named dropdown and is the model to copy —
`preferences_dialog.language_name` turns a code into the language's own name
and is tested. The first two are free text and cannot simply become the same
list: a Tesseract code is not an ISO 639-1 code, several scripts of one
language are separate Tesseract data files, and the recogniser list depends
on what is installed on the machine rather than on what the world contains.

Three things not to lose:

- **The plan still stores codes.** A name in the window, a code in the file;
  a plan whose header reads `Italian` is a plan another version cannot read.
- **An unknown code must stay usable.** A dropdown that only offers what it
  knows makes a language it has not heard of impossible to type, and a plan
  written by hand with a code this list lacks must still open and still show
  something. Editable combo, or a list with a free-text row.
- **OCR languages are plural and ordered.** The field takes several; a single
  dropdown cannot replace it. This half may be better as a checklist of what
  is actually installed, which is a different widget and arguably its own
  milestone if it grows.

## 17 A shape palette for drawing a region

A rectangle and an ellipse beside the existing polygon tool, chosen from a
palette rather than being the only way to draw.

**The safe half of an idea that has an unsafe half.** A rectangle is four
points and an ellipse is a polygon approximated to as many as it needs, so
both produce exactly what the polygon tool produces: one simple ring, a
`Geometry.MANUAL` region, no schema change and no `PLAN_VERSION` bump. The
work is a fourth and fifth `CanvasMode`, their lines on the hint bar, and the
palette itself.

**The brush stays an idea, deliberately.** It is in *Ideas* below with the
reason: a stroke can paint a doughnut or two separate blobs, and a plan's
region is one simple ring, so something has to decide what that *means*
before a cursor is drawn. Putting the palette in first is not a step towards
the brush being skipped — it is the palette the brush would join, built while
the question it raises is still open.

Worth settling while the palette is being designed: whether an ellipse is
stored as the polygon it is approximated to, which is what the schema allows
today and loses the fact that it was an ellipse, or whether a region grows a
shape field, which is a `PLAN_VERSION` bump for something nothing downstream
reads. The first, unless reshaping an ellipse as an ellipse turns out to be
wanted.

## 4.27 Lock a region

Mark a region finished, so that changing it means deliberately unlocking it
first.

**It costs a plan version.** The reader rejects unknown keys on purpose, so
that a typo is an error rather than a silent no-op — which means a plan
carrying `locked:` cannot be read by a build that predates it. That is what
`PLAN_VERSION` is for: 3 becomes 4. Worth spending that bump on every field
the roadmap wants at once rather than twice; nothing else pending needs
one, so today this is alone.

**In the plan rather than in settings**, for the reason everything else is:
the plan is the thing handed to someone else, and "these are final, leave
them" is exactly the sort of thing worth handing over. In settings it would
live on one machine and be lost on the next.

**It is not `skip`, and the two will be confused unless the labels are
careful.** `skip` means do not render this region; `locked` means do not
edit it, and a locked region still renders. Saying which is which without
the manual is this milestone's job, under the casing and wording rules the
interface review settled.

**What it has to reach**: every edit path. The inspector's fields,
reshaping, the arrow keys, merge, delete — and re-extraction, where locked
regions are the ones that should come through untouched, which is half the
reason to want it.

## 4.28 Region context menu

Right-click — and Control-click, on macOS — on a region: extract its text,
lock or unlock it, edit its shape, select it.

**After the commands it lists.** A context menu is a shortcut to things
that already exist; built before them it is a menu of two items.

**It has to respect one-mode-at-a-time.** The canvas holds a single
`CanvasMode` precisely because a click means different things in each, and
a menu offering a command that contradicts the mode in progress is the
wrong place to find that out.

`contextMenuEvent` on the canvas, choosing the region under the cursor the
same way a left-click chooses it, so the two cannot disagree about what was
clicked.

## 18 Chapter metadata in the plan

The enabling milestone for the two below it, and the reason they are not each
carrying their own copy of it.

**The plan header knows almost nothing about the comic.** It holds the two
languages, the font and the typesetting limits, what wrote it and when — and
that is all. Not the series, not the volume, not the number, not the year, not
the reading direction. Milestone 8 refused to write a `ComicInfo.xml` for
exactly this reason, and the refusal is written into `README.md` and
`ARCHITECTURE.md` as a decision rather than an omission: a file claiming
metadata the tool does not have is worse than no file.

19 and 20 both need that to stop being true. Doing it inside either one means
a `PLAN_VERSION` bump inside a format milestone, and doing it inside both
means two bumps and two migrations for one idea. So it happens once, here,
and 19 and 20 become file formats wrapped around a header that already has
the answers.

What it is:

- **New header fields**, optional and empty by default, so a plan that names
  none is exactly the plan written today. Series, title, volume, number,
  year, publisher, writer, and the one that is not bibliographic: **reading
  direction**, left-to-right or right-to-left, which a manga chapter needs
  and which 20 writes into the spine.
- **`PLAN_VERSION` 4, and a reader that still accepts 3.** The reader rejects
  unknown keys by design, so a version-3 plan opened by this version must
  gain the new fields empty rather than be refused, and a version-4 plan
  opened by an older build will be refused — which is correct and is what the
  version is for.
- **The header dialog grows a section**, and `extract` fills in nothing: none
  of this is measurable from the pages, so every field is typed or imported.

Not in scope here: reading any of it from anywhere, which is 19, and writing
it anywhere, which is 19 and 20. This milestone ends with fields nothing
fills in yet, the same bargain the experimental flag just made.

## 19 ComicInfo.xml, read and written

With 18 in, this is small and mostly mechanical — and it reverses a decision,
which is the part to do deliberately.

**Reading.** A `.cbz` regularly carries `ComicInfo.xml` at its root, and
`sources` already skips it by name as "not a page". Reading it at unpack time
gives series, number, volume, year, page count, language and reading
direction, which is most of what 18 just added — so `extract` over a chapter
file would fill the header in rather than leaving it to be typed. That is the
half with the most value per line of code in this list.

**Writing.** `pack` adds one entry to the archive, built from the header.
Only the fields that are set: an element per fact the plan actually holds, and
nothing invented. This is where the `README.md` and `ARCHITECTURE.md`
paragraphs that say no metadata is ever written have to be rewritten — the
reasoning in them was "the plan knows only the language pair", which 18 makes
untrue, so the honest change is to say what changed rather than to delete the
paragraph.

Worth knowing: the format is a de-facto standard with no schema anybody
maintains, several mutually contradictory field lists, and readers that
ignore what they do not recognise. Write a small, conservative subset; be
liberal about what is read.

## 20 EPUB output

A chapter as a fixed-layout EPUB 3. **Export only** — see *Ideas* for why
input is a different and larger problem.

An EPUB is a zip with a mimetype, a container pointer, a package document and
one XHTML file per page, so `pack` is the shape to follow and most of the
work is the package document rather than the archive.

Three things it needs that nothing here does yet:

- **Reading direction**, which is `page-progression-direction` on the spine
  and is the whole reason a manga chapter reads correctly in a reader. 18 puts
  it in the header; this is its first consumer.
- **Metadata that is required rather than optional.** A package document
  must carry a title, a language and a unique identifier. The first two come
  from 18; the identifier does not exist anywhere yet and has to be minted —
  a UUID generated at export, which is fine, but it means two exports of the
  same chapter are two different books unless it is recorded, which argues for
  recording it in the header alongside the rest.
- **Per-page XHTML and a viewport.** Fixed layout means each page declares
  its pixel size, so the page's own dimensions become part of the output
  rather than something only the renderer knew.

Sizes it L rather than M: none of it is hard, there is simply a lot of it, and
a malformed EPUB fails in a reader rather than at the point it was written —
so this is the milestone in this list most in need of a validator in its
tests rather than an eyeball.

## 6 PDF output

Write a chapter as one PDF rather than a directory of images.

**Low priority, and the shape is deliberately still open.** One file per
chapter or one per page; images only, or a selectable text layer built from
the translations; page size and DPI. These are real questions with very
different amounts of work behind them — the searchable version is a much
larger job than the image-only one — and they are worth settling when this
is picked up rather than guessing now.

What is already known: it extends `render_dialog`'s format choice and
`apply.output_path`, both of which assume one output file per source image
today. And it is the other milestone that makes the page order mean
something on disk rather than only in the window.

## 21 Plugins: the runtime, and one example

Promoted from *Ideas*, where the shape and the costs were already worked out;
what follows is what changed now that it is scheduled, and why it is two
milestones rather than one.

**The experimental flag changes the risk.** The Plugin menu appears only when
**Preferences ▸ this window ▸ experimental features** is on, and it is off in
a fresh installation. That is the difference between "this tool runs
third-party code" and "this tool can be asked to", and it is what lets this
be built in the open without the disclaimer and the network invariant having
to be settled first — though they still have to be settled before it is
recommended to anybody. `Preferences.experimental_on` already exists and
nothing reads it; this is what it was put there for.

**What the idea already established**, and still holds: plan in, plan out, so
plugins never touch images and "source images are never modified" survives
without enforcement; the reader rejects unknown keys, so a plugin cannot
invent plan fields; undo is whole-plan snapshots, so a plugin rewriting every
region is one step like any other edit.

**What it still costs.** "No network calls anywhere in the pipeline" is an
invariant in `CLAUDE.md` today and becomes "this tool makes none; a plugin you
installed might" — a different sentence that has to be written in `README.md`
as well as here. Discovery needs a directory to read plugins from, which is a
new read location. And the disclaimer, already about unreviewed code, earns a
paragraph rather than a footnote.

**The example.** Add a note to every region in the open plan, with the note's
text configurable. It is a good example precisely because it is not case
switching — the header already has `case`, and an example that duplicates a
built-in teaches the wrong thing. `Region.notes` exists, so it needs no schema
change, and running it over a chapter is one undo step.

This milestone ends with plugins that run and one that ships. Their
configuration is a file they read; the window for editing it is 22.

## 22 Plugins: configuring them

**Configure Plugins…** in the Plugin menu opens a list of what is installed;
selecting one shows its settings; a checkbox says whether it is active.
Inactive plugins stay in the list and leave the menu.

Split from 21 because it is the half with a design problem in it. **A generic
dialog cannot build a form for a configuration it knows nothing about.** Each
plugin has to declare its settings in a shape the window can render — a name,
a type, a default, a label — and that declaration is an interface this project
then has to keep, which is exactly the kind of commitment worth making
separately from "can a plugin run at all". Options run from "a handful of
typed fields" to "a schema", and the smallest thing that works for the example
is the right starting point.

Where the settings live: `QSettings` under a `plugins/` prefix, beside
`preferences/` and `window/`, keyed by plugin. Active-or-not is one more
setting of the same kind, which is why the checkbox belongs in this milestone
and not in 21.

Worth deciding here: whether a plugin that fails to load appears in the list
greyed with its error, which is the friendly answer and the one consistent
with how this window reports a failed run, or is silently absent.

## 23 An empty plan, and pages added by hand

Create a plan with no pages; add pages to one; remove pages from one.
Everything in the tool today starts from a folder that already holds the
pages, and a chapter assembled by hand has no way in.

**The one real question is where an added page lives.** A plan names its pages
relative to itself and hashes each one, and every pass resolves them that way.
So adding `~/Desktop/page-042.png` to a plan in `~/comics/ch-07/` means one of
three things, and the milestone is mostly this decision:

- **Copy it in beside the plan.** Simple, and it writes into the directory the
  pages live in — which is not a source image being modified, so the invariant
  holds, but it is a write somewhere the tool has so far only ever read.
- **Store a relative path that climbs out** — `../../Desktop/page-042.png`.
  No copy, and the plan stops being a thing you can hand to somebody with the
  folder it sits in.
- **Refuse anything outside the plan's own directory**, and say so. The most
  conservative, and it makes assembling a chapter from scattered files a job
  for the Finder first.

Removing a page has a smaller version of the same question: the page's regions
go with it, which is one undo step, and the image file itself is not touched.

Also here: an empty plan has to be a thing the reader accepts and the window
can show without a page selected, which is closer to true than it looks — a
test already writes a plan with no regions — and **New Plan** needs somewhere
to put a file that does not exist yet.

## 24 The guide, checked against the window

The in-application guide is written as each milestone lands, which is the
rule that keeps it from rotting; this is the sweep for what that misses.

Not a rewrite. Read `resources/help/en.html` against the window as it then
is: every menu item it names still exists and still does that, every shortcut
is the shortcut, nothing shipped since is missing, and nothing describes a
dialog that has changed shape. Then the same for the Swedish catalogue, which
can be current in its translations and stale in what it is translating.

It is last of the window milestones on purpose — see *Why this order* — and it
is worth having as a milestone at all because the alternative is that it is
nobody's job.

## 10 Code quality review

A reading of the whole codebase for the things tests do not catch:
duplication that has crept in, modules that have grown past what their
docstring claims, names that no longer match what they do, and comments
that describe an earlier version of the code.

**After the features, before the audit.** Reviewing while milestones are
still landing means reviewing the same code twice; leaving it until after 7
means the audit reads code nobody has tidied. Between them is the one place
it pays for itself.

Worth deciding in advance what it is allowed to change. A quality pass that
also fixes behaviour is two changes wearing one commit message, and this
repository has `known-bugs.md` precisely so that "while I was in there" is
not how a decision gets reversed.

## 7 Security audit

A pass over this code and over the dependency surface: Pillow, NumPy,
OpenCV, ruamel.yaml, pyphen, pytesseract, PySide6 and pyobjc-Vision, plus
`rarfile` and the external `unrar` and `rar` binaries that reading and
writing chapter files bring in.

**Two halves worth keeping apart.** Dependency CVEs are a tooling question
— `pip-audit` or equivalent, run on a schedule, reporting versions against
advisories. The code half is a reading, and it has one thing to check that
nothing else does: **no network calls anywhere in the pipeline** is an
invariant in `CLAUDE.md` and is enforced nowhere in the suite. An audit is
where that stops being a rule people remember and starts being something
that fails a check.

**Both halves of the chapter-file work are now in.** Reading an archive
means handing an untrusted file to an unpacker, and for CBR that unpacker is
an external binary; writing one invokes a second external binary with paths
someone else chose. Both are genuinely new attack surface — the first this
tool has had that is not a Python library — and both are in scope. What
shells out is `sources._unrar_tool` through `rarfile`, and `pack._pack_rar`,
which builds its own argument list with no shell and validates the
configured path only as far as "something executable is there".

Last on the list, and the only item here that is a recurring activity
rather than something that ships once and is deleted from this file.

## Ideas, not milestones

Things to look into, none of them decided. **Unnumbered on purpose**: a
number in this file is a name that other sections refer to and that the
table above orders, and none of these has earned one. Nothing here is
planned, nothing here is sized, and nothing here should be started without
the decision being made first.

What each note is for is the part that would otherwise be rediscovered: what
it would cost, and what it runs into. Several of these are not features on
top of the tool as it stands — they are changes to what it promises.

**What a page costs to decode.** Milestone 12 went looking for why
rendering an eleven-megapixel page peaked at 540MB and found it in `erase`,
which now works inside a window and costs nothing measurable. What that
leaves at the top is `load_page`: 77MB of traced allocations and about 230MB
of process peak for one 11 MP PNG, 0.29s, to end up holding 44MB — the page
and its alpha. Most of the difference is inside Pillow rather than here: an
RGBA decode, `convert("RGBA")` again to pull the alpha channel out, and
`flatten_to_rgb` after it. Whether that can be had for less without giving up
the metadata the apply pass needs is a measurement nobody has taken. It
matters most where pages are read in a loop, which is `apply` over a chapter
rather than a preview of one page.

**Rotate a region.** Cheaper than it looks, and it splits in two. A `Region`
already holds an arbitrary polygon, so a rotated outline is representable
today with no schema change and no `PLAN_VERSION` bump — a tilted rectangle
is just four points. What that does *not* buy is rotated text: the
typesetter fits each line to the widest horizontal run inside the polygon on
every row of its band, so text inside a tilted outline would still be laid
out level. So this is worth having on its own, for a balloon that sits at an
angle, and it is not a step towards the next one.

**Rotated and vertical text.** The other half, and a different order of
work. It needs a field on a region — an angle, or a writing mode — which the
reader rejects until `PLAN_VERSION` goes up, and it needs the typesetter to
stop thinking in horizontal bands, which is the shape of `typeset` rather
than a parameter to it. Vertical CJK is more again: line breaking and
hyphenation are not the same algorithm turned sideways. Worth knowing which
of the two is actually wanted before either is designed.

**A plugin system (experimental).** Scheduled — milestones 21 and 22. The
costs worked out here are kept there rather than in two places.

**Extracting and rendering emphasis, italic and bold.** This one is not a
feature, it is a change to an invariant, and should be decided as one.
`CLAUDE.md` says emphasis renders as bold, never italic, that a bold face is
never synthesised, and that the oblique face is never used. Bold already
works: `**bold**` in a translation is parsed in `typeset`. So what is being
asked for is the italic half, and the invariant exists to stop it being
faked. Doing it honestly means requiring a real italic face, which means
`fonts` resolving a third file per family and a plan that names a family
without one failing the same way a missing bold does. Doing it dishonestly
means synthesising a slant, which is the thing the rule forbids. The
extraction half is separate again and probably harder: telling italic from
upright in scanned comic lettering is not something either recogniser
reports.

**Sound effects, identified and lettered over the artwork.** Half of this
exists. `erase: none` is already documented as being for exactly this — it
paints nothing and letters straight onto the page — so the render side needs
no new machinery, and a region can already carry it per region.

What is missing is the other two thirds. Detection would have to tell a
sound effect from a balloon, which is a new class of thing for `detect` to
recognise rather than a threshold to move, and the flag on `extract` is the
easy part of that. And the colour: a sound effect has no balloon to sample,
so "a visible colour" means choosing one for contrast against whatever is
underneath, which is a decision nothing in the tool makes yet —
`sample_colors` reads the colours that are there. Note which pass may do
that: `review` may measure the page, `apply` may not, so the choice has to
be made at extract time and recorded, like every other colour in a plan.

**WebP, in and out.** Reading one is nearly free and writing one is not.
Pillow decodes WebP already — `features.check("webp")` is true in this
project's own environment — so an input would be a suffix added to
`IMAGE_SUFFIXES` and a fixture to prove it, with the rest of the pipeline
none the wiser: it works on the array `load_page` hands back.

Output is where it runs into the promises `apply` makes. Two of them:

- **The lossy trap `apply` already avoids.** A JPEG source is written out as
  PNG rather than re-encoded, because a second generation of artefacts on
  artwork nobody asked to change is damage. WebP is both — lossless WebP
  round-trips a page bit for bit (measured), lossy does not — so it is not
  one format to the output rules but two, and the default would have to be
  lossless for the same reason `output_format_for` turns JPEG into PNG.
- **DPI is dropped.** Measured, saving the same page through Pillow at 300
  DPI: PNG keeps it, TIFF keeps it, WebP hands back `None`. `save_page`
  carries DPI and the ICC profile across deliberately — the profile does
  survive WebP — so writing one would either lose a fact about the page or
  need it written into EXIF, which is a thing to decide rather than a
  parameter to pass.

Alpha is fine: WebP stores it, so it would join `ALPHA_FORMATS` rather than
warn like JPEG does. The format is also capped at 16383 pixels a side, which
no comic page reaches and a double-page scan at 1200 DPI would.

**Drawing a region with a brush.** Still an idea, and deliberately not part
of milestone 17, which builds the palette it would join: the unresolved
question below is about the plan format, not about a cursor. The conversion
this needs already exists and is not the hard part. `detect` turns a raster mask into a polygon today:
largest contour, `approxPolyDP` at three epsilons in turn, rejecting anything
that comes out non-simple or with fewer than three points. A brush stroke is
a raster mask. So a brush that paints into a scratch mask and converts on
release would reuse that, produce a `Geometry.MANUAL` region like the polygon
tool does, need no schema change and no `PLAN_VERSION` bump.

What it runs into is what a polygon cannot hold. A plan's region is *one
simple ring* — `MIN_POLYGON_POINTS` and `polygon_is_simple`, enforced by the
reader and again by the GUI so that the window cannot write a file it could
not reopen. A brush makes holes, and two strokes apart make two blobs;
largest-contour-wins throws both away silently, which is the same thing
detection does and would be a surprise coming from a tool that draws what
you drew. Deciding what a stroke that paints a doughnut *means* is the
design work, and it is a decision about the plan format before it is one
about a cursor.

The rest is window work of a kind that already has a shape: a fourth
`CanvasMode` beside select, reshape and draw, its own line on the hint bar,
and whole-plan undo swallowing a stroke the way it swallows a drag, through
the same run-key coalescing the arrow keys use.

**PSD, in and out.** Asked for as rendered regions on one layer and
translated text on another, which is the right shape — and the writing half
runs into the library situation rather than into anything about this tool.
`psd-tools` is a reader; its write support does not cover building a layered
document from scratch. `pytoshop` does write, and is dormant. So this starts
with a decision nobody has made: which dependency, or a PSD writer of our
own, for a format whose layer records are not a weekend.

The second question is what "translated text in another layer" means. A PSD
text layer is a real, editable text object with its own font and transform,
and writing one is a different and much larger job than writing pixels; the
alternative is a rasterised layer that looks identical and is not editable in
Photoshop, which may be exactly what is wanted or may be the whole point of
asking. Worth answering before either is costed, because they are not the
same milestone.

What is cheap either way, and worth noticing: the two layers already exist
inside `render`. The erase pass and the typeset pass are separate steps over
the same page, so producing them as separate images needs no new rendering —
only somewhere to put them. That makes a two-layer TIFF, or a folder of
erase/text pairs, a much smaller thing to offer than PSD, and possibly the
thing that was actually wanted.

**EPUB input.** Export is milestone 20; reading one back is the harder
direction and does not follow from it. An EPUB's pages are XHTML, not images:
a fixed-layout comic usually holds one image per page and is close to a
zip full of pages, but nothing requires that, and the general case is a
document with text, styling and images in arbitrary arrangement. Deciding
which EPUBs are "a chapter" and which are refused is the work, and it is the
same shape as the PDF page-image rule in `sources` — which took a `_page_doubt`
heuristic and a `LOOK AT` report to get right. Worth doing after 20, if at
all, with that experience in hand.

**Running on an iPad.** Not a port, on the evidence. What the tool is built
from, as the wheel index has it today:

| | iOS wheel |
| --- | --- |
| PySide6 — the window | none; macOS, Linux and Windows only |
| numpy | none |
| opencv-python-headless | none |
| pyobjc, which is how Vision is reached | none |
| Pillow | yes — `ios_13_0_arm64_iphoneos` and the simulators |
| ruamel.yaml, pyphen | pure Python |

So the window does not run there and neither does the pipeline. What does
exist is the thing this tool was built around: **the plan file is the
interface**, and reviewing one needs none of those four. Reading and writing
YAML, drawing polygons over a page, typing a translation and walking the
flagged regions is a document editor over a text file — an iPad application
in its own right, sharing the format rather than the code, with `extract` and
`apply` staying on the Mac where the recognisers and the renderer are. That
is a second front end, not a second copy of the tool, and the two-pass design
is what makes it possible to say so.

Worth knowing before starting it: this window's gestures are keyboard-shaped.
Move is `Cmd`-drag, a corner is added by double-clicking an edge, Escape
cancels a shape, and three docks share the width. None of that transfers to a
finger. An iPad front end is a different interaction design onto the same
file, which is the work, and the file is what makes the work worth anything.
