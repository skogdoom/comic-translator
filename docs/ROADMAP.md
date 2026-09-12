# Roadmap

What is planned and not yet built, in the order it is worth building.

This is a different list from the two that already exist. `known-bugs.md`
records defects that are understood and deliberately left alone;
**Known weak points on real scans** in `ARCHITECTURE.md` records what
detection does badly on real pages. Both describe the tool as it stands.
This file describes what it is not yet.

Nothing here is a commitment, and nothing here is a work item an agent
should pick up on its own — the same rule `known-bugs.md` carries. Work a
milestone when the request names it.

The numbered sections are the plan. **Ideas, not milestones** at the end is
a step further back: things worth looking into that have not been decided
on, kept unnumbered so that nothing can refer to one as though it were
scheduled.

**A milestone includes its own documentation.** A milestone that changes
what the window does updates the in-application guide in the same change —
that is live now, since the guide has shipped — and runs
`resources/translations/recompile.py --extract`, then translates what it
added, which is live now too. Where that turns out to be a body of work
rather than a paragraph it becomes its own milestone — but it is never
simply left for later, because help describing the previous version is worse
than no help at all.

Sizes are relative effort, not estimates. When a milestone ships, delete
its section and its row from the table, and say so in the Status section of
`README.md`. Now that 1.0.0 is out, it also earns a line in `CHANGELOG.md`,
under the heading of the version being worked towards — `1.1.0 — unreleased`
while this tree is `1.1.0.dev0`. The changelog is a record of releases, and
milestones accumulate into one; a test holds that heading to the version, so
there is always somewhere to write the line at the time rather than however
many merges later.

**The numbers are names, not positions.** They were allocated in the order
the milestones were thought of, and the order worth building them in has
moved since. The table below is the plan; a number is only there so that
one section can refer to another without ambiguity.

## Order

| # | Milestone | Size |
|---|-----------|------|
| 5 | Validate a plan file | S |
| 4.26 | Extract text for one region | M |
| 4.27 | Lock a region | M |
| 4.28 | Region context menu | S |
| 3 | CBZ, PDF and CBR input | L |
| 8 | CBZ and CBR output | M |
| 6 | PDF output | L |
| 10 | Code quality review | M |
| 7 | Security audit | M |

**1.0.0 is out.** Everything a first release needed has shipped, and the
macOS pass that could only happen on a built application has been done.
Everything in this table is what comes after a release.

The 4.x numbering says these follow milestone 4, the review GUI. Milestone
3 is older than all of them and independent of the GUI; it sits at the
bottom because nothing else waits on it, not because it matters least.

Three things decide this order.

**Cheap and immediately felt comes first.** 4.1 to 4.3 were small and
visible the moment the window opened, which is why they went first, and
4.11, 4.12 and 4.15 were the same shape: felt on every page of every
review, and waiting on nothing, as was moving a region after them.

**Cross-cutting comes last.** Localisation touched every user-visible
string, so it went after the milestones that added strings; packaging
bundled whatever the application was by then. Both are why the rename went
first rather than being filed with the other small things: renaming after
either one would have meant doing that work a second time, and the interface
review was filed just ahead of them for exactly that reason — it decided
what the labels say, and deciding that after they have been translated is
the same mistake twice. That paid out as expected: the catalogues were
extracted once, from labels nobody had to argue about again. Help text
describes the UI, so it went after the UI stopped moving — after that review
and before the release froze it, which is where it landed.

**Foundations come before what stands on them.** 4.4 and zoom went early for
that reason, and region editing — the largest of the minor milestones, now
shipped — stood on both: undo built for five text fields would have needed
rewriting the moment a polygon could move, so it snapshots whole plans
instead, and dragging a polygon vertex accurately means being able to see
it.

**A release changed what "first" meant.** The milestones above the line were
there because a release needed them, not because they were cheap or ready.
That is why the region tools, the archive formats and the validate command
sat below a line they would otherwise have been well up: none of them was
what made this releasable, and each is easier to get right against a released
version. That line has now been crossed, and what is left is in the order it
is worth building rather than in the order a release forced.

