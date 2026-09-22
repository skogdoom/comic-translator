# Changelog

Release notes, so that what changed between two versions lives somewhere
other than `git log`. Newest first.

What a release *is* here is unusual enough to say once, at the top. There is
no download. The bundle `tools/build_app.py` produces is unsigned — no
Developer ID, no notarisation, no stapling — so Gatekeeper quarantines it on
any machine but the one that built it. A release is therefore the repository
at a tag, a documented build command, and an application each person makes
for themselves. See **Build the application** in `README.md`.

## 1.2.0 — unreleased

Where a shipped milestone earns its line, until there is a release to put it
in.

**The plan format has moved.**
Version 4. The reader also accepts versions 1 and 2 and 3 and upgrades them on
the way in, so a plan written by any earlier build opens with its translations
intact. Opening one and saving it writes version 4, and that is the whole of
what changes: every field version 4 adds is optional and defaults to the value
that means "as before", and the writer leaves out each one at that value — so a
plan that says nothing new is byte for byte the file it was.

- **A region can be marked finished, and then left alone.** **Lock Region** —
  Ctrl+L, the checkbox in the Region panel, or the balloon's own right-click
  menu — says this one is done. A locked region's fields go dead, its outline
  cannot be reshaped or nudged, and Delete and Merge stop being offered; the
  lock itself stays live, because it is the way back out. It is not `skip`,
  and the labels say so: `skip` means do not letter this region, `locked`
  means do not change it, and a locked region is lettered exactly as any
  other. The lock lives in the plan rather than in this machine's settings,
  so it travels with the translations it applies to — **and it survives a
  re-extraction**: extract over the same pages again and a locked region
  comes through exactly as you left it, outline and colours and text,
  instead of being rebuilt around the fresh reading. That is most of the
  reason to want one.
- **Every field the roadmap still wanted, added at once.** A region may now
  carry `locked` (finished, not to be edited), `angle` (a tilt in degrees) and
  `stroke_color` (an outline around the lettering, or none); the header may
  carry the chapter's own details — series, title, volume, number, year,
  publisher, writer, and which way the pages read. **Nothing reads any of them
  yet.** They arrive together because the reader rejects unknown keys on
  purpose, which makes each added field a compatibility break: four of them
  added separately would be four version bumps, four migrations, and four
  windows in which two builds disagree about what a plan may contain. Done
  once it is one of each, and the milestones that will use them — locking a
  region, lettering a tilted one, a sound effect over the artwork, and the
  chapter metadata that `ComicInfo.xml` and EPUB both need — each arrive to
  find their field already there.

## 1.1.0 — 2026-09-18

A chapter now goes in and comes out as one file, the window speaks Swedish,
and a page renders in a fraction of the time it used to. Plugins are here as
an experiment, off until asked for. The rest is a week of using 1.0.0 in
earnest and fixing what that turned up: the keyboard behaving as a keyboard,
dialogs that fit on the screen, and a crash on quit that is gone.

**The plan format has not moved.** It is still version 3, so a plan written
by 1.0.0 opens in this one with its translations intact and nothing to
convert. Nothing about how the tool is installed or run has changed either —
see **Build the application** in `README.md`, which is what a release is here.

### Chapters as one file

- **A chapter can arrive as one file.** `comictrans extract chapter.cbz`
  reads CBZ, CBR and PDF by unpacking them into a folder of pages beside the
  file — `chapter-pages/` — and everything after that is an ordinary folder
  of images, so the plan, the window and the per-page hash check all work
  exactly as they did. Pages keep the order they had inside the container,
  carried in the filenames. Unpacking the same chapter twice writes nothing
  the second time, and a file already there holding something else stops the
  run instead of being overwritten. A PDF gives up the image on each page
  byte for byte rather than being rasterised, so nothing is resampled; a page
  that is not one photograph is named and skipped. CBR needs a RAR tool that
  is already on the machine — `unrar`, `unar`, `bsdtar` or `7z`, or
  `COMICTRANS_UNRAR` pointing at one — because unrar's licence is not one an
  MIT project can redistribute. The unpacked folder has to hold that chapter
  and nothing else page-shaped, and the run stops rather than letting a
  re-release leave its old pages in with the new ones. A page whose one image
  cannot be a photograph of it — a logo on a born-digital page — is unpacked
  and flagged for you to look at.
