"""The plugin runtime: discovery, and running one plugin over a plan.

Everything here runs against an explicit directory the test owns, so
nothing here reads or imports whatever is sitting in the plugin directory
of whoever runs the suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from comictrans import plugins
from comictrans.errors import PluginError
from comictrans.model import Box, Color, Geometry, PlanHeader, Region, TextCase
from comictrans.plugins import FailedPlugin, LoadedPlugin, SettingField

from .conftest import make_plan

BALLOON = Box(80, 80, 520, 320)


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


def _region(image: str = "page-001.png", **overrides: object) -> Region:
    base: dict[str, object] = {
        "id": "r1",
        "image": image,
        "order": 1,
        "geometry": Geometry.EXACT,
        "polygon": BALLOON.as_polygon(),
        "fill_color": Color(250, 250, 250),
        "text_color": Color(20, 20, 20),
        "confidence": 0.9,
        "source_text": "CIAO",
        "translation": "HELLO",
    }
    base.update(overrides)
    return Region(**base)  # type: ignore[arg-type]


def _write(directory: Path, plugin_name: str, source: str) -> Path:
    """Write one plugin folder, with ``source`` as its ``__init__.py``."""
    folder = directory / plugin_name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "__init__.py").write_text(source, encoding="utf-8")
    return folder


def _write_sibling(folder: Path, filename: str, source: str) -> Path:
    """Add another file to an existing plugin folder."""
    path = folder / filename
    path.write_text(source, encoding="utf-8")
    return path


UPPERCASE_NOTES = """\
from dataclasses import replace

PLUGIN_NAME = "Uppercase Notes"


def run(plan, settings):
    return replace(
        plan, regions=tuple(replace(r, notes=r.notes.upper()) for r in plan.regions)
    )
"""


def _named(name: str) -> str:
    """The smallest possible plugin body, named and doing nothing."""
    return f'PLUGIN_NAME = "{name}"\n\n\ndef run(plan, settings):\n    return plan\n'


def _loaded(found: list[LoadedPlugin | FailedPlugin], index: int = 0) -> LoadedPlugin:
    """The plugin at this index, type-narrowed for a test that expects it to have loaded."""
    plugin = found[index]
    assert isinstance(plugin, LoadedPlugin), plugin
    return plugin


def _failed(found: list[LoadedPlugin | FailedPlugin], index: int = 0) -> FailedPlugin:
    plugin = found[index]
    assert isinstance(plugin, FailedPlugin), plugin
    return plugin


# -- where plugins are read from ----------------------------------------


def test_macos_plugins_go_under_application_support(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(plugins.PLUGIN_PATH_ENV, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")

    assert (
        plugins.plugin_directory()
        == Path.home() / "Library" / "Application Support" / "comictrans" / "plugins"
    )


def test_elsewhere_they_go_to_the_xdg_data_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(plugins.PLUGIN_PATH_ENV, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/var/data")

    assert plugins.plugin_directory() == Path("/var/data/comictrans/plugins")

    monkeypatch.delenv("XDG_DATA_HOME")
    assert plugins.plugin_directory() == Path.home() / ".local" / "share" / "comictrans" / "plugins"


def test_the_environment_can_put_them_somewhere_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(plugins.PLUGIN_PATH_ENV, str(tmp_path))
    assert plugins.plugin_directory() == tmp_path


def test_plugin_directory_is_never_created_just_by_asking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    wanted = tmp_path / "not-there-yet"
    monkeypatch.setenv(plugins.PLUGIN_PATH_ENV, str(wanted))

    plugins.plugin_directory()

    assert not wanted.exists()


# -- discovery: found, or not a plugin at all -----------------------------


def test_a_missing_directory_has_no_plugins(tmp_path: Path) -> None:
    assert plugins.discover_plugins(tmp_path / "nowhere") == []


def test_a_working_plugin_is_discovered_and_named(tmp_path: Path) -> None:
    _write(tmp_path, "uppercase", UPPERCASE_NOTES)

    found = plugins.discover_plugins(tmp_path)

    plugin = _loaded(found)
    assert plugin.name == "Uppercase Notes"
    assert plugin.path == tmp_path / "uppercase"


def test_a_loose_file_at_the_top_level_is_not_a_plugin(tmp_path: Path) -> None:
    """A plugin is a folder. A ``.py`` file dropped beside one is not seen at all."""
    (tmp_path / "stray.py").write_text(UPPERCASE_NOTES, encoding="utf-8")

    assert plugins.discover_plugins(tmp_path) == []


def test_a_folder_with_no_init_is_not_a_plugin_at_all(tmp_path: Path) -> None:
    """Nothing was ever dropped in to report on, so this is not a FailedPlugin either."""
    folder = tmp_path / "not_a_plugin"
    folder.mkdir()
    (folder / "notes.txt").write_text("hello", encoding="utf-8")

    assert plugins.discover_plugins(tmp_path) == []


def test_plugins_are_discovered_in_folder_name_order(tmp_path: Path) -> None:
    _write(tmp_path, "b_plugin", _named("B"))
    _write(tmp_path, "a_plugin", _named("A"))

    found = plugins.discover_plugins(tmp_path)

    assert [p.name for p in found if isinstance(p, LoadedPlugin)] == ["A", "B"]


def test_discovery_order_does_not_depend_on_the_filesystems_own_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``iterdir`` makes no ordering promise, so the sort has to be explicit."""
    _write(tmp_path, "b_plugin", _named("B"))
    _write(tmp_path, "a_plugin", _named("A"))
    real_iterdir = Path.iterdir

    def reversed_iterdir(self: Path) -> list[Path]:
        return list(reversed(list(real_iterdir(self))))

    monkeypatch.setattr(Path, "iterdir", reversed_iterdir)

    found = plugins.discover_plugins(tmp_path)

    assert [p.name for p in found if isinstance(p, LoadedPlugin)] == ["A", "B"]