The test suite was the exception that proved it, and it went first for
that reason: not a release blocker, but free, invisible to users, and paid
for by every milestone above the line. It came in at 78 seconds and leaves
at 56.

**Measurement comes before the thing it would justify.** The suite's
saving was found by timing it rather than by guessing which tests looked
slow, and then confirmed by counting the calls rather than by trusting the
clock. Preview on a thread paid that out: the guess in its own section was
"three copies of an eleven-megapixel page, about 120MB", and measuring found
116MB retained and a 540MB peak, most of it inside a single `erase` call —
which became milestone 12, and would not have been found by shipping the
thread and moving on. That milestone then did the same thing to itself: 540MB
was a number rather than a diagnosis, and walking one `erase` call line by
line turned it into "a colour distance computed for the whole page to decide
a mask a balloon wide", which is a one-paragraph fix worth 15x the time and
all of the memory. The same is still owed to 4.26: what a crop does to
recognition accuracy is a number somebody has to produce before that design
is settled.

One ordering was a judgement call rather than a dependency, and it paid out:
**rendering (4.14) went before extract (4.6)**. Both run a pipeline pass from
the window and both needed the same worker thread, progress and cancel.
Rendering was the simpler of the two — no OCR, so no question about what is
safe to call off the main thread — and the more valuable, because reviewing a
plan and then leaving for a terminal to render it was the obvious hole in the
window. The threading was built on the easy case, and extract reused it: by
the time it landed, the harness was a base class and one `work()` method.

**Two things were deliberately held back from 1.0.0.** Putting preview on a
thread would have replaced a working wait cursor with threading, in the one
code path that has produced a segfault in this project — risk taken on
immediately before a release, for a benefit 4.21 already largely delivered.
It has since shipped, and found milestone 12 on the way, which has now
shipped too. And 4.9 would have
shipped translation machinery for a single language while freezing every
string just as the milestones below started adding more, which under the
documentation rule above makes every one of them run a translation pass too.
It has since shipped as well — so that rule is live, and every milestone
below now ends with an extraction pass and whatever it added translated.
Neither was what made 1.0 releasable; both were simply next.

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

## 4.26 Extract text for one region

Run the recogniser over a single region and put what it reads into
`source_text`: for a region drawn by hand, which has no reading at all, and
for one where detection read the lettering badly.

**Two ways to do it, and the cheap one is wrong.** Recognising the whole
page and keeping the lines inside the polygon needs almost no new code, but
spends a full-page recognition on one balloon. Cropping to the region and
recognising that is the one worth building, and its risk is accuracy rather
than speed: recognisers do better with a margin around the text than with a
tight crop. So the crop wants padding, and the result wants comparing
against what full-page detection finds on the same fixtures before this is
called done.

**It writes over something a person may have typed**, which nothing else in
the window does — every other edit replaces the reviewer's text with the
reviewer's text. So it asks first when `source_text` is not what extract
left there, and it is one undo step like any other edit.

**It wants the worker thread, which preview has now built out.** A
recogniser is seconds, not milliseconds, and unlike preview this is a
per-balloon action rather than an occasional one; a wait cursor is the wrong
answer at that frequency. `PreviewJob` is the shape to copy — one unit of
work, no page loop, nothing to cancel — and a per-region request keyed the
same way answers the same staleness question.

`apply` is untouched and still runs no OCR. This is `review` doing what
`extract` does, to one region.

## 4.27 Lock a region

Mark a region finished, so that changing it means deliberately unlocking it
first.

**It costs a plan version.** The reader rejects unknown keys on purpose, so
that a typo is an error rather than a silent no-op — which means a plan
carrying `locked:` cannot be read by a build that predates it. That is what
`PLAN_VERSION` is for: 3 becomes 4. Worth spending that bump on every field
the roadmap wants at once rather than twice; nothing else pending needs
one, so today this is alone.

