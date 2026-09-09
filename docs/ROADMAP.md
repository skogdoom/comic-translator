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
review, and waiting on nothing, as was moving a region after them.

**Cross-cutting comes last.** Localisation touches every user-visible
string, so it goes after the milestones that add strings. Packaging bundles
whatever the application is by then. Help text describes the UI, so it goes
after the UI stops moving.

**Foundations come before what stands on them.** 4.4 and zoom went early for
that reason, and region editing — the largest of the minor milestones, now
shipped — stood on both: undo built for five text fields would have needed
rewriting the moment a polygon could move, so it snapshots whole plans
instead, and dragging a polygon vertex accurately means being able to see
it.

One ordering was a judgement call rather than a dependency, and it paid out:
**rendering (4.14) went before extract (4.6)**. Both run a pipeline pass from
the window and both needed the same worker thread, progress and cancel.
Rendering was the simpler of the two — no OCR, so no question about what is
safe to call off the main thread — and the more valuable, because reviewing a
plan and then leaving for a terminal to render it was the obvious hole in the
window. The threading was built on the easy case, and extract reused it: by
the time it landed, the harness was a base class and one `work()` method.

## 4.13 Preferences

Application defaults, in the `QSettings` 4.2 already set up.

**A preference never overrides a plan value.** It fills in a blank when
something new is created, and does nothing else, ever. A "default font"
that quietly won over a plan's header would mean the same plan renders
differently on two machines, and re-runnability — edit a translation, run
it again, and only that text changes — is the property the whole two-pass
design exists to have. 4.12 edits *this* plan; 4.13 decides what a *new*
one starts from. Those two must not blur into each other.

That is also why this milestone waited: 4.14 and 4.6 were the first things in
the tool that create something a default could seed. Both have shipped, and
both now ask the same questions on every run.

What it holds:

- For the render dialog: default output directory, erase strategy, output
  format
- For the extract dialog: source and target language, OCR languages and
  engine, and a default font to record in a new plan's header — extract
  currently walks its own fallback chain, which is right as a default and
  wrong as the only answer
- Everywhere: which directory the Open and Save dialogs start in

A fourth is now visible that was not before: **the detection-tuning flags.**
The extract dialog deliberately leaves all fifteen on the command line, and
preferences is where a machine-wide default for one of them would go if any
of them turns out to want one. None has yet.

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
  region editing has finished adding buttons to. Icon sets carry licences;
  whichever is chosen needs recording in `LICENSE` and in the About dialog.
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
