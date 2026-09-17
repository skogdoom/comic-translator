"""Where a plugin's own settings and active flag live in ``QSettings``.

Keyed by folder name — the same identity :func:`plugins.discover_plugins`
already uses, see ``LoadedPlugin.path`` — under ``plugins/``, beside
``preferences/`` (:data:`preferences.PREFIX`) and ``window/``.

Unlike :class:`preferences.Preferences`, a plugin's set of fields is not
fixed in this codebase: it is declared by the plugin's own ``SETTINGS``,
which can differ from one plugin to the next and can change when a plugin's
own file changes. So this reads and writes one field at a time rather than
round-tripping a whole dataclass the way ``load_preferences`` and
``save_preferences`` do.
"""

from __future__ import annotations

from ..plugins import LoadedPlugin
from .preferences import SettingsStore

PREFIX = "plugins/"

ACTIVE_ON = "yes"
ACTIVE_OFF = "no"


def _key(plugin: LoadedPlugin, *parts: str) -> str:
    return "/".join((f"{PREFIX}{plugin.path.name}", *parts))


def _clean(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def is_active(store: SettingsStore | None, plugin: LoadedPlugin) -> bool:
    """Whether this plugin belongs in the run menu.

    Active until told otherwise. Nothing stored yet is what a plugin just
    discovered looks like, and the whole point of the one that ships
    already installed is that it is ready to run the moment it is there —
    not something to come here and turn on first.
    """
    if store is None:
        return True
    return _clean(store.value(_key(plugin, "active"), ACTIVE_ON)) != ACTIVE_OFF


def set_active(store: SettingsStore | None, plugin: LoadedPlugin, active: bool) -> None:
    if store is None:
        return
    store.setValue(_key(plugin, "active"), ACTIVE_ON if active else ACTIVE_OFF)


def resolved_settings(store: SettingsStore | None, plugin: LoadedPlugin) -> dict[str, str]:
    """Every field this plugin declares, at its current value.

    Stored if somebody changed it, the field's own declared default
    otherwise — the same "empty means unset" rule ``load_preferences``
    reads by, applied one field at a time since there is no fixed
    dataclass to round-trip here.
    """
    if store is None:
        return {field.key: field.default for field in plugin.settings}
    values: dict[str, str] = {}
    for field in plugin.settings:
        stored = _clean(store.value(_key(plugin, "settings", field.key), ""))
        values[field.key] = stored or field.default
    return values


def set_setting(store: SettingsStore | None, plugin: LoadedPlugin, key: str, value: str) -> None:
    if store is None:
        return
    store.setValue(_key(plugin, "settings", key), value)


__all__ = [
    "ACTIVE_OFF",
    "ACTIVE_ON",
    "PREFIX",
    "is_active",
    "resolved_settings",
    "set_active",
    "set_setting",
]