**In the plan rather than in settings**, for the reason everything else is:
the plan is the thing handed to someone else, and "these are final, leave
them" is exactly the sort of thing worth handing over. In settings it would
live on one machine and be lost on the next.

**It is not `skip`, and the two will be confused unless the labels are
careful.** `skip` means do not render this region; `locked` means do not
edit it, and a locked region still renders. Saying which is which without
the manual is this milestone's job, under the casing and wording rules the
interface review settled.

**What it has to reach**: every edit path. The inspector's fields,
reshaping, the arrow keys, merge, delete — and re-extraction, where locked
regions are the ones that should come through untouched, which is half the
reason to want it.

## 4.28 Region context menu

Right-click — and Control-click, on macOS — on a region: extract its text,
lock or unlock it, edit its shape, select it.

**After the commands it lists.** A context menu is a shortcut to things
that already exist; built before them it is a menu of two items.

**It has to respect one-mode-at-a-time.** The canvas holds a single
`CanvasMode` precisely because a click means different things in each, and
a menu offering a command that contradicts the mode in progress is the
wrong place to find that out.

`contextMenuEvent` on the canvas, choosing the region under the cursor the
same way a left-click chooses it, so the two cannot disagree about what was
clicked.

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
package, so it cannot be declared in `pyproject.toml`.

**It is never bundled — decided, not open.** The unrar licence is not
OSI-free, and this is an MIT project; shipping the binary inside a `.app`
would put someone else's terms on the whole thing. So the tool uses one the
person running it already has: found on `PATH`, and when it is somewhere
unusual, named in Preferences. That field is the same shape as the output
directory already there, and it costs nothing when the binary is on `PATH`
like everybody else's. Absent entirely, CBR input is unavailable and says
so — it does not fail halfway through opening a chapter.

Behind the sidecar decision above, a CBR reader is one more unpacker and
nothing else. Writing one is a different matter, and 8 covers it.

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
already licensed. So: `rar` is never bundled. It is looked for on `PATH`,
and Preferences can name it where it lives somewhere unusual, which is the
same field 3 needs for `unrar` and probably the same one. Present, CBR is
offered; absent, it is not, and the reason says which binary would provide
it rather than the option quietly not being there.

That keeps this an MIT project and still gives anyone holding a licence
the format they asked for. It also puts a configurable path to an
executable this tool then runs into the codebase, which 7 should look at
closely — how it is validated, and what happens when it names something
that is not `rar` at all.

**Naming inside the archive carries the reading order, not the source
filenames.** A reader sorts entries by name, so the page order somebody
set by dragging rows has to survive into the archive as a zero-padded prefix
or equivalent.
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
today. And it is the other milestone that makes the page order mean
something on disk rather than only in the window.

## 10 Code quality review

A reading of the whole codebase for the things tests do not catch:
duplication that has crept in, modules that have grown past what their
docstring claims, names that no longer match what they do, and comments
that describe an earlier version of the code.

**After the features, before the audit.** Reviewing while milestones are
still landing means reviewing the same code twice; leaving it until after 7
means the audit reads code nobody has tidied. Between them is the one place
it pays for itself.

Worth deciding in advance what it is allowed to change. A quality pass that
also fixes behaviour is two changes wearing one commit message, and this
repository has `known-bugs.md` precisely so that "while I was in there" is
not how a decision gets reversed.

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

## Ideas, not milestones

Things to look into, none of them decided. **Unnumbered on purpose**: a
number in this file is a name that other sections refer to and that the
table above orders, and none of these has earned one. Nothing here is
planned, nothing here is sized, and nothing here should be started without
the decision being made first.

What each note is for is the part that would otherwise be rediscovered: what
it would cost, and what it runs into. Several of these are not features on
top of the tool as it stands — they are changes to what it promises.

