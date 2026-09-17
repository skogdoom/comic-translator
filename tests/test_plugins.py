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


def run(plan):
    return replace(
        plan, regions=tuple(replace(r, notes=r.notes.upper()) for r in plan.regions)
    )
"""


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


# -- discovery ------------------------------------------------------------


def test_a_missing_directory_has_no_plugins(tmp_path: Path) -> None:
    assert plugins.discover_plugins(tmp_path / "nowhere") == []


def test_a_working_plugin_is_discovered_and_named(tmp_path: Path) -> None:
    _write(tmp_path, "uppercase", UPPERCASE_NOTES)

    found = plugins.discover_plugins(tmp_path)

    assert [p.name for p in found] == ["Uppercase Notes"]
    assert found[0].path == tmp_path / "uppercase"


def test_a_loose_file_at_the_top_level_is_not_a_plugin(tmp_path: Path) -> None:
    """A plugin is a folder. A ``.py`` file dropped beside one is not seen at all."""
    (tmp_path / "stray.py").write_text(UPPERCASE_NOTES, encoding="utf-8")

    assert plugins.discover_plugins(tmp_path) == []


def test_a_folder_with_no_init_is_skipped(tmp_path: Path) -> None:
    folder = tmp_path / "not_a_plugin"
    folder.mkdir()
    (folder / "notes.txt").write_text("hello", encoding="utf-8")

    assert plugins.discover_plugins(tmp_path) == []


def test_a_syntax_error_is_skipped_not_raised(tmp_path: Path) -> None:
    _write(tmp_path, "broken", "def run(plan\n    this is not python")

    assert plugins.discover_plugins(tmp_path) == []


def test_a_folder_with_no_plugin_name_is_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "nameless", "def run(plan):\n    return plan\n")

    assert plugins.discover_plugins(tmp_path) == []


def test_a_folder_with_an_empty_plugin_name_is_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "nameless", 'PLUGIN_NAME = ""\n\n\ndef run(plan):\n    return plan\n')

    assert plugins.discover_plugins(tmp_path) == []


def test_a_folder_with_no_run_is_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "runless", 'PLUGIN_NAME = "No Run"\n')

    assert plugins.discover_plugins(tmp_path) == []


def test_one_broken_plugin_does_not_hide_a_working_one(tmp_path: Path) -> None:
    _write(tmp_path, "broken", "not python at all (")
    _write(tmp_path, "uppercase", UPPERCASE_NOTES)

    found = plugins.discover_plugins(tmp_path)

    assert [p.name for p in found] == ["Uppercase Notes"]


def test_plugins_are_discovered_in_folder_name_order(tmp_path: Path) -> None:
    _write(tmp_path, "b_plugin", 'PLUGIN_NAME = "B"\n\n\ndef run(plan):\n    return plan\n')
    _write(tmp_path, "a_plugin", 'PLUGIN_NAME = "A"\n\n\ndef run(plan):\n    return plan\n')

    found = plugins.discover_plugins(tmp_path)

    assert [p.name for p in found] == ["A", "B"]


def test_discovery_order_does_not_depend_on_the_filesystems_own_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``iterdir`` makes no ordering promise, so the sort has to be explicit."""
    _write(tmp_path, "b_plugin", 'PLUGIN_NAME = "B"\n\n\ndef run(plan):\n    return plan\n')
    _write(tmp_path, "a_plugin", 'PLUGIN_NAME = "A"\n\n\ndef run(plan):\n    return plan\n')
    real_iterdir = Path.iterdir

    def reversed_iterdir(self: Path) -> list[Path]:
        return list(reversed(list(real_iterdir(self))))

    monkeypatch.setattr(Path, "iterdir", reversed_iterdir)

    found = plugins.discover_plugins(tmp_path)

    assert [p.name for p in found] == ["A", "B"]


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


def run(plan):
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


def run(plan):
    return replace(
        plan, regions=tuple(replace(r, notes=shout(r.notes)) for r in plan.regions)
    )
