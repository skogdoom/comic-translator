# Roadmap

What is planned and not yet built, in the order it is worth building.

This is a different list from the three that already exist. `known-bugs.md`
records defects that are understood and deliberately left alone;
**Known weak points on real scans** in `ARCHITECTURE.md` records what
detection does badly on real pages; `SECURITY.md` records what the audit
read and what it decided. All three describe the tool as it stands. This
file describes what it is not yet.

`SECURITY.md` also holds the one job this file used to carry that does not
finish: checking the dependencies against published advisories, which goes
stale on somebody else's schedule rather than on this project's. It is a
thing to re-run, not a thing to build, so it lives where it is run from.

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
`README.md`. It also earns a line in `CHANGELOG.md`, under the heading of the
version being worked towards: a tree at `1.2.0.dev0` writes under
`1.2.0 — unreleased`. The changelog is a record of releases, and milestones
accumulate into one; a test holds that heading to the version, so there is
always somewhere to write the line at the time rather than however many
merges later.

**Between releases the tree carries a `.devN` version**, which is what makes
that heading exist. A tree sitting at a plain version is a release and has no
heading open; what opens the next one is the bump to `.devN`, which adds the
heading in the same change — on its own once the release is tagged, or as the
first thing the next milestone does.

**The numbers are names, not positions.** They were allocated in the order
the milestones were thought of, and the order worth building them in has
moved since. The table below is the plan; a number is only there so that
one section can refer to another without ambiguity.

## Order

| # | Milestone | Size |
|---|-----------|------|
| 26 | Rotate a region | M |
| 30 | Drawing a region with a brush | M |
| 18 | Chapter metadata in the plan | M |
| 27 | Rotated text | M |
| 28 | Sound effects, lettered over the artwork | M |
| 19 | ComicInfo.xml, read and written | M |
| 20 | EPUB output | L |
| 6 | PDF output | L |
| 23 | An empty plan, and pages added by hand | M |
| 24 | The guide, checked against the window | S |
| 33 | Plugins: what `run` is given | M |
| 32 | Plugins with their own dependencies | M |

**1.1.0 is out**, a week after 1.0.0: chapters in and out as one file, the
window in Swedish, a page rendering in a fraction of the time, and plugins as
an experiment. Everything in this table is what comes after it.

The 4.x numbering says these follow milestone 4, the review GUI.

**Why this order.** The window irritations first, and they are done: 4.28
shipped the region context menu without the lock/unlock command its own
section asked for, because 4.27 had not landed and it was built without it
rather than wait; 4.27 has now landed and put that third command in the menu
where it belongs. What is left below is no longer sorted by how often it is
met. 16 and 34 have shipped as well, which leaves no field in the window
that asks for a code without offering names beside it, and so has 31, the
reader window, which waited on nothing above it.

**The schema change has shipped, and four milestones are cheaper for it.**
Milestone 29 took the plan format to version 4 and added every field this file
still wanted — `locked`, an angle for 27, a stroke colour for 28, and the
chapter header fields for 18 — all optional, all inert, and filled in by
nothing. `locked` has since been taken up by 4.27, which is the shape the
rest are waiting for. It went first because the reader rejects unknown keys
by design, so a field added on its own is its own bump, its own migration and
its own window in
which two builds disagree about what a plan may contain; four of them
separately would have been four of each. Each of those milestones now arrives
to find its field already there and spends itself on behaviour, which is also
what makes them independent of one another: none waits on another's migration.
18 keeps the job it was sequenced for — 19 and 20 still need the header to stop
being *empty*, and that is 18 rather than either format milestone. 6 sits with
them as the third member of the same family and the least urgent of the three.

**A field being there is not an instruction to fill it in.** The temptation 29
existed to resist outlives it: four fields are now sitting in the format with
nothing reading them, and each belongs to exactly one milestone below. Borrow
`known-bugs.md`'s instinct here — meeting one while doing something else is
not a reason to start on it.

