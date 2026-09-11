#!/usr/bin/env python3
"""Ask a built bundle what languages it says it has, in every way macOS might.

    uv run --extra gui tools/inspect_bundle.py "dist/Comic Translator.app"

For one question: System Settings > General > Language & Region says an
application "doesn't support additional languages", and there is no way from
inside the application to tell whether that is the bundle being wrong or
macOS answering from somewhere else.

So this reads the same bundle four ways. The Info.plist key and the
``.lproj`` directories are what the build puts there. ``NSBundle`` is what
macOS itself would answer — the same Core Foundation call anything asking
about an application's localizations goes through, so a bundle that satisfies
it and a panel that still refuses are two different problems. And the
signature, because a bundle whose resources no longer match it has been
modified since it was built, which is its own answer.

Nothing here changes anything. It is a question, not a repair.
"""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

PLIST_KEYS = (
    "CFBundleIdentifier",
    "CFBundleName",
    "CFBundleDisplayName",
    "CFBundleVersion",
    "CFBundleShortVersionString",
    "CFBundleDevelopmentRegion",
    "CFBundleLocalizations",
)
"""Everything that could plausibly decide this, and the two names beside it
so that a bundle being confused for another one is visible as well."""


def plist_facts(bundle: Path) -> dict[str, object]:
    """What the built Info.plist says, for the keys that bear on this."""
    path = bundle / "Contents" / "Info.plist"
    try:
        with path.open("rb") as handle:
            plist = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException) as exc:
        return {"Info.plist": f"unreadable: {exc}"}
    return {key: plist.get(key, "— not set —") for key in PLIST_KEYS}


def lproj_facts(bundle: Path) -> dict[str, object]:
    """Every ``.lproj`` in the bundle and what is inside it.

    The contents matter as much as the names: a directory macOS is meant to
    read a localization out of and an empty folder look the same in a listing.
    """
    resources = bundle / "Contents" / "Resources"
    found: dict[str, object] = {}
    for folder in sorted(resources.glob("*.lproj")):
        inside = sorted(path.name for path in folder.iterdir())
        found[folder.name] = inside or "— empty —"
    return found or {"Contents/Resources/*.lproj": "— none —"}


def cocoa_localizations(bundle: Path) -> tuple[str, ...] | None:
    """What macOS itself says, or ``None`` where it cannot be asked.

    ``NSBundle.localizations`` is the Core Foundation answer: the union of
    the ``.lproj`` directory names and ``CFBundleLocalizations``. If this
    lists every language and a panel still does not, the bundle is not what
    is wrong.

    pyobjc is a macOS-only dependency this project already has, for Vision.
    """
    try:
        from Foundation import NSBundle
    except ImportError:
        return None
    loaded = NSBundle.bundleWithPath_(str(bundle))
    if loaded is None:
        return ()
    return tuple(str(name) for name in loaded.localizations())


def signature(bundle: Path) -> str:
    """Whether the bundle still matches the signature it was built with."""
    try:
        finished = subprocess.run(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)],
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return f"could not run codesign: {exc}"
    if finished.returncode == 0:
        return "valid"
    return f"invalid ({finished.stderr.strip() or finished.returncode})"


def report(bundle: Path) -> int:
    """Print everything, and return 0 if the bundle itself looks right."""
    print(f"bundle: {bundle}")
    if not (bundle / "Contents").is_dir():
        print("  there is no bundle there")
        return 1

    facts = plist_facts(bundle)
    print("\nInfo.plist")
    for key, value in facts.items():
        print(f"  {key}: {value}")
    if "Info.plist" in facts:
        return 1

    print("\nlocalization directories")
    for name, inside in lproj_facts(bundle).items():
        print(f"  {name}: {inside}")

    print("\nwhat macOS reads")
    cocoa = cocoa_localizations(bundle)
    if cocoa is None:
        print("  pyobjc is not installed here, so NSBundle could not be asked")
    else:
        print(f"  NSBundle.localizations: {list(cocoa)}")

    print("\nsignature")
    print(f"  {signature(bundle)}")

    print()
    if cocoa is None:
        print(
            "NSBundle is the one answer that settles this, and it cannot be "
            "asked from here. Run this on the Mac that built the bundle."
        )
        return 2
    if len(cocoa) < 2:
        print("macOS sees fewer than two languages in this bundle, so the panel is right.")
        return 1
    print(
        "The bundle says what it should, in the way macOS itself reads it. A "
        "Language & Region panel that still disagrees is answering from its "
        "own database rather than from this."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print(__doc__)
        return 2
    return report(Path(arguments[0]).expanduser().resolve())


if __name__ == "__main__":
    raise SystemExit(main())
