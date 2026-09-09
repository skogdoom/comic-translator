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
| 4.17 | Error handling, and somewhere for a crash to go | M |
| 4.8 | macOS look and feel | M |
| 4.7 | Help instructions | S–M |
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
after the UI stops moving — which means after 4.8, since replacing a text
toolbar with icons changes what there is to describe.

**Foundations come before what stands on them.** 4.4 and zoom went early for
that reason, and region editing — the largest of the minor milestones, now
shipped — stood on both: undo built for five text fields would have needed
rewriting the moment a polygon could move, so it snapshots whole plans
instead, and dragging a polygon vertex accurately means being able to see
it.

**4.17 goes first because it is under the other four.** A packaged
application (4.10) has no stderr at all, so the only diagnostic channel the
window has today disappears exactly when it is most needed. Localisation
(4.9) touches every user-visible string, and error messages are strings, so
doing them afterwards means a second `lupdate` pass over all of them. Help
(4.7) has to say where the log is. And it is the one item on this list
answering something that is happening now rather than something that would be
nice.

One ordering was a judgement call rather than a dependency, and it paid out:
**rendering (4.14) went before extract (4.6)**. Both run a pipeline pass from
the window and both needed the same worker thread, progress and cancel.
Rendering was the simpler of the two — no OCR, so no question about what is
safe to call off the main thread — and the more valuable, because reviewing a
plan and then leaving for a terminal to render it was the obvious hole in the
window. The threading was built on the easy case, and extract reused it: by
the time it landed, the harness was a base class and one `work()` method.

## 4.17 Error handling, and somewhere for a crash to go

Go over what the window does when something goes wrong, and give it a log
file, because right now a failure has nowhere to be seen.

**"Crash" is three different things, and the one being hit is the hard one.**
Measured on PySide6 6.11.2:

- **The process dies.** A segfault or an abort: the window vanishes outright
  with nothing on screen and, launched without a terminal, nothing anywhere
  else either. This is the reported symptom, so it is what this milestone
  leads with. `qFatal` — Qt's own way of giving up, on a `QThread` destroyed
  while running among other things — takes this route too, so "segfault" and
  "Qt refused to continue" look identical from outside.
- **A swallowed exception.** An exception raised in a slot — a menu action, a
  signal handler, a mouse event — is printed to stderr and the event loop
  carries on. The application does *not* abort. The window survives with its
  state half-updated, and what you see is a button that did nothing. Not the
  reported symptom, but almost certainly also present and unnoticed.
- **An exception outside a slot.** Everything `gui.app.run` does before
  `app.exec()` — building the window, opening a plan named on the command
  line. That propagates to `cli.main`, which catches only `ComictransError`,
  so anything else exits with a raw traceback. The window vanishes here too,
  but stderr says why.

**`faulthandler` came first, and has already shipped.** It was pulled out of
this milestone and done on its own, because nothing else here could start
without it: `gui.crash` turns it on before the `QApplication` exists and
writes a Python traceback — the exact line, on every thread — to
`~/Library/Logs/comictrans/review-crash.log`. `SIGSEGV` and `SIGABRT` are both
caught, measured at exit 139 and exit 134. So the next crash names itself, and
what is left below is the work that trace makes possible.

**What is known so far.** It was seen during **preview**, which eliminates
more than it implicates: `render_preview` is synchronous on the main thread
and touches no OCR, no recogniser and no worker thread at all. If the crash
is only ever in preview, the whole thread-related branch of the search is
out — Apple Vision on a `QThread`, a `QThread` destroyed while running, the
job read after `deleteLater`. Those stay worth a look only if it turns out to
happen elsewhere too.

**A reproduction was attempted and did not crash.** The reported plan and its
page — `tests/fixtures/11-complex_six_panel_page.png`, byte-identical — were
run through `render_preview`, `to_pixmap` and a real paint into a
`QGraphicsScene`, on Linux under the offscreen platform with PySide6 6.11.2.
It rendered 2840×3880 RGB, converted to a non-null pixmap and painted, twice
over. So the preview path is not unconditionally broken on that input, and
whatever this is depends on the platform, the display, or state built up
across a session. Recorded because a negative result is worth as much as a
positive one when the next person starts here.

**What is left, in the order worth checking:**

- **`to_pixmap`, which is `PIL.ImageQt`.** The one place a Pillow buffer
  becomes something Qt paints, and the classic shape of this kind of crash:
  `ImageQt` wraps the image's memory rather than copying it, so its lifetime
  and Qt's have to be reasoned about rather than assumed. `QPixmap.fromImage`
  copies, which is why it survives here, but the margin is one function call
  wide. Converting through an explicit `QImage.copy()` — or through raw bytes
  with an explicit `bytesPerLine` — would remove the question entirely, and is
  cheap enough to do on suspicion.
- **Three copies of an eleven-megapixel page, per preview.** That page is
  2840×3880: about 33MB as PIL RGB, 44MB as ARGB32 inside `ImageQt`, and 44MB
  again as the `QPixmap`. `Ctrl+R` toggles, so a session spent comparing the
  overlay against the render does that repeatedly. Worth measuring what is
  actually released between toggles before assuming it is fine; a graphics
  allocation that fails on macOS need not come back as a `MemoryError`.
- **The Retina backing store.** A 2× display doubles what the view rasterises,
  and none of the testing has ever run on one. Nothing specific is suspected
  here; it is simply an entire dimension the offscreen platform does not have.
- **`QGraphicsScene.clear()` and a stale wrapper.** `show_page` clears the
  scene and every dict that held its items, which is exactly right, so this
  one looks handled — but it is the other classic, and preview is the code
  path that calls it most.

