# Roadmap

What is planned and not yet built, in the order it is worth building.

This is a different list from the two that already exist. `known-bugs.md`
records defects that are understood and deliberately left alone;
**Known weak points on real scans** in `ARCHITECTURE.md` records what
detection does badly on real pages. Both describe the tool as it stands.
This file describes what it is not yet.

Nothing here is a commitment, and nothing here is a work item an agent
should pick up on its own — the same rule `known-bugs.md` carries. Work a
milestone when the request names it. Sizes are relative effort, not
estimates. When a milestone ships, delete its section and its row from the
table; when a whole milestone ships, say so in the Status section of
`README.md` as well.

**The numbers are names, not positions.** They were allocated in the order
the milestones were thought of, and the order worth building them in has
moved since. The table below is the plan; a number is only there so that
one section can refer to another without ambiguity.

## Order

| # | Milestone | Size |
|---|-----------|------|
| 4.11 | Font selection dropdown | S–M |
| 4.12 | Plan header editing | S–M |
| 4.5 | Region editing | XL |
| 4.14 | Render pages from the GUI | M–L |
| 4.6 | Extract from the GUI | L |
| 4.13 | Preferences | M |
| 4.7 | Help instructions | S–M |
| 4.8 | macOS look and feel | M |
| 4.9 | Localisation | M |
| 4.10 | Package as an application | M |
| 3 | CBZ and PDF input | L |

The 4.x numbering says these follow milestone 4, the review GUI. Milestone
3 is older than all of them and independent of the GUI; it sits at the
bottom because nothing else waits on it, not because it matters least.

Three things decide this order.

**Cheap and immediately felt comes first.** 4.1 to 4.3 were small and
visible the moment the window opened, which is why they went first, and
4.11 and 4.12 are the same shape, as 4.15 was: each one is felt on every
page of every review, and none of them waits on anything.

**Cross-cutting comes last.** Localisation touches every user-visible
string, so it goes after the milestones that add strings. Packaging bundles
whatever the application is by then. Help text describes the UI, so it goes
after the UI stops moving.

**Foundations come before what stands on them.** 4.4 went first for that
reason: undo built for five text fields would have needed rewriting the
moment a polygon could move, so it snapshots whole plans instead and 4.5
inherits it. Zoom went before 4.5 for a plainer reason: dragging a polygon
vertex accurately means being able to see it.

Two orderings are judgement calls rather than dependencies.

**Region editing (4.5) before the two pipeline milestones.** Extract and
apply both already work from a terminal. A bad polygon cannot be fixed
anywhere at all — not in the GUI, and not without hand-editing pixel
coordinates in YAML. Given what `known-bugs.md` already records about
detection, editing earns its place first despite being the larger job.

**Rendering (4.14) before extract (4.6).** Both run a pipeline pass from
the window, and both need the same worker thread, progress and cancel.
Rendering is the simpler of the two — no OCR, so no question about what is
safe to call off the main thread — and the more valuable, because reviewing
a plan and then leaving for a terminal to render it is the obvious hole in
the window as it stands. Build the threading on the easy case; extract
reuses it with the harder question on top.

## 4.11 Font selection dropdown

An editable `QComboBox` in place of the inspector's font line edit.

**Populate it from `fonts.py`, never from `QFontDatabase`.** They answer
different questions. Qt lists what Qt can draw with; `fonts.py` resolves a
family by scanning `search_dirs()` for files, matching filename slugs, and
demanding a real bold face — `resolve_family(..., require_bold=True)`
raises `FontError` without one, because emphasis is bold and a bold is
never synthesised. A dropdown built from Qt would offer families that
`apply` then refuses, turning a two-click choice into a render failure.

So: a new `fonts.available_families()` that enumerates faces across the
search directories and then *verifies* each candidate through
`resolve_family`. The verify step is not belt-and-braces —
`_candidate_files` matches on filename slug, so a family whose internal
name does not match its filename enumerates but does not resolve. Verifying
is what lets the dropdown promise that everything it offers will render.

**It must stay editable, and must keep a name it cannot resolve.** A plan
written on another Mac can name a font this one does not have. A closed
dropdown would silently swap that font the moment the field was touched,
which is the one thing the spec says never happens. Editable, current value
always shown, and marked when it will not resolve — that marking is worth
having on its own, since it says so before you render rather than after.

**Cache the scan.** Measured at roughly 4ms per font file, so a few hundred
files is on the order of a second, and macOS `.ttc` collections hold many
faces each. Scan once, behind a wait cursor on first use, with an explicit
rescan for when a font is installed mid-session.

The list needs an explicit "(plan default)" entry meaning `None`, which the
empty line edit expresses today only by accident.

## 4.12 Plan header editing

The plan header — `font`, `case`, `font_size_min_ratio`, `condense_min`,
`source_language`, `target_language`, `ocr_engine` — cannot be changed in
the GUI at all. The inspector edits regions; the header is hand-edit-the-
YAML. This is the milestone that actually delivers "set the font for this
comic".

