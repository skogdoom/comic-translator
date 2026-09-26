# Security

Two halves, kept apart because they age differently. The **dependency check**
is a thing to re-run, and goes stale the moment somebody publishes an
advisory; the **code reading** is a thing that was done, against a tree that
is named, and does not repeat itself.

This is a different list again from the two the repository already has.
`known-bugs.md` records defects left alone on purpose; **Known weak points on
real scans** in `ARCHITECTURE.md` records what detection does badly. Neither
is about what somebody else's file can make this tool do, which is what this
is.

## What is trusted, and what is not

A local tool with no accounts, no server and no network has a short list.

**Not trusted.** A chapter file — CBZ, CBR or PDF — arrives from wherever
chapters arrive from, and is handed to an unpacker without being understood
first. A plan file is passed between translators and is a document this tool
parses. Both are somebody else's bytes.

**Trusted, and deliberately.** The person at the keyboard: the paths they
type, the `rar` binary they name in Preferences, and the plugin folder they
drop Python into. Each of those is the user acting as themselves on their own
machine, and a check that second-guessed one would refuse an ordinary
Homebrew install without stopping anybody who can already write to their own
`PATH`.

**Out of scope.** Multi-user isolation on a shared Mac. Files are written with
the process umask and the log holds the paths of what was rendered; anyone
who can read another user's home directory can read their comics too.

## Running the dependency check

```
uv run --with pip-audit tools/audit_dependencies.py
```

It reads the versions `uv.lock` pins, asks OSV about each one, and prints
what it found. **This is the only thing in this repository that uses the
network, and it is not part of the tool** — nothing under `src/comictrans`
may reach out, and `tests/test_security.py` fails if any of it ever starts.

Two things it prints that a bare `pip-audit` run does not, both worth reading
rather than skipping to the last line:

- **How many packages it actually audited, out of how many the lock names.**
  Asked the ordinary way, `pip-audit` silently drops two kinds of package —
  one whose environment marker excludes the machine it is running on, and one
  already present in the environment it builds to resolve in. On this lock
  that was five `pyobjc` packages on Linux, and `packaging` on *every*
  platform, because `pip-audit` depends on `packaging` itself. The second is
  the nastier kind: no machine anywhere would have audited it, and nothing in
  the output said so.

  Both are the resolver's doing, so the resolver is turned off —
  `--disable-pip`, which is all that is wanted from a file the lock has
  already pinned in full — and with nothing being resolved the markers come
  off the requirements too. The count now reads **18 of 18 from any machine**,
  and an entry under *Not audited* is a real alarm rather than a standing
  caveat about where you ran it. The two changes only work together: markers
  stripped with the resolver still on makes pip try to build pyobjc off a Mac
  and fails the whole run.
- **The external binaries**, which no lock describes: `unrar`, `rar`,
  `tesseract`. Two of them take a file somebody else made, none of them ships
  here, and the version in use is whatever the machine has.

The run answers non-zero for an advisory *or* for a package it could not
audit, so it works as a scheduled job without the schedule quietly
congratulating itself.

It is not a dependency of anything and is not installed by `uv sync` —
installing comictrans must not pull an auditor, the same rule that keeps the
bundler out of `dev`.

### What it said on 2026-09-18

All eighteen packages the lock pins: **no known vulnerabilities**. Confirmed
on both Linux and macOS, 18 of 18 each, which is the point of the markers
coming off — the two machines now give the same answer.

Getting there took three runs and the macOS one is why. With the resolver
still on, Linux covered 12 of 18 and looked like a platform story; the Mac
covered 17 of 18, and the single package left over was not a platform gap at
all but the auditor shadowing what it was auditing. One run on the other
machine is what separated the two causes, and neither could have been found
by reading the output of the other. The external binaries on that Mac: none
of the three installed.

## The code reading

Done against the tree that became 1.1.0, over all 75 modules under
`src/comictrans`. What
follows is what it found, in the order it is worth knowing.