""",
    )
    _write_sibling(folder, "helper.py", 'def shout(text):\n    return text.upper() + "!"\n')

    plugin = plugins.discover_plugins(tmp_path)[0]
    plan = make_plan(_header(), [_region(notes="hush")])
    after = plugins.run_plugin(plugin, plan)

    assert after.regions[0].notes == "HUSH!"


def test_two_plugins_can_each_have_a_sibling_module_of_the_same_name(tmp_path: Path) -> None:
    """Each plugin folder is its own package, so same-named siblings never collide."""
    first = _write(
        tmp_path,
        "first",
        "from .helper import VALUE\n\nPLUGIN_NAME = 'First'\n\n\ndef run(plan):\n    return plan\n",
    )
    _write_sibling(first, "helper.py", "VALUE = 1\n")
    second = _write(
        tmp_path,
        "second",
        "from .helper import VALUE\n\nPLUGIN_NAME = 'Second'\n\n\n"
        "def run(plan):\n    return plan\n",
    )
    _write_sibling(second, "helper.py", "VALUE = 2\n")

    found = plugins.discover_plugins(tmp_path)

    assert [p.name for p in found] == ["First", "Second"]


# -- running one ------------------------------------------------------------


def test_running_a_plugin_returns_its_new_plan(tmp_path: Path) -> None:
    folder = _write(tmp_path, "uppercase", UPPERCASE_NOTES)
    plugin = plugins.discover_plugins(tmp_path)[0]
    plan = make_plan(_header(), [_region(notes="hush")])

    after = plugins.run_plugin(plugin, plan)

    assert after.regions[0].notes == "HUSH"
    assert plugin.path == folder


def test_a_plugin_that_throws_is_reported_and_the_plan_is_untouched(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "explodes",
        'PLUGIN_NAME = "Explodes"\n\n\ndef run(plan):\n    raise ValueError("nope")\n',
    )
    plugin = plugins.discover_plugins(tmp_path)[0]
    plan = make_plan(_header(), [_region()])

    with pytest.raises(PluginError, match=r"Explodes.*nope"):
        plugins.run_plugin(plugin, plan)


def test_a_plugin_that_returns_something_else_is_refused(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "wrong_type",
        'PLUGIN_NAME = "Wrong Type"\n\n\ndef run(plan):\n    return "not a plan"\n',
    )
    plugin = plugins.discover_plugins(tmp_path)[0]
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


def run(plan):
    return replace(plan, header=replace(plan.header, target_language="sv"))
""",
    )
    plugin = plugins.discover_plugins(tmp_path)[0]
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


def run(plan):
    return replace(plan, images=())
""",
    )
    plugin = plugins.discover_plugins(tmp_path)[0]
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


def run(plan):
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


def run(plan):
    return replace(plan, regions=())
""",
            "added, removed, reordered, or moved",
        ),
        (
            # Moves a region to another page.
            """\
from dataclasses import replace

PLUGIN_NAME = "Moves A Region"


def run(plan):
    moved = replace(plan.regions[0], image="page-002.png")
    return replace(plan, regions=(moved,))
""",
            "added, removed, reordered, or moved",
        ),
    ],
)
def test_a_plugin_may_not_restructure_the_regions(tmp_path: Path, source: str, match: str) -> None:
    _write(tmp_path, "structural", source)
    plugin = plugins.discover_plugins(tmp_path)[0]
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


def run(plan):
    from dataclasses import replace

    return replace(plan, regions=tuple(reversed(plan.regions)))
""",
    )
    plugin = plugins.discover_plugins(tmp_path)[0]
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


def run(plan):
    moved = replace(
        plan.regions[0],
        polygon=((0, 0), (1, 0), (1, 1), (0, 1)),
    )
    return replace(plan, regions=(moved,))
""",
    )
    plugin = plugins.discover_plugins(tmp_path)[0]
    plan = make_plan(_header(), [_region()])

    after = plugins.run_plugin(plugin, plan)

    assert after.regions[0].polygon == ((0, 0), (1, 0), (1, 1), (0, 1))
