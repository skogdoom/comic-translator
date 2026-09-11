"""The macOS application build, checked from a machine that cannot run it.

Nothing here produces a bundle: PyInstaller does not cross-compile, so the
`.app` itself can only be made on a Mac. What can be checked anywhere is
everything that decides what goes into one — the refusal, the icon slots, and
the Info.plist keys the spec asks for — and those are exactly the parts that
fail silently. An icon in the wrong slot is a blurry Dock, a missing
``CFBundleName`` is the application menu saying ``python3``, and
``LSBackgroundOnly`` set by accident is an application with no Dock icon at
all. None of the three raises anything.

The spec is run here rather than read for strings. It is a Python file that
PyInstaller ``exec``s with five names injected, so injecting recorders
instead runs its real code — including the part that skips an extra this
machine does not have — and hands back what it would have asked the bundler
to build.
"""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import build_app
import pytest

ROOT = Path(build_app.__file__).resolve().parent.parent
SPEC = Path(build_app.__file__).parent / "comictrans.spec"


def test_a_bundle_is_refused_where_one_cannot_be_built(monkeypatch: pytest.MonkeyPatch) -> None:
    """And says why, rather than failing inside a bundler three minutes later."""
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(SystemExit) as refusal:
        build_app.check_platform()

    said = str(refusal.value)
    assert "macOS" in said
    assert "cross-compile" in said, "the reason, not just the refusal"


def test_a_mac_is_not_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    build_app.check_platform()


def test_the_interpreter_the_bundle_would_carry_is_the_tested_one() -> None:
    """PyInstaller freezes whichever Python builds it, and requires-python is
    only a floor. On a Mac with a newer one installed `uv` picked 3.14 and the
    application shipped running an interpreter this suite has never executed a
    line on. ``.python-version`` is what makes the default the tested one.
    """
    pinned = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert pinned == build_app.TESTED_PYTHON
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert running == pinned, "the suite is running on something else than the pin"