def test_a_plugins_own_top_level_code_runs_once_on_discovery(tmp_path: Path) -> None:
    """Confirms, rather than assumes, that a plugin is real imported code."""
    marker = tmp_path / "ran.txt"
    _write(
        tmp_path,
        "sideeffect",
        f"""\
from pathlib import Path

Path({str(marker)!r}).write_text("yes")

PLUGIN_NAME = "Side Effect"


def run(plan, settings):
    return plan
""",
    )

    plugins.discover_plugins(tmp_path)

    assert marker.read_text() == "yes"


def test_a_plugin_may_be_more_than_one_file(tmp_path: Path) -> None:
    """The folder is an ordinary Python package: __init__.py may import a sibling."""
    folder = _write(
        tmp_path,
        "multi",
        """\
from dataclasses import replace

from .helper import shout

PLUGIN_NAME = "Multi File"


def run(plan, settings):
    return replace(
        plan, regions=tuple(replace(r, notes=shout(r.notes)) for r in plan.regions)
    )
""",
    )
    _write_sibling(folder, "helper.py", 'def shout(text):\n    return text.upper() + "!"\n')

    plugin = _loaded(plugins.discover_plugins(tmp_path))
    plan = make_plan(_header(), [_region(notes="hush")])
    after = plugins.run_plugin(plugin, plan)

    assert after.regions[0].notes == "HUSH!"


def test_two_plugins_can_each_have_a_sibling_module_of_the_same_name(tmp_path: Path) -> None:
    """Each plugin folder is its own package, so same-named siblings never collide."""
    first = _write(
        tmp_path,
        "first",
        "from .helper import VALUE\n\nPLUGIN_NAME = 'First'\n\n\n"
        "def run(plan, settings):\n    return plan\n",
    )
    _write_sibling(first, "helper.py", "VALUE = 1\n")
    second = _write(
        tmp_path,
        "second",
        "from .helper import VALUE\n\nPLUGIN_NAME = 'Second'\n\n\n"
        "def run(plan, settings):\n    return plan\n",
    )
    _write_sibling(second, "helper.py", "VALUE = 2\n")

    found = plugins.discover_plugins(tmp_path)

    assert [p.name for p in found if isinstance(p, LoadedPlugin)] == ["First", "Second"]


# -- discovery: a folder that tried and failed -----------------------------


def test_a_syntax_error_is_a_failed_plugin_not_a_raise(tmp_path: Path) -> None:
    _write(tmp_path, "broken", "def run(plan\n    this is not python")

    failed = _failed(plugins.discover_plugins(tmp_path))

    assert failed.path == tmp_path / "broken"
    assert failed.error