- **The window opens a chapter file as well.** **Extract Pages** takes one
  through **File…**, the same panel as a single page: it unpacks on the worker
  thread, says what it is unpacking until it knows how many pages there are,
  and can be stopped between pages like any other run. The plan goes in with the pages,
  which is where the command line puts it too. Where `unrar` lives is in
  Preferences, because an application opened from the Finder cannot see a
  Homebrew one. Which of the three formats a chapter file is, its first bytes
  decide rather than its name: a CBR that is really a zip, or a CBZ that is
  really a RAR, is read as what it is.
- **A chapter can leave as one file too.** `comictrans apply plan.yaml
  --output chapter.cbz` renders the chapter and writes it as one archive
  instead of a folder of pages; `.cbr` does the same as a RAR, and any other
  name is a directory exactly as before. Pages are numbered inside the file in
  the order the plan holds them — `001-page-004.png` — so a reader shows them
  in the order you set rather than the order they were scanned. **Render
  Pages** in the window asks the same thing as a box beside the path: pick
  `.cbz` and the path is renamed, type a `.cbz` path and the box follows.
  Cancelling differs from a folder run and deliberately: a stopped folder run
  leaves the pages it finished, a stopped chapter run leaves nothing at all,
  because the file is packed once every page is drawn rather than grown as
  they arrive. CBZ needs nothing. CBR needs the `rar` compressor that comes
  with WinRAR — `unrar` cannot write archives, and rar is paid and not
  redistributable — so it is looked for on `PATH`, `COMICTRANS_RAR` names one
  elsewhere, and Preferences has a field for it beside the one for `unrar`.
  You are told a `.cbr` cannot be written before a page is rendered rather
  than after the chapter. Nothing else goes into the file: no ComicInfo.xml,
  because a plan knows the language pair and nothing else about your chapter.

### Checking a plan without rendering it

- **`comictrans validate <plan>` says what is wrong with a plan without
  rendering it.** For the moment before a long run: the schema, every page
  still being the one that was extracted, every polygon fitting on its own
  page, and every font the plan names resolving on this machine — bold face
  included, which is the failure this is really for, since nothing in a plan
  file can record whether a font exists here. It prints every problem it
  finds rather than stopping at the first, exits 1 if there is one, and
  writes nothing. It checks what it checks by calling the same functions
  `apply` calls, so what it accepts is what `apply` accepts.

### Speed

- **Rendering a page costs a fraction of what it did.** Erasing a region used
  to compute a colour distance for every pixel of the page to decide a mask a
  balloon wide: measured on an eleven-megapixel page, 386MB and 1.3 seconds
  for a region covering 3.4% of it. It now works inside a window around the
  region — 37MB, of which 33MB is the copy of the page it hands back, and
  0.083 seconds. Ten regions of that page took 13.1 seconds and take 0.8. The
  pages that come out are identical, which was checked against every fixture,
  four regions on each, all four erase strategies, hashed before and after.
  `apply` gets this as much as the preview does; they call the same function,
  which is why they still do.
- **The preview renders on a worker thread.** The window stays yours while a
  page renders: keep reading, keep typing, change page. Switching page stops
  the render between regions rather than waiting it out, and a bar in the
  status bar fills as each region is erased. `apply` is untouched: it still
  stops between pages, never inside one.
- **Looking at the same page twice costs one render.** The last rendered page
  is kept and reused while nothing it was rendered from has changed — three
  toggles of an eleven-megapixel page went from three renders and 34 seconds
  to one and 13.

### Languages

- **The window speaks Swedish.** Every word of the interface is translated
  from a catalogue now. Pick a language in Preferences — it applies the next
  time the application starts, and it says so when you choose one — or leave
  it to the machine, which is what a Mac set to Swedish throughout gets.
  `COMICTRANS_LANGUAGE` overrules both. The guide the Help menu opens follows the same language. The command
  line, the plan file's content, and what the pipeline says when something
  goes wrong are deliberately not translated. Swedish is the one translation
  that ships; the English one holds nothing but plural forms, so a window
  says "1 page" rather than "1 page(s)".
- **Seven things the window said only in English.** The Extract dialog's
  three file panels were titled in English whatever language the window was
  in, along with the kinds of file they offered; so were the line saying a
  region a report names has since been deleted, and the word *order* beside
  a region's id. Each had a translated twin doing the same job a few lines
  away, which is why nothing looked wrong. They are in the catalogue now,
  and in Swedish — and a test walks the widget code for the next one, since
  an English window looks right whatever a string did or did not go through.

### Working on a page

