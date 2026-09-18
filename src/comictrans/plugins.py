"""The plugin runtime: discovery, and running one plugin over a plan.

Experimental, and off unless ``Preferences.experimental_on`` says otherwise
(``gui/preferences.py``) — that switch, and the menu it gates, are the
review window's concern; this module does not read it and does not care.
What it guarantees on its own is narrower and holds regardless: **plan in,
plan out**. A plugin never sees an image and never touches the filesystem
through anything here, so "source images are never modified" survives a
plugin without this module having to enforce it.

A plugin is a folder, discovered by dropping it into
:func:`plugin_directory`. ``__init__.py`` inside it is the entry point —
which makes the folder an ordinary Python package, so a plugin that needs
more than one file can import its own siblings the way any package does —
and declares two names at module level::

    PLUGIN_NAME = "..."
    def run(plan: Plan, settings: dict[str, str]) -> Plan: ...

Two more are optional. ``PLUGIN_VERSION`` is a plain string, shown in
Configure Plugins and read as nothing but text — comictrans does not compare
it to anything. ``REQUIRES_APP_VERSION`` is: the oldest comictrans a plugin
declares itself to work with, checked at discovery time against this
build's own version. A plugin that asks for a newer one than this becomes a
:class:`FailedPlugin`, the same as a missing ``PLUGIN_NAME`` would — running
a plugin written against an interface this build does not have is not a
risk worth taking silently.

``run`` gets the plan as it stands, and the plugin's own settings resolved
to their current values — see :class:`SettingField` for how a plugin
declares what it takes, and ``gui.plugin_settings`` for where a value a
person chose is kept. A plugin with nothing to configure still takes
``settings``; it is simply handed an empty dict.

``run`` returns the plan it should become. It may change field values on
regions already in the plan — a translation, a note, a flag, even a
polygon — but not add, remove, reorder, or move a region between pages,
and not touch the header or the page list. That is what keeps a plugin's
result one checkable, whole-plan undo step rather than a second place the
plan's *shape* can change: see :func:`run_plugin`.

Loading a plugin runs whatever top-level code its ``__init__.py`` has, same
as any ``import`` would; nothing here sandboxes that, because nothing in
Python does. Only the disclaimer stands between a plugin and the
interpreter it runs in, which is why the menu this hides behind spells
that out.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .errors import PluginError
from .model import Plan

log = logging.getLogger(__name__)

APPLICATION = "comictrans"

PLUGIN_PATH_ENV = "COMICTRANS_PLUGIN_PATH"
"""Overrides where plugins are read from — the same escape hatch
``COMICTRANS_FONT_PATH`` and ``COMICTRANS_LOG_DIR`` are for their own
directories, and for the same reason: the suite must not read whatever is
sitting in the directory of whoever runs it."""

SETTING_TYPES = frozenset({"str"})
"""Every type a :class:`SettingField` may declare.

One member today — the smallest thing that works for the one plugin that
ships — checked at runtime rather than typed as a ``Literal``: a plugin is
loaded from an arbitrary file, so an unknown type is exactly the kind of
fact this has to catch rather than trust a type checker to have already
ruled out. Adding a second kind of field later is additive: a new member
here, a new widget in ``gui.plugin_config_dialog``, nothing about a plugin
that only ever declared ``"str"`` changes underneath it.
"""


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
class SettingField:
    """One value a plugin lets a person configure, and what the dialog needs to show it.

    ``key`` is what ``run`` finds it under in ``settings``, and what
    ``gui.plugin_settings`` stores it under — stable, so renaming ``label``
    to reword a form does not lose a value someone already set. ``default``
    is a plain string like every other stored value here, on the same
    "empty means unset" terms ``gui.preferences`` uses.
    """

    key: str
    label: str
    default: str = ""
    type: str = "str"


@dataclass(frozen=True, slots=True)
class LoadedPlugin:
    """One plugin folder that imported cleanly and declared what it needed to."""

    name: str
    path: Path
    """The plugin's own folder — not the ``__init__.py`` inside it."""
    run: Callable[[Plan, dict[str, str]], Plan]
    settings: tuple[SettingField, ...] = ()
    """What this plugin lets a person configure, in declaration order.
    Empty for a plugin with nothing to set — ``run`` still takes a
    ``settings`` dict, just an empty one."""
    version: str = ""
    """The plugin's own ``PLUGIN_VERSION``, or empty if it declared none.
    Text only — nothing here compares it to anything, which is
    ``REQUIRES_APP_VERSION`` and the running comictrans, not this."""


