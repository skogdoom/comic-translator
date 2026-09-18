# comictrans — notes for agents

`README.md` covers how the tool is used, `docs/ARCHITECTURE.md` why it is
built this way. Both are kept current; read them rather than re-deriving
their contents from the source.

## Known bugs

`known-bugs.md` records defects that have been found, reproduced and measured,
and then deliberately left alone. **Read it before proposing or making a fix**,
so you do not spend a session rediscovering something already understood.

Nothing in it is a work item. Fix an entry only when the request explicitly
names that bug, and delete the entry in the same commit. Meeting one while
doing something else — including while reviewing, auditing, or tidying nearby
code — is not an instruction to fix it. If a change already in progress would
alter one of those behaviours, stop and say so rather than folding the fix in.

It is a different list from **Known weak points on real scans** in
`docs/ARCHITECTURE.md`, which is about what detection does badly on real
pages, and from `docs/SECURITY.md`, which is what an untrusted chapter or
plan file is allowed to do. That first one describes the state of the art
here; `known-bugs.md` describes choices; `docs/SECURITY.md` describes a
reading that was done, and is the thing to read before changing how an
archive is unpacked, a plan is parsed, or a plugin is loaded.

## Invariants

Spec, not preference. Breaking one of these is never a refactor:

- Source images are never modified, moved, or written to; they are opened
  read-only. The only things ever written are `--debug-dir`, `--output` (both
  refuse to write inside the source tree), a plan file, by `extract` or by
  `review`'s Save / Save As — a plan file is not image data, so it living
  beside the images it describes, the usual case, is expected and fine — and
  the plugin directory (`plugins.plugin_directory()`), which is written to
  only when experimental features are turned on (the bundled example is
  installed there, unless already present) or Open Plugin Folder is asked
  for by name.
- `apply` runs no detection and no OCR. Every polygon, colour and font
  decision comes from the plan file, which is what makes the pass
  deterministic and re-runnable: edit a translation, run it again, and only
  that text changes.
- No network calls anywhere in the pipeline. Translation is manual by design.
  A plugin is not bound by this: it is off by default (Preferences ▸ this
  window ▸ experimental features) and always third-party code the user chose
  to install. `plugins.py` itself makes no network call and never touches
  the network on a plugin's behalf, but nothing stops a plugin's own code
  from doing so — see the disclaimer in `README.md`. This invariant is the
  one that had nothing holding it; it now has two checks, in
  `tests/test_security.py`. Nothing under `src/comictrans` may import a
  module that speaks to another machine, and both passes run end to end with
  `socket` unusable. An installed plugin is outside both, as it is outside
  the invariant.
- Emphasis renders as bold, never italic. A bold face is never synthesised and
  the oblique face is never used.
- A font is never silently substituted for the one the plan file names.
- A region that cannot be rendered is left completely alone — not erased, not
  half-drawn — and named in the report.

## Checks

```
uv run pytest              # the suite
uv run ruff check .        # lint
uv run ruff format .       # format
uv run mypy                # strict, over src/comictrans
```

All four are expected to pass before a commit. There is a fifth, which is
not one of them: `uv run --with pip-audit tools/audit_dependencies.py` checks
the locked dependencies against published advisories. It uses the network, so
it is not part of the suite and is not run per commit — see `docs/SECURITY.md`.

Thresholds and guards in `config.py` are tuned against the pages in
`tests/fixtures/`; if you change one, re-run detection across every fixture
and say what moved, rather than trusting the suite alone to catch it.

Touching anything under `src/comictrans/gui/` needs PySide6: `uv sync --extra
gui`. Without it those files still lint and still type-check — mypy falls
back to treating PySide6 as untyped rather than failing outright, deliberately
(see the comment on its override in `pyproject.toml`) — but `pytest` skips
every widget test, so a change there is unverified until the extra is
installed.
