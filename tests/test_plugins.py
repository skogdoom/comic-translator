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


def _write(directory: Path, filename: str, source: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
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
    _write(tmp_path, "uppercase.py", UPPERCASE_NOTES)

    found = plugins.discover_plugins(tmp_path)

    assert [p.name for p in found] == ["Uppercase Notes"]
    assert found[0].path == tmp_path / "uppercase.py"


def test_a_syntax_error_is_skipped_not_raised(tmp_path: Path) -> None:
    _write(tmp_path, "broken.py", "def run(plan\n    this is not python")

    assert plugins.discover_plugins(tmp_path) == []


def test_a_file_with_no_plugin_name_is_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "nameless.py", "def run(plan):\n    return plan\n")

    assert plugins.discover_plugins(tmp_path) == []


def test_a_file_with_an_empty_plugin_name_is_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "nameless.py", 'PLUGIN_NAME = ""\n\n\ndef run(plan):\n    return plan\n')

    assert plugins.discover_plugins(tmp_path) == []


def test_a_file_with_no_run_is_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "runless.py", 'PLUGIN_NAME = "No Run"\n')

    assert plugins.discover_plugins(tmp_path) == []


def test_one_broken_file_does_not_hide_a_working_one(tmp_path: Path) -> None:
    _write(tmp_path, "broken.py", "not python at all (")
    _write(tmp_path, "uppercase.py", UPPERCASE_NOTES)

    found = plugins.discover_plugins(tmp_path)

    assert [p.name for p in found] == ["Uppercase Notes"]


def test_plugins_are_discovered_in_filename_order(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "b_plugin.py",
        'PLUGIN_NAME = "B"\n\n\ndef run(plan):\n    return plan\n',
    )
    _write(
        tmp_path,
        "a_plugin.py",
        'PLUGIN_NAME = "A"\n\n\ndef run(plan):\n    return plan\n',
    )

    found = plugins.discover_plugins(tmp_path)

    assert [p.name for p in found] == ["A", "B"]


def test_a_plugin_files_own_top_level_code_runs_once_on_discovery(tmp_path: Path) -> None:
    """Confirms, rather than assumes, that a plugin is real imported code."""
    marker = tmp_path / "ran.txt"
    _write(
        tmp_path,
        "sideeffect.py",
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


# -- running one ------------------------------------------------------------


def test_running_a_plugin_returns_its_new_plan(tmp_path: Path) -> None:
    path = _write(tmp_path, "uppercase.py", UPPERCASE_NOTES)
    plugin = plugins.discover_plugins(tmp_path)[0]
    plan = make_plan(_header(), [_region(notes="hush")])

    after = plugins.run_plugin(plugin, plan)

    assert after.regions[0].notes == "HUSH"
    assert plugin.path == path


def test_a_plugin_that_throws_is_reported_and_the_plan_is_untouched(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "explodes.py",
        'PLUGIN_NAME = "Explodes"\n\n\ndef run(plan):\n    raise ValueError("nope")\n',
    )
    plugin = plugins.discover_plugins(tmp_path)[0]
    plan = make_plan(_header(), [_region()])

    with pytest.raises(PluginError, match=r"Explodes.*nope"):
        plugins.run_plugin(plugin, plan)


def test_a_plugin_that_returns_something_else_is_refused(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "wrong_type.py",
        'PLUGIN_NAME = "Wrong Type"\n\n\ndef run(plan):\n    return "not a plan"\n',
    )
    plugin = plugins.discover_plugins(tmp_path)[0]
    plan = make_plan(_header(), [_region()])

    with pytest.raises(PluginError, match="did not return a plan"):
        plugins.run_plugin(plugin, plan)


def test_a_plugin_may_not_change_the_header(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "rewrites_header.py",
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
        "rewrites_images.py",
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
    _write(tmp_path, "structural.py", source)
    plugin = plugins.discover_plugins(tmp_path)[0]
    plan = make_plan(_header(), [_region()])

    with pytest.raises(PluginError, match=match):
        plugins.run_plugin(plugin, plan)


def test_reordering_the_regions_themselves_is_also_refused(tmp_path: Path) -> None:
    """Same ids and images, different order: still a shape change, not a field edit."""
    _write(
        tmp_path,
        "reorders.py",
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
        "reshapes.py",
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
