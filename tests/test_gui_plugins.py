"""The Plugins menu: hidden by default, and what it does once switched on.

The plugin runtime itself — discovery, and what a plugin is and is not
allowed to change — is covered without Qt in test_plugins.py. Where a
plugin's active flag and settings values are stored is covered without Qt
in test_gui_plugin_settings.py. What is worth testing here is the window's
side of it: the menu only exists when Preferences says so, the example
plugin is there the first time without being asked for, it is not
rescanned just by being looked at, running a plugin from it reaches the
open document the same way undo does, and Configure Plugins can turn one
off, edit its settings, and show a broken one's error — without any of
that re-importing a single plugin.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from comictrans import plugins
from comictrans.gui.preferences import Preferences
from comictrans.model import Box, Color, Geometry, PlanHeader, Region, TextCase
from comictrans.planfile import write_plan
from comictrans.plugins import LoadedPlugin
from comictrans.util import sha256_file

from .conftest import ART_DARK, BALLOON_WHITE, INK_BLACK, make_page_array, make_plan, save_page

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMenu

from comictrans.gui.main_window import MainWindow

BALLOON = Box(80, 80, 520, 320)
TEXT_BOX = Box(140, 170, 460, 210)

EXAMPLE_NAME = "Add a Note to Every Region"

UPPERCASE_NOTES = """\
from dataclasses import replace

PLUGIN_NAME = "Uppercase Notes"


def run(plan, settings):
    return replace(
        plan, regions=tuple(replace(r, notes=r.notes.upper()) for r in plan.regions)
    )
"""


def _header(**overrides: object) -> PlanHeader:
    base: dict[str, object] = {
        "version": 3,
        "generator": "comictrans test",
        "created": "2026-09-17T12:00:00Z",
        "source_language": "it",
        "target_language": "en",
        "ocr_engine": "fake",
        "font": "Comic Sans MS",
        "case": TextCase.UPPER,
        "font_size_min_ratio": 0.012,
        "condense_min": 0.9,
    }
    base.update(overrides)
    return PlanHeader(**base)  # type: ignore[arg-type]


def _region(image: str, **overrides: object) -> Region:
    base: dict[str, object] = {
        "id": "page-001-001",
        "image": image,
        "order": 1,
        "geometry": Geometry.EXACT,
        "polygon": BALLOON.as_polygon(),
        "fill_color": Color(250, 250, 250),
        "text_color": Color(20, 20, 20),
        "confidence": 0.9,
        "source_text": "CIAO",
        "translation": "HELLO",
        "notes": "hush",
    }
    base.update(overrides)
    return Region(**base)  # type: ignore[arg-type]


@pytest.fixture
def one_page_plan(tmp_path: Path) -> Path:
    source = tmp_path / "pages"
    source.mkdir()
    image = save_page(
        make_page_array(
            (600, 400), ART_DARK, [("ellipse", BALLOON, BALLOON_WHITE, INK_BLACK, [TEXT_BOX])]
        ),
        source / "page-001.png",
    )
    plan_path = source / "comic-plan.yaml"
    plan = make_plan(
        _header(),
        (_region("page-001.png"),),
        {"page-001.png": sha256_file(image)},
    )
    write_plan(plan, plan_path)
    return plan_path


@pytest.fixture(autouse=True)
def _empty_plugin_directory(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Every test starts from its own empty plugin directory, never a real one."""
    directory = tmp_path_factory.mktemp("plugins")
    monkeypatch.setenv(plugins.PLUGIN_PATH_ENV, str(directory))
    return directory


def _write(directory: Path, plugin_name: str, source: str) -> Path:
    """Write one plugin folder, with ``source`` as its ``__init__.py``."""
    folder = directory / plugin_name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "__init__.py").write_text(source, encoding="utf-8")
    return folder


def _plugins_menu(window: MainWindow) -> QMenu:
    for action in window.menuBar().actions():
        if action.text() == "Pl&ugins":
            menu = action.menu()
            assert menu is not None
            return menu
    raise AssertionError("no Plugins menu")