**What 29 did not settle, and should not be read as settling.** Two open
questions in this file were answered partly on the grounds that a field costs
a version bump: whether an ellipse remembers it is an ellipse, which 17
answered no when it shipped, and whether a rotated region remembers its angle
(26). The bump is now paid for and the
mechanism is proven, so both rest on the half that is still true — nothing
downstream reads either, and a field nothing reads is still a field to
maintain. Neither is reopened; they are standing on one leg instead of two.

26 and 28 are placed by what they need rather than by size. 26 waited on the
shapes, because rotation is a handle on a shape, and 17 has shipped them. 28
would have been the cheapest of the three and is not, because the outline it
needs is a field: had it been implied by `erase: none` it would have needed no
bump at all, and that was considered and refused.

30 waited on the same thing: the brush is a tool in the shape palette, which
17 shipped with room in its grid for more. Which of 26 and 30 goes first is a
judgement and not a dependency — neither needs the other, and 26 was sequenced
there before the brush had a number. The question that kept the brush out of
17 has been answered, so nothing holds it where it is except that somebody has
to be second.

**33 and 32 are last by judgement rather than by dependency**, which is worth
saying plainly because nothing forces it. They touch `plugins.py`, `run_job.py`
and one call site in the window, and not one milestone above them goes near
those. What puts them here is that plugins are experimental and off by default,
so they are not felt every few minutes the way the window irritations were —
the same test those passed and these do not. They are adjacent because both
end in a pass over the same documentation, and 33 before 32 because 33 settles
what a
plugin *is* handed before 32 changes what it may bring with it.

24 is late on purpose. The guide goes stale against window changes, and
checking it before 26 and 30 land would mean checking it twice. Each of
those milestones still updates the guide for its own change, as every
milestone here does; 24 is the sweep for what that misses.

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

## 26 Rotate a region

Tilt a region so its outline follows a balloon that sits at an angle.

**No schema change, and no memory of the angle.** A `Region` already holds an
arbitrary polygon, so a tilted rectangle is four points and nothing more. The
rotation is a gesture rather than a stored property: once applied, the region
is the points it now has, exactly as if they had been dragged there. That is
what keeps this clear of `PLAN_VERSION`, and it is a trade made deliberately —
a region cannot afterwards be un-rotated or re-rotated cleanly, because
nothing records the angle it was put at.

That is the same trade 17 made for an ellipse, settled the same way: stored
as the polygon it comes out as, losing the fact that it was ever an ellipse,
because a shape field is a `PLAN_VERSION` bump for something nothing
downstream reads. Consistency here is worth more than either answer.

**Driven from the shape palette** that 17 built: rotation is a handle on a
shape, and the palette is where the shapes are. It has room in its grid for a
tool of this kind beside the polygon, the rectangle and the ellipse.

**It does not letter rotated text**, and is worth having without it. The
typesetter fits each line to the widest horizontal run inside the polygon on
every row of its band, so text inside a tilted outline is still laid out
level. Measured on a 320x180 box: the same sentence fits at 87px over three
lines upright, 68px over four at 20 degrees, and 66px over six at 45. What
this milestone buys on its own is an erase and an outline that follow the
balloon instead of a bounding box. 27 is what makes the text follow too.

## 30 Drawing a region with a brush

Paint a region instead of clicking its corners, with a brush sized from the
shape palette.

**Decided: the region is the outline of whatever was painted.** A plan's region
is *one simple ring* — `MIN_POLYGON_POINTS` and `polygon_is_simple`, enforced
by the reader and again by the window so it cannot write a file it could not
reopen — and a stroke can paint a doughnut, so something has to say what
becomes of the hole. It is filled. The ring is the outer boundary of the
painted area and nothing else is recorded.

**Nothing is discarded, which is the point of it.** Keeping the largest contour
was considered first and refused: it is what `detect` does to a mask, and a
tool that draws what you drew and then throws part of it away — silently, or
with a line in the status bar — is a surprise from a tool whose whole promise
is that the window cannot write a plan it could not reopen. Taking the outline
has no such half: what you painted is what you get, minus a hole you could not
have stored anyway.