def test_building_on_an_untested_interpreter_says_so_without_refusing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A newer Python is somebody's decision to make, not this script's."""
    monkeypatch.setattr(build_app, "TESTED_PYTHON", "3.1")

    build_app.check_interpreter()

    warning = capsys.readouterr().err
    assert "3.1" in warning
    assert "carries the interpreter" in warning


def test_the_iconset_is_exactly_the_ten_slots_apple_reads() -> None:
    """``iconutil`` ignores a file it does not recognise, without saying so."""
    names = {build_app.slot_name(points, scale) for points, scale in build_app.ICON_SLOTS}
    assert names == {
        "icon_16x16.png",
        "icon_16x16@2x.png",
        "icon_32x32.png",
        "icon_32x32@2x.png",
        "icon_128x128.png",
        "icon_128x128@2x.png",
        "icon_256x256.png",
        "icon_256x256@2x.png",
        "icon_512x512.png",
        "icon_512x512@2x.png",
    }


def test_which_drawing_a_slot_gets_follows_points_and_not_pixels() -> None:
    """A 16x16@2x slot is 32 pixels shown at 16 points, and wants the small one.

    Keyed off pixels it would get the detailed master while the 32-point slot
    beside it got the simplified drawing — the two swapped, at the sizes where
    the difference is the whole reason there are two drawings.
    """
    assert build_app.drawing_for(16) == build_app.DERIVED
    assert build_app.drawing_for(32) == build_app.DERIVED
    assert build_app.drawing_for(128) == build_app.MASTER
    assert build_app.drawing_for(512) == build_app.MASTER

    at_16_points = {build_app.drawing_for(points) for points, _scale in build_app.ICON_SLOTS[:2]}
    assert at_16_points == {build_app.DERIVED}, "both 16-point slots, one drawing"


def test_the_rendered_slots_are_the_sizes_they_are_named_for(qapp: object, tmp_path: Path) -> None:
    from PySide6.QtGui import QImage

    iconset = build_app.build_iconset(tmp_path)

    written = sorted(path.name for path in iconset.iterdir())
    assert len(written) == len(build_app.ICON_SLOTS)
    for points, scale in build_app.ICON_SLOTS:
        image = QImage(str(iconset / build_app.slot_name(points, scale)))
        assert image.width() == image.height() == points * scale
        assert image.hasAlphaChannel(), "an icon without a cut-out is a square"


def test_the_two_drawings_are_actually_different_where_it_matters(
    qapp: object, tmp_path: Path
) -> None:
    """Otherwise the slot rule is a comment rather than a decision."""
    from PySide6.QtGui import QImage

    build_app.render(build_app.MASTER, 32, tmp_path / "master.png")
    build_app.render(build_app.DERIVED, 32, tmp_path / "derived.png")

    master = QImage(str(tmp_path / "master.png"))
    derived = QImage(str(tmp_path / "derived.png"))
    changed = sum(
        1
        for y in range(32)
        for x in range(32)
        if abs(master.pixelColor(x, y).value() - derived.pixelColor(x, y).value()) > 8
    )
    assert changed > 50, f"only {changed} of 1024 pixels differ; the simplification does nothing"


class _Recorder:
    """Stands in for one of the five names PyInstaller injects into a spec.

    Calling it keeps the keyword arguments and hands back something the next
    call can reach into — the spec reads ``analysis.pure`` and passes the
    results of one call into the next, so the stand-in has to answer for any
    attribute rather than only be callable.
    """

    def __init__(self, name: str, seen: dict[str, Any]) -> None:
        self._name = name
        self._seen = seen

    def __call__(self, *args: object, **kwargs: object) -> _Recorder:
        self._seen[self._name] = kwargs
        return self

    def __getattr__(self, attribute: str) -> _Recorder:
        return _Recorder(f"{self._name}.{attribute}", self._seen)


def _run_spec(monkeypatch: pytest.MonkeyPatch, lproj: Path | None = None) -> dict[str, Any]:
    """Execute the spec with the bundler stubbed, and return its arguments.

    Both paths the spec reads from the environment are set here: a fake one
    for the icon, and a directory with nothing in it for the localizations,
    which the one test that cares about them overrides.
    """
    hooks = types.ModuleType("PyInstaller.utils.hooks")

    def collect_data_files(package: str) -> list[tuple[str, str]]:
        return [(f"{package}/resources", "comictrans/resources")]

    def copy_metadata(distribution: str, recursive: bool = False) -> list[tuple[str, str]]:
        if distribution == "definitely-not-installed":
            raise ValueError(distribution)
        return [(f"{distribution}.dist-info", ".")]

    hooks.collect_data_files = collect_data_files  # type: ignore[attr-defined]
    hooks.copy_metadata = copy_metadata  # type: ignore[attr-defined]
    utils = types.ModuleType("PyInstaller.utils")
    utils.hooks = hooks  # type: ignore[attr-defined]
    root = types.ModuleType("PyInstaller")
    root.utils = utils  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "PyInstaller", root)
    monkeypatch.setitem(sys.modules, "PyInstaller.utils", utils)
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
    monkeypatch.setenv("COMICTRANS_ICNS", "/somewhere/Comic Translator.icns")
    monkeypatch.setenv("COMICTRANS_LPROJ", str(lproj or "/somewhere/lproj"))

    seen: dict[str, Any] = {}
    namespace: dict[str, Any] = {
        name: _Recorder(name, seen) for name in ("Analysis", "PYZ", "EXE", "COLLECT", "BUNDLE")
    }
    exec(compile(SPEC.read_text(encoding="utf-8"), str(SPEC), "exec"), namespace)
    seen["namespace"] = namespace
    return seen


def test_the_bundle_is_named_so_that_the_application_menu_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one string this milestone exists to turn on.

    macOS titles "About X", "Hide X" and "Quit X" from ``CFBundleName``, and
    falls back to the ``argv[0]`` name only when there is no bundle — so a
    bundle that omits the key puts ``comictrans`` back in that menu.
    """
    from comictrans.gui import about

    plist = _run_spec(monkeypatch)["BUNDLE"]["info_plist"]
    assert plist["CFBundleName"] == about.NAME
    assert plist["CFBundleDisplayName"] == about.NAME


