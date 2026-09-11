"""Build ``Comic Translator.app``, unsigned, on the machine that will run it.

    uv run --extra gui --group bundle tools/build_app.py

**Unsigned is the decision, not an omission.** There is no Developer ID here,
no notarisation and no stapling, so the bundle is quarantined by Gatekeeper on
any machine but the one that built it. That makes it something you build, not
something you download — which is the same bargain the disclaimer in
``README.md`` already strikes, rather than a weaker one dressed up.

One consequence is worth knowing before meeting it: macOS marks a source tree
downloaded as a zip with the quarantine attribute, and everything built from
it inherits the mark. A ``git clone`` carries no such attribute. Clone the
repository, or the application you build will refuse to open and look broken.

**The bundler stays a development dependency.** It lives in the ``bundle``
dependency group, not in the project's requirements, so installing
``comictrans`` for the command line does not pull a bundler that will never be
run.

**The interpreter goes in the bundle, so it is pinned.** PyInstaller freezes
whichever Python the build environment has, and ``requires-python`` only sets
a floor: on a Mac with a newer one installed, `uv` picked 3.14 and the
application shipped running an interpreter this project's suite has never
executed a line on. ``.python-version`` pins 3.12 so the everyday environment,
the tests and the bundle are the same one. A crash on quit was seen once on a
3.14 build and never on 3.12, which is a reason to notice rather than a
finding — but shipping what is tested needs no finding.

This script does the three things the spec cannot: it renders the icon,
because ``.icns`` holds one image per size rather than one drawing; it writes
the ``.lproj`` directories that decide whether macOS will offer this
application a language of its own; and it refuses early and in plain words on
a machine that cannot produce a bundle at all.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = Path(__file__).resolve().parent / "comictrans.spec"
WORK = ROOT / "build" / "pyinstaller"
DIST = ROOT / "dist"
ICNS = ROOT / "build" / "Comic Translator.icns"
LPROJ = ROOT / "build" / "lproj"

APPICON = ROOT / "src" / "comictrans" / "gui" / "resources" / "appicon"
MASTER = APPICON / "dog-book-master-1024.svg"
DERIVED = APPICON / "dog-book-icon-512.svg"

ICON_SLOTS: tuple[tuple[int, int], ...] = (
    (16, 1),
    (16, 2),
    (32, 1),
    (32, 2),
    (128, 1),
    (128, 2),
    (256, 1),
    (256, 2),
    (512, 1),
    (512, 2),
)
"""Apple's ten iconset slots, as (points, scale). ``iconutil`` wants exactly
these names and sizes; anything else it ignores without saying so."""

TESTED_PYTHON = "3.12"
"""What `.python-version` pins and what the suite runs on. The bundle carries
whichever interpreter builds it, so the two should be the same one."""

SIMPLIFIED_UP_TO = 32
"""The largest slot the derived drawing is used for, in **points**.

Points, not pixels, because what decides which drawing reads is how big the
icon looks and not how many pixels it is given: a 16x16@2x slot is 32 pixels
shown at 16 points, physically the same size as a 16x16 on a display without
Retina, and it wants the same drawing. Keying off pixels would put the
detailed master into that slot and the simplified one into a 32-point slot
next to it.

16 and 32 points are the menu bar, the Finder list and the sidebar, where the
master's fur lines and page edges are the mud ``recreate-icons.py`` exists to
avoid. From 128 points up — the Dock and Finder's icon view — that detail is
the drawing, so the master goes there.
"""


APPLICATION_NAME = "Comic Translator"
"""What the bundle is called, which is what its localized name is too."""

LSREGISTER = Path(
    "/System/Library/Frameworks/CoreServices.framework/Frameworks"
    "/LaunchServices.framework/Support/lsregister"
)
"""Launch Services' own registration tool. Not on ``PATH``, by Apple's
choice: there is no ``lsregister`` to find, only this path."""

LOCALIZED_NAME_KEYS = ("CFBundleName", "CFBundleDisplayName")
"""What each ``InfoPlist.strings`` carries: the application's name.

