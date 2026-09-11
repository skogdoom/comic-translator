"""The in-application guide, and the things about it that can go stale.

Most of this is not about the window. A guide's failure mode is not crashing
— it is quietly describing the version before last, which no amount of
exception handling catches. So the tests that matter here read the shipped
document and hold it to the code it describes: the outline colours against
``canvas``, the flag names against the inspector's own table, and every link
in it against every anchor.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from comictrans.gui.help_dialog import (
    HELP_DIR,
    LANGUAGE_DEFAULT,
    SUFFIX,
    available_languages,
    document,
    document_path,
)

SWATCH = re.compile(r"background-color:(#[0-9a-f]{6})")
ANCHOR = re.compile(r'<a name="([^"]+)"></a>')
LINK = re.compile(r'href="#([^"]+)"')


def _documents() -> list[Path]:
    """Every language's guide. Today that is one; 4.9 makes it several."""
    return sorted(HELP_DIR.glob(f"*{SUFFIX}"))


def test_the_guide_ships() -> None:
    assert LANGUAGE_DEFAULT in available_languages()
    assert document_path().is_file()


def test_a_language_with_no_guide_of_its_own_falls_back_to_english() -> None:
    """A partial translation is the normal state of one of these."""
    assert document_path("xx-not-a-language") == document_path(LANGUAGE_DEFAULT)


def test_a_missing_guide_costs_the_text_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A Help menu whose item raises would be the worse failure."""
    monkeypatch.setattr("comictrans.gui.help_dialog.HELP_DIR", tmp_path)
    assert available_languages() == ()
    assert "could not be read" in document()


@pytest.mark.parametrize("path", _documents(), ids=lambda path: path.stem)
def test_every_link_in_the_guide_has_an_anchor_to_land_on(path: Path) -> None:
    """A link to a section that has been renamed does not fail quietly.

    Measured: ``scrollToAnchor`` on a name the document does not have
    scrolls to the *top* — not an exception, not a no-op — so a renamed
    section turns its contents entry into a link back to the contents.
    Nothing but a test notices.
    """
    text = path.read_text(encoding="utf-8")
    assert set(LINK.findall(text)) <= set(ANCHOR.findall(text))


@pytest.mark.parametrize("path", _documents(), ids=lambda path: path.stem)
def test_the_colours_the_guide_names_are_the_colours_the_canvas_draws(path: Path) -> None:
    """The guide's whole first section is a key to an overlay it cannot see.

    Written out in the document rather than substituted in, so that a
    translator receives prose and no machinery — which puts the drift here.
    """
    from comictrans.gui import canvas

    drawn = {
        canvas.COLOR_EXACT.name(),
        canvas.COLOR_APPROXIMATE.name(),
        canvas.COLOR_MANUAL.name(),
        canvas.COLOR_SELECTED.name(),
        canvas.COLOR_FLAGGED.name(),
    }
    described = set(SWATCH.findall(path.read_text(encoding="utf-8")))
    assert described == drawn, "every outline colour is shown, and only those"


@pytest.mark.parametrize("path", _documents(), ids=lambda path: path.stem)
def test_the_guide_explains_every_flag_by_the_name_the_window_gives_it(path: Path) -> None:
    """A flag explained under a different name is a flag nobody can look up."""
    from comictrans.gui.inspector import _FLAG_LABELS

    text = path.read_text(encoding="utf-8")
    missing = [label for label in _FLAG_LABELS.values() if label not in text]
    assert missing == [], f"flags the guide does not name: {missing}"