The first task is therefore still not a fix: read the trace the next crash
leaves, and let that pick from the list rather than picking by argument. The
failed reproduction above is why — an afternoon of plausible reasoning
narrowed this less than one captured trace will.

**The log file.** `review` configures logging exactly as the CLI does —
`basicConfig` onto stderr — and a window launched from Finder, or from a
bundle once 4.10 lands, has no stderr anyone will ever read. Every
`log.warning` about a skipped page, every `log.exception` from a worker
thread, is already being written and thrown away.

Add a file handler alongside the stream one. On macOS the place a user and
Console.app both look is `~/Library/Logs/comictrans/`, which
`QStandardPaths` has no enum for; `AppDataLocation` is the portable answer
and the wrong one on the target platform. Recommend the macOS convention
with `AppDataLocation/logs/` as the fallback elsewhere, and rotate it —
`RotatingFileHandler`, a megabyte or so, a couple of backups — so it cannot
grow without bound on a machine nobody tidies.

**A log record survives a segfault.** `logging.FileHandler` flushes on every
record, measured: a process that logs a line and then dereferences null exits
139 with the line on disk. So the ordinary log is a second, independent trace
for a hard crash — log the risky thing *before* doing it, at debug level, and
a log that ends mid-page names the page even when `faulthandler` cannot say
why. The two answer different halves: `faulthandler` says where the process
was, the log says what it was trying to do.

`faulthandler` already has its own file and keeps it: it writes from a signal
handler and must not contend with the logging module's locks. The application
log is the second, wider half — every `log.warning` about a skipped page, and
the tracebacks of exceptions that did *not* kill the process.

**Nothing is ever sent anywhere.** "Crash report" normally means telemetry;
here it means a file on your own disk that you may choose to attach to
something. No network calls anywhere in the pipeline is an invariant, and it
does not stop being one because the payload is a stack trace.

**The log must not contain the comic.** `source_text` and `translation` are
the user's material, and a log that dumps region text is both a privacy
problem and enormous. Region ids, image names, counts, exception types and
tracebacks — not content. Worth a test, because the easy way to write a log
line is to interpolate the object that has the text in it.

**Three hooks, once, at startup.** `sys.excepthook` for the main thread,
`threading.excepthook` for anything the worker threads do not catch
themselves (`RunJob` already catches, but its own handler could raise), and
`qInstallMessageHandler` so Qt's warnings land in the same file instead of
the terminal. The last one is likely to be the most informative of the three:
Qt says a good deal about layouts and dangling objects that nobody currently
sees.

**A swallowed exception is not a success.** Once the hooks exist, decide what
the window does after one. Carrying on silently is what happens today and is
the worst option, since the document may be half-edited. The cheap answer is
a status-bar line and a log entry — "something went wrong; see the log" —
which at least matches what the user experienced. The expensive answer is to
work out per site whether the state is recoverable. Start cheap.

**Then the audit.** The window's own call sites are patchy rather than
missing: `_on_region_drawn` guards `sample_region_colors`, and
`_merged_colors` calls the same function unguarded; `_on_rescan_fonts` calls
`fonts.available_families()` with nothing around it; `_on_image_selected` and
`_on_render_preview` both catch `ComictransError` and are the model to follow.
That is a starting list, not the list — the pass is to walk every slot and ask
what it does when the thing under it raises.

Two rules for the pass. A `ComictransError` is an expected failure and gets a
message the user can act on; anything else is a bug and gets logged with its
traceback. And a failure must leave the document either unchanged or
consistent — never half-edited, since undo is a stack of whole plans and a
partial edit poisons it.

**Somewhere to find it.** A **Help > Open Log Folder** item, so attaching a
log to a bug report is one click rather than a paragraph of instructions.
Cheap, and it is what makes the rest of this milestone useful to anyone but
the person who wrote it.

**What this is not.** Not a crash-reporting service, not telemetry, not
automatic issue filing, and not a general refactor of the pipeline's error
handling — `extract` and `apply` already report through `ExtractReport` and
`ApplyReport`, and the CLI's exit codes and stderr behaviour must not move.
This is about the window.

**Testing.** The hooks are testable: point the file handler at a `tmp_path`,
raise from a slot, and assert the traceback landed in the file and the comic
text did not. The audit is testable one guarded site at a time, by making the
thing under it raise. What is not testable is the segfault, which is why the
log matters.

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
  menu where it belongs. Preferences already carries `PreferencesRole` and
  `QKeySequence.StandardKey.Preferences`, set when 4.13 shipped; like the
  rest of this milestone, neither has been seen working on a Mac
- Dark mode: check the canvas overlay colours stay legible against dark
  chrome
- Full screen, and the unified toolbar look

**None of this is verifiable by the test suite.** The widget tests run
under the offscreen platform on whatever machine is to hand; menu bar
placement, the About role and dark mode only exist on a real Mac. This is
the one milestone the four checks cannot defend, and it has to be looked at
by hand on the target machine.

## 4.7 Help instructions

A short in-application guide to reviewing a plan: what the badge colours
mean, what each flag means, what preview does and does not tell you.

After the milestones that change the UI, because it documents them, and
after 4.8 in particular. That one collides with this one directly rather
than vaguely: toolbar icons replace the text labels help would otherwise
name, About moves into the application menu so "Help > About" stops being
where it is, and dark mode is explicitly about whether the canvas overlay
colours still read — which is the first thing on the list above.

A dialog with a `QTextBrowser` over a bundled document, rather than strings
in the source, keeps 4.9 to one file per language.

**One caveat on the order.** 4.8 is the milestone the test suite cannot
defend and the only one that needs hands on a Mac, so it is the one most
able to sit. If it does, do not hold help behind it — the numbers are names,
not positions, and most of what help has to say is about the plan and the
canvas rather than the chrome.

`README.md` is not a substitute; it is written for the command line.

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