def _settings_in(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


# -- visibility -----------------------------------------------------------


def test_the_plugins_menu_is_hidden_until_experimental_features_are_on(qapp: object) -> None:
    window = MainWindow()

    assert window._plugins_menu.menuAction().isVisible() is False


def test_turning_on_experimental_features_shows_and_populates_the_menu(
    qapp: object, _empty_plugin_directory: Path
) -> None:
    _write(_empty_plugin_directory, "uppercase", UPPERCASE_NOTES)
    window = MainWindow()

    window._on_preferences_changed(Preferences(experimental="yes"))

    assert window._plugins_menu.menuAction().isVisible() is True
    assert {p.name for p in window._plugins} == {EXAMPLE_NAME, "Uppercase Notes"}
    assert any(a.text() == "Uppercase Notes" for a in window._plugin_run_actions)


def test_turning_off_experimental_features_hides_the_menu_again(qapp: object) -> None:
    window = MainWindow()
    window._on_preferences_changed(Preferences(experimental="yes"))

    window._on_preferences_changed(Preferences(experimental=""))

    assert window._plugins_menu.menuAction().isVisible() is False


def test_the_menu_is_not_rescanned_just_by_being_shown(
    qapp: object, _empty_plugin_directory: Path
) -> None:
    """Opening the menu must not itself import whatever is sitting there."""
    window = MainWindow()
    window._on_preferences_changed(Preferences(experimental="yes"))
    assert [p.name for p in window._plugins] == [EXAMPLE_NAME]

    _write(_empty_plugin_directory, "uppercase", UPPERCASE_NOTES)
    _plugins_menu(window).aboutToShow.emit()

    assert [p.name for p in window._plugins] == [EXAMPLE_NAME], (
        "a file dropped in after the fact needs Rescan Plugins"
    )


def test_rescan_plugins_picks_up_a_newly_added_file(
    qapp: object, _empty_plugin_directory: Path
) -> None:
    window = MainWindow()
    window._on_preferences_changed(Preferences(experimental="yes"))
    _write(_empty_plugin_directory, "uppercase", UPPERCASE_NOTES)

    window._on_rescan_plugins()

    assert {p.name for p in window._plugins} == {EXAMPLE_NAME, "Uppercase Notes"}
    assert "2 plugins" in window.statusBar().currentMessage()


# -- the example, already installed ------------------------------------------


def test_turning_on_experimental_features_installs_the_example_automatically(
    qapp: object, _empty_plugin_directory: Path
) -> None:
    """No install step: it is there the first time anyone looks."""
    window = MainWindow()

    window._on_preferences_changed(Preferences(experimental="yes"))

    installed = _empty_plugin_directory / "add_a_note" / "__init__.py"
    assert installed.is_file()
    assert any(a.text() == EXAMPLE_NAME for a in window._plugin_run_actions)


def test_an_older_installed_copy_of_the_example_is_brought_up_to_date(
    qapp: object, _empty_plugin_directory: Path
) -> None:
    """A machine that turned this on before a change to the example must still get it.

    The example is a demonstration of what a plugin looks like today, not a
    personal fork — its one configurable value has lived in Configure
    Plugins, not the file, since that shipped.
    """
    _write(
        _empty_plugin_directory,
        "add_a_note",
        'PLUGIN_NAME = "Add a Note to Every Region"\n\n\n'
        "def run(plan, settings):\n    return plan\n",
    )
    window = MainWindow()

    window._on_preferences_changed(Preferences(experimental="yes"))

    plugin = next(p for p in window._plugins if isinstance(p, LoadedPlugin))
    assert plugin.settings, "the installed copy should now be the current one, settings and all"


def test_a_setting_chosen_in_configure_plugins_survives_being_brought_up_to_date(
    qapp: object, tmp_path: Path, _empty_plugin_directory: Path
) -> None:
    from comictrans.gui import plugin_settings

    window = MainWindow(settings=_settings_in(tmp_path))
    window._on_preferences_changed(Preferences(experimental="yes"))
    plugin = next(p for p in window._plugins if isinstance(p, LoadedPlugin))
    plugin_settings.set_setting(window._settings, plugin, "note_text", "chosen earlier")

    window._on_rescan_plugins()  # re-installs the example over itself

    plugin = next(p for p in window._plugins if isinstance(p, LoadedPlugin))
    assert plugin_settings.resolved_settings(window._settings, plugin) == {
        "note_text": "chosen earlier"
    }


# -- the plugin folder ------------------------------------------------------


def test_opening_the_plugin_folder_makes_it_first(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "not-there-yet"
    monkeypatch.setenv(plugins.PLUGIN_PATH_ENV, str(target))
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda _url: True)
    window = MainWindow()

    window._on_open_plugin_folder()

    assert target.is_dir()


def test_a_desktop_that_will_not_open_it_still_says_where_it_is(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(plugins.PLUGIN_PATH_ENV, str(tmp_path))
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda _url: False)
    window = MainWindow()

    window._on_open_plugin_folder()

    assert str(tmp_path) in window.statusBar().currentMessage()


# -- running one ------------------------------------------------------------


def test_running_a_plugin_from_the_menu_edits_the_plan_as_one_undo_step(
    qapp: object, one_page_plan: Path, _empty_plugin_directory: Path
) -> None:
    _write(_empty_plugin_directory, "uppercase", UPPERCASE_NOTES)
    window = MainWindow()
    window.open_plan(one_page_plan)
    window._on_preferences_changed(Preferences(experimental="yes"))

    action = next(a for a in window._plugin_run_actions if a.text() == "Uppercase Notes")
    action.trigger()

    assert window.document is not None
    assert window.document.region("page-001-001").notes == "HUSH"
    assert window.document.can_undo
    assert "Uppercase Notes ran" in window.statusBar().currentMessage()

    window.document.undo()
    assert window.document.region("page-001-001").notes == "hush"


def test_a_plugin_that_makes_no_change_says_so_and_costs_no_undo_step(
    qapp: object, one_page_plan: Path, _empty_plugin_directory: Path
) -> None:
    _write(
        _empty_plugin_directory,
        "noop",
        'PLUGIN_NAME = "No Op"\n\n\ndef run(plan, settings):\n    return plan\n',
    )
    window = MainWindow()
    window.open_plan(one_page_plan)
    window._on_preferences_changed(Preferences(experimental="yes"))

    action = next(a for a in window._plugin_run_actions if a.text() == "No Op")
    action.trigger()

    assert not window.document.can_undo  # type: ignore[union-attr]
    assert "made no change" in window.statusBar().currentMessage()


def test_a_plugin_that_throws_is_reported_and_the_plan_is_untouched(
    qapp: object, one_page_plan: Path, _empty_plugin_directory: Path
) -> None:
    _write(
        _empty_plugin_directory,
        "explodes",
        'PLUGIN_NAME = "Explodes"\n\n\ndef run(plan, settings):\n    raise ValueError("nope")\n',
    )
    window = MainWindow()
    window.open_plan(one_page_plan)
    window._on_preferences_changed(Preferences(experimental="yes"))
    before = window.document.plan  # type: ignore[union-attr]

    action = next(a for a in window._plugin_run_actions if a.text() == "Explodes")
    action.trigger()

    assert window.document.plan == before  # type: ignore[union-attr]
    assert not window.document.can_undo  # type: ignore[union-attr]
    assert "running Explodes" in window.statusBar().currentMessage()


def test_a_plugin_that_restructures_the_plan_is_refused_and_the_plan_is_untouched(
    qapp: object, one_page_plan: Path, _empty_plugin_directory: Path
) -> None:
    _write(
        _empty_plugin_directory,
        "removes",
        "from dataclasses import replace\n\n"
        'PLUGIN_NAME = "Removes A Region"\n\n\n'
        "def run(plan, settings):\n    return replace(plan, regions=())\n",
    )
    window = MainWindow()
    window.open_plan(one_page_plan)
    window._on_preferences_changed(Preferences(experimental="yes"))
    before = window.document.plan  # type: ignore[union-attr]

    action = next(a for a in window._plugin_run_actions if a.text() == "Removes A Region")
    action.trigger()

    assert window.document.plan == before  # type: ignore[union-attr]
    assert "running Removes A Region" in window.statusBar().currentMessage()


def test_plugin_actions_are_disabled_without_a_document(
    qapp: object, _empty_plugin_directory: Path
) -> None:
    _write(_empty_plugin_directory, "uppercase", UPPERCASE_NOTES)
    window = MainWindow()
    window._on_preferences_changed(Preferences(experimental="yes"))

    action = next(a for a in window._plugin_run_actions if a.text() == "Uppercase Notes")

    assert action.isEnabled() is False


# -- configuring: active, inactive, and failed -------------------------


def test_the_configure_plugins_action_is_in_the_menu(qapp: object) -> None:
    window = MainWindow()

    assert window._configure_plugins_action.text() == "&Configure Plugins…"
    assert window._configure_plugins_action in window._plugins_menu.actions()


def test_no_action_in_this_menu_is_left_to_the_menu_role_heuristic(
    qapp: object, _empty_plugin_directory: Path
) -> None:
    """ "Configure Plugins…" once read as "Preferences" to macOS and replaced it.

    ``test_every_menu_role_macos_moves_is_spelled_out`` in test_gui_widgets.py
    checks that only About, Preferences and Quit carry an explicit role —
    it does not, and cannot from this platform, catch an action left at
    the default ``TextHeuristicRole`` that a real Cocoa menu bar would have
    folded into one of those anyway. Every action in this menu, including
    one named for a plugin's own arbitrary ``PLUGIN_NAME``, has to rule
    that out explicitly instead.
    """
    from PySide6.QtGui import QAction

    _write(_empty_plugin_directory, "uppercase", UPPERCASE_NOTES)
    window = MainWindow()
    window._on_preferences_changed(Preferences(experimental="yes"))

    for action in window._plugins_menu.actions():
        if action.isSeparator():
            continue
        assert action.menuRole() == QAction.MenuRole.NoRole, action.text()


def test_an_inactive_plugin_has_no_run_action(
    qapp: object, tmp_path: Path, _empty_plugin_directory: Path
) -> None:
    from comictrans.gui import plugin_settings

    _write(_empty_plugin_directory, "uppercase", UPPERCASE_NOTES)
    window = MainWindow(settings=_settings_in(tmp_path))
    window._on_preferences_changed(Preferences(experimental="yes"))
    plugin = next(p for p in window._plugins if p.name == "Uppercase Notes")

    plugin_settings.set_active(window._settings, plugin, False)
    window._rebuild_plugin_actions()

    assert not any(a.text() == "Uppercase Notes" for a in window._plugin_run_actions)
    # Still discovered, and still Configure Plugins' to show — just not runnable.
    assert any(p.name == "Uppercase Notes" for p in window._plugins)


def test_rebuilding_actions_does_not_reimport_anything(
    qapp: object, tmp_path: Path, _empty_plugin_directory: Path
) -> None:
    """Toggling active in Configure Plugins must not re-run every plugin's top level."""
    marker = _empty_plugin_directory / "ran.txt"
    _write(
        _empty_plugin_directory,
        "sideeffect",
        f"""\
with open({str(marker)!r}, "a") as _f:
    _f.write("x")

PLUGIN_NAME = "Side Effect"


def run(plan, settings):
    return plan
""",
    )
    window = MainWindow(settings=_settings_in(tmp_path))
    window._on_preferences_changed(Preferences(experimental="yes"))
    ran_once = marker.read_text()

    window._rebuild_plugin_actions()
    window._rebuild_plugin_actions()

    assert marker.read_text() == ran_once


def test_a_failed_plugin_has_no_run_action(qapp: object, _empty_plugin_directory: Path) -> None:
    _write(_empty_plugin_directory, "broken", "not python at all (")
    window = MainWindow()

    window._on_preferences_changed(Preferences(experimental="yes"))

    assert not any(a.text() == "broken" for a in window._plugin_run_actions)
    from comictrans.plugins import FailedPlugin

    assert any(isinstance(p, FailedPlugin) for p in window._plugins)


def test_configure_plugins_opens_with_every_discovered_plugin_listed(
    qapp: object, _empty_plugin_directory: Path
) -> None:
    from comictrans.gui.plugin_config_dialog import PluginConfigDialog

    _write(_empty_plugin_directory, "uppercase", UPPERCASE_NOTES)
    _write(_empty_plugin_directory, "broken", "not python at all (")
    window = MainWindow()
    window._on_preferences_changed(Preferences(experimental="yes"))

    dialog = PluginConfigDialog(window._settings, window._plugins, window)

    shown = {dialog._list.item(row).text() for row in range(dialog._list.count())}
    assert shown == {EXAMPLE_NAME, "Uppercase Notes", "broken"}


def test_toggling_active_in_the_dialog_rebuilds_the_run_menu(
    qapp: object, tmp_path: Path, _empty_plugin_directory: Path
) -> None:
    from PySide6.QtWidgets import QCheckBox

    from comictrans.gui.plugin_config_dialog import PluginConfigDialog

    _write(_empty_plugin_directory, "uppercase", UPPERCASE_NOTES)
    window = MainWindow(settings=_settings_in(tmp_path))
    window._on_preferences_changed(Preferences(experimental="yes"))

    dialog = PluginConfigDialog(window._settings, window._plugins, window)
    dialog.changed.connect(window._rebuild_plugin_actions)
    row = next(
        row
        for row in range(dialog._list.count())
        if dialog._list.item(row).text() == "Uppercase Notes"
    )
    dialog._list.setCurrentRow(row)
    checkbox = dialog.findChild(QCheckBox)
    assert checkbox is not None

    checkbox.setChecked(False)

    assert not any(a.text() == "Uppercase Notes" for a in window._plugin_run_actions)


def test_editing_a_setting_in_the_dialog_is_used_the_next_time_the_plugin_runs(
    qapp: object, tmp_path: Path, one_page_plan: Path, _empty_plugin_directory: Path
) -> None:
    from PySide6.QtWidgets import QLineEdit

    from comictrans.gui.plugin_config_dialog import PluginConfigDialog

    window = MainWindow(settings=_settings_in(tmp_path))
    window.open_plan(one_page_plan)
    window._on_preferences_changed(Preferences(experimental="yes"))

    dialog = PluginConfigDialog(window._settings, window._plugins, window)
    row = next(
        row for row in range(dialog._list.count()) if dialog._list.item(row).text() == EXAMPLE_NAME
    )
    dialog._list.setCurrentRow(row)
    edit = dialog.findChild(QLineEdit)
    assert edit is not None

    edit.setText("a note chosen in the dialog")

    action = next(a for a in window._plugin_run_actions if a.text() == EXAMPLE_NAME)
    action.trigger()

    assert window.document.region("page-001-001").notes == "a note chosen in the dialog"  # type: ignore[union-attr]


def test_a_settings_field_is_wider_than_a_plain_line_edit(
    qapp: object, _empty_plugin_directory: Path
) -> None:
    """Not a promise every value fits — just wider than the seventeen-character default."""
    from PySide6.QtWidgets import QLineEdit

    from comictrans.gui.plugin_config_dialog import PluginConfigDialog

    window = MainWindow()
    window._on_preferences_changed(Preferences(experimental="yes"))

    dialog = PluginConfigDialog(window._settings, window._plugins, window)
    row = next(
        row for row in range(dialog._list.count()) if dialog._list.item(row).text() == EXAMPLE_NAME
    )
    dialog._list.setCurrentRow(row)
    edit = dialog.findChild(QLineEdit)
    assert edit is not None

    assert edit.minimumWidth() > QLineEdit().minimumSizeHint().width()


def test_a_plugins_version_is_shown_in_the_dialog(
    qapp: object, _empty_plugin_directory: Path
) -> None:
    from PySide6.QtWidgets import QLabel

    from comictrans.gui.plugin_config_dialog import PluginConfigDialog

    _write(
        _empty_plugin_directory,
        "versioned",
        'PLUGIN_NAME = "Versioned"\nPLUGIN_VERSION = "2.3.1"\n\n\n'
        "def run(plan, settings):\n    return plan\n",
    )
    window = MainWindow()
    window._on_preferences_changed(Preferences(experimental="yes"))

    dialog = PluginConfigDialog(window._settings, window._plugins, window)
    row = next(
        row for row in range(dialog._list.count()) if dialog._list.item(row).text() == "Versioned"
    )
    dialog._list.setCurrentRow(row)

    detail = dialog._detail.itemAt(0).widget()
    assert isinstance(detail, QLabel)
    assert "2.3.1" in detail.text()


def test_a_plugin_with_no_version_shows_no_version_line(
    qapp: object, _empty_plugin_directory: Path
) -> None:
    from PySide6.QtWidgets import QCheckBox

    from comictrans.gui.plugin_config_dialog import PluginConfigDialog

    _write(_empty_plugin_directory, "uppercase", UPPERCASE_NOTES)
    window = MainWindow()
    window._on_preferences_changed(Preferences(experimental="yes"))

    dialog = PluginConfigDialog(window._settings, window._plugins, window)
    row = next(
        row
        for row in range(dialog._list.count())
        if dialog._list.item(row).text() == "Uppercase Notes"
    )
    dialog._list.setCurrentRow(row)

    detail = dialog._detail.itemAt(0).widget()
    assert isinstance(detail, QCheckBox), "no version declared, so Active is the first thing shown"


def test_a_plugin_requiring_a_newer_app_version_fails_to_load(
    qapp: object, _empty_plugin_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window's side of the gate itself: covered without Qt in test_plugins.py."""
    monkeypatch.setattr(plugins, "__version__", "1.0.0")
    _write(
        _empty_plugin_directory,
        "toonew",
        'PLUGIN_NAME = "Too New"\nREQUIRES_APP_VERSION = "9.0.0"\n\n\n'
        "def run(plan, settings):\n    return plan\n",
    )
    window = MainWindow()

    window._on_preferences_changed(Preferences(experimental="yes"))

    assert not any(p.name == "Too New" for p in window._plugins if isinstance(p, LoadedPlugin))
    failed = next(
        p for p in window._plugins if not isinstance(p, LoadedPlugin) and p.path.name == "toonew"
    )
    assert "9.0.0" in failed.error


def test_a_failed_plugin_shows_its_error_instead_of_settings(
    qapp: object, _empty_plugin_directory: Path
) -> None:
    from PySide6.QtWidgets import QLabel

    from comictrans.gui.plugin_config_dialog import PluginConfigDialog

    _write(_empty_plugin_directory, "broken", "not python at all (")
    window = MainWindow()
    window._on_preferences_changed(Preferences(experimental="yes"))

    dialog = PluginConfigDialog(window._settings, window._plugins, window)
    row = next(
        row for row in range(dialog._list.count()) if dialog._list.item(row).text() == "broken"
    )
    assert dialog._list.item(row).toolTip()  # the raw error, from discover_plugins

    dialog._list.setCurrentRow(row)

    detail = dialog._detail.itemAt(0).widget()
    assert isinstance(detail, QLabel)
    assert "could not be loaded" in detail.text()
