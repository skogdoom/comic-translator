---
name: release-version
description: The version bookkeeping for this repository — opening a version (bumping to .devN and opening its changelog heading) and closing one (making a release and tagging it). Use whenever the request is to cut a release, bump the version, start the next version, tag a release, or update the release notes, and whenever a milestone is the first work after a release. It names the four files, the order, the routes to verify through, and the traps, so the sequence is not re-derived from git each time.
---

# Opening and closing a version

Two sequences that touch the same four files in opposite directions. Getting
either half-done leaves the tree in a state a test will catch, which is the
good case; the bad case is a release whose notes say the wrong thing.

Between releases the tree carries a `.devN` version and the changelog has an
open `unreleased` heading, so a shipped milestone has somewhere to write its
line at the time rather than however many merges later. A tree at a plain
version is a release and has no heading open.

## Opening a version

Done once the previous release is tagged, either on its own or as the first
thing the next milestone does.

1. **`src/comictrans/__init__.py`** — `__version__` from `X.Y.Z` to the next
   `X.Y+1.0.dev0`. This is the single source; everything else derives from it.
2. **`CHANGELOG.md`** — add `## X.Y+1.0 — unreleased` above the newest dated
   heading, with a line saying it is where a shipped milestone earns its entry.
   `tests/test_release.py` requires a heading matching the version being worked
   towards, so this is not optional.
3. **`README.md`**, the Status section — back to the two-part form:
   `**Released: X.Y.Z. This tree: X.Y+1.0.dev0**`, and a clause saying this
   tree is work since that release rather than something to build from.
4. **`docs/ROADMAP.md`** — check the `.devN` paragraph still reads true. It
   states the rule; if it carries a concrete version as an example, that
   example has just gone stale.

## Closing a version

Making a release.

1. **`CHANGELOG.md`** — the real work, and the reason this is not a one-liner.
   `## X.Y.Z — unreleased` becomes `## X.Y.Z — YYYY-MM-DD`, and the accumulated
   merge-ordered bullets get regrouped into themed `###` sections with a lead
   paragraph. Say whether the plan format moved, because that is the first
   thing an upgrade asks.
2. **`src/comictrans/__init__.py`** — `X.Y.Z.dev0` to a plain `X.Y.Z`.
3. **`README.md`**, Status — back to the one-part form: `**Released: X.Y.Z**`,
   and this tree is it.
4. **`docs/ROADMAP.md`** — delete what shipped, and say what the release was.
5. **Anything dated to a release.** `docs/SECURITY.md` carries a dated audit
   result and a "done against" line; 1.1.0 changed `Done against 1.1.0.dev0` to
   `Done against the tree that became 1.1.0`. Grep for the outgoing `.dev0`
   string across `docs/` and `README.md` before committing.
6. **Tag on master, after the merge** — `v1.1.0` sits on the merge commit, not
   on the release branch. By the paragraph at the top of `CHANGELOG.md` a
   release *is* the repository at a tag, so an untagged release is not one.
   A tag is outward-facing and awkward to retract: confirm before pushing it.

## Verify through the routes that read it

Do not trust the constant. Three things read the version and only two of them
are held by a test:

- **`comictrans --version`** — `uv run comictrans --version`.
- **The plan header `extract` writes** — run a real `extract` and read the
  `generator:` line. **No test holds this to `__version__`**; the fixtures use
  string literals, so it is the route that can drift silently. Off macOS this
  needs a font: point `COMICTRANS_FONT_PATH` at a directory holding
  `Comic Sans MS.ttf` and `Comic Sans MS Bold.ttf` (any TTF pair copied under
  those names), or `extract` refuses rather than substituting.
- **The About dialog** — `gui.about.package_version()`, held by three tests in
  `tests/test_gui_about.py`, plus the crash-log banner held by
  `tests/test_gui_crash.py`.

Then the four checks, and `recompile.py --check`.

## Traps

Each of these has cost time at least once.

- **`recompile.py` is at `src/comictrans/gui/resources/translations/`**, not
  `resources/translations/`. Run it as
  `uv run python src/comictrans/gui/resources/translations/recompile.py --check`.
- **`pyproject.toml` sets `addopts = "-q"`.** Passing `-q` again gives `-qq`
  and suppresses the summary line. Run `uv run pytest` bare.
- **The venv's `.dist-info` goes stale and reads a version behind.** This is
  why `gui/about.py` reads `__version__` directly instead of asking
  `importlib.metadata`; a bundle once shipped reporting 0.1.0 while the crash
  log said 1.0.0. Refreshing it needs the network, so on a machine without one
  the metadata simply stays wrong — harmless, because nothing reads it.
- **Tagging the branch instead of master.** The previous tags sit on master.
- **A stale example in prose.** Both the ROADMAP `.devN` paragraph and the
  `__version__` docstring have carried statements that a later change made
  false. When editing a line, read the prose attached to it.

## Mutation-check what you touch

The changelog heading is held by `tests/test_release.py`. Rename the heading
and confirm that test fails before believing it is guarding anything — see the
`mutation-check` skill.
