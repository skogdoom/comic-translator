"""The plugin runtime: discovery, and running one plugin over a plan.

Experimental, and off unless ``Preferences.experimental_on`` says otherwise
(``gui/preferences.py``) — that switch, and the menu it gates, are the
review window's concern; this module does not read it and does not care.
What it guarantees on its own is narrower and holds regardless: **plan in,
plan out**. A plugin never sees an image and never touches the filesystem
through anything here, so "source images are never modified" survives a
plugin without this module having to enforce it.

A plugin is one ``.py`` file, discovered by dropping it into
:func:`plugin_directory`. It declares two names at module level::

    PLUGIN_NAME = "..."
    def run(plan: Plan) -> Plan: ...

``run`` gets the plan as it stands and returns the plan it should become.
It may change field values on regions already in the plan — a translation,
a note, a flag, even a polygon — but not add, remove, reorder, or move a
region between pages, and not touch the header or the page list. That is
what keeps a plugin's result one checkable, whole-plan undo step rather
than a second place the plan's *shape* can change: see
:func:`run_plugin`.

Loading a plugin runs whatever top-level code its file has, same as any
``import`` would; nothing here sandboxes that, because nothing in Python
does. Only the disclaimer stands between a plugin and the interpreter it
runs in, which is why the menu this hides behind spells that out.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .errors import PluginError
from .model import Plan

log = logging.getLogger(__name__)

APPLICATION = "comictrans"

PLUGIN_PATH_ENV = "COMICTRANS_PLUGIN_PATH"
"""Overrides where plugins are read from — the same escape hatch
``COMICTRANS_FONT_PATH`` and ``COMICTRANS_LOG_DIR`` are for their own
directories, and for the same reason: the suite must not read whatever is
sitting in the directory of whoever runs it."""


def plugin_directory() -> Path:
    """Where plugins are read from, by the convention of the platform.

    macOS keeps content a user adds to an application under ``~/Library/
    Application Support``, which is not where ``logfile.log_directory``
    points — that is diagnostics, read by the user, not content the
    application reads back. Elsewhere, the closest equivalent is the XDG
    data directory.

    Read-only and side-effect-free, like ``fonts.SEARCH_DIRS`` and
    ``logfile.log_directory``: it never creates the directory it names.
    """
    override = os.environ.get(PLUGIN_PATH_ENV)
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APPLICATION / "plugins"
    data = os.environ.get("XDG_DATA_HOME")
    root = Path(data) if data else Path.home() / ".local" / "share"
    return root / APPLICATION / "plugins"


@dataclass(frozen=True, slots=True)
class LoadedPlugin:
    """One plugin file that imported cleanly and declared what it needed to."""

    name: str
    path: Path
    run: Callable[[Plan], Plan]


def discover_plugins(directory: Path | None = None) -> list[LoadedPlugin]:
    """Import every plugin in ``directory``, in filename order.

    A file that fails to import, or does not declare a ``PLUGIN_NAME`` string
    and a callable ``run``, is left out and logged rather than raised — one
    broken file next to two working ones should not hide the two that work.
    That is a *load* failure; see :func:`run_plugin` for what happens when a
    plugin runs and fails instead, which is a different thing reported a
    different way.
    """
    directory = directory or plugin_directory()
    if not directory.is_dir():
        return []
    return [
        plugin for path in sorted(directory.glob("*.py")) if (plugin := _load_one(path)) is not None
    ]


def _load_one(path: Path) -> LoadedPlugin | None:
    # A name unique to this call, not to the file: re-discovering must not
    # collide with a module the same path was loaded as last time, and must
    # not reuse another plugin's name either.
    module_name = f"comictrans._plugin_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        log.warning("%s: could not be read as a Python module", path)
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # a plugin's own top level: entirely unknown code
        log.warning("%s: failed to load: %s", path, exc)
        return None

    name = getattr(module, "PLUGIN_NAME", None)
    run = getattr(module, "run", None)
    if not isinstance(name, str) or not name or not callable(run):
        log.warning("%s: missing PLUGIN_NAME or run()", path)
        return None
    return LoadedPlugin(name=name, path=path, run=run)


def run_plugin(plugin: LoadedPlugin, plan: Plan) -> Plan:
    """Run one plugin over ``plan`` and hand back the plan it returned.

    Raises :class:`PluginError` rather than letting anything through
    unchecked: the plugin's own exception, a return value that is not a
    ``Plan``, or a ``Plan`` whose header, pages, or region ids and page
    assignments do not match what it was given — only field values on
    existing regions may change. The plan passed in is never touched by
    this call either way; nothing here writes anything back, that is the
    caller's decision once it trusts the result.
    """
    try:
        result = plugin.run(plan)
    except Exception as exc:
        raise PluginError(f"{plugin.name}: {exc}") from exc
    if not isinstance(result, Plan):
        raise PluginError(f"{plugin.name} did not return a plan")
    _check_same_shape(plugin.name, plan, result)
    return result


def _check_same_shape(name: str, before: Plan, after: Plan) -> None:
    if after.header != before.header:
        raise PluginError(f"{name} changed the plan header, which a plugin may not do")
    if after.images != before.images:
        raise PluginError(f"{name} changed the plan's pages, which a plugin may not do")
    shape = tuple((region.id, region.image) for region in before.regions)
    after_shape = tuple((region.id, region.image) for region in after.regions)
    if shape != after_shape:
        raise PluginError(
            f"{name} added, removed, reordered, or moved a region, which a plugin may not do"
        )


__all__ = [
    "APPLICATION",
    "PLUGIN_PATH_ENV",
    "LoadedPlugin",
    "discover_plugins",
    "plugin_directory",
    "run_plugin",
]