A dialog, or a third dock. `PlanDocument` gains `set_header_*` beside the
region setters: the same `dataclasses.replace`, the same dirty flag, and
the snapshot undo 4.4 built covers it without a line of undo code.

Reuses 4.11's dropdown for the header font, which is why it sits directly
after it.

One thing to decide: changing the header font changes every region that has
no override, so a preview changes wholesale. Worth a confirmation, or at
least a status line saying how many regions it moves.

## 4.5 Region editing

Edit a region's polygon, add a region, delete a region, merge two.

The largest milestone on the list and the one that changes what the GUI is.
Worth doing in four passes: model and schema decisions, then move and
reshape, then add and delete, then merge.

### Settled

**Merging is refused unless the polygons genuinely overlap.** With that
gate, the merged polygon is the convex hull of both. This covers the case
merge exists for — one balloon traced as two regions, the class of bug
`known-bugs.md` and the fixtures already show — and refuses the case the
single-polygon model cannot represent honestly. Two balloons on opposite
sides of a panel have no simple polygon covering both and only both; a hull
across them would swallow the artwork between, which erase would then wipe.

The gate must test real polygon intersection, not the bounding-box ratio
`overlapping_region_ids` uses — that one exists to warn about regions
drawing over each other at apply time and is deliberately loose.
`model.segments_intersect` already gives the edge-crossing half; a
ray-cast point-in-polygon test for the containment half belongs beside it,
where it stays free of OpenCV. `detect._contains_centers` is the same test
but built on `cv2.pointPolygonTest`, so it cannot be reused from the
document layer.

**`Geometry` gains a `manual` value**, unless the first pass finds a reason
not to. `exact` means traced from a balloon contour and `approximate` means
OCR boxes plus a margin, which is what drives the orange "check this"
badge. A hand-drawn polygon is neither: it is the most trustworthy geometry
in the file, so `approximate` flags it wrongly and forever, while `exact`
quietly makes the docstring untrue and loses the one thing worth knowing on
a second pass — which regions were already fixed by hand.

The cost is a plan file schema change: the reader's enum, the writer, and a
decision on `header.version`, currently `1`. Note the compatibility
direction — an older comictrans meeting `geometry: manual` fails with a
`PlanError`. At `0.1.0` with one user that is cheap, and it will not stay
cheap.

### Open

- **A page with no regions is invisible.** `Plan.images()` derives from the
  region list, so a page where detection found nothing is not in the plan at
  all, and a region cannot be added to it. It is also where the
  `image_sha256` for a new region would have come from. Either the header
  gains a list of images, or the GUI lets the file be picked and hashes it.
  Decide before the add-and-delete pass, not during it.
- **Where new colours come from.** A new region needs `fill_color` and
  `text_color`, which means sampling through `detect.color`. That is fine
  for `review` — the invariant is that *`apply`* runs no detection — but it
  pulls numpy and OpenCV in, so it belongs in a new module and **never** in
  `gui/document.py`, which is deliberately free of both. Sampling inside
  the new polygon, with a colour picker as an override, is the obvious
  shape.
- **Ids and order.** The reader enforces unique ids. Do not reuse the
  suffix of a deleted region; take `max + 1` per page. Merging two regions
  has to pick an order. Reordering by hand is out of scope here.
- **Deleting loses the only record that OCR found text there.** Undo covers
  it in the session; after a save it is gone. `skip: true` may be the
  better default action, with delete kept explicit.

### One correctness trap

`write_plan` performs no schema validation — the reader does. Today that is
harmless, because the GUI cannot produce a plan the reader would reject.
Once polygons are editable it can: `polygon_is_simple` rejects
self-intersecting and degenerate polygons at load, so the GUI could write a
file it then refuses to reopen. Validate every geometry edit before it
reaches the document.

## 4.14 Render pages from the GUI

Run `apply` without leaving the window.

**Require a save first.** `apply_plan` takes a `Plan` object, so the GUI
*could* render unsaved edits, and preview already does exactly that. But
preview is ephemeral and output files are not: pages rendered from a plan
that is not on disk are pages that cannot be regenerated, which is the
whole point of the two-pass design. Offer "Save and Render…" rather than
rendering a document that exists only in the window.

**`check_output_dir` becomes a dialog with no override.** It refuses an
output directory inside the source tree, which is how the invariant that
source images are never written to is actually enforced. Every other
refusal in this tool has a `--force`; this one must not grow one.

**`apply_plan` needs an optional per-page progress callback.** Its loop is
internal, so the alternative is the GUI reimplementing the loop, and a
second copy of the loop is a second place for the two to drift. One
optional parameter keeps a single implementation, the same way
`render_preview` calls `render_page` rather than drawing its own.

The rest follows the CLI: `--force` becomes a confirmation rather than a
default, `--skip-hash-check` needs an equivalent or an explicit refusal,
and the choice of erase strategy and output format belongs in the dialog.

`resolve_styles` runs before any page is written, so an unresolvable font
fails cleanly with nothing on disk. Keep that ordering.

`ApplyReport` carries `pages_written`, `outcomes` and `page_failures`. In a
window that wants to be a readable panel rather than a modal that vanishes
— ideally one whose rows select the region that would not fit.

