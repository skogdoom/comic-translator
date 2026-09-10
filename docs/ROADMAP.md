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
| 4.8 | macOS look and feel | S — mostly shipped |
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
