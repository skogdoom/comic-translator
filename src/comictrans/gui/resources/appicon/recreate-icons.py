#!/usr/bin/env python3
"""Regenerate dog-book-icon-512.svg from dog-book-master-1024.svg.

The icon is not drawn separately: it is the master with three detail groups
hidden and a heavier stroke weight. Edit shapes in the master only, then run
this. Geometry is never touched, so the two files stay in sync.

    ./recreate-icons.py            # write the icon
    ./recreate-icons.py --check    # exit 1 if the icon is out of date

``--check`` is what `tests/test_gui_appicon.py` runs, so a master edited
without regenerating fails the suite rather than shipping quietly.

**On the C2PA metadata.** Both files carry a signed provenance manifest, and
the two are different: each was signed for its own bytes. So the icon's
manifest is kept on write rather than being overwritten with the master's,
and comparisons ignore ``<metadata>`` entirely — otherwise ``--check`` could
never pass, the manifests differing while the drawing agreed. Worth being
plain about the limit: a manifest is a signature over a file, so once the
geometry is regenerated the one carried here no longer validates against the
bytes around it. It is kept as a record of where the drawing came from, not
as a credential this script can keep true.
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ElementTree
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

    return _substitute(
        f'stroke-width="{STROKE_MASTER}"',
        f'stroke-width="{STROKE_ICON}"',
        svg,
        f'stroke-width="{STROKE_MASTER}"',
        regex=False,
    )


def geometry_of(svg: str) -> list[tuple[str, tuple[tuple[str, str], ...]]]:
    """Every element and every attribute the derive must not have touched.

    Parsed rather than pattern-matched, because the thing being guarded is
    the drawing and not the file: this master's eyes, pupils and nose are
    four circles and three ellipses, which carry no ``d`` at all. A guard
    that compared path data would have waved a moved pupil straight through.

    The root ``<svg>`` is skipped — its width and height are the one part of
    the transform that is meant to change.
    """
    root = ElementTree.fromstring(svg)
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
        for element in root.iter()
        if element is not root
    ]


def without_metadata(svg: str) -> str:
    """The file as a drawing, with the provenance manifest normalised away."""
    return METADATA.sub("<metadata/>", svg)


def keeping_manifest_of(derived: str, current: str | None) -> str:
    """The derived icon, carrying the icon's own manifest rather than the master's.

    With no icon yet there is nothing to carry, and the master's comes
    through — which is the honest answer for a file that has just been made
    from it.
    """
    if current is None:
        return derived
    existing = METADATA.search(current)
    if existing is None:
        return derived
    return METADATA.sub(lambda _match: existing.group(0), derived, count=1)


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

    current = ICON.read_text() if ICON.exists() else None

    if args.check:
        if current is not None and without_metadata(current) == without_metadata(icon):
            print(f"{ICON.name} is up to date")
            return 0
        print(f"{ICON.name} is out of date — run without --check", file=sys.stderr)
        return 1

    ICON.write_text(keeping_manifest_of(icon, current))
    shapes = len(geometry_of(icon))
    print(f"wrote {ICON.name} ({shapes} elements, {len(HIDE_GROUPS)} detail groups hidden)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