**It is the retrieval mode `detect` already uses.** `cv2.findContours` with
`RETR_EXTERNAL` returns outermost contours and ignores holes, which is exactly
this rule, and it is already the call in `detect/__init__.py` and
`detect/colorseg.py`. So the conversion is not new work: a brush that paints
into a scratch mask and converts on release reuses `detect`'s own path —
`RETR_EXTERNAL`, then `approxPolyDP` at three epsilons in turn, rejecting
anything non-simple or under three points — and produces a `Geometry.MANUAL`
region exactly as the polygon tool does. **No schema change and no
`PLAN_VERSION` bump**, which is why nothing was reserved for it when the
format last moved.

**What is left open is a second stroke, not a hole.** `RETR_EXTERNAL` gives one
contour per painted blob, so two strokes apart still give two rings and one of
them has to go — the same problem, moved. It does not arise at all if a stroke
commits on release and one stroke is one region, which is the simplest thing to
build and worth trying first. If strokes should instead accumulate until an
explicit commit, the answer is already in the tree and need not be invented:
`model.convex_hull` is what merging two regions produces, on the reasoning that
"the hull of both outlines is the smallest convex shape that covers what either
of them covered". That is the same question a brush would be asking.

**Brush size, not stroke width.** The control is the brush's own size, and the
label has to say so, because *stroke* is already spoken for twice: `config.py`
uses stroke width for the morphological closing over the original lettering's
pen strokes, and 28 adds `stroke_color` for the outline around drawn text. A
third meaning, on a control a person sets, is one too many.

**The rest is window work of a kind that already has a shape**: another mode
and another cell in the shape palette, beside the polygon, the rectangle and
the ellipse, its own line on the hint bar, and whole-plan undo swallowing a stroke the way it swallows a
drag, through the same run-key coalescing the arrow keys use.

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

19 and 20 both need that to stop being true, and neither should be the
milestone that changes the plan format to fix it. Version 4 added the fields;
this one is where they stop being empty. After it, 19 and 20 are each a file
format wrapped around a header that already holds the answers.

What it is:

- **The header fields put to use.** The format already carries them — series,
  title, volume, number, year, publisher, writer, and the one that is not
  bibliographic: **reading direction**, left-to-right or right-to-left, which
  a manga chapter needs and which 20 writes into the spine. Empty is still a
  valid answer for every one of them, so a plan that names none stays exactly
  the plan written today. The reader window has a direction toggle of its own
  and opens chapters rather than plans, so reading this field is a connection
  it could make later, not one it waits on.
- **The header dialog grows a section**, and `extract` fills in nothing: none
  of this is measurable from the pages, so every field is typed or imported.

Not in scope here: reading any of it from anywhere, which is 19, and writing
it anywhere, which is 19 and 20.

## 27 Rotated text

Letter a tilted region at its own angle rather than level inside it.

**Latin only.** Vertical CJK was considered and is not this: breaking lines
down a column is not the horizontal algorithm turned sideways, and the two
share nothing but a schema field.

**The typesetter does not change.** Measured, not assumed. The band algorithm
does not need to stop thinking in horizontal bands — it needs to be handed a
polygon that is already upright. Rotating the polygon into the region's own
frame and calling `layout_text` unmodified recovers the full fit at every
angle tried: 87px over three lines at 0, 20, 30 and 45 degrees alike, against
87, 68, 70 and 66 for the same box laid out level in page coordinates.

So the work is three things and none of them is in `typeset`:

- **An angle on the region**, which the plan format already carries — so
  nothing here touches it.
- **A coordinate transform** either side of the fit: rotate the polygon in,
  rotate the drawn result back.
- **`draw_layout` drawing into a region-local layer** and rotating it once
  before compositing. It already draws each line into a transparent layer and
  pastes that, so this changes where the layer lands rather than how it is
  made.

**Not yet measured**: what a single rotation resample does to small text. The
fit is proven; the rendering of it is not.

## 28 Sound effects, lettered over the artwork

Letter a sound effect straight onto the art, in something that can be read on
top of it.

**Detection is not part of this.** Telling a sound effect from a balloon would
be a new class of thing for `detect` to recognise, and it was decided against:
a sound effect is a region somebody draws. That leaves most of this milestone
as an affordance over machinery that is already here — `erase: none` is
documented for exactly this case, the inspector already sets erase per region
and already picks a text colour, and the canvas already draws a region by
hand. What is missing is one action that does all three at once, and the
outline below.

