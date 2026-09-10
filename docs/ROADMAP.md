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

**A milestone includes its own documentation.** Once 4.7 has shipped, a
milestone that changes what the window does updates the help in the same
change; once 4.9 has shipped, it updates the strings that need
re-extracting too. Where that turns out to be a body of work rather than a
paragraph it becomes its own milestone — but it is never simply left for
later, because help describing the previous version is worse than no help
at all.

Sizes are relative effort, not estimates. When a milestone ships, delete
its section and its row from the table, and say so in the Status section of
`README.md`.

**The numbers are names, not positions.** They were allocated in the order
the milestones were thought of, and the order worth building them in has
moved since. The table below is the plan; a number is only there so that
one section can refer to another without ambiguity.

## Order

| # | Milestone | Size |
|---|-----------|------|
| 9 | Test suite speed | S |
| 4.23 | Page order | S–M |
| 4.24 | Interface review: conventions and wording | M |
| 4.7 | Help instructions | S–M |
| 4.10 | Package as an application | M |
| **11** | **First release (1.0.0)** | **S–M** |
| 4.22 | Preview off the main thread | M |
| 4.9 | Localisation | M |
| 5 | Validate a plan file | S |
| 4.26 | Extract text for one region | M |
| 4.27 | Lock a region | M |
| 4.28 | Region context menu | S |
| 3 | CBZ, PDF and CBR input | L |
| 8 | CBZ and CBR output | M |
| 6 | PDF output | L |
| 10 | Code quality review | M |
| 7 | Security audit | M |

Everything above 11 is what a first release needs. Everything below it is
what comes after one, and none of it should start before 1.0 is out.

The 4.x numbering says these follow milestone 4, the review GUI. Milestone
3 is older than all of them and independent of the GUI; it sits at the
bottom because nothing else waits on it, not because it matters least.

Three things decide this order.

**Cheap and immediately felt comes first.** 4.1 to 4.3 were small and
visible the moment the window opened, which is why they went first, and
4.11, 4.12 and 4.15 were the same shape: felt on every page of every
review, and waiting on nothing, as was moving a region after them.

**Cross-cutting comes last.** Localisation touches every user-visible
string, so it goes after the milestones that add strings. Packaging bundles
whatever the application is by then. Both are why the rename went first
rather than being filed with the other small things: renaming after either
one would have meant doing that work a second time, and 4.24 is filed just
ahead of them for exactly that reason — it decides what the labels say, and
deciding that after they have been translated is the same mistake twice.
Help text describes the UI, so it goes after the UI stops moving — which
for a first release means after 4.24 has settled the wording and before the
release freezes it.

**Foundations come before what stands on them.** 4.4 and zoom went early for
that reason, and region editing — the largest of the minor milestones, now
shipped — stood on both: undo built for five text fields would have needed
rewriting the moment a polygon could move, so it snapshots whole plans
instead, and dragging a polygon vertex accurately means being able to see
it.

**A release changes what "first" means.** Everything above 11 is there
because a release needs it, not because it is cheap or because it is ready.
That is why the region tools, the archive formats and the validate command
all sit below a line they would otherwise be well up: none of them is what
makes this releasable, and each is easier to get right once there is a
released version to compare against.

9 is the exception that proves it. A slow suite is not what stops a
release — but it is free, it touches nothing a user sees, and every
milestone above the line pays the 70 seconds. Doing it first costs one
milestone and refunds it across five.

**Measurement comes before the thing it would justify.** 9's saving was
found by timing the suite rather than by guessing which tests looked slow,
and the same is owed to 4.26 and 4.22: what a crop does to recognition
accuracy, and what a thread does and does not do about three copies of an
eleven-megapixel page, are numbers somebody has to produce before either
design is settled.

One ordering was a judgement call rather than a dependency, and it paid out:
**rendering (4.14) went before extract (4.6)**. Both run a pipeline pass from
the window and both needed the same worker thread, progress and cancel.
Rendering was the simpler of the two — no OCR, so no question about what is
safe to call off the main thread — and the more valuable, because reviewing a
plan and then leaving for a terminal to render it was the obvious hole in the
window. The threading was built on the easy case, and extract reused it: by
the time it landed, the harness was a base class and one `work()` method.

## 9 Test suite speed

