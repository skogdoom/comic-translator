"""The Plugins menu: hidden by default, and what it does once switched on.

The plugin runtime itself — discovery, and what a plugin is and is not
allowed to change — is covered without Qt in test_plugins.py. What is worth
testing here is the window's side of it: the menu only exists when
Preferences says so, the example plugin is there the first time without
being asked for, it is not rescanned just by being looked at, and running a
plugin from it reaches the open document the same way undo does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from comictrans import plugins
from comictrans.gui.preferences import Preferences
from comictrans.model import Box, Color, Geometry, PlanHeader, Region, TextCase
from comictrans.planfile import write_plan
from comictrans.util import sha256_file

from .conftest import ART_DARK, BALLOON_WHITE, INK_BLACK, make_page_array, make_plan, save_page

pytest.importorskip("PySide6")

from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMenu

from comictrans.gui.main_window import MainWindow

BALLOON = Box(80, 80, 520, 320)
TEXT_BOX = Box(140, 170, 460, 210)

EXAMPLE_NAME = "Add a Note to Every Region"

UPPERCASE_NOTES = """\
from dataclasses import replace

PLUGIN_NAME = "Uppercase Notes"


def run(plan):
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


def test_it_does_not_overwrite_an_edited_copy_of_the_example(
    qapp: object, _empty_plugin_directory: Path
) -> None:
    """Editing the installed example and rescanning must not lose the edit."""
    _write(
        _empty_plugin_directory,
        "add_a_note",
        'PLUGIN_NAME = "Edited"\n\n\ndef run(plan):\n    return plan\n',
    )
    window = MainWindow()

    window._on_preferences_changed(Preferences(experimental="yes"))

    assert [p.name for p in window._plugins] == ["Edited"]


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
        'PLUGIN_NAME = "No Op"\n\n\ndef run(plan):\n    return plan\n',
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
        'PLUGIN_NAME = "Explodes"\n\n\ndef run(plan):\n    raise ValueError("nope")\n',
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
        "def run(plan):\n    return replace(plan, regions=())\n",
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