**The outline is the only genuinely new rendering, and it is not optional.** A
flat colour does not work on a comic page, measured over the thirteen fixture
pages as the share of page area where the text would be hard to read:

| fill | outline | worst page | mean |
| --- | --- | --- | --- |
| magenta `255,0,255` | none | 67.9% | 49.6% |
| hot pink `255,20,147` | none | 43.3% | 25.2% |
| hot pink `255,20,147` | black | 11.2% | 1.3% |
| white | black | 0.0% | 0.0% |

A mid-luminance colour has poor contrast against both the paper and the ink,
which is why magenta — the obvious "nobody draws in this" choice — is the
worst of them. **Hot pink with a black outline** is what to build. White on
black is perfect and was not chosen: it reads as ordinary lettering, and a
sound effect that announces itself as placed is the point.

**The outline is not implied by `erase: none`.** Decided, so that a plan
already using `erase: none` for a caption over artwork does not quietly change
what it renders. It is a field, and the format already carries it.

**One field, not two.** `stroke_color: Color | None`, `None` meaning no
outline. The width is a ratio in `TypesetConfig` derived from the fitted font
size, which is the idiom every other size there already follows —
`padding_ratio`, `max_size_ratio` — and which keeps the outline proportional
between two regions whose sizes were searched separately. An absolute width in
pixels would look different on every region of one page. `draw_layout` already
calls `ImageDraw.text`, which takes `stroke_width` and `stroke_fill`.

**The colour is chosen at extract time and recorded**, like every other colour
in a plan: `review` may measure the page, `apply` may not.

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

## 33 Plugins: what `run` is given

Three things a plugin cannot see today, added in one interface change.

**One change rather than three**, which is the argument version 4 of the plan
format already settled, applied to a different format. Each of these alters
what a plugin is handed, and done separately each is its own interface
version, its own window in which a plugin and a build disagree about the
signature, and its own pass over the same documentation.

What it adds:

- **Progress the plugin reports itself**, through a helper it calls as it goes.
- **A dialog the plugin describes and the window builds**, shown before the
  run starts.
- **The id of the selected region**, or nothing when none is selected.

The dialog is most of the work; the other two are a subclass and a parameter.

**The selected region is the cheap one.** `main_window._current_region` is
already a `str | None` sitting at the call site, and the canvas selects one
region at a time — `_selected_id` is singular — so that is the whole of the
type. It is here because it changes the signature, not because it is work.

**Progress means a plugin stops running on the UI thread.** `_on_run_plugin`
calls `run_plugin` synchronously today, which is fine for the one example that
ships and is the reason no plugin can report anything: there is no window left
to draw in. The harness for this exists and already has four subclasses —
`RunJob` in `gui/run_job.py`, one `work()` method, `progressed`, `completed`
and `failed` delivered in the window's thread. A `PluginJob` is the fifth, and
the helper a plugin calls is the `ProgressCallback` those jobs already take.

**Dialogs are pre-run and declared rather than built**, and those two decisions
together are what keep this an M.

*Pre-run*, because a plugin on a worker thread cannot build a widget — Qt
forbids it — so a dialog raised in the middle of a run would mean the worker
blocking on the main thread and waiting for an answer, which nothing in this
project does anywhere. Asked before the thread starts, it is the shape that
already exists: the window asks, the plugin is handed values, and the run is
only the run.

*Declared*, because **a plugin never imports PySide6**. It describes what it
wants and the window builds it. `plugins.py` deliberately imports no Qt, the
`gui` extra deliberately stays optional, and a widget built inside a plugin
would put a Qt ABI in something that has to keep loading across builds. It also
keeps what a dialog looks like the window's business rather than a plugin
author's.

**The vocabulary is small on purpose**, and it is all of what a plugin may ask
for:

| | |
| --- | --- |
| output | a label |
| input | text field, dropdown, radio buttons, checkboxes |
| buttons | OK — OK and Cancel — Yes and No |

