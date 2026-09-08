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
pages. That one describes the state of the art here; `known-bugs.md`
describes choices.

## Invariants

Spec, not preference. Breaking one of these is never a refactor:

- Source images are never modified, moved, or written to; they are opened
  read-only. The only things ever written are `--debug-dir`, `--output` (both
  refuse to write inside the source tree), and a plan file, by `extract` or by
  `review`'s Save / Save As — a plan file is not image data, so it living
  beside the images it describes, the usual case, is expected and fine.
- `apply` runs no detection and no OCR. Every polygon, colour and font
  decision comes from the plan file, which is what makes the pass
  deterministic and re-runnable: edit a translation, run it again, and only
  that text changes.
- No network calls anywhere in the pipeline. Translation is manual by design.
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

All four are expected to pass before a commit. Thresholds and guards in
`config.py` are tuned against the pages in `tests/fixtures/`; if you change
one, re-run detection across every fixture and say what moved, rather than
trusting the suite alone to catch it.

Touching anything under `src/comictrans/gui/` needs PySide6: `uv sync --extra
gui`. Without it those files still lint and still type-check — mypy falls
back to treating PySide6 as untyped rather than failing outright, deliberately
(see the comment on its override in `pyproject.toml`) — but `pytest` skips
every widget test, so a change there is unverified until the extra is
installed.