- **The window can read one balloon off the page.** **Edit > Extract Text
  from Region…** runs the recogniser over the selected region and puts what it
  reads into the source text — for a region you drew by hand, which has no
  reading, and for one whose lettering the first pass read badly. It reads
  that region rather than the whole page: a crop of the outline with a margin
  round it, measured to be worth having (a crop cut to the outline agreed with
  a full-page reading 10 times out of 31; the same crops with a margin, 27),
  with anything the margin lets in from the next balloon dropped. It runs
  in the background like the other two passes and is one undo step. A region
  with neither a reading nor a translation — one you have just drawn — gets
  both, the way a whole extract seeds them; one that already has text is asked
  about first.
- **Right-click a region — Control-click on a Mac with one button — for a
  menu of what can be done to it.** Extract its text, or edit its shape,
  offered nowhere new: the menu reuses the same two commands already on the
  Edit menu. It selects the region under the pointer first, the same as a
  plain click would, and only appears while simply looking at the page —
  reshaping, drawing and merging already give a click a meaning of their
  own, and this does not contradict it.
- **Enter finishes reshaping a region, the same as it already finished
  drawing one.** Edit Region Shape had no keyboard way to turn itself back
  off; every edit it makes is already on the region as it is made, so there
  is nothing left for Enter to do but leave the mode. Turning on a tool
  mode — Edit Region Shape, Add Region, Merge Region — now also takes
  keyboard focus off whatever field a moment ago selecting the region put
  it in, so Esc and Enter reach the mode they are meant for instead of
  landing in the translation as a stray keystroke.
- **Clicking a balloon, or stepping to one, no longer interrupts typing.**
  With the caret in source text, translation or notes, clicking a region and
  Next, Previous and Next Flagged Region all now carry it into that same
  field of the region you land on, caret at the end, even one on another
  page. With nothing focused, stepping leaves the keyboard on the page,
  where the arrow keys nudge the region; a click puts the caret in the
  translation instead, since it means you are about to type. **Escape** hands
  the page back either way.
- **The text fields no longer swallow the window's keyboard shortcuts.** A
  text field tells Qt it wants a key whether or not it has any use for it, so
  with the caret in a translation, Ctrl+Z did nothing and — on a Mac, where
  they are also "go to the start and end of the document" — Cmd+Up and
  Cmd+Down stopped walking between regions. A key this window has bound now
  belongs to the window wherever the caret is; everything a text field
  actually uses, it keeps. **Tab** walks out of the prose fields instead of
  being typed into them, and the caret lands at the end of the text rather
  than in front of it.
- **Ctrl+Z works inside the text fields.** It did not: the field claimed the
  key and, having no undo of its own, did nothing with it, so nothing
  happened at all until you clicked somewhere else first. It is the same undo
  as everywhere else in the window — one history over the whole plan — and
  typing is now taken back a word at a time rather than everything since you
  entered the field.
- **Undo shows you what it is undoing.** Holding Ctrl+Z down in one balloon
  carries on past your typing into whatever was done before, which may have
  been an edit to another region on another page — and you would not have
  seen it happen, since the panel was showing this one. The step still
  happens, in order, but the region it is about is now selected first, so it
  is on screen as it changes.

### Windows and dialogs

- **Quitting the review window no longer crashes.** It segfaulted on the way
  out, after the window had gone, while Python was shutting down — PySide
  destroys whatever Qt objects are still alive at that point, and one of them
  was a dialog whose child had already been freed. The window is now taken
  down deliberately when the application ends, and a dialog is disposed of
  when you are finished with it rather than kept on the window: opening
  Preferences three times used to leave three of them behind, each holding
  its widgets, and a render dialog holding a whole plan.
- **Help text under a field is no longer cut off.** The grey line under a
  control, and the red one that says why a render cannot start, were being
  allocated the height they asked for rather than the height they turned out
  to need at the width the form gave them — so the last line went missing and
  the row below was drawn over what was left. It showed on macOS and nowhere
  else, because the form column is narrower there and the system font wider.
  The preferences dialog had it in three places and the render dialog in two.
  A field whose placeholder is longer than the box, `same as the sourc…`, is
  now wide enough to read as well.
- **The render dialog says where to set the rar path, in one sentence.** The
  message about a missing compressor was the pipeline's: a licence
  explanation naming `COMICTRANS_RAR`, which is the command line's answer and
  no use in a window that has a field for it. It now says what to do instead
  — set it in Preferences, or save as .cbz — and the licence is explained
  where the field is. A path that was set and does not work still says which
  path, because that is the useful half.