**What a page costs to decode.** Milestone 12 went looking for why
rendering an eleven-megapixel page peaked at 540MB and found it in `erase`,
which now works inside a window and costs nothing measurable. What that
leaves at the top is `load_page`: 77MB of traced allocations and about 230MB
of process peak for one 11 MP PNG, 0.29s, to end up holding 44MB — the page
and its alpha. Most of the difference is inside Pillow rather than here: an
RGBA decode, `convert("RGBA")` again to pull the alpha channel out, and
`flatten_to_rgb` after it. Whether that can be had for less without giving up
the metadata the apply pass needs is a measurement nobody has taken. It
matters most where pages are read in a loop, which is `apply` over a chapter
rather than a preview of one page.

**Rotate a region.** Cheaper than it looks, and it splits in two. A `Region`
already holds an arbitrary polygon, so a rotated outline is representable
today with no schema change and no `PLAN_VERSION` bump — a tilted rectangle
is just four points. What that does *not* buy is rotated text: the
typesetter fits each line to the widest horizontal run inside the polygon on
every row of its band, so text inside a tilted outline would still be laid
out level. So this is worth having on its own, for a balloon that sits at an
angle, and it is not a step towards the next one.

**Rotated and vertical text.** The other half, and a different order of
work. It needs a field on a region — an angle, or a writing mode — which the
reader rejects until `PLAN_VERSION` goes up, and it needs the typesetter to
stop thinking in horizontal bands, which is the shape of `typeset` rather
than a parameter to it. Vertical CJK is more again: line breaking and
hyphenation are not the same algorithm turned sideways. Worth knowing which
of the two is actually wanted before either is designed.

**A plugin system (experimental).** Plan file in, plan file out, with each
plugin under a Plugin menu, carrying its own configuration, and a dialog
only if it needs one. Ship one simple example.

The shape is already right in two ways worth noticing. Plan-in, plan-out
keeps plugins away from the images entirely, so "source images are never
modified" survives without anyone having to enforce it. And the reader
rejects unknown keys, so a plugin cannot invent plan fields — it is
constrained to the schema whether its author meant to be or not. Undo fits
too: the history is whole-plan snapshots, so a plugin rewriting everything
is one step like any other edit.

What it does cost is the network promise. "No network calls anywhere in the
pipeline" is an invariant today and would become "this tool makes none; a
plugin you installed might", which is a different sentence and has to be
written as one — in `README.md`, not only here. Running third-party code
also lands on a project whose disclaimer is already about unreviewed code;
that is worth a paragraph rather than a footnote. Discovery needs a
directory to read plugins from, which is a new read location, and the
example should not be case switching — the header already has `case`, and an
example that duplicates a built-in teaches the wrong thing.

**Extracting and rendering emphasis, italic and bold.** This one is not a
feature, it is a change to an invariant, and should be decided as one.
`CLAUDE.md` says emphasis renders as bold, never italic, that a bold face is
never synthesised, and that the oblique face is never used. Bold already
works: `**bold**` in a translation is parsed in `typeset`. So what is being
asked for is the italic half, and the invariant exists to stop it being
faked. Doing it honestly means requiring a real italic face, which means
`fonts` resolving a third file per family and a plan that names a family
without one failing the same way a missing bold does. Doing it dishonestly
means synthesising a slant, which is the thing the rule forbids. The
extraction half is separate again and probably harder: telling italic from
upright in scanned comic lettering is not something either recogniser
reports.

**Sound effects, identified and lettered over the artwork.** Half of this
exists. `erase: none` is already documented as being for exactly this — it
paints nothing and letters straight onto the page — so the render side needs
no new machinery, and a region can already carry it per region.

What is missing is the other two thirds. Detection would have to tell a
sound effect from a balloon, which is a new class of thing for `detect` to
recognise rather than a threshold to move, and the flag on `extract` is the
easy part of that. And the colour: a sound effect has no balloon to sample,
so "a visible colour" means choosing one for contrast against whatever is
underneath, which is a decision nothing in the tool makes yet —
`sample_colors` reads the colours that are there. Note which pass may do
that: `review` may measure the page, `apply` may not, so the choice has to
be made at extract time and recorded, like every other colour in a plan.