**Measured, not guessed.** The suite takes 70 seconds, and 53 of them are
`test_fixtures.py`. Everything else put together is 17, of which the widget
tests are 10 for 191 of them.

OCR is already cached per fixture page — `_page_and_lines` does that and
says so. Detection is not: `find_regions` runs four times per image, once
for geometry, once for colours, and twice for the determinism check. Three
of those four ask the same question of the same pixels.

**The saving is in what the determinism test compares against.** One cached
detection per image, and one fresh run in the determinism test to compare
it to, is two runs instead of four. It is also a slightly stronger check
than the present one: the two runs are separated by whatever else the
session did in between, rather than being back to back in one function.

No coverage changes. Every assertion still runs against every fixture.
Measure before and after and put both numbers in the commit.

**Not `pytest-xdist` first.** Parallelism would cut wall time further and is
worth considering afterwards, but it adds a dependency and it hides
ordering bugs — exactly the kind the stray-window fixture was added to stop
hiding. Fix the arithmetic before adding processes.

## 4.23 Page order

Reorder a plan's pages by dragging rows in the page list.

**One order, not two.** The plan's `images` list is already a sequence;
this makes that sequence mean something and makes it editable. Review
follows it, and so does anything that writes pages into a single file — 8
and 6 below. A separate reading order and writing order would be two
things to keep in step for a case nobody has asked for.

**Until 8 lands it is half a feature, and that is the honest half.**
`apply` writes one file per source image, named after the source, so on its
own the order decides the sequence pages are worked in and nothing about
what ends up on disk. The half that works immediately — reviewing a chapter
in reading order instead of in whatever order the filenames happen to sort
— is worth having by itself. Once an archive is being written, the same
list becomes the reading order of the thing someone else opens.

**One rule needs writing down.** `extract` builds its image list from the
directory scan, so re-extracting over a plan whose pages have been
reordered by hand must not put them back. Re-extraction already knows how
to keep hand edits to regions; this is the same promise for the list.

Regions name their image by name rather than by index, so reordering does
not touch them.

## 4.24 Interface review: conventions and wording

Two passes over one surface, together because they touch the same strings.
They are listed as one milestone for that reason; split them if the
conventions half turns out to be large.

**Against Apple's Human Interface Guidelines.** Menu structure and where
commands belong, standard shortcuts, dialog button order and roles, what
belongs in a preferences window versus a document window — the macOS
conventions Qt does not decide on our behalf.

**Wording and casing, where the finding is a decision rather than a bug.**
Sampled: buttons and menu items are title case — "Cancel", "Choose…",
"Open Plan…", "Clear Menu" — which is what the HIG asks for. Form labels
and section headings are deliberately all-lowercase — "fill colour",
"source language", "a new plan starts as" — which is internally consistent
and is not what the HIG asks for; Apple wants sentence case there. So the
question is whether that lowercase style is kept on purpose or brought into
line, and it should be answered once and written down rather than drifting.

The same pass settles spelling. User-facing text is British — "colour",
"licence" — while the plan format is American, `fill_color`. That split is
defensible, since one is prose and the other is a data format that cannot
change without a version bump, but it should be deliberate.

**Before 4.7 and 4.9, and after the milestones that add strings.** Help
describes this text and localisation freezes it; renaming a label after
either means doing that work twice, which is the same trap 4.18 was moved
up to avoid.

## 4.7 Help instructions

A short in-application guide to reviewing a plan: what the badge colours
mean, what each flag means, what preview does and does not tell you.

After the milestones that change the UI, because it documents them, and
after 4.24 in particular — that one decides what the labels say, and help
quoting a label that is about to be reworded is help wrong on arrival.

A dialog with a `QTextBrowser` over a bundled document, rather than strings
in the source, keeps 4.9 to one file per language.

`README.md` is not a substitute; it is written for the command line.

## 4.10 Package as an application

A build script producing a runnable `.app`.

py2app, PyInstaller and briefcase all work. PyInstaller is the least
macOS-specific and the best documented.

**Decide distribution first.** An unsigned bundle is quarantined by
Gatekeeper on any machine but the one that built it, and signing means a
paid Developer ID and notarisation. Given the disclaimer in `README.md`,
the honest target is an unsigned local build, documented as such, not a
release artifact.