- **Preferences fits on the screen.** The notes under the two chapter-file
  fields were five lines each and are two; all three notes now wrap at the
  same width rather than one spanning the window and the next stopping half
  way; and the settings scroll between a fixed heading and a fixed **Done**,
  so the window is capped at the display however long the text runs in
  whatever language.
- **The toolbar's icons are the size they were always meant to be.** On a
  Retina screen every one of them had been drawn at half scale and anchored
  up and to the left of its button — 14 points of ink in a 42-point button
  where 27 were asked for. The drawings were also refitted to one grid while
  this was being chased: all fifteen now fill the same box and sit in the
  middle of it, where they had ranged from 14 units across to 22 and sat as
  much as 2 off centre. The bar's height is unchanged, and it no longer
  carries a drag grip, which no Mac toolbar does.

### Plugins, experimental

- **Plugins, experimental.** Preferences ▸ this window ▸ experimental
  features turns on a Plugins menu that runs a plan-in, plan-out folder of
  Python — one folder per plugin, so it can be more than one file — a
  translation, a note, a flag, anything on a region already in the plan, as
  one undo step, never a region added, removed or moved. One example is
  already there the first time you turn this on: it adds a note to every
  region. Off by default, and always third-party code once turned on — see
  the disclaimer in `README.md`.
- **Plugins can be configured, and turned off one at a time.** Plugins ▸
  Configure Plugins… lists what is installed — a plugin that failed to load
  too, greyed, with its own error rather than just being absent — and lets
  you edit whatever it declared as settings, or untick it to leave it out of
  the Plugins menu without removing it. A plugin that declares nothing to
  configure just says so. Turning one off costs no re-import of anything;
  neither does changing a value, which the next run simply reads fresh.
- **A plugin can declare its own version and the oldest comictrans it
  needs.** `PLUGIN_VERSION` is a plain string, shown in Configure Plugins
  and read as nothing else. `REQUIRES_APP_VERSION` is checked at discovery
  time: a plugin asking for a newer comictrans than this build fails to
  load, with that said plainly rather than left to guess at — the same
  `FailedPlugin` a broken one already becomes. Both are optional; a plugin
  that declares neither behaves exactly as one always has.
- **Preferences has an "experimental features" switch**, off, with nothing
  behind it. It is there so the first piece of unfinished work has a flag to
  hang off and a settled place in the window rather than one being added in a
  hurry beside the feature it gates.

### Security and code quality

- **A security audit, and the one thing it changed.** A reading of all 75
  modules for what somebody else's file is allowed to do here: a chapter that
  arrives as a CBZ, CBR or PDF, and a plan file passed between translators.
  Most of what it looked at was already sound and is now held to by a test —
  an archive entry cannot name its way out of the directory it unpacks into,
  a symlink is not a page, a plan file cannot construct a Python object, and
  no plugin is imported until experimental features are switched on. What was
  not sound: an archive could declare a page larger than memory and be
  believed. Measured at 1029 to 1 — 255KB of CBZ delivering 256MB on one
  member — which makes a 4MB file a 4GB allocation. An entry claiming more
  than half a gigabyte is now skipped and named in the report, decided from
  the header before anything is decompressed. The whole reading is written up
  in `docs/SECURITY.md`, including what was measured and deliberately left.
- **"No network calls anywhere in the pipeline" is a check now.** It was an
  invariant in `CLAUDE.md` that nothing enforced. It is checked twice, since
  one check cannot do it: nothing under `src/comictrans` may import a module
  that speaks to another machine, read off the source so a branch no test
  reaches is covered too; and both passes run end to end with `socket`
  unusable, which is the only way to catch a dependency doing it on this
  tool's behalf.
- **`tools/audit_dependencies.py`**, for the half of an audit that has to be
  re-run: the versions `uv.lock` pins, against published advisories. It
  reports what a bare `pip-audit` run does not — how many packages it
  actually audited out of how many the lock names, and the versions of
  `unrar`, `rar` and `tesseract`, which no lock describes. That count earned
  itself immediately: asked the ordinary way, `pip-audit` drops a requirement
  whose platform marker excludes the machine, and one already present in the
  environment it builds to resolve in, and says nothing about either. The
  first hid every `pyobjc` package on Linux; the second hid `packaging`
  everywhere, because `pip-audit` depends on `packaging`. Both are the
  resolver's doing, so it is turned off and the markers come off with it,
  which reads 18 of 18 from any machine. It is the only thing in the
  repository that uses the network, and it is not part of the tool.