@dataclass(frozen=True, slots=True)
class FailedPlugin:
    """A folder that looked like a plugin but did not become one, and why.

    Kept distinct from being silently left out — a folder with no
    ``__init__.py`` at all is not this, see :func:`discover_plugins` — so
    that Configure Plugins can say a folder is there and broken rather than
    making it indistinguishable from nothing having been dropped in yet.
    """

    path: Path
    error: str


def discover_plugins(directory: Path | None = None) -> list[LoadedPlugin | FailedPlugin]:
    """Import every plugin in ``directory``, in folder-name order.

    A plugin is a folder, not a loose file. One with no ``__init__.py`` —
    ``__pycache__``, a stray file, a folder that is not a plugin at all —
    is left out entirely: nothing was ever dropped in to report on. One
    whose ``__init__.py`` exists but fails to import, or does not declare a
    usable ``PLUGIN_NAME``, ``run`` and ``SETTINGS``, comes back as a
    :class:`FailedPlugin` instead of being dropped — worth a line in
    Configure Plugins, since somebody did put something here. Either way
    one broken plugin never hides a working one next to it. This is a
    *load* failure; see :func:`run_plugin` for what happens when a plugin
    runs and fails instead, which is a different thing reported a
    different way.
    """
    directory = directory or plugin_directory()
    if not directory.is_dir():
        return []
    entries = sorted(directory.iterdir())
    return [result for entry in entries if (result := _load_one(entry)) is not None]


def _load_one(folder: Path) -> LoadedPlugin | FailedPlugin | None:
    """Import one plugin folder as a package, ``__init__.py`` its entry point.

    ``None`` when ``folder`` plainly never claimed to be a plugin — nothing
    to report. A package rather than a bare module because a plugin may be
    more than one file: naming the entry point ``__init__.py`` is what lets
    it say ``from . import helper`` and find a sibling in the same folder,
    exactly as it would in any other Python package — ``importlib`` infers
    ``submodule_search_locations`` from that name on its own.
    """
    entry = folder / "__init__.py"
    if not entry.is_file():
        return None
    # A name unique to this call, not to the folder: re-discovering must not
    # collide with a package the same folder was loaded as last time, and a
    # plugin's own sibling modules must not collide with another plugin's.
    module_name = f"comictrans._plugin_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, entry)
    if spec is None or spec.loader is None:
        log.warning("%s: could not be read as a Python package", folder)
        return FailedPlugin(folder, "could not be read as a Python package")
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: a relative import in __init__.py needs its own
    # package findable in sys.modules while that import runs, the same as
    # any Python package being imported for the first time.
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # entirely unknown code — the plugin's own top level
        log.warning("%s: failed to load: %s", folder, exc)
        del sys.modules[module_name]
        return FailedPlugin(folder, str(exc))

    name = getattr(module, "PLUGIN_NAME", None)
    run = getattr(module, "run", None)
    if not isinstance(name, str) or not name or not callable(run):
        log.warning("%s: missing PLUGIN_NAME or run()", folder)
        del sys.modules[module_name]
        return FailedPlugin(folder, "missing PLUGIN_NAME or run()")

    version, error = _read_version(module)
    if error is not None:
        del sys.modules[module_name]
        return FailedPlugin(folder, error)

    error = _check_app_version(module)
    if error is not None:
        del sys.modules[module_name]
        return FailedPlugin(folder, error)

    settings, error = _read_settings(module)
    if error is not None:
        del sys.modules[module_name]
        return FailedPlugin(folder, error)

    return LoadedPlugin(name=name, path=folder, run=run, settings=settings, version=version)


def _read_version(module: object) -> tuple[str, str | None]:
    """The plugin's own ``PLUGIN_VERSION``, or an error naming what was wrong.

    Absent is fine, the empty string a plugin that never declared one also
    gets. Present and not a string is not, the same standard ``PLUGIN_NAME``
    is held to — this is shown as text in Configure Plugins, and nothing
    here can show what is not one.
    """
    declared = getattr(module, "PLUGIN_VERSION", "")
    if not isinstance(declared, str):
        return "", "PLUGIN_VERSION must be a string"
    return declared, None