**`CFBundleName` has to be "Comic Translator", and it is not optional.**
macOS titles its application menu — "About X", "Hide X", "Quit X" — from
`qt_mac_applicationName()`, which reads `CFBundleName` out of the bundle's
`Info.plist` and only falls back to the `argv[0]`-derived name when there
is no bundle. `gui.app` sets the application name before Qt starts, which
is what makes the unbundled case right; the moment a bundle exists,
`Info.plist` outranks it and a bundle that omits the key would put
`comictrans` back in that menu. Set it, and set `CFBundleDisplayName` with
it.

**The icon set is already two drawings, and an `.icns` wants both.** That
format holds one image per size rather than one scalable drawing, which is
exactly the distinction `resources/appicon/` was built around: render the
1024 master into the large slots, where its fur lines and page edges read,
and the derived 512 icon into the small ones, where they would turn to mud.
Rendering one file into every slot throws away the reason there are two.

On macOS the Dock reads the bundle rather than `setWindowIcon`, so until
this milestone the icon is visible everywhere except the first place a Mac
user looks.

The bundle needs Pillow, numpy and OpenCV whatever else happens: the review
window renders previews through `render_page`, which erases and typesets
like any other page. Since 4.6 it needs pyobjc-Vision too — extract runs from
the window now, so the recogniser is part of the application rather than
something only the command line reaches.

## 11 First release (1.0.0)

The version, the release notes, and the decisions a release forces that a
development branch is free to leave open.

**What changes mechanically.** `__version__` in `src/comictrans/__init__.py`
is the single source — hatch reads it from there and a test holds the About
box to it — so 0.1.0 becomes 1.0.0 in one place. `README.md`'s Status
section still lists which milestones are implemented, which is a sentence
for a repository rather than for a release. And there is no changelog:
release notes need somewhere to live that is not `git log`.

**What a release forces a decision on, and 4.10 should not be written
before it is made.** That milestone records the problem plainly: an
unsigned bundle is quarantined by Gatekeeper on any machine but the one
that built it, and signing means a paid Developer ID and notarisation.
While the target is a build for oneself that is a footnote. The moment
"releasable" means somebody else double-clicks it, it is the whole
question, and it decides whether 1.0 is a `.app` at all or a documented
`uv run` with a bundle to follow.

**What the release notes have to say that the code cannot.**
`known-bugs.md` holds four entries and every one is a decision rather than
a defect — that distinction is the file's whole purpose, and a release is
exactly when somebody else needs it. Whatever ships as "known limitations"
should be written from that file rather than alongside it, so the two
cannot drift apart.

**One pass on a Mac, on the built thing rather than the source tree.**
This is the whole of what the macOS work has left, and it cannot happen any
earlier, because none of it exists until there is a bundle. Everything on
this list is set from code and none of it has been seen working: menu bar
placement and `AboutRole`, the unified title-and-toolbar look, full screen,
the Dock icon, and the toolbar drawings against real dark-mode chrome
rather than a palette a test set for itself.

**The suite was never going to defend this part.** What the tests do check
is that every drawing an action asks for ships, that each renders at every
baked size, that it comes out in the colour it was asked for, and that a
palette change repaints the set — all under the offscreen platform, on
whatever machine is to hand. None of that is evidence about where macOS
puts a menu item. A release is the honest deadline for looking.

**Two things were deliberately left below this line.** 4.22 would replace
a working wait cursor with threading, in the one code path that has
produced a segfault in this project — risk taken on immediately before a
release, for a benefit 4.21 already largely delivered. And 4.9 would ship
translation machinery for a single language while freezing every string
just as the milestones below start adding more, which under the
documentation rule above makes every one of them run a translation pass
too. Both are improvements. Neither is what makes 1.0 releasable.

## 4.22 Preview off the main thread

What the wait cursor papers over, done properly: `render_preview` on a
`RunJob`, so
the window stays live while a page renders and the render can be called
off. The harness exists — 4.14 and 4.6 built it, and by now it is a base
class and one `work()` method.

**No longer gated.** This waited on entry 5 of `known-bugs.md` — the
window seen vanishing during preview — because that entry ruled out every
thread-related explanation on the grounds that preview was synchronous, and
putting it on a thread would have retired the reasoning while the hunt was
still open. The crash turned out to be a stale shiboken wrapper outliving
`QGraphicsScene.clear()`, which is fixed and the entry deleted, so nothing
is being disturbed by moving preview off the main thread now.

