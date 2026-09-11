"""What the About dialog reports, without opening one.

The awkward part of an About box is not the widget, it is working out what is
actually installed — which is why that lives in a Qt-free module and is
tested here, the same way ``document.py`` is.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError

import pytest

import comictrans
from comictrans.gui import about


def test_the_version_has_one_source_of_truth() -> None:
    """Nothing to keep in step: hatch reads the version out of this module.

    If the two ever disagree again, whatever put a second version somewhere
    has to answer for it here rather than in an About box on someone's
    screen.
    """
    assert about.package_version() == comictrans.__version__


def test_a_stale_installed_copy_of_the_version_does_not_win(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An application bundle shipped saying 0.1.0 while its log said 1.0.0.

    The distribution metadata is a copy hatch makes from ``__version__`` at
    install time. An editable install leaves it behind from the next edit
    until something reinstalls, and a build in between carries the stale copy
    into a bundle, where nothing ever will.
    """
    monkeypatch.setattr(about, "version", lambda _name: "0.0.1-from-an-old-install")

    assert about.package_version() == comictrans.__version__


def test_the_author_and_licence_come_from_the_package_metadata() -> None:
    assert about.author() == "David Andréasson"
    assert about.licence() == "MIT"
    assert "comic" in about.summary().lower()


def test_libraries_lists_what_is_installed_with_real_versions() -> None:
    installed = {library.name: library.version for library in about.libraries()}

    # The three the pipeline cannot run without, whatever else is present.
    for name in ("pillow", "numpy", "opencv-python-headless"):
        assert name in installed, f"{name} is a declared dependency and is installed"
        assert installed[name][0].isdigit(), f"{name} version looks wrong: {installed[name]!r}"


def test_library_names_are_parsed_off_their_version_specifiers() -> None:
    for library in about.libraries():
        assert not set(library.name) & set("<>=!~;[ "), library.name


def test_libraries_are_sorted_and_not_repeated() -> None:
    names = [library.name for library in about.libraries()]
    assert names == sorted(names, key=str.lower)
    assert len(names) == len(set(names))


def test_libraries_leaves_out_what_is_not_installed() -> None:
    """The pyobjc packages are macOS-only, and absent everywhere else.

    A library that is not installed is not one this run is using, so it has
    no version to report and no business on the list.
    """
    names = {library.name for library in about.libraries()}
    for name in names:
        assert _is_installed(name), f"{name} is listed but not installed"


def _is_installed(name: str) -> bool:
    from importlib.metadata import version

    try:
        version(name)
    except PackageNotFoundError:
        return False
    return True


def test_every_lookup_survives_the_package_not_being_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running from a source tree nobody installed still opens a window."""

    def missing(_name: str) -> str:
        raise PackageNotFoundError(_name)

    monkeypatch.setattr(about, "version", missing)
    monkeypatch.setattr(about, "metadata", missing)
    monkeypatch.setattr(about, "requires", missing)

    assert about.package_version() == comictrans.__version__
    assert about.author() == "unknown"
    assert about.licence() == "MIT"
    assert about.summary() == ""
    assert about.libraries() == ()
