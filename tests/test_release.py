"""What a release says about itself, held to what the code says.

Release notes are the one document nobody runs, so every fact in them decays
quietly. Three of them are checkable and are checked here: the version, the
plan format, and the limitations — that last one because ``known-bugs.md``
and a "known limitations" list written beside it would otherwise drift the
moment either changed, and the whole value of that file is that it is the
record.

The prose is not tested and should not be. What is tested is that the two
lists name the same four things.
"""

from __future__ import annotations

import re
from pathlib import Path

import comictrans
from comictrans.planfile import PLAN_VERSION
from comictrans.planfile.schema import READABLE_VERSIONS

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
KNOWN_BUGS = ROOT / "known-bugs.md"

BUG_TITLE = re.compile(r"^## \d+\. (.+)$", re.MULTILINE)
"""A numbered entry in ``known-bugs.md``."""

LIMITATION = re.compile(r"^- \*\*(.+?)\*\* —", re.MULTILINE)
"""One bullet in the release notes' Known limitations list."""


def _limitations() -> set[str]:
    text = CHANGELOG.read_text(encoding="utf-8")
    start = text.index("### Known limitations")
    end = text.index("### Not in this release", start)
    return set(LIMITATION.findall(text[start:end]))


def test_the_release_notes_exist_and_name_this_version() -> None:
    assert CHANGELOG.is_file()
    assert f"## {comictrans.__version__} —" in CHANGELOG.read_text(encoding="utf-8")


def test_the_known_limitations_are_exactly_the_known_bugs() -> None:
    """Written *from* that file rather than alongside it, so they cannot part.

    A bug fixed and its entry deleted — which is the rule for that file —
    leaves a release note claiming a limitation the tool no longer has; a new
    entry leaves one it has and does not admit to.
    """
    recorded = set(BUG_TITLE.findall(KNOWN_BUGS.read_text(encoding="utf-8")))
    assert recorded, "known-bugs.md has entries in the shape this reads"
    assert _limitations() == recorded


def test_the_release_notes_state_the_plan_format_the_reader_actually_accepts() -> None:
    """Whether an older plan still opens is the first thing an upgrade asks."""
    text = CHANGELOG.read_text(encoding="utf-8")
    assert f"Version {PLAN_VERSION}." in text
    older = sorted(version for version in READABLE_VERSIONS if version != PLAN_VERSION)
    spelled = " and ".join(str(version) for version in older)
    assert f"versions {spelled}" in text, "the versions it upgrades on the way in"


def test_the_version_is_not_a_development_one() -> None:
    """0.x says "not yet"; a release has to have stopped saying it."""
    major, _, _ = comictrans.__version__.partition(".")
    assert int(major) >= 1, comictrans.__version__