_RELEASE = re.compile(r"\d+(?:\.\d+)*")


def _release(text: str) -> tuple[int, ...] | None:
    """The leading dotted-integer release segment of a version string.

    ``"1.2.0"`` is ``(1, 2, 0)``; ``"1.2.0.dev0"`` is the same tuple, since
    only the release numbers decide compatibility here — comictrans is not
    on PyPI, and a full PEP 440 comparison is a dependency this project does
    not otherwise need for one field.
    """
    match = _RELEASE.match(text.strip())
    return None if match is None else tuple(int(part) for part in match.group().split("."))


def _at_least(actual: tuple[int, ...], required: tuple[int, ...]) -> bool:
    """Whether ``actual`` is ``required`` or newer, treating a short tuple as
    padded with zeros — ``(1, 2)`` and ``(1, 2, 0)`` compare equal."""
    width = max(len(actual), len(required))
    return actual + (0,) * (width - len(actual)) >= required + (0,) * (width - len(required))


def _check_app_version(module: object) -> str | None:
    """``None`` if this build satisfies ``REQUIRES_APP_VERSION``, an error if not.

    Unset, empty, or not a string at all is not a requirement — a plugin
    that never declared one runs on any build, the same as one that never
    declared ``SETTINGS`` has none to configure. Checked at discovery, not
    every run: a plugin too new for this build is a load failure, the same
    kind ``FailedPlugin`` already reports a broken one as.
    """
    declared = getattr(module, "REQUIRES_APP_VERSION", "")
    if not isinstance(declared, str) or not declared.strip():
        return None
    required = _release(declared)
    if required is None:
        return f"REQUIRES_APP_VERSION {declared!r} could not be understood"
    running = _release(__version__)
    if running is None or not _at_least(running, required):
        return f"requires comictrans {declared} or newer, this build is {__version__}"
    return None


def _read_settings(module: object) -> tuple[tuple[SettingField, ...], str | None]:
    """The plugin's ``SETTINGS``, validated, or an error naming what was wrong.

    Absent entirely is fine — that is a plugin with nothing to configure,
    the common case today. Present and malformed is not: a plugin whose
    declared settings the dialog cannot render is a plugin the dialog
    cannot trust, the same standard ``PLUGIN_NAME`` and ``run`` are held to.
    """
    declared = getattr(module, "SETTINGS", ())
    if not isinstance(declared, tuple | list) or not all(
        isinstance(field, SettingField) for field in declared
    ):
        return (), "SETTINGS must be a tuple of SettingField"
    fields = tuple(declared)
    keys = [field.key for field in fields]
    if len(keys) != len(set(keys)):
        return (), "SETTINGS has two fields with the same key"
    for field in fields:
        if field.type not in SETTING_TYPES:
            return (), f"SETTINGS field {field.key!r} has an unknown type {field.type!r}"
    return fields, None


def run_plugin(plugin: LoadedPlugin, plan: Plan, settings: dict[str, str] | None = None) -> Plan:
    """Run one plugin over ``plan`` and hand back the plan it returned.

    ``settings`` is what ``run`` sees; left out, it is built from the
    plugin's own declared defaults, which is what every caller that does
    not care about a person's overrides wants — ``gui.main_window`` passes
    the resolved values it read out of ``QSettings`` instead.

    Raises :class:`PluginError` rather than letting anything through
    unchecked: the plugin's own exception, a return value that is not a
    ``Plan``, or a ``Plan`` whose header, pages, or region ids and page
    assignments do not match what it was given — only field values on
    existing regions may change. The plan passed in is never touched by
    this call either way; nothing here writes anything back, that is the
    caller's decision once it trusts the result.
    """
    if settings is None:
        settings = {field.key: field.default for field in plugin.settings}
    try:
        result = plugin.run(plan, settings)
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
    "SETTING_TYPES",
    "FailedPlugin",
    "LoadedPlugin",
    "SettingField",
    "discover_plugins",
    "plugin_directory",
    "run_plugin",
]
