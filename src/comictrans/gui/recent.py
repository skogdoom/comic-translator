"""The plans opened lately, for the File menu's Open Recent.

No Qt, like ``preferences.py``, and it shares that module's
:class:`SettingsStore` protocol: ``QSettings`` satisfies it structurally, so
the rules here — how many are kept, what order they come back in, what
counts as the same file twice — are tested against a dictionary.

**This is the third thing stored outside a plan file**, after the window
layout and the preferences, and the only one that is a record of what
someone has been reading rather than of how they like the tool set up. That
is why it has its own key and its own way to be emptied: clearing it has to
clear it, not leave it folded in with the geometry of the docks.
"""

from __future__ import annotations

import os
from pathlib import Path

from .preferences import SettingsStore

KEY = "recent/plans"
"""Its own key, so that emptying the list is a single removal.

Deliberately not under ``preferences/``: those are settings, this is
history, and the difference matters the moment someone wants one gone and
not the other.
"""

LIMIT = 10
"""How many to keep. Ten is what macOS offers under Recent Items."""


def _canonical(path: Path | str) -> str:
    """One spelling per file, decided without touching the disk.

    ``~`` is expanded and the path is made absolute and lexically tidy, so
    the same plan reached as ``./comic-plan.yaml`` and as an absolute path
    is one entry rather than two. Deliberately *not* ``resolve()``: that
    follows symlinks, which is a filesystem call, and a list of recent files
    should never be the reason a menu waits on a network volume that is not
    answering.
    """
    expanded = Path(path).expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    # ``normpath`` rather than ``Path.resolve``: it collapses ``..``
    # textually and asks the filesystem nothing at all.
    return os.path.normpath(expanded)


def load(store: SettingsStore) -> tuple[Path, ...]:
    """The list, most recently opened first.

    Tolerant of whatever the file actually held, the way ``preferences``
    is: a hand-edited INI can put a bare string where a list belongs, and a
    missing key reads as nothing at all. Neither is worth an exception over
    a menu.
    """
    stored = store.value(KEY, None)
    if stored is None:
        return ()
    if isinstance(stored, str):
        stored = [stored]
    if not isinstance(stored, list | tuple):
        return ()
    seen: dict[str, None] = {}
    for entry in stored:
        if isinstance(entry, str) and entry.strip():
            seen.setdefault(_canonical(entry), None)
    return tuple(Path(name) for name in list(seen)[:LIMIT])


def _store(store: SettingsStore, paths: tuple[Path, ...]) -> tuple[Path, ...]:
    store.setValue(KEY, [str(path) for path in paths])
    return paths


def remember(store: SettingsStore, path: Path) -> tuple[Path, ...]:
    """Put ``path`` at the front, and return the list as it now stands.

    Opening something already listed moves it up rather than adding it
    again, which is what makes the order mean "recently", and what stops a
    plan someone is working through from pushing everything else out.
    """
    name = _canonical(path)
    kept = [entry for entry in load(store) if _canonical(entry) != name]
    return _store(store, (Path(name), *kept)[:LIMIT])


def forget(store: SettingsStore, path: Path) -> tuple[Path, ...]:
    """Drop one entry — for a plan that turned out not to be there any more."""
    name = _canonical(path)
    return _store(store, tuple(entry for entry in load(store) if _canonical(entry) != name))


def clear(store: SettingsStore) -> tuple[Path, ...]:
    """Empty the list. What Clear Menu does, and the whole of it."""
    return _store(store, ())


__all__ = ["KEY", "LIMIT", "clear", "forget", "load", "remember"]
