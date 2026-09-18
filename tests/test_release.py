"""What a release says about itself, held to what the code says.

Release notes are the one document nobody runs, so every fact in them decays
quietly. Three of them are checkable and are checked here: the version, the
plan format, and the limitations — that last one because ``known-bugs.md``
and a "known limitations" list written beside it would otherwise drift the
moment either changed, and the whole value of that file is that it is the
record.

The prose is not tested and should not be. What is tested is that the two
lists name the same things.
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


def _version_under_development() -> str:
    """The release a ``.devN`` version is working towards, or the version itself.

    A ``.devN`` version is not a thing anyone releases: it marks a tree past
    one release and not yet at the next, and the notes it accumulates go
    under the release it is heading for. A tree at a plain version is that
    release, and the heading is its own.
    """
    return comictrans.__version__.partition(".dev")[0]


def _limitations() -> set[str]:
    """Every Known limitations bullet, from every release that lists any.

    One list per release rather than one list: a limitation found while
    working towards 1.1.0 was not a limitation of 1.0.0, which had none of
    the code it is about. What has to hold is that the bullets and
    ``known-bugs.md`` name the same things between them.
    """
    text = CHANGELOG.read_text(encoding="utf-8")
    found: set[str] = set()
    for heading in re.finditer(r"^### Known limitations$", text, re.MULTILINE):
        rest = text[heading.end() :]
        next_heading = re.search(r"^#{2,3} ", rest, re.MULTILINE)
        found |= set(LIMITATION.findall(rest[: next_heading.start()] if next_heading else rest))
    return found


def test_the_release_notes_exist_and_have_somewhere_to_put_this_version() -> None:
    """A milestone that ships needs a heading to be written under, now.

    Not when somebody remembers at release time, by which point the work is
    however many merges back — which is the whole reason this file exists
    rather than `git log`.
    """
    assert CHANGELOG.is_file()
    assert f"## {_version_under_development()} —" in CHANGELOG.read_text(encoding="utf-8")


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


def test_the_version_is_past_the_one_that_said_not_yet() -> None:
    """0.x says "not yet". Something has been released, so it cannot say it.

    ``.devN`` is allowed and is not the same claim: it marks a tree between
    releases rather than one that has never had one.
    """
    major, _, _ = comictrans.__version__.partition(".")
    assert int(major) >= 1, comictrans.__version__