**Cancel means the plugin does not run at all**, which is the reason the dialog
comes before the thread rather than inside it: there is nothing started to
stop. Yes and No both run it, and which was pressed is one of the answers.

**It is not `SETTINGS`, and the two will be confused unless the milestone keeps
them apart.** `SETTINGS` is configuration: edited in Configure Plugins, stored
per plugin, resolved and handed to every run whether or not anybody looked at
it. A dialog is asked on each run and its answers are not stored. That is the
same care 4.27 took between `skip` and `locked` — two neighbouring things
that are not the same thing, told apart in the label itself. Answers arrive as
strings on the same "empty means unset" terms `SettingField` already uses, so
there is one
convention here rather than two.

**One thing left open: whether the dialog is a constant or a call.** A
module-level `DIALOG = (...)` matches how `SETTINGS` is declared and is
simpler. A function the window calls first — handed the plan and the selection,
returning the same description — is what lets a dropdown be filled from the
plan, and what makes the obvious example of this feature possible at all: a
plugin that shows the text of the selected region needs a label whose contents
are not known until it is asked. A constant cannot do that, so the call is the
recommendation unless something is found that it makes worse.

**The signature is declared, not sniffed.** A module-level `PLUGIN_API`, read
at discovery exactly as `REQUIRES_APP_VERSION` already is, decides which shape
`run` is called with; a plugin declaring nothing keeps today's `run(plan,
settings)`. Inspecting the callable's arity was considered and refused: every
other fact about a plugin here is declared and then validated, precisely
because a plugin is arbitrary code loaded out of a file, and guessing an
interface from a function object is the opposite of that.

## 32 Plugins with their own dependencies

Let a plugin carry a library comictrans does not ship, without it leaking into
every other plugin.

**Half of this already works, and the half that does not is the half that
matters.** Measured, with fixture plugins loaded through `discover_plugins`:

| what the plugin does | result |
| --- | --- |
| `from .vendor.helper import VALUE` — its own modules | loads |
| a vendored library whose internals say `from foolib.util import x` | `No module named 'foolib'` |
| the plugin puts its own `vendor/` on `sys.path`, then imports it | loads |

The second row is every real third-party library, because a library refers to
itself by its own name. So the only recipe that works today is a plugin
mutating `sys.path` — and **`sys.path` is global to the process and outlives
the plugin that changed it**. Two plugins carrying different versions of the
same library collide, first to load wins, silently, for the rest of the
session. `_load_one` already goes to some trouble to keep plugin *module names*
from colliding, with a fresh uuid per load so that one plugin's siblings cannot
shadow another's; their dependencies have nothing of the kind.

Two shapes, and choosing between them is most of the milestone:

- **Publish the recipe and name the hazard.** A documented `sys.path` insert,
  with the collision written where a plugin author will meet it. Cheap, honest,
  and it does not actually hold.
- **Give each plugin its own import namespace.** A finder on `sys.meta_path`
  scoped to the plugin being loaded, so that `foolib` resolves to that plugin's
  `foolib` and to nothing else. Real isolation, and a real amount of work.

Worth knowing before either: a dependency with a compiled extension has to
match the interpreter and platform it is loaded into, which inside a frozen
bundle is whichever one PyInstaller built. That is the plugin author's problem
rather than this project's, but it is not one they can see coming, so the
documentation is where it gets said.

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
`flatten_to_rgb` after it.

**Measured since, and deliberately left here.** On an 11.2 MP page: 0.28s and
a 68MB traced peak for an opaque PNG, ending up holding 34MB; 0.56s and 79MB
for the same page carrying an alpha channel. The doubling is the two
conversions — `convert("RGBA")` to pull the alpha out, then `flatten_to_rgb`
over the same image — and it only happens on a page that *has* alpha, which a
comic scan usually does not. The opaque path is already close to its floor, so
the candidate worth trying is narrow: read the alpha channel directly when the
mode already carries one, instead of converting the whole image to reach it.
It stays an idea on that evidence. It is the only thing in this file with no
user-visible change, and the measurement says the common case has little to
win. It would matter most where pages are read in a loop, which is `apply`
over a chapter rather than a preview of one page.

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

**The lock, enforced where the plan rules live rather than in the window.**
4.27 put the plugin half of the lock in `gui.document.apply_plugin`, which is
where a plugin's returned plan is taken. That was the smaller and more honest
change against the code as it stands — the commit point already holds both
the old plan and the new one, so the regions held back fall out of the
comparison for free and `plugins.run_plugin` keeps its single job of
validating and refusing.

It is arguably in the wrong module. `locked` is a field on `Region`, not a GUI
concept, and `run_plugin` is already where "what a plugin may not do" is
written down — `_check_same_shape` refuses a plugin that adds, removes,
reorders or moves a region. A lock is the same kind of rule and sits oddly
apart from it.

**What decides it is whether a plugin ever runs outside the window.** Today
none can: there is no plugin subcommand, `discover_plugins` is called only
from `main_window`, and so the window is the only door. A CLI plugin runner
would add a second one, and that second door would not inherit the lock —
which is the same shape of bug 4.27 shipped with and had to be told about.
So: if a plugin runner reaches the command line, move the rule into
`run_plugin` in the same change rather than afterwards.

The one cost to weigh when moving it. `run_plugin` returns a `Plan` and
nothing else, and the window needs to know *which* regions were held back in
order to say so — `apply_plugin` returns a `PluginOutcome` carrying exactly
that. Moving the rule means either widening what `run_plugin` returns or
giving the caller a second way to ask, and the first is an interface change
that milestone 33 may want to make anyway.

**Packaging for Windows and Linux.** Asked about, and kept here rather than
sequenced: it is cross-cutting, and *Why this order* says what that costs —
packaging bundles whatever the application is by the time it runs, so doing it
early means doing it twice. What it would run into was measured rather than
guessed, and the blocker is not the bundler.

**Fonts are the blocker, and they fail hard rather than degrade.**
`SEARCH_DIRS` holds four macOS paths and nothing else, so on this project's own
Linux machine not one of them exists and `resolve_default()` raises:

```
none of the fallback fonts could be resolved
(Comic Sans MS, Chalkboard SE, Marker Felt, Noteworthy, Helvetica)
```

Not a downgrade — a stop, before a page is rendered. Three of those families
are Apple's own and exist nowhere else. `Comic Sans MS` ships with Windows,
where the search directory is the only thing missing, and is on no Linux box by
default. `resolve_family` also requires a **real bold face**, because emphasis
renders as bold and synthesising one is forbidden, so a replacement chain
cannot simply name whatever is installed. And nothing is bundled: the suite has
never had to answer this, because `tests/conftest.py` hands it a *fake* macOS
font directory on `COMICTRANS_FONT_PATH`. Shipping an open comic face inside
the application would answer it and runs straight into "a font is never
silently substituted" — a decision about an invariant, not a packaging detail,
and the reason this is an idea rather than a size.

**OCR degrades rather than stops.** Apple Vision is macOS only, and
`get_recognizer` falls back to Tesseract and says so in the log — *"OCR
quality will be noticeably worse on comic lettering"*. A Windows or Linux
build ships that as its ordinary state rather than as a fallback few people
meet, which is a decision about what those builds are worth, not a thing
to fix.

**What is already right, and would need nothing.**
`plugins.plugin_directory()` and `gui.logfile.log_directory()` both branch
macOS against XDG already. `MOVE_MODIFIER` is `ControlModifier`, which Qt maps
to Command on macOS, and the hint asks Qt what this platform calls it rather
than deciding from `sys.platform`. The dependencies are fine: the pyobjc pair
is already guarded by `sys_platform == 'darwin'`, and everything else has
wheels for all three.

**The bundler does not cross-compile.** PyInstaller freezes the interpreter and
libraries of the machine it runs on, which is why `tools/build_app.py` refuses
anywhere but macOS. Each platform needs its own build script, run on that
platform, with its own icon story — `.ico` for Windows, and no single answer on
Linux — and neither has had the thought put into signing that macOS already
has. Linux is the cheaper of the two to try, because this project's own
development and its widget tests already run there.

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