- **A reading of the whole codebase for what the tests cannot see.** One
  rule per place it is decided: resolving a page's path against its plan was
  written out five times, the overlap test the window flags with and the one
  `apply` warns with were two copies of one number, the four erase modes were
  named in three dialogs and translated twice over, and the quiet grey under
  a field was mixed in four. Comments that described an earlier version of
  the code went — among them one promising the review GUI as a future thing
  and one saying nothing yet read the experimental-features switch. The guide
  no longer claims the outline colours are the same two the debug overlays
  draw; they mean the same things in shades picked for what each sits on.
  Nothing about what the tool does changed, deliberately: a quality pass that
  also fixes behaviour is two changes wearing one commit message.

### Known limitations

- **macOS will not give the application its own language** — System Settings
  > General > Language & Region says the application supports no additional
  languages, for a bundle that declares two every way macOS documents.
  Choose the language in the window's own Preferences instead; nothing about
  the translation itself is affected.

## 1.0.0 — 2026-09-11

The first release. Nothing before this was tagged, so this is the whole tool
rather than a list of changes.

### What it does

Translate scanned comic pages from one language into another in two passes,
with the translation done by hand in between.

`extract` reads a folder of pages, finds the lettering, and writes a plan
file: one entry per region, carrying the outline it traced, the fill and text
colours it read off the page, and the text it recognised. It writes no
images.

You then translate — in an editor, or in `review`, which opens the plan
beside the pages it describes and lets you fix an outline, draw one that was
missed, merge two tracings of the same balloon, and see a real render of the
page before committing to one.

`apply` draws the plan onto copies of your pages. Every decision it makes
comes from the plan file, so changing one translation and running it again
changes that text and nothing else.

### What it will not do

These are the shape of the tool rather than things not got to yet.

- **It never writes to your pages.** They are opened read-only. The only
  things ever written are the plan file, `--debug-dir`, and `--output` — and
  the last two refuse to write inside the source tree, with no override.
- **It makes no network calls, anywhere.** Translation is manual by design,
  and neither pass, nor the window, nor the crash log, reaches anything.
- **It never translates for you.** No machine translation is called or
  bundled.
- **It never invents a font.** A plan names a family; if it cannot be
  resolved with a real bold face, the region is left alone and named in the
  report rather than rendered in something else. Emphasis is bold, never
  synthesised, and never the oblique face.
- **A region it cannot render is left completely alone** — not erased, not
  half-drawn — and named in the report.

### Requirements

macOS on Apple Silicon and Python 3.12. Apple Vision does the OCR, through
pyobjc, and installs automatically on macOS; Tesseract is an optional
fallback and is noticeably worse on comic lettering. The `review` window
needs PySide6, which is an extra rather than a requirement — the command line
works without it.

Most of the tool is platform-independent and its test suite runs anywhere.
The OCR and the application bundle are not.

### The plan file

Version 3. The reader also accepts versions 1 and 2 and upgrades them on the
way in, so a plan written by an earlier build opens with its translations
intact. It writes version 3, so opening and saving an older plan moves it
forward.

The format is YAML with a stable key order, and the comments you add to it
are preserved. It is meant to be read, edited by hand, diffed and handed to
somebody else; the window is one way to edit it and not a privileged one.

### Known limitations

Every one of these has been found, reproduced and measured, and then left
alone deliberately, because fixing it needs a decision rather than a patch.
`known-bugs.md` carries the measurements and the reasoning; this is the
summary.

- **Re-wrapping turns the source's soft hyphens into word breaks** — a
  hyphenated line break in the recognised text renders as two words. Only
  visible while the seeded source text is still in place; a real translation
  replaces it.
- **Erase only reaches ink inside the polygon** — original lettering that
  falls outside a region's outline survives and the translation is drawn over
  it. Mostly a Tesseract problem: Apple Vision read the measured case in
  full.
- **A solid caption box is detected as a padded text box, not as the box** —
  a filled caption box comes back as an approximate polygon covering about
  60% of its width. Nothing is damaged; the typesetter simply works with less
  room than the art offers.
- **A long region id sets a floor under the Region panel's width** — region
  ids are built from the page filename, and a long one stops the panel from
  being made narrow. Space only: nothing is hidden and no plan data is
  affected.

### Not in this release

Two improvements were deliberately held back rather than rushed in front of
it. Rendering the preview on a worker thread would put threading into the one
code path that has produced a segfault in this project, for a benefit the
wait cursor already largely delivers. And localisation would freeze every
string in the interface just as the milestones after this one start adding
more. Both are in `docs/ROADMAP.md`.
