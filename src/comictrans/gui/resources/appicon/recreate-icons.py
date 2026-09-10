#!/usr/bin/env python3
"""Regenerate dog-book-icon-512.svg from dog-book-master-1024.svg.

The icon is not drawn separately: it is the master with three detail groups
hidden and a heavier stroke weight. Edit shapes in the master only, then run
this. Geometry is never touched, so the two files stay in sync.

    ./recreate-icons.py            # write the icon
    ./recreate-icons.py --check    # exit 1 if the icon is out of date

``--check`` is what `tests/test_gui_appicon.py` runs, so a master edited
without regenerating fails the suite rather than shipping quietly.

**On provenance metadata.** Neither file carries any, and the derive strips
``<metadata>`` so that neither starts to. The pair arrived with a signed
C2PA manifest each — 63% of the bytes, and the two different, having been
signed separately — which made ``--check`` impossible to pass: the derive
carries the master's manifest forward, and that is never the icon's. The
deeper problem is that a manifest signs a file's bytes, so regenerating the
geometry invalidates whichever one is carried. A credential this script
cannot keep true is worse than none, so there is none.
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterator
from pathlib import Path

HERE = Path(__file__).resolve().parent
MASTER = HERE / "dog-book-master-1024.svg"
ICON = HERE / "dog-book-icon-512.svg"

ICON_SIZE = 512
STROKE_MASTER = "4.5"
STROKE_ICON = "5.4"
HIDE_GROUPS = ("detail-book", "detail-fills", "detail-lines")

DERIVED_ATTRIBUTES = frozenset({"display", "stroke-width"})
"""What the derive is allowed to change on an element. Everything else — the
path data, the circles' centres and radii, fills, transforms — has to come
through untouched, and :func:`geometry_of` is what holds it to that."""

METADATA = re.compile(r"<metadata>.*?</metadata>", re.S)

ICON_COMMENT = (
    "  <!-- DERIVED from dog-book-master-1024.svg. Identical geometry: the three\n"
    "       detail groups are hidden and stroke-width is 5.4. Do not edit shapes here. -->"
)


def _substitute(pattern: str, replacement: str, svg: str, what: str, *, regex: bool = True) -> str:
    """One edit that has to land. A silent no-op here is a wrong icon.

    Every transform below is "find this and change it", and `re.sub` and
    `str.replace` both answer "not found" by handing back the string
    unchanged. That is how a master re-saved with ``stroke-width="4.50"``
    would produce an icon at the master's hairline weight, reported as
    written and exiting 0. So nothing substitutes without counting.
    """
    if regex:
        out, count = re.subn(pattern, replacement, svg, count=1, flags=re.S)
    else:
        count = svg.count(pattern)
        out = svg.replace(pattern, replacement)
    if count < 1:
        sys.exit(f"error: {what} not found in the master — refusing to write")
    return out


def derive(master: str) -> str:
    """Apply the master -> icon transform. Attributes only, never geometry."""
    svg = _substitute(
        r'(<svg[^>]*?)width="1024" height="1024"',
        rf'\g<1>width="{ICON_SIZE}" height="{ICON_SIZE}"',
        master,
        "the 1024 width/height on the <svg> element",
    )
    svg = _substitute(
        r"<title>(.*?) — master</title>",
        rf"<title>\g<1> — {ICON_SIZE} icon</title>",
        svg,
        "the master's <title>",
    )
    svg = _substitute(r"  <!-- MASTER\..*?-->", ICON_COMMENT, svg, "the MASTER comment")

    for gid in HIDE_GROUPS:
        svg = _substitute(
            f'<g id="{gid}"', f'<g id="{gid}" display="none"', svg, f"group id={gid!r}"
        )

    svg = _substitute(
        f'stroke-width="{STROKE_MASTER}"',
        f'stroke-width="{STROKE_ICON}"',
        svg,
        f'stroke-width="{STROKE_MASTER}"',
        regex=False,
    )

    # The one edit allowed to match nothing, because the usual case is that
    # there is nothing to strip. It is here so that a master re-exported by
    # a tool that embeds provenance cannot quietly put a manifest into the
    # icon — one that would not survive the next regeneration anyway.
    return METADATA.sub("", svg)


def _drawing(parent: ElementTree.Element) -> Iterator[ElementTree.Element]:
    """Every element under ``parent`` that is part of the picture.

    A ``<metadata>`` subtree is not, and is skipped whole: the derive strips
    it, so comparing it would read that removal as the drawing changing and
    refuse to write over a difference that was the point.
    """
    for child in parent:
        if child.tag.rpartition("}")[2] == "metadata":
            continue
        yield child
        yield from _drawing(child)


def geometry_of(svg: str) -> list[tuple[str, tuple[tuple[str, str], ...]]]:
    """Every element and every attribute the derive must not have touched.

    Parsed rather than pattern-matched, because the thing being guarded is
    the drawing and not the file: this master's eyes, pupils and nose are
    four circles and three ellipses, which carry no ``d`` at all. A guard
    that compared path data would have waved a moved pupil straight through.

    The root ``<svg>`` is skipped — its width and height are the one part of
    the transform that is meant to change.
    """
    return [
        (
            element.tag,
            tuple(
                sorted(
                    (name, value)
                    for name, value in element.attrib.items()
                    if name not in DERIVED_ATTRIBUTES
                )
            ),
        )
        for element in _drawing(ElementTree.fromstring(svg))
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify only, write nothing")
    args = parser.parse_args()

    if not MASTER.exists():
        sys.exit(f"error: {MASTER.name} not found")

    master = MASTER.read_text()
    icon = derive(master)

    if geometry_of(master) != geometry_of(icon):
        sys.exit("error: geometry drifted during derive — refusing to write")

    if args.check:
        current = ICON.read_text() if ICON.exists() else None
        if current == icon:
            print(f"{ICON.name} is up to date")
            return 0
        print(f"{ICON.name} is out of date — run without --check", file=sys.stderr)
        return 1

    ICON.write_text(icon)
    shapes = len(geometry_of(icon))
    print(f"wrote {ICON.name} ({shapes} elements, {len(HIDE_GROUPS)} detail groups hidden)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
