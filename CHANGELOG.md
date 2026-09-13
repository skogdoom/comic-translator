# Changelog

Release notes, so that what changed between two versions lives somewhere
other than `git log`. Newest first.

What a release *is* here is unusual enough to say once, at the top. There is
no download. The bundle `tools/build_app.py` produces is unsigned — no
Developer ID, no notarisation, no stapling — so Gatekeeper quarantines it on
any machine but the one that built it. A release is therefore the repository
at a tag, a documented build command, and an application each person makes
for themselves. See **Build the application** in `README.md`.

## 1.1.0 — unreleased

- **The preview renders on a worker thread.** The window stays yours while a
  page renders: keep reading, keep typing, change page. Switching page stops
  the render between regions rather than waiting it out, and a bar in the
  status bar fills as each region is erased. `apply` is untouched: it still
  stops between pages, never inside one.
- **Looking at the same page twice costs one render.** The last rendered page
  is kept and reused while nothing it was rendered from has changed — three
  toggles of an eleven-megapixel page went from three renders and 34 seconds
  to one and 13.
- **The window speaks Swedish.** Every word of the interface is translated
  from a catalogue now. Pick a language in Preferences — it applies the next
  time the application starts, and it says so when you choose one — or leave
  it to the machine, which is what a Mac set to Swedish throughout gets.
  `COMICTRANS_LANGUAGE` overrules both. The guide the Help menu opens follows the same language. The command
  line, the plan file's content, and what the pipeline says when something
  goes wrong are deliberately not translated. Swedish is the one translation
  that ships; the English one holds nothing but plural forms, so a window
  says "1 page" rather than "1 page(s)".
- **The toolbar's icons are the size they were always meant to be.** On a
  Retina screen every one of them had been drawn at half scale and anchored
  up and to the left of its button — 14 points of ink in a 42-point button
  where 27 were asked for. The drawings were also refitted to one grid while
  this was being chased: all fifteen now fill the same box and sit in the
  middle of it, where they had ranged from 14 units across to 22 and sat as
  much as 2 off centre. The bar's height is unchanged, and it no longer
  carries a drag grip, which no Mac toolbar does.

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
- **`comictrans validate <plan>` says what is wrong with a plan without
  rendering it.** For the moment before a long run: the schema, every page
  still being the one that was extracted, every polygon fitting on its own
  page, and every font the plan names resolving on this machine — bold face
  included, which is the failure this is really for, since nothing in a plan
  file can record whether a font exists here. It prints every problem it
  finds rather than stopping at the first, exits 1 if there is one, and
  writes nothing. It checks what it checks by calling the same functions
  `apply` calls, so what it accepts is what `apply` accepts.

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

- **Preferences has an "experimental features" switch**, off, with nothing
  behind it. It is there so the first piece of unfinished work has a flag to
  hang off and a settled place in the window rather than one being added in a
  hurry beside the feature it gates.

- **Quitting the review window no longer crashes.** It segfaulted on the way
  out, after the window had gone, while Python was shutting down — PySide
  destroys whatever Qt objects are still alive at that point, and one of them
  was a dialog whose child had already been freed. The window is now taken
  down deliberately when the application ends, and a dialog is disposed of
  when you are finished with it rather than kept on the window: opening
  Preferences three times used to leave three of them behind, each holding
  its widgets, and a render dialog holding a whole plan.

- **Ctrl+Z works inside the text fields.** It did not: the field claimed the
  key and, having no undo of its own, did nothing with it, so nothing
  happened at all until you clicked somewhere else first. It is the same undo
  as everywhere else in the window — one history over the whole plan — and
  typing is now taken back a word at a time rather than everything since you
  entered the field.
- **Click a balloon and start typing.** Clicking a region puts the caret in
  its translation. Getting there any other way leaves the keyboard on the
  page, where the arrow keys nudge the region; **Escape** hands the page back.

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