## 4.6 Extract from the GUI

Choose an input and run extract without leaving the window.

**It cannot run on the UI thread.** Detection alone is 0.1 to 0.3 seconds
per page, and 2.8 seconds on the screentoned fixture, before OCR. A
forty-page chapter is a frozen window for a minute or more. It needs a
worker thread, progress, and cancel — the harness 4.14 will already have
built. `extract_page` is the per-page hook; `extract` loops internally and
offers nowhere to report from.

Verify that Apple Vision works off the main thread before building on the
assumption. This is the question 4.14 does not have to answer, and the
reason it goes first.

Scope for a first version: input path, plan path, languages, OCR engine.
Extract has around fifteen detection-tuning flags and they can stay on the
command line.

Leave `--merge` out. Re-extracting over an open plan is exactly the merge
case, including its lost-hand-work reporting and its exit status, and that
is a second feature. Extract to a new plan, then open it.

Once 4.5 exists, re-extracting a page is destructive against hand-edited
regions. Keep the two apart.

## 4.13 Preferences

Application defaults, in the `QSettings` 4.2 already set up.

**A preference never overrides a plan value.** It fills in a blank when
something new is created, and does nothing else, ever. A "default font"
that quietly won over a plan's header would mean the same plan renders
differently on two machines, and re-runnability — edit a translation, run
it again, and only that text changes — is the property the whole two-pass
design exists to have. 4.12 edits *this* plan; 4.13 decides what a *new*
one starts from. Those two must not blur into each other.

That is also why this milestone waits: 4.14 and 4.6 are the first things in
the tool that create something a default could seed. Before them, a
preferences dialog has almost nothing legitimate to hold.

What it then holds:

- For 4.14: default output directory, erase strategy, output format
- For 4.6: default font, case, `font_size_min_ratio`, `condense_min`, OCR
  engine and languages, seeded into a new plan's header
- Everywhere: which directory the Open and Save dialogs start in

On macOS this wants Cmd+, and the application menu — see 4.8.

## 4.7 Help instructions

A short in-application guide to reviewing a plan: what the badge colours
mean, what each flag means, what preview does and does not tell you.

After the milestones that change the UI, because it documents them. A
dialog with a `QTextBrowser` over a bundled document, rather than strings
in the source, keeps 4.9 to one file per language.

`README.md` is not a substitute; it is written for the command line.

## 4.8 macOS look and feel

Qt already supplies the native style, the native menu bar, and Cmd for Ctrl
through `QKeySequence.StandardKey`, so less of this is missing than it
appears. What is actually left, roughly by value:

- **Toolbar icons.** 4.2 shipped the toolbar text-only, because half of
  these commands have no standard pixmap in any Qt style. Bundled SVGs
  answer that, and bundling assets is the same plumbing the `.icns` icon
  below and 4.10's build config already need — `QIcon.fromTheme` returns
  nothing on macOS, so there is no route that avoids shipping files. Doing
  it here rather than earlier also means drawing icons once for a toolbar
  4.5 has finished adding buttons to. Icon sets carry licences; whichever
  is chosen needs recording in `LICENSE` and in the About dialog.
- An `.icns` icon and bundle identity, which mostly overlaps with 4.10
- `AboutRole` on the About action, so macOS moves it into the application
  menu where it belongs, and the same for a Preferences action once 4.13
  exists
- Dark mode: check the canvas overlay colours stay legible against dark
  chrome
- Full screen, and the unified toolbar look

**None of this is verifiable by the test suite.** The widget tests run
under the offscreen platform on whatever machine is to hand; menu bar
placement, the About role and dark mode only exist on a real Mac. This is
the one milestone the four checks cannot defend, and it has to be looked at
by hand on the target machine.

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

## 4.10 Package as an application

A build script producing a runnable `.app`.

py2app, PyInstaller and briefcase all work. PyInstaller is the least
macOS-specific and the best documented.

**Decide distribution first.** An unsigned bundle is quarantined by
Gatekeeper on any machine but the one that built it, and signing means a
paid Developer ID and notarisation. Given the disclaimer in `README.md`,
the honest target is an unsigned local build, documented as such, not a
release artifact.

The bundle already needs Pillow, numpy and OpenCV whatever else happens:
the review window renders previews through `render_page`, which erases and
typesets like any other page. Only pyobjc-Vision is contingent, on 4.6.

## 3 CBZ and PDF input

Read pages from an archive or a PDF instead of a directory.

Older than every 4.x milestone and independent of the GUI, with one
exception that is worth settling before any of it is written.

`PlanDocument.source_path` resolves an image as the plan's directory plus
the image name, and `apply.source_for` does the same. Reading pages from
inside an archive on demand breaks that assumption in `apply` and in
`review` at once, and takes the `image_sha256` check with it.

**Unpack to a sidecar directory** and everything downstream keeps working
unchanged, including the invariant that source images are never written to
— unpacked pages are outputs of this stage, not sources being modified.
Reading from the archive on demand means an abstraction across three
modules for no gain that anyone has asked for.