### One thing was changed

**A chapter archive could name a page larger than memory.** Both archive
formats declare each member's unpacked size in their own header, and nothing
read it: `_archive_pages` called `read` and got back whatever came. Measured
on a deflate bomb built for it — 255KB of CBZ declaring, and delivering,
256MB on one member, at 1029:1. Deflate tops out near that ratio, so a 4MB
file of the same shape is 4GB, read into memory in one piece and then written
to disk.

`sources.MAX_PAGE_BYTES` is now a cap on what an archive may *claim*, checked
before anything is decompressed. The declared size is worth trusting as an
upper bound because the unpackers already hold themselves to it — CPython's
`ZipExtFile` truncates at `zinfo.file_size`, `rarfile` counts down from the
same number — so refusing on it costs no read at all. Half a gigabyte, which
is several times the largest thing anyone puts in a CBZ: a 600dpi colour scan
of a comic page is about 40MB as PNG.

An entry past it is skipped and named in the report, which is what the reader
already did for an entry that is not a page. A chapter with one bomb in it is
still a chapter.

**The reader window goes through the same door.** `comictrans read`, which
came after this reading, reads a chapter file where it is rather than
unpacking it, and it is handed the entries `unpack` writes rather than
opening the archive for itself: the cap, the link test and the two skips are
made once, before either of them sees an entry. `tests/test_security.py`
builds one archive carrying all of them and checks the two agree. What it
holds is in memory rather than on disk, and it is bounded: the spread on
screen and one either side, six pages, each decoded by Qt, which refuses an
image it reckons at more than its default allocation limit of 256MB, counting
four bytes a pixel before it decodes anything — measured, and nothing here
raises it; `tests/test_gui_security.py` would notice if something did. The
price is that a page past about 64 megapixels, 8000 pixels square, is not
shown; a 600dpi scan of a comic page is about 24.

**A chapter's ComicInfo.xml is parsed now, and was not when this reading
was done.** Milestone 19 made `unpack` read it rather than skip it, which is
the first time any part of a chapter file other than a page's bytes is
interpreted rather than copied. Three things bound it, and each has a test in
`tests/test_security.py` or beside the module's own:

- It comes through the same walk as the pages, so a link is not read and the
  size is checked on the archive's claim before anything is decompressed —
  at a megabyte, `comicinfo.MAX_BYTES`, rather than the half gigabyte a page
  may be, since this is held in memory and parsed whole.
- A document type is refused the moment one starts, before any entity in it
  is declared. Entity expansion — a few hundred bytes declaring gigabytes —
  is the XML attack a file this size still carries, and an external entity
  is the other; neither can be reached without a document type, and
  ComicInfo.xml has no use for one. The Python here links expat 2.6.1, and
  its own limit does hold, measured: the 803-byte billion laughs in the test
  is stopped by expat with "limit on input amplification factor breached",
  but only after 2.6 million characters have been handed over. The refusal
  stops it at the first line and means an older expat is not what is being
  relied on.
- What is read goes into eight text fields of the plan header, whitespace
  folded, and a year that must be in `YEAR_RANGE`. Nothing in it names a path,
  runs anything, or reaches the language OCR runs in.

Writing one, into a packed chapter, is not an untrusted input and is covered
only for correctness: characters XML 1.0 cannot carry are dropped, so a plan
cannot make a file no reader can open.

### What was checked and found sound

Nothing below was changed. What each did gain is a test, because a defence
nothing holds you to is one the next refactor can take out without noticing —
`tests/test_security.py` and `tests/test_gui_security.py`.

**No network calls anywhere.** Nothing under `src/comictrans` imports a
module that speaks to another machine. The one call that could reach out is
`QDesktopServices.openUrl`, used twice to open the Finder, and both are given
`QUrl.fromLocalFile`. `HelpDialog` turns `QTextBrowser`'s navigation off
outright and the guide names nothing outside itself.