def test_a_plugin_name_missing_is_a_failed_plugin(tmp_path: Path) -> None:
    _write(tmp_path, "nameless", "def run(plan, settings):\n    return plan\n")

    failed = _failed(plugins.discover_plugins(tmp_path))

    assert "PLUGIN_NAME" in failed.error


def test_an_empty_plugin_name_is_a_failed_plugin(tmp_path: Path) -> None:
    _write(
        tmp_path, "nameless", 'PLUGIN_NAME = ""\n\n\ndef run(plan, settings):\n    return plan\n'
    )

    failed = _failed(plugins.discover_plugins(tmp_path))

    assert "PLUGIN_NAME" in failed.error


def test_a_missing_run_is_a_failed_plugin(tmp_path: Path) -> None:
    _write(tmp_path, "runless", 'PLUGIN_NAME = "No Run"\n')

    failed = _failed(plugins.discover_plugins(tmp_path))

    assert "run" in failed.error


def test_one_broken_plugin_does_not_hide_a_working_one(tmp_path: Path) -> None:
    _write(tmp_path, "broken", "not python at all (")
    _write(tmp_path, "uppercase", UPPERCASE_NOTES)

    found = plugins.discover_plugins(tmp_path)

    assert isinstance(found[0], FailedPlugin)
    assert isinstance(found[1], LoadedPlugin)
    assert found[1].name == "Uppercase Notes"


# -- discovery: SETTINGS -----------------------------------------------


def test_a_plugin_with_no_settings_declares_none(tmp_path: Path) -> None:
    _write(tmp_path, "uppercase", UPPERCASE_NOTES)

    plugin = _loaded(plugins.discover_plugins(tmp_path))

    assert plugin.settings == ()


def test_a_plugin_can_declare_a_setting(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "configurable",
        """\
from comictrans.plugins import SettingField

PLUGIN_NAME = "Configurable"

SETTINGS = (SettingField(key="text", label="Text", default="hello"),)


def run(plan, settings):
    return plan
""",
    )

    plugin = _loaded(plugins.discover_plugins(tmp_path))

    assert plugin.settings == (SettingField(key="text", label="Text", default="hello"),)


@pytest.mark.parametrize(
    ("settings_line", "match"),
    [
        ('SETTINGS = "not a tuple"', "must be a tuple"),
        ("SETTINGS = (1, 2)", "must be a tuple"),
        ("SETTINGS = 42", "must be a tuple"),  # not even iterable
        (
            'SETTINGS = (SettingField(key="x", label="X"), SettingField(key="x", label="Y"))',
            "same key",
        ),
        ('SETTINGS = (SettingField(key="x", label="X", type="int"),)', "unknown type"),
    ],
)
def test_a_malformed_settings_declaration_is_a_failed_plugin(
    tmp_path: Path, settings_line: str, match: str
) -> None:
    _write(
        tmp_path,
        "malformed",
        f"""\
from comictrans.plugins import SettingField

PLUGIN_NAME = "Malformed"

{settings_line}


def run(plan, settings):
    return plan
""",
    )

    failed = _failed(plugins.discover_plugins(tmp_path))

    assert match in failed.error


# -- discovery: PLUGIN_VERSION -------------------------------------------


def test_a_plugin_with_no_version_declares_none(tmp_path: Path) -> None:
    _write(tmp_path, "uppercase", UPPERCASE_NOTES)

    plugin = _loaded(plugins.discover_plugins(tmp_path))

    assert plugin.version == ""


def test_a_plugin_can_declare_a_version(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "versioned",
        'PLUGIN_NAME = "Versioned"\nPLUGIN_VERSION = "2.3.1"\n\n\n'
        "def run(plan, settings):\n    return plan\n",
    )

    plugin = _loaded(plugins.discover_plugins(tmp_path))

    assert plugin.version == "2.3.1"


def test_a_non_string_plugin_version_is_a_failed_plugin(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "malformed",
        'PLUGIN_NAME = "Malformed"\nPLUGIN_VERSION = 3\n\n\n'
        "def run(plan, settings):\n    return plan\n",
    )

    failed = _failed(plugins.discover_plugins(tmp_path))

    assert "PLUGIN_VERSION" in failed.error


