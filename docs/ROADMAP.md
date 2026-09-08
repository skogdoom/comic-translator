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

## Order

| # | Milestone | Size |
|---|-----------|------|
| 4.4 | Undo and redo | M |
| 4.5 | Region editing | XL |
| 4.6 | Extract from the GUI | L |
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
visible the moment the window opened, which is why they went first.

**Cross-cutting comes last.** Localisation touches every user-visible
string, so it goes after the milestones that add strings. Packaging bundles
whatever the application is by then, so it goes after the milestone that
decides whether OCR is part of it. Help text describes the UI, so it goes
after the UI stops moving.

**Foundations come before what stands on them.** Undo built for five text
fields would need rewriting the moment a polygon can move, so 4.4 comes
before 4.5 and is built knowing 4.5 is coming.

Region editing (4.5) before extract from the GUI (4.6) is the one ordering
that is a judgement call rather than a dependency. Extract already works
from a terminal, once per chapter. A bad polygon cannot be fixed anywhere
at all today — not in the GUI, and not without hand-editing pixel
coordinates in YAML. Given what `known-bugs.md` already records about
detection, editing earns its place first despite being the larger job.

## 4.4 Undo and redo

One change at a time, not all unsaved changes at once.

**Snapshot the plan rather than writing a command per operation.** `Plan`
is a frozen dataclass holding a tuple of frozen `Region`s, and
`PlanDocument._update` already builds a whole new one on every edit. So an
undo entry is simply the previous `Plan`. Because regions are immutable and
reused, that is a new tuple of pointers, not a deep copy; a few hundred
regions costs nothing.

The reason this matters is 4.5. A command-per-operation stack would need a
new command class for every geometry operation added later. A snapshot
stack covers add, delete, merge and polygon editing the day they land,
with no undo code written for any of them.

Two decisions:

- **Coalescing.** 4.1 settled that the prose fields commit on every
  keystroke — for the reasons in `gui/inspector.py`'s module docstring — so
  raw snapshots would give per-character undo. Merge consecutive edits to
  the same region and field until focus changes or a timeout expires. This
  is what `QUndoCommand.mergeWith` exists for.
- **Ctrl+Z while typing.** Qt dispatches shortcuts before key events reach
  the focused widget, so a window-level undo action takes undo away from a
  text field mid-sentence. Decide the rule deliberately.

Fold in `setWindowModified` with `[*]` in the window title, replacing the
hand-rolled asterisk. That is the native idiom, 4.8 would ask for it
anyway, and undo needs the clean-state concept regardless: undoing back to
the last save should clear the marker, which a bool that only ever goes
true cannot express.

Undo is in-memory and does not reach the file. Undoing past a save does not
revert what is on disk.

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

## 4.6 Extract from the GUI

Choose an input and run extract without leaving the window.

**It cannot run on the UI thread.** Detection alone is 0.1 to 0.3 seconds
per page, and 2.8 seconds on the screentoned fixture, before OCR. A
forty-page chapter is a frozen window for a minute or more. It needs a
worker thread, progress, and cancel. `extract_page` is the per-page hook
for both; `extract` loops internally and offers nowhere to report from.

Verify that Apple Vision works off the main thread before building on the
assumption.

Scope for a first version: input path, plan path, languages, OCR engine.
Extract has around fifteen detection-tuning flags and they can stay on the
command line.

Leave `--merge` out. Re-extracting over an open plan is exactly the merge
case, including its lost-hand-work reporting and its exit status, and that
is a second feature. Extract to a new plan, then open it.

Once 4.5 exists, re-extracting a page is destructive against hand-edited
regions. Keep the two apart.

## 4.7 Help instructions

A short in-application guide to reviewing a plan: what the badge colours
mean, what each flag means, what preview does and does not tell you.

After 4.5 and 4.6, because it documents a UI that both of them change. A
dialog with a `QTextBrowser` over a bundled document, rather than strings
in the source, keeps 4.9 to one file per language.

`README.md` is not a substitute; it is written for the command line.

## 4.8 macOS look and feel

Needs defining before it can be scheduled. Qt already supplies the native
style, the native menu bar, and Cmd for Ctrl through
`QKeySequence.StandardKey`, so less of this is missing than it appears.
What is actually left, roughly by value:

- An `.icns` icon and bundle identity, which mostly overlaps with 4.10
- `AboutRole` on the About action, so macOS moves it into the application
  menu where it belongs
- `setWindowModified` and `[*]`, already folded into 4.4
- Preferences under Cmd+, once there are preferences
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

Size depends entirely on 4.6. Without extract the bundle needs Pillow and
PySide6. With it, add numpy, OpenCV and pyobjc-Vision.

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