**Zip slip.** An archive entry's name is taken as a basename and written under
an index prefix, so it is one path component starting with a digit whatever
the archive called it. `../../etc/passwd`, an absolute path and a bare `..`
were all tried, and neither half of the defence is obviously load-bearing
when read, which is why both are now tested.

**A link is not a file.** A zip entry can be a symlink, and reading one gives
the path it points at rather than any image; the two formats record this
differently and both are asked.

**A plan file cannot construct a Python object.** The reader uses ruamel's
round-trip loader, which has no constructor for `!!python/…`: the tag is
dropped and the plan is refused for an ordinary reason. There is now a test
against the day somebody decides the unsafe loader is faster.

**Third-party code runs only when it is asked for.** Importing a plugin runs
its module-level code — there is no reading Python without running it — so
what matters is not that the Plugins menu is hidden but that nothing is
imported until Preferences ▸ experimental features is on. It is not.

**`rar` is run without a shell**, with an argument list built here and
nothing from the plan file in it. Two properties do the work and neither was
written down: every entry name begins with `{index:03d}-`, so no page can be
read as a switch, and the archive path is `resolve()`d, so it begins with a
separator. Both are now spelled out in `_pack_rar`, since either is easier to
break than to notice.

**A font name from a plan never becomes a path.** It is slugified and
prefix-matched against a directory listing; the only filenames joined to a
directory are constants in `fonts.KNOWN_FILES`.

**Tesseract's arguments.** `lang` comes from the plan and sits in an argument
position, never a switch position; the page-segmentation flags are a module
constant. pytesseract builds a list, not a command line.

**Pillow's decompression-bomb limit is at its default** of 89 million pixels.
Nothing in this tree raises it or sets it to `None`, which is the usual way
that protection is lost.

### What was measured and left

**A plan may name a page outside its own directory.** `source_path` resolves
`../pages/page-001.png` against the plan, which is a real and documented use.
An absolute path works the same way. Two things bound it: the page must hash
to what the plan says it hashes to, so a plan can only point at a file whose
contents its author already had, and the output name is `source.stem` — one
path component — so nothing is ever written outside `--output`. Left as is.

**A PDF page image has no pre-read size bound.** The archive cap has no
counterpart here: a PDF declares no decompressed length for an image stream,
so there is nothing to check before decoding. What limits it is that the
bytes are written through rather than decoded, and Pillow's own limit applies
the moment anything reads the page back. Worth revisiting if a cheap bound
appears; not worth decoding a page to find out how big it is.

**`rar` is run without a timeout.** A compressor that hangs hangs the run.
The user can cancel, and the failure is visible rather than silent, so this
is a papercut rather than a hole.

**The configured `rar` and `unrar` paths are checked only for being an
executable file.** The roadmap flagged this, and the answer is that it is the
right depth. The person naming the binary is the person running the
application; anything stricter refuses a legitimate install.

**Log and crash files are written with the process umask**, and hold paths
and tracebacks — never plan text, source text or translations. On a shared
machine with a permissive umask another user can read them. Consistent with
the scope above.

## What is enforced

| Where | What it holds |
| --- | --- |
| `tests/test_security.py` | No module imports anything that speaks to a network; `openUrl` only ever gets a local file; the pipeline runs end to end with `socket` unusable; zip slip; the size cap; the reader window handed exactly what `unpack` writes; a plan file constructing nothing |
| `tests/test_gui_security.py` | No plugin is imported until experimental features are on; the guide names nothing remote and cannot navigate; the reader refuses a page past Qt's allocation limit |
| `tests/test_audit.py` | The dependency tool's bookkeeping — which packages were audited, which were skipped, and the exit code a scheduled run reads |

The network invariant is checked twice on purpose. Reading the source is
total over code this project wrote and sees a branch no test reaches; running
the passes with the sockets taken away is the only one that can catch a
dependency doing it on our behalf. Neither sees a C library calling
`connect` itself, Qt included, which is the honest limit of both.