# -- discovery: REQUIRES_APP_VERSION -------------------------------------


@pytest.mark.parametrize(
    ("running", "required", "should_load"),
    [
        ("1.1.0", "1.1.0", True),
        ("1.2.0", "1.1.0", True),
        ("1.0.0", "1.1.0", False),
        # A dev build satisfies the release it is building toward: only the
        # release numbers decide, comictrans is not on PyPI.
        ("1.1.0.dev0", "1.1.0", True),
        # A short version is padded with zeros, not treated as smaller.
        ("1.1", "1.1.0", True),
        ("1.1.0", "1.1", True),
    ],
    ids=[
        "equal",
        "newer",
        "older",
        "dev-satisfies-its-own-release",
        "short-running",
        "short-required",
    ],
)
def test_app_version_is_checked_at_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    running: str,
    required: str,
    should_load: bool,
) -> None:
    monkeypatch.setattr(plugins, "__version__", running)
    _write(
        tmp_path,
        "gated",
        f'PLUGIN_NAME = "Gated"\nREQUIRES_APP_VERSION = "{required}"\n\n\n'
        "def run(plan, settings):\n    return plan\n",
    )

    found = plugins.discover_plugins(tmp_path)

    if should_load:
        assert isinstance(found[0], LoadedPlugin)
    else:
        failed = _failed(found)
        assert required in failed.error
        assert running in failed.error


def test_a_plugin_with_no_requirement_loads_on_any_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plugins, "__version__", "0.0.1")
    _write(tmp_path, "uppercase", UPPERCASE_NOTES)

    assert isinstance(_loaded(plugins.discover_plugins(tmp_path)), LoadedPlugin)


def test_a_malformed_app_version_requirement_is_a_failed_plugin(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "malformed",
        'PLUGIN_NAME = "Malformed"\nREQUIRES_APP_VERSION = "banana"\n\n\n'
        "def run(plan, settings):\n    return plan\n",
    )

    failed = _failed(plugins.discover_plugins(tmp_path))

    assert "REQUIRES_APP_VERSION" in failed.error


# -- running one ------------------------------------------------------------


def test_running_a_plugin_returns_its_new_plan(tmp_path: Path) -> None:
    folder = _write(tmp_path, "uppercase", UPPERCASE_NOTES)
    plugin = _loaded(plugins.discover_plugins(tmp_path))
    plan = make_plan(_header(), [_region(notes="hush")])

    after = plugins.run_plugin(plugin, plan)

    assert after.regions[0].notes == "HUSH"
    assert plugin.path == folder


def test_running_a_plugin_with_no_settings_hands_it_an_empty_dict(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "records_settings",
        """\
PLUGIN_NAME = "Records Settings"

SEEN = []


def run(plan, settings):
    SEEN.append(settings)
    return plan
""",
    )
    plugin = _loaded(plugins.discover_plugins(tmp_path))
    plan = make_plan(_header(), [_region()])

    plugins.run_plugin(plugin, plan)

    module = sys.modules[plugin.run.__module__]
    assert module.SEEN == [{}]


def test_omitted_settings_default_to_the_declared_values(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "configurable",
        """\
from dataclasses import replace

from comictrans.plugins import SettingField

PLUGIN_NAME = "Configurable"

SETTINGS = (SettingField(key="text", label="Text", default="fallback"),)


def run(plan, settings):
    return replace(
        plan, regions=tuple(replace(r, notes=settings["text"]) for r in plan.regions)
    )
""",
    )
    plugin = _loaded(plugins.discover_plugins(tmp_path))
    plan = make_plan(_header(), [_region()])

    after = plugins.run_plugin(plugin, plan)

    assert after.regions[0].notes == "fallback"

    overridden = plugins.run_plugin(plugin, plan, {"text": "chosen"})
    assert overridden.regions[0].notes == "chosen"


def test_a_plugin_that_throws_is_reported_and_the_plan_is_untouched(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "explodes",
        'PLUGIN_NAME = "Explodes"\n\n\ndef run(plan, settings):\n    raise ValueError("nope")\n',
    )
    plugin = _loaded(plugins.discover_plugins(tmp_path))
    plan = make_plan(_header(), [_region()])

    with pytest.raises(PluginError, match=r"Explodes.*nope"):
        plugins.run_plugin(plugin, plan)


