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
| 4.20 | Recently opened files | S |
| 4.21 | Preview says it is working | S |
| 4.23 | Page order | S–M |
| 5 | Validate a plan file | S |
| 4.8 | macOS look and feel | S — mostly shipped |
| 4.7 | Help instructions | S–M |
| 4.9 | Localisation | M |
| 4.10 | Package as an application | M |
| 3 | CBZ, PDF and CBR input | L |
| 8 | CBZ and CBR output | M |
| 4.22 | Preview off the main thread | M |
| 6 | PDF output | L |
| 7 | Security audit | M |

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
one would have meant doing that work a second time. Help text describes the UI, so it goes
after the UI stops moving — which meant after 4.8, since replacing a text
toolbar with icons changed what there was to describe. That change has now
landed, so nothing in 4.8's remainder holds help back.

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

## 4.20 Recently opened files

A **File > Open Recent** submenu, with **Clear Menu** at the bottom of it
the way macOS applications have one.

**It makes an existing claim false in three places, and all three have to be
fixed in the same change.** `README.md`, `docs/ARCHITECTURE.md` and
`gui/app.py` each say the window layout is the only thing this tool stores
outside a plan file. That stopped being true when 4.13 shipped, since
preferences write to the same `QSettings`; a recent-files list makes it a
third. The documents are wrong now rather than because of this milestone —
this is just the milestone that cannot avoid noticing.

**A recent list is a small privacy surface**, which is what Clear Menu is
for. It remembers the paths of everything opened, and for this tool that is
a list of which comics someone has been working on. Its own key rather than
being folded in with the layout, so clearing it clears it.

Two rules to settle before writing it: what an entry does when its file has
moved or been deleted — offered and reported, or dropped silently as the
menu is built — and how many entries to keep.

## 4.21 Preview says it is working

`render_preview` runs synchronously on the main thread —
`_on_render_preview` calls it directly — so on a large page the window is
frozen for the duration and nothing on screen says why. A wait cursor and a
line in the status bar while it runs.

**Deliberately the cheap version.** Doing it properly is 4.22, and that is
a separate milestone rather than the obvious way to write this one because
of what it would cost: entry 5 in `known-bugs.md` rules out every
thread-related explanation for the vanishing window on the grounds that
preview is synchronous and touches no worker thread. This milestone leaves
that elimination standing.

It also does nothing about the other suspect in that entry — three copies
of an eleven-megapixel page per preview — which threading would not fix
either.

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

## 4.8 macOS look and feel

Qt supplies the native style, the native menu bar, and Cmd for Ctrl through
`QKeySequence.StandardKey`, so less of this was missing than it appeared.
Most of the rest has now shipped: the toolbar is icons, drawn for this tool
and tinted to the palette; the application has an icon of its own, set on
the `QApplication` and shown in the About box; `AboutRole` puts About in the
application menu and Preferences already carried `PreferencesRole` from
4.13; the unified title-and-toolbar look is asked for; and the one thing
dark mode was actually breaking — the mark on a font name this machine
cannot resolve, measured at 1.5:1 against a dark base — now picks its red
from the palette and clears 4.5:1 both ways round.

Two things are left.

- **An `.icns` icon and bundle identity.** The artwork exists now —
  `resources/appicon/` holds the master and the derived icon, and Qt shows
  it wherever it can — so what is left is the conversion and the bundle to
  put it in, which is 4.10's. On macOS the Dock reads the bundle rather
  than `setWindowIcon`, so until then the icon is visible everywhere except
  the one place a Mac user looks first.
- **Confirmation on a real Mac.** Everything above is set from code and
  none of it has been seen working: the menu bar placement and `AboutRole`,
  the unified toolbar, full screen, and the icons against real dark-mode
  chrome rather than a palette a test set for itself.

**The suite still cannot defend the second one.** What the tests added here
check is that every drawing an action asks for ships, that each renders at
every baked size, that it comes out in the colour it was asked for, and
that a palette change repaints the set — all under the offscreen platform,
on whatever machine is to hand. None of that is evidence about where macOS
puts a menu item. That step needs hands on the target machine, and it is
the only part of this milestone the four checks were never going to cover.

## 4.7 Help instructions

A short in-application guide to reviewing a plan: what the badge colours
mean, what each flag means, what preview does and does not tell you.

After the milestones that change the UI, because it documents them, and
after 4.8 in particular. That one collided with this one directly rather
than vaguely: toolbar icons replaced the text labels help would otherwise
have named, About moved into the application menu so "Help > About" stopped
being where it is, and dark mode was explicitly about whether the overlay
colours still read. All three have now landed, so the collision is spent —
what is left of 4.8 is a bundle icon and a pass on a Mac, neither of which
changes what help has to say.

A dialog with a `QTextBrowser` over a bundled document, rather than strings
in the source, keeps 4.9 to one file per language.

**One caveat on the order.** What remains of 4.8 is the part the test
suite cannot defend, and it needs hands on a Mac, so it is the item most
able to sit. Do not hold help behind it — the numbers are names, not
positions, and most of what help has to say is about the plan and the
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

The bundle needs Pillow, numpy and OpenCV whatever else happens: the review
window renders previews through `render_page`, which erases and typesets
like any other page. Since 4.6 it needs pyobjc-Vision too — extract runs from
the window now, so the recogniser is part of the application rather than
something only the command line reaches.

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
package, so it cannot be declared in `pyproject.toml`, and 4.10 has to
decide whether to bundle it or require it and degrade politely when it is
absent. Bundling is the awkward half: the unrar licence is not OSI-free,
which is a real question for an MIT project. Behind the sidecar decision
above, a CBR reader is one more unpacker and nothing else. Writing one is
a different matter, and 8 covers it.

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
already licensed. So the shape here is: look for `rar` on `PATH`, offer
CBR when it is there, and when it is not, say that it is missing and what
would provide it rather than silently omitting the option. Never bundle
it. That keeps this an MIT project and still gives anyone with a licence
the format they asked for.

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

## 4.22 Preview off the main thread

What 4.21 papers over, done properly: `render_preview` on a `RunJob`, so
the window stays live while a page renders and the render can be called
off. The harness exists — 4.14 and 4.6 built it, and by now it is a base
class and one `work()` method.

**Gated on entry 5 in `known-bugs.md`, and it rewrites that entry.** The
reasoning there rules out a `QThread` destroyed while running, Vision on a
thread, and the job read after `deleteLater`, all on the grounds that
preview is synchronous. Putting preview on a thread retires that reasoning,
so the entry has to be rewritten in the same commit — `CLAUDE.md` does not
allow a recorded behaviour to change quietly. Better done once that crash
is understood or closed than while it is still an open hunt, since
otherwise this widens the search space for a bug nobody has reproduced.

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
