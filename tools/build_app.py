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

This script does the two things the spec cannot: it renders the icon, because
``.icns`` holds one image per size rather than one drawing, and it refuses
early and in plain words on a machine that cannot produce a bundle at all.
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


def main() -> int:
    check_platform()
    check_tools()
    print("rendering the icon…")
    build_icns(build_iconset(ROOT / "build"), ICNS)
    print(f"icon: {ICNS}")
    bundle = build_bundle()
    print()
    print(f"built: {bundle}")
    print()
    print(
        "It is unsigned, so it will open on this machine and be refused by "
        "Gatekeeper on any other. Copy it to /Applications if you want it there."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
