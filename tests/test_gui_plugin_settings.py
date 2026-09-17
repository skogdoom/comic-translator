"""Where a plugin's active flag and settings values live in QSettings.

No Qt: like ``preferences.py``, everything here is tested against a plain
dictionary standing in for ``QSettings``.
"""

from __future__ import annotations

from pathlib import Path

from comictrans.gui import plugin_settings
from comictrans.plugins import LoadedPlugin, SettingField


class FakeStore:
    """The two methods of ``QSettings`` this module actually uses."""

    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = dict(values or {})

    def value(self, key: str, defaultValue: object = None) -> object:  # noqa: N803
        return self.values.get(key, defaultValue)

    def setValue(self, key: str, value: object) -> None:  # noqa: N802
        self.values[key] = value


def _plugin(name: str = "add_a_note", *settings: SettingField) -> LoadedPlugin:
    return LoadedPlugin(
        name="Example",
        path=Path(f"/plugins/{name}"),
        run=lambda plan, values: plan,
        settings=settings,
    )


# -- active -----------------------------------------------------------------


def test_a_plugin_nothing_has_touched_is_active() -> None:
    assert plugin_settings.is_active(FakeStore(), _plugin()) is True


def test_a_window_with_no_settings_treats_every_plugin_as_active() -> None:
    assert plugin_settings.is_active(None, _plugin()) is True


def test_turning_a_plugin_off_is_remembered() -> None:
    store = FakeStore()
    plugin = _plugin()

    plugin_settings.set_active(store, plugin, False)

    assert plugin_settings.is_active(store, plugin) is False


def test_turning_it_back_on_is_remembered_too() -> None:
    store = FakeStore()
    plugin = _plugin()
    plugin_settings.set_active(store, plugin, False)

    plugin_settings.set_active(store, plugin, True)

    assert plugin_settings.is_active(store, plugin) is True


def test_setting_active_on_a_store_less_window_does_nothing_and_does_not_raise() -> None:
    plugin_settings.set_active(None, _plugin(), False)  # must not raise


def test_two_plugins_are_kept_apart_by_folder_name() -> None:
    store = FakeStore()
    plugin_settings.set_active(store, _plugin("first"), False)

    assert plugin_settings.is_active(store, _plugin("first")) is False
    assert plugin_settings.is_active(store, _plugin("second")) is True


# -- settings values ----------------------------------------------------


def test_resolved_settings_start_at_their_declared_defaults() -> None:
    field = SettingField(key="text", label="Text", default="fallback")
    plugin = _plugin("add_a_note", field)

    assert plugin_settings.resolved_settings(FakeStore(), plugin) == {"text": "fallback"}


def test_a_window_with_no_settings_still_returns_the_declared_defaults() -> None:
    field = SettingField(key="text", label="Text", default="fallback")
    plugin = _plugin("add_a_note", field)

    assert plugin_settings.resolved_settings(None, plugin) == {"text": "fallback"}


def test_a_plugin_with_no_declared_fields_resolves_to_an_empty_dict() -> None:
    assert plugin_settings.resolved_settings(FakeStore(), _plugin()) == {}


def test_a_changed_value_is_remembered() -> None:
    store = FakeStore()
    field = SettingField(key="text", label="Text", default="fallback")
    plugin = _plugin("add_a_note", field)

    plugin_settings.set_setting(store, plugin, "text", "chosen")

    assert plugin_settings.resolved_settings(store, plugin) == {"text": "chosen"}


def test_clearing_a_value_back_to_empty_falls_back_to_the_default() -> None:
    """The same 'empty means unset' rule ``load_preferences`` reads by."""
    store = FakeStore()
    field = SettingField(key="text", label="Text", default="fallback")
    plugin = _plugin("add_a_note", field)
    plugin_settings.set_setting(store, plugin, "text", "chosen")

    plugin_settings.set_setting(store, plugin, "text", "")

    assert plugin_settings.resolved_settings(store, plugin) == {"text": "fallback"}


def test_setting_a_value_on_a_store_less_window_does_nothing_and_does_not_raise() -> None:
    field = SettingField(key="text", label="Text", default="fallback")
    plugin_settings.set_setting(None, _plugin("add_a_note", field), "text", "chosen")  # no raise


def test_two_plugins_own_settings_do_not_collide() -> None:
    store = FakeStore()
    field = SettingField(key="text", label="Text", default="fallback")
    plugin_settings.set_setting(store, _plugin("first", field), "text", "for first")

    assert plugin_settings.resolved_settings(store, _plugin("first", field)) == {
        "text": "for first"
    }
    assert plugin_settings.resolved_settings(store, _plugin("second", field)) == {
        "text": "fallback"
    }