def test_the_bundle_declares_every_language_it_is_translated_into(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without this key, macOS says the application has only one language.

    System Settings > General > Language & Region decides what to offer for
    an application from its bundle, not from what is inside it: the catalogues
    can all be there and the picker still reads "doesn't support additional
    languages" — which is what it did. ``.lproj`` directories are the usual
    way to say it and are for strings macOS itself loads; these are Qt
    catalogues loaded by Qt, which is the case ``CFBundleLocalizations`` is
    for.

    Built from what ships rather than written out, so adding a catalogue and
    forgetting the spec cannot happen.
    """
    from comictrans.gui import translations

    plist = _run_spec(monkeypatch)["BUNDLE"]["info_plist"]

    assert plist["CFBundleDevelopmentRegion"] == translations.SOURCE_LANGUAGE
    assert set(plist["CFBundleLocalizations"]) == {
        translations.SOURCE_LANGUAGE,
        *translations.available(),
    }


def test_a_localization_directory_is_written_for_every_language(tmp_path: Path) -> None:
    """What the Info.plist key alone did not buy.

    ``CFBundleLocalizations`` is Apple's documented key for an application
    that loads its own strings, and it is set — and the Language & Region
    panel went on saying the application supports no additional languages
    with it there. So the directories are written too. Both are declarations
    of the same fact and neither is a workaround for the other; this one is
    what every application offering the choice actually ships.
    """
    codes = build_app.languages()

    written = build_app.write_localizations(tmp_path, codes)

    assert [path.parent.name for path in written] == [f"{code}.lproj" for code in codes]
    for path in written:
        assert path.name == "InfoPlist.strings"
        # Not an empty directory: it carries the one localized resource this
        # application has, and PyInstaller collects files rather than folders.
        assert "Comic Translator" in path.read_text(encoding="utf-8")


def test_the_localizations_are_collected_rather_than_added_afterwards(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Into the bundle before it is signed, not into it after.

    PyInstaller signs the bundle and then verifies its own signature, so a
    directory dropped into ``Contents/Resources`` afterwards breaks the seal
    it just made — a worse problem than the one being fixed, and a silent one
    until something checks.

    A destination of ``sv.lproj`` is what puts the directory at
    ``Contents/Resources/sv.lproj``, which is where macOS looks.
    """
    build_app.write_localizations(tmp_path, ("en", "sv"))

    datas = _run_spec(monkeypatch, tmp_path)["Analysis"]["datas"]

    carried = {destination: source for source, destination in datas}
    assert set(carried) >= {"en.lproj", "sv.lproj"}
    for destination, source in carried.items():
        if destination.endswith(".lproj"):
            assert Path(source).name == "InfoPlist.strings"


def test_the_built_bundle_is_read_back_for_both_declarations(tmp_path: Path) -> None:
    """Neither one is visible from inside the application when it is missing."""
    bundle = tmp_path / "Comic Translator.app"
    resources = bundle / "Contents" / "Resources"

    assert build_app.bundled_localizations(bundle) == (), "not built at all"

    resources.mkdir(parents=True)
    assert build_app.bundled_localizations(bundle) == (), "built without them"

    for code in ("sv", "en"):
        (resources / f"{code}.lproj").mkdir()
    assert build_app.bundled_localizations(bundle) == ("en", "sv")


def test_launch_services_is_asked_to_look_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """macOS answers that panel from its own database, not from the bundle.

    So a bundle rebuilt in place can keep answering with what the last build
    said. Best effort — ``lsregister`` lives at a fixed path inside a
    framework and has never been on ``PATH``, so a macOS that moves it costs
    a refresh rather than a build.
    """
    bundle = tmp_path / "Comic Translator.app"
    monkeypatch.setattr(build_app, "LSREGISTER", tmp_path / "not-here")
    assert build_app.register(bundle) is False, "nothing to run, and not a failure"

    tool = tmp_path / "lsregister"
    tool.touch()
    monkeypatch.setattr(build_app, "LSREGISTER", tool)
    ran: list[list[str]] = []

    def record(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        ran.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(build_app.subprocess, "run", record)

    assert build_app.register(bundle) is True
    assert ran == [[str(tool), "-f", str(bundle)]]


def test_the_languages_written_are_the_catalogues_that_ship() -> None:
    from comictrans.gui import translations

    assert set(build_app.languages()) == {
        translations.SOURCE_LANGUAGE,
        *translations.available(),
    }


def test_a_bundle_that_declares_no_languages_is_noticed(tmp_path: Path) -> None:
    """The build reads its own Info.plist back, because nothing else would.

    A bundle missing the key builds, runs and translates itself perfectly;
    only System Settings is any the wiser, and only for somebody looking.
    """
    import plistlib

    bundle = tmp_path / "Comic Translator.app"
    plist = bundle / "Contents" / "Info.plist"
    plist.parent.mkdir(parents=True)

    assert build_app.declared_languages(bundle) == (), "no Info.plist at all"

    plist.write_bytes(plistlib.dumps({"CFBundleName": "Comic Translator"}))
    assert build_app.declared_languages(bundle) == (), "an Info.plist without the key"

    plist.write_bytes(plistlib.dumps({"CFBundleLocalizations": ["en", "sv"]}))
    assert build_app.declared_languages(bundle) == ("en", "sv")


def test_the_bundle_is_not_built_as_a_background_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PyInstaller reads ``console=True`` as ``LSBackgroundOnly``.

    Which is an application with no Dock icon and no menu bar — and nothing
    in this repository can launch a bundle to find that out.
    """
    seen = _run_spec(monkeypatch)
    assert seen["EXE"]["console"] is False
    assert seen["BUNDLE"]["info_plist"]["LSBackgroundOnly"] is False


def test_the_bundle_carries_the_files_nothing_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    """The icons, the guide and the metadata the About dialog reads.

    None of the three is reachable by import, so the analysis would not find
    any of them, and all three fail quietly rather than loudly.
    """
    carried = [source for source, _destination in _run_spec(monkeypatch)["Analysis"]["datas"]]

    assert "comictrans/resources" in carried, "the icons and the guide"
    assert "comictrans.dist-info" in carried, "what the About dialog reads itself from"
    assert "pyside6.dist-info" in carried, (
        "PySide6 is an extra, so a recursive walk of the required dependencies "
        "does not reach it — measured on a build, where it was simply absent"
    )


def test_an_extra_this_machine_does_not_have_does_not_stop_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Which extras are installed is the builder's choice, not the spec's."""
    namespace = _run_spec(monkeypatch)["namespace"]
    # extra_metadata reads EXTRAS out of the namespace it was defined in, so
    # this replaces the list it walks without touching the file.
    namespace["EXTRAS"] = ("pyside6", "definitely-not-installed")

    assert namespace["extra_metadata"]() == [("pyside6.dist-info", ".")]


def test_the_icon_comes_from_the_environment_rather_than_a_written_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing icon is not an error PyInstaller reports.

    It is Python's own icon on somebody's Dock, so the spec must not be
    runnable without the step that renders one.
    """
    seen = _run_spec(monkeypatch)
    assert seen["BUNDLE"]["icon"] == "/somewhere/Comic Translator.icns"

    monkeypatch.delenv("COMICTRANS_ICNS")
    namespace: dict[str, Any] = {
        name: _Recorder(name, {}) for name in ("Analysis", "PYZ", "EXE", "COLLECT", "BUNDLE")
    }
    with pytest.raises(KeyError):
        exec(compile(SPEC.read_text(encoding="utf-8"), str(SPEC), "exec"), namespace)