What a wait cursor could not touch is still here: three copies of an
eleven-megapixel page per preview, about 120MB of them. A thread makes the
window answer while that happens; it does not make it less. Worth measuring
before deciding this milestone is only about threading.

**Ungated, and still below the release line.** The gate came off when that
crash was explained, but this is threading in the one code path that has
produced a segfault in this project, replacing a wait cursor that already
works. Immediately before a first release is the wrong moment for it. 4.26
wants the same harness and is below the line too, so the pair can be built
together afterwards.

## 4.9 Localisation

GUI chrome only.

Not the CLI, which is a large surface for a different audience, and
emphatically not plan file content: `source_text` and `translation` are the
comic, not the interface. `source_language` and `target_language` in the
plan header describe the comic too, and have nothing to do with this.

Last of the GUI work because every milestone above adds or changes strings,
and each one would otherwise mean another `lupdate` pass.

Two mechanics worth knowing before starting: `self.tr()` needs a `QObject`
subclass, so module-level constants — the label table in
`inspector._flags_text`, for one — need `QCoreApplication.translate`
instead. And there are no translator files yet, so this brings `lupdate`
and `lrelease` into the workflow and `.qm` files into the wheel. Add a
check that the compiled files are current.

## 5 Validate a plan file

`comictrans validate <plan>`: read a plan, check everything checkable
without rendering it, and say what is wrong. For debugging, and
deliberately not in the GUI for now — the window opens plans and reports
problems as it goes, which is a different job for a different moment.

Most of the machinery exists. `load_plan` enforces the schema and
`check_images` verifies the per-page hashes; both already produce messages
worth printing. This is largely a matter of running them from one command
and reporting what they say instead of raising on the first one.

**It also checks that every font named resolves**, and that is the failure
this command is really for. A plan can be perfectly well-formed, name every
image correctly, hash clean, and still refuse to render every region on the
page because the font it names has no bold face on this machine. Nothing in
the schema catches that, because it is a fact about the machine rather than
about the file. `fonts.resolve_family` is the call `apply` makes, so what
`validate` accepts is what `apply` accepts.

Exit non-zero on any failure, so it is usable from a script.

## 4.26 Extract text for one region

Run the recogniser over a single region and put what it reads into
`source_text`: for a region drawn by hand, which has no reading at all, and
for one where detection read the lettering badly.

**Two ways to do it, and the cheap one is wrong.** Recognising the whole
page and keeping the lines inside the polygon needs almost no new code, but
spends a full-page recognition on one balloon. Cropping to the region and
recognising that is the one worth building, and its risk is accuracy rather
than speed: recognisers do better with a margin around the text than with a
tight crop. So the crop wants padding, and the result wants comparing
against what full-page detection finds on the same fixtures before this is
called done.

**It writes over something a person may have typed**, which nothing else in
the window does — every other edit replaces the reviewer's text with the
reviewer's text. So it asks first when `source_text` is not what extract
left there, and it is one undo step like any other edit.

**It wants the worker thread, which is why it sits after 4.22.** A
recogniser is seconds, not milliseconds, and unlike preview this is a
per-balloon action rather than an occasional one; a wait cursor is the
wrong answer at that frequency.

`apply` is untouched and still runs no OCR. This is `review` doing what
`extract` does, to one region.

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
the manual is as much 4.24's job as this one's.

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

## 3 CBZ, PDF and CBR input

Read pages from an archive or a PDF instead of a directory.

Older than every 4.x milestone and independent of the GUI, with one
exception that is worth settling before any of it is written.

`PlanDocument.source_path` resolves an image as the plan's directory plus
the image name, and `apply.source_for` does the same. Reading pages from
inside an archive on demand breaks that assumption in `apply` and in
`review` at once, and takes the plan's per-page hash check with it.

**Unpack to a sidecar directory** and everything downstream keeps working
unchanged, including the invariant that source images are never written to
— unpacked pages are outputs of this stage, not sources being modified.
Reading from the archive on demand means an abstraction across three
modules for no gain that anyone has asked for.