The same name in every language — it is a made-up compound, not a phrase to
translate — so these files exist for their directories rather than their
contents. That is not a trick: a localization is where an application's
localized resources go, and its name is a localized resource whether or not
it happens to differ.
"""


class BuildRefused(SystemExit):
    """Stop with something a person can act on, not a traceback."""

    def __init__(self, message: str) -> None:
        super().__init__(f"cannot build the application: {message}")


def check_platform() -> None:
    """Refuse anywhere a ``.app`` cannot be made, and say why.

    PyInstaller does not cross-compile: it bundles the interpreter and the
    libraries of the machine it runs on, so a bundle has to be built on macOS
    whatever the target. Failing here with a sentence beats failing three
    minutes later inside a bundler.
    """
    if sys.platform != "darwin":
        raise BuildRefused(
            f"a macOS application bundle can only be built on macOS, and this is "
            f"{sys.platform}. PyInstaller bundles the interpreter and libraries of "
            "the machine it runs on; it does not cross-compile."
        )


def check_interpreter() -> None:
    """Warn if the interpreter about to be frozen is not the tested one.

    Not a refusal: a newer Python is a thing somebody may deliberately want to
    try, and this script has no business forbidding it. But the bundle carries
    whichever interpreter is running it, and shipping one the suite has never
    executed a line on should be a decision rather than an accident —
    ``.python-version`` makes the default the tested one, and this says so
    when something has overridden it.
    """
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    if running != TESTED_PYTHON:
        print(
            f"warning: building with Python {running}, and this project's suite "
            f"runs on {TESTED_PYTHON}. The bundle carries the interpreter that "
            "builds it. Delete .python-version, or keep it, deliberately.",
            file=sys.stderr,
        )


def check_tools() -> None:
    """Everything this needs that is not a Python package."""
    if shutil.which("iconutil") is None:
        raise BuildRefused(
            "iconutil is not on PATH. It ships with the macOS command line tools: "
            "xcode-select --install"
        )
    for drawing in (MASTER, DERIVED):
        if not drawing.is_file():
            raise BuildRefused(f"the application icon is missing: {drawing}")


def slot_name(points: int, scale: int) -> str:
    """What ``iconutil`` insists a slot is called."""
    suffix = "@2x" if scale == 2 else ""
    return f"icon_{points}x{points}{suffix}.png"


def drawing_for(points: int) -> Path:
    """Which of the two drawings a slot of this many points gets."""
    return DERIVED if points <= SIMPLIFIED_UP_TO else MASTER


def render(source: Path, pixels: int, destination: Path) -> None:
    """One slot, rendered from a drawing at exactly the size asked for."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    image = QImage(pixels, pixels, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    QSvgRenderer(str(source)).render(painter, QRectF(0, 0, pixels, pixels))
    painter.end()
    # No explicit format: PySide6 takes it from the suffix, and its stub for
    # the format argument disagrees with what the runtime accepts — the
    # annotation says bytes, the call refuses anything but str.
    if not image.save(str(destination)):
        raise BuildRefused(f"could not write {destination}")


def build_iconset(directory: Path) -> Path:
    """Write the ten PNGs ``iconutil`` reads, and return the directory."""
    from PySide6.QtGui import QGuiApplication

    # QPainter wants an application before it will paint. Gui rather than
    # Widgets: nothing here opens a window.
    QGuiApplication.instance() or QGuiApplication([])

    iconset = directory / "Comic Translator.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True)
    for points, scale in ICON_SLOTS:
        render(drawing_for(points), points * scale, iconset / slot_name(points, scale))
    return iconset