def test_a_plugin_that_returns_something_else_is_refused(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "wrong_type",
        'PLUGIN_NAME = "Wrong Type"\n\n\ndef run(plan, settings):\n    return "not a plan"\n',
    )
    plugin = _loaded(plugins.discover_plugins(tmp_path))
    plan = make_plan(_header(), [_region()])

    with pytest.raises(PluginError, match="did not return a plan"):
        plugins.run_plugin(plugin, plan)


def test_a_plugin_may_not_change_the_header(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "rewrites_header",
        """\
from dataclasses import replace

PLUGIN_NAME = "Rewrites Header"


def run(plan, settings):
    return replace(plan, header=replace(plan.header, target_language="sv"))
""",
    )
    plugin = _loaded(plugins.discover_plugins(tmp_path))
    plan = make_plan(_header(), [_region()])

    with pytest.raises(PluginError, match="header"):
        plugins.run_plugin(plugin, plan)


def test_a_plugin_may_not_change_the_pages(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "rewrites_images",
        """\
from dataclasses import replace

PLUGIN_NAME = "Rewrites Images"


def run(plan, settings):
    return replace(plan, images=())
""",
    )
    plugin = _loaded(plugins.discover_plugins(tmp_path))
    plan = make_plan(_header(), [_region()])

    with pytest.raises(PluginError, match="pages"):
        plugins.run_plugin(plugin, plan)


@pytest.mark.parametrize(
    ("source", "match"),
    [
        (
            # Adds a region.
            """\
from dataclasses import replace

PLUGIN_NAME = "Adds A Region"


def run(plan, settings):
    extra = replace(plan.regions[0], id="r2")
    return replace(plan, regions=(*plan.regions, extra))
""",
            "added, removed, reordered, or moved",
        ),
        (
            # Removes a region.
            """\
from dataclasses import replace

PLUGIN_NAME = "Removes A Region"


def run(plan, settings):
    return replace(plan, regions=())
""",
            "added, removed, reordered, or moved",
        ),
        (
            # Moves a region to another page.
            """\
from dataclasses import replace

PLUGIN_NAME = "Moves A Region"


def run(plan, settings):
    moved = replace(plan.regions[0], image="page-002.png")
    return replace(plan, regions=(moved,))
""",
            "added, removed, reordered, or moved",
        ),
    ],
)
def test_a_plugin_may_not_restructure_the_regions(tmp_path: Path, source: str, match: str) -> None:
    _write(tmp_path, "structural", source)
    plugin = _loaded(plugins.discover_plugins(tmp_path))
    plan = make_plan(_header(), [_region()])

    with pytest.raises(PluginError, match=match):
        plugins.run_plugin(plugin, plan)


def test_reordering_the_regions_themselves_is_also_refused(tmp_path: Path) -> None:
    """Same ids and images, different order: still a shape change, not a field edit."""
    _write(
        tmp_path,
        "reorders",
        """\
PLUGIN_NAME = "Reorders"


def run(plan, settings):
    from dataclasses import replace

    return replace(plan, regions=tuple(reversed(plan.regions)))
""",
    )
    plugin = _loaded(plugins.discover_plugins(tmp_path))
    plan = make_plan(_header(), [_region(id="r1", order=1), _region(id="r2", order=2)])

    with pytest.raises(PluginError, match="added, removed, reordered, or moved"):
        plugins.run_plugin(plugin, plan)


def test_reshaping_a_polygon_is_a_field_edit_not_a_restructure(tmp_path: Path) -> None:
    """The scope is 'no new/removed/reordered/moved regions', not 'notes only'."""
    _write(
        tmp_path,
        "reshapes",
        """\
from dataclasses import replace

PLUGIN_NAME = "Reshapes"


def run(plan, settings):
    moved = replace(
        plan.regions[0],
        polygon=((0, 0), (1, 0), (1, 1), (0, 1)),
    )
    return replace(plan, regions=(moved,))
""",
    )
    plugin = _loaded(plugins.discover_plugins(tmp_path))
    plan = make_plan(_header(), [_region()])

    after = plugins.run_plugin(plugin, plan)

    assert after.regions[0].polygon == ((0, 0), (1, 0), (1, 1), (0, 1))
