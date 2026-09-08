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
4.11, 4.12 and 4.15 were the same shape: felt on every page of every
review, and waiting on nothing.

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

## 4.5 Region editing

Edit a region's polygon, add a region, delete a region, merge two.

The largest milestone on the list and the one that changes what the GUI is.
Four passes: model and schema, then move and reshape, then add and delete,
then merge. **The first two passes are done** — what they settled is
recorded below as done, not as a plan.

### Done: model and schema

Plan schema version 2. Each page's hash moved out of every region into a
top-level `images` list, so a page with no text on it is in the plan and can
be drawn on; `Geometry` gained `manual`, for a polygon a person drew.
Version 1 files are upgraded as they are read — the images list is rebuilt
from the regions, which carry the same facts — so no existing plan is
orphaned. `README.md` and `ARCHITECTURE.md` carry the details.

Note the compatibility direction: an older comictrans meeting `geometry:
manual` or an `images` list fails with a `PlanError`. At `0.1.0` with one
user that is cheap, and it will not stay cheap.

### Done: move and reshape

**Edit > Edit Region Shape** (`Ctrl+E`) puts handles on the selected
region's corners: drag one to move it, drag inside to move the whole shape,
double-click an edge to add a corner or a corner to remove one, `Esc` to
abandon a drag. A mode rather than always on, because dragging inside a
region is also how the page pans.

The canvas draws the drag and reports the outline once, on mouse up, so one
drag is one undo step. `PlanDocument.set_polygon` validates it by the
reader's own rules — the correctness trap below, now closed — and marks the
geometry `manual`; a refused shape is put back on screen from the document.
`README.md` and `ARCHITECTURE.md` carry the details.

Vertex editing did not need a colour of its own, but `manual` did: violet,
beside green for traced and orange for approximate.

### Settled, not yet built

**A new region is drawn by hand, and gets no OCR.** The user draws the
outline; `source_text` and `translation` are both typed in. Nothing in
`review` re-reads the page for text, so an added region has no OCR
confidence to report and `confidence` should say so rather than claim a
measurement that was never made.

**Colours come from the page or from a swatch.** A new region needs
`fill_color` and `text_color`. Sampling inside the drawn polygon through
`detect.color` is the default; clicking a pixel to sample it and picking
from a small set of standard colours are the two overrides. Sampling pulls
numpy and OpenCV in, which is fine for `review` — the invariant is that
*`apply`* runs no detection — but it belongs in a new module and **never**
in `gui/document.py`, which is deliberately free of both.

**A delete is a delete.** Not a `skip: true` in disguise. Undo covers it
within the session; after a save it is gone, the same as deleting the
region's block by hand in the YAML.

**Every one of these is undoable and redoable.** 4.4's snapshot stack
already gives this for free: an add, a delete, a merge or a moved vertex is
a new `Plan` pushed through the same `_record`, with no undo code of its
own.

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

### Open

- **Ids and order.** The reader enforces unique ids. Do not reuse the
  suffix of a deleted region; take `max + 1` per page. Merging two regions
  has to pick an order. Reordering by hand is out of scope here.
- **What a hand-drawn region records for confidence.** `0.0` reads as a
  terrible OCR result and would flag the region as low confidence forever;
  the field is not optional. Decide during the add-and-delete pass.

### The correctness trap, closed

`write_plan` performs no schema validation — the reader does, which was
harmless only while the GUI could not produce a plan the reader would
reject. Editable polygons could: `polygon_is_simple` rejects
self-intersecting and degenerate polygons at load, so the window could have
written a file it then refused to reopen. Geometry edits are validated in
`gui.document` before they reach the plan, against limits shared with the
reader through `planfile.schema`. The two passes still to come add
polygons of their own and inherit that: an added or merged region goes
through the same `set_polygon` gate.

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
`review` at once, and takes the plan's per-page hash check with it.

**Unpack to a sidecar directory** and everything downstream keeps working
unchanged, including the invariant that source images are never written to
— unpacked pages are outputs of this stage, not sources being modified.
Reading from the archive on demand means an abstraction across three
modules for no gain that anyone has asked for.