def build_icns(iconset: Path, destination: Path) -> Path:
    """Hand the slots to Apple's own tool rather than writing the container.

    ``.icns`` is a typed container and its type codes are not guessable — the
    difference between a 32-pixel image in the 16-point slot and in the
    32-point one is four bytes with no error if you get it wrong. ``iconutil``
    knows them; this does not need to.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Short flags, which is the spelling in Apple's own examples: -c is
    # --convert and -o is --output. Nothing in this repository can run
    # iconutil to find out whether the long forms are accepted by every
    # version of it, so this uses the form that is documented everywhere.
    command = ["iconutil", "-c", "icns", "-o", str(destination), str(iconset)]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        raise BuildRefused(f"iconutil refused the iconset ({exc}): {iconset}") from exc
    return destination


def build_bundle() -> Path:
    """Run PyInstaller over the spec, and return the bundle it wrote."""
    import PyInstaller.__main__

    os.environ["COMICTRANS_ICNS"] = str(ICNS)
    os.environ["COMICTRANS_LPROJ"] = str(LPROJ)
    PyInstaller.__main__.run(
        [
            str(SPEC),
            "--noconfirm",
            "--distpath",
            str(DIST),
            "--workpath",
            str(WORK),
        ]
    )
    return DIST / "Comic Translator.app"


def languages() -> tuple[str, ...]:
    """Every language the window has a catalogue for, English included."""
    from comictrans.gui.translations import SOURCE_LANGUAGE, available

    return tuple(sorted({SOURCE_LANGUAGE, *available()}))


def write_localizations(directory: Path, codes: tuple[str, ...]) -> tuple[Path, ...]:
    """Write one ``<language>.lproj`` for the spec to collect, and return them.

    This is what makes System Settings > General > Language & Region offer
    the application a language of its own. ``CFBundleLocalizations`` in the
    Info.plist is Apple's documented key for an application that loads its
    own strings, which is exactly this one — and it is set, and on its own it
    was not enough: that panel went on reporting "doesn't support additional
    languages" for a bundle that declared it. Observed on macOS rather than
    reasoned about, and not reproducible from here, which is why both are
    there now rather than one replacing the other. A directory per language
    is what every application that offers the choice actually ships.

    Written here and collected by the spec rather than added to the bundle
    afterwards, because PyInstaller signs the bundle and then verifies it:
    anything dropped into ``Contents/Resources`` after that breaks the seal
    it just made, and a bundle whose resources no longer match its signature
    is a different and worse problem than the one being fixed.

    Each holds an ``InfoPlist.strings`` naming the application, so the
    directory carries a localized resource rather than being an empty folder
    — which PyInstaller would have nothing to collect from anyway.
    """
    written = []
    for code in codes:
        folder = directory / f"{code}.lproj"
        folder.mkdir(parents=True, exist_ok=True)
        strings = folder / "InfoPlist.strings"
        lines = [f'"{key}" = "{APPLICATION_NAME}";' for key in LOCALIZED_NAME_KEYS]
        # UTF-16, which is what Apple documents a .strings file as and what
        # Xcode writes. UTF-8 is read correctly by everything current, and
        # this is one variable fewer in a question that has already had two
        # answers that looked right and were not.
        strings.write_text("\n".join(lines) + "\n", encoding="utf-16")
        written.append(strings)
    return tuple(written)


def bundled_localizations(bundle: Path) -> tuple[str, ...]:
    """Which ``.lproj`` directories the built bundle actually carries."""
    resources = bundle / "Contents" / "Resources"
    if not resources.is_dir():
        return ()
    return tuple(sorted(path.stem for path in resources.glob("*.lproj")))


def register(bundle: Path) -> bool:
    """Tell Launch Services the bundle changed, and say whether it could.

    macOS keeps what it knows about an application in a database, and the
    Language & Region panel reads that rather than the bundle in front of it.
    A bundle rebuilt in place can therefore keep answering with what the
    previous build said — which is the other half of why that panel might
    still be wrong, and the half no amount of getting the bundle right can
    fix.

    Best effort: ``lsregister`` is not on ``PATH`` and never has been, so a
    macOS that has moved it costs a refresh rather than a build.
    """
    if not LSREGISTER.is_file():
        return False
    finished = subprocess.run([str(LSREGISTER), "-f", str(bundle)], capture_output=True, text=True)
    return finished.returncode == 0


def declared_languages(bundle: Path) -> tuple[str, ...]:
    """What the built Info.plist actually says, read back off the disk.

    The one key here whose absence is invisible: a bundle missing it builds,
    runs, and translates itself perfectly, and only the Language & Region
    panel is any the wiser.
    """
    import plistlib

    plist = bundle / "Contents" / "Info.plist"
    try:
        with plist.open("rb") as handle:
            return tuple(plistlib.load(handle).get("CFBundleLocalizations", ()))
    except (OSError, plistlib.InvalidFileException):
        return ()


def main() -> int:
    check_platform()
    check_interpreter()
    check_tools()
    print("rendering the icon…")
    build_icns(build_iconset(ROOT / "build"), ICNS)
    print(f"icon: {ICNS}")
    codes = languages()
    write_localizations(LPROJ, codes)
    bundle = build_bundle()

    # Both of these are invisible when they go wrong: a bundle missing them
    # builds, runs, and translates itself perfectly, and only the Language &
    # Region panel is any the wiser.
    for what, found in (
        ("declares", declared_languages(bundle)),
        ("carries", bundled_localizations(bundle)),
    ):
        if set(found) != set(codes):
            print(
                f"warning: the bundle {what} {found or 'no languages'} where the "
                f"catalogues are {codes} — System Settings reads the bundle",
                file=sys.stderr,
            )

    print()
    print(f"built: {bundle}")
    print(f"languages: {', '.join(codes)}")
    if not register(bundle):
        print(
            "Launch Services was not told about it. If System Settings > General > "
            f"Language & Region does not offer this application a language, run:\n"
            f"  {LSREGISTER} -f '{bundle}'"
        )
    print()
    print(
        "It is unsigned, so it will open on this machine and be refused by "
        "Gatekeeper on any other. Copy it to /Applications if you want it there."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