**CBR too.** Reading it needs `rarfile` plus an external `unrar` or
`bsdtar` — the first dependency this tool has had that is not a Python
package, so it cannot be declared in `pyproject.toml`.

**It is never bundled — decided, not open.** The unrar licence is not
OSI-free, and this is an MIT project; shipping the binary inside a `.app`
would put someone else's terms on the whole thing. So the tool uses one the
person running it already has: found on `PATH`, and when it is somewhere
unusual, named in Preferences. That field is the same shape as the output
directory already there, and it costs nothing when the binary is on `PATH`
like everybody else's. Absent entirely, CBR input is unavailable and says
so — it does not fail halfway through opening a chapter.

Behind the sidecar decision above, a CBR reader is one more unpacker and
nothing else. Writing one is a different matter, and 8 covers it.

## 8 CBZ and CBR output

Write a chapter as a single archive rather than a directory of images.

**CBZ is a zip and needs nothing.** Rendered pages, stored or deflated,
and the format is done. It is the one every reader on every platform
opens.

**CBR needs a compressor this project cannot ship, which is not the same
as cannot use.** RAR compression is proprietary: `unrar` only reads, and
its licence explicitly forbids using it to create archives, so the only
thing that writes a `.rar` is the `rar` binary from WinRAR, which is paid
and not redistributable. What that licence restricts is *redistributing*
the compressor — it says nothing about someone driving the copy they have
already licensed. So: `rar` is never bundled. It is looked for on `PATH`,
and Preferences can name it where it lives somewhere unusual, which is the
same field 3 needs for `unrar` and probably the same one. Present, CBR is
offered; absent, it is not, and the reason says which binary would provide
it rather than the option quietly not being there.

That keeps this an MIT project and still gives anyone holding a licence
the format they asked for. It also puts a configurable path to an
executable this tool then runs into the codebase, which 7 should look at
closely — how it is validated, and what happens when it names something
that is not `rar` at all.

**Naming inside the archive carries the reading order, not the source
filenames.** A reader sorts entries by name, so the order 4.23 lets someone
set has to survive into the archive as a zero-padded prefix or equivalent.
Source names that happen to sort correctly today are luck, not a
guarantee, and a reordered chapter would silently come out in the old
order if the entry names were copied straight through.

**It collides with how cancel works, and that has to be decided.** Today a
cancelled render leaves whole pages on disk and re-running finishes the
job — `README.md` says so, and that is the reason cancel stops after a page
rather than during one. An archive has no half-way state worth keeping: the
honest equivalent is to render into a temporary directory, cancel there,
and only build the archive once every page is done, so a cancelled run
leaves no archive at all rather than a truncated one. That is a different
promise from the directory case and both should be written down, not left
to whichever one the code happens to implement.

The invariant is unchanged and matters more here: a region that cannot be
rendered is left alone and named in the report. With a directory output the
page is right there to look at; inside an archive it is one step further
away, so the report is the only thing that will tell someone a page came
out untouched.

Where it plugs in: `render_dialog`'s format choice becomes two questions
rather than one — what each page is encoded as, and what contains them —
and `apply.output_path` assumes one output file per source image
throughout.

One thing left open: whether to write a `ComicInfo.xml` alongside the
pages. Readers use it for series, volume and language, and the plan header
already knows the language pair. Worth deciding when this is picked up.

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
today. And it is what makes 4.23's page order mean something on disk.

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
`rarfile` and the external `unrar`/`rar` binaries that 3 and 8 bring in.

**Two halves worth keeping apart.** Dependency CVEs are a tooling question
— `pip-audit` or equivalent, run on a schedule, reporting versions against
advisories. The code half is a reading, and it has one thing to check that
nothing else does: **no network calls anywhere in the pipeline** is an
invariant in `CLAUDE.md` and is enforced nowhere in the suite. An audit is
where that stops being a rule people remember and starts being something
that fails a check.

**After 3 and 8.** Reading an archive means handing an untrusted file to an
unpacker, and for CBR that unpacker is an external binary; writing one
means invoking a second external binary with paths someone else chose.
Both are genuinely new attack surface — the first this tool has had that is
not a Python library — and worth being in scope the first time round rather
than the second. Whatever shells out to `unrar` or `rar` is where argument
handling wants reading closely.

Last on the list, and the only item here that is a recurring activity
rather than something that ships once and is deleted from this file.
