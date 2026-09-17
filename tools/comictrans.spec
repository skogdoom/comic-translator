# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Comic Translator.app. Run it through tools/build_app.py.

Read by PyInstaller with exec(), which is why the names below are undefined
here and why this file is not linted or type-checked with the rest.

Two collections that are easy to leave out and hard to notice missing.

``collect_data_files`` carries ``gui/resources/`` — the toolbar drawings, the
application icon, and the guide the Help menu opens. None of them is imported,
so nothing in the analysis would have found them, and all three fail quietly:
a warning in the log and an empty icon, or a Help window saying the guide
could not be read.

It excludes ``.py`` files by default, which is right for everything above —
none of it is source — and wrong for ``gui/resources/plugins/``, whose one
file *is* Python, meant to be copied out and run as a plugin rather than
imported here. A second, narrower call with ``include_py_files=True`` carries
that one directory; without it, Plugins > Install Example Plugin would ask
for a file that shipped everywhere except inside the bundle.

``copy_metadata`` carries the installed distribution's own metadata. The About
dialog reads its version, author and licence from it at run time rather than
repeating them, and lists every declared dependency that is actually
installed — a bundle without it reports "running from an uninstalled source
tree", which inside a bundle is simply wrong. ``recursive=True`` because the
dependency list is read the same way.

``recursive=True`` walks the *required* dependencies, which is not all of
them: an extra's packages are not required by anything, so PySide6 — the
whole reason this bundle exists — was missing from the About dialog's list
until it was named here. Measured on a build: its ``.dist-info`` was simply
not in the tree.
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

from comictrans import __version__ as VERSION
from comictrans.gui.translations import SOURCE_LANGUAGE, available

NAME = "Comic Translator"

LANGUAGES = sorted({SOURCE_LANGUAGE, *available()})
"""Every language the window has a catalogue for, read rather than listed.

macOS decides what the per-application Language list offers from the
bundle — an application that declares nothing is one System Settings says
"doesn\'t support additional languages" about, however many catalogues are
inside it. Read from ``gui.translations`` so that a catalogue added and this
file forgotten cannot happen.
"""

IDENTIFIER = "io.github.skogdoom.comictrans"
"""Reverse DNS from the owner and the distribution, and not to be tidied.

It survived the repository being renamed because it never named the
repository — but the reason to leave it alone is stronger than that. macOS
identifies an installed application by this string: its preferences, its
Launch Services registration, anything it is ever granted. Changing it after
1.0.0 shipped would make the next build a different application that happens
to look the same, having forgotten everything the last one was told.
"""

EXTRAS = ("pyside6", "pytesseract")
"""Distributions from the project's extras, whose metadata ``recursive=True``
does not reach. Asked for one at a time and skipped when absent, because
which extras are installed is the builder's choice: a build on a machine
without Tesseract must not fail over a library that run would never load."""


def extra_metadata():
    """The metadata of whichever extras this machine actually has."""
    collected = []
    for distribution in EXTRAS:
        try:
            collected += copy_metadata(distribution)
        except Exception:  # PyInstaller raises its own type for "not installed"
            pass
    return collected


ICNS = os.environ["COMICTRANS_ICNS"]
"""Where build_app.py left the icon it rendered. From the environment rather
than a path written here, because this spec must not be runnable without the
step that makes the thing it names: a missing icon is not an error PyInstaller
reports, it is Python's default icon on somebody's Dock."""

LPROJ = os.environ["COMICTRANS_LPROJ"]
"""Where build_app.py left the ``.lproj`` directories, for the same reason.

They go in as data rather than being written into the bundle afterwards
because PyInstaller signs the bundle and then verifies it: anything added to
``Contents/Resources`` after that breaks the seal it just made. Collected
here, they are sealed with everything else.
"""


def localizations():
    """``(source, destination)`` for each ``.lproj``, as PyInstaller takes it.

    A destination of ``sv.lproj`` lands in ``Contents/Resources/sv.lproj``,
    which is where macOS looks to decide whether this application can be
    given a language of its own. ``CFBundleLocalizations`` below says the
    same thing in the Info.plist and did not, on its own, move System
    Settings — so both are here.
    """
    return [
        (str(strings), strings.parent.name)
        for strings in sorted(Path(LPROJ).glob("*.lproj/InfoPlist.strings"))
    ]

analysis = Analysis(  # noqa: F821 - PyInstaller injects this
    ["app_entry.py"],
    pathex=[],
    binaries=[],
    datas=(
        collect_data_files("comictrans")
        + collect_data_files(
            "comictrans", subdir="gui/resources/plugins", include_py_files=True
        )
        + copy_metadata("comictrans", recursive=True)
        + extra_metadata()
        + localizations()
    ),
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    # Only what this project provably never imports: the source names
    # QtCore, QtGui and QtWidgets and nothing else. Qt itself is not trimmed
    # further — the bundle is large, and how much of that is trimmable is a
    # measurement nobody has made on a Mac yet.
    excludes=["tkinter", "pytest", "PyInstaller"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)  # noqa: F821

executable = EXE(  # noqa: F821
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # A window, not a terminal — and this one carries further than it looks.
    # PyInstaller reads console=True as LSBackgroundOnly=True, which is an
    # application with no Dock icon and no menu bar. COLLECT and BUNDLE each
    # inherit the flag from the object below them, so this is where it is
    # decided for all three.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

collected = COLLECT(  # noqa: F821
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=NAME,
)

app = BUNDLE(  # noqa: F821
    collected,
    name=f"{NAME}.app",
    icon=ICNS,
    bundle_identifier=IDENTIFIER,
    version=VERSION,
    info_plist={
        # The reason this milestone exists. macOS titles its application
        # menu — "About X", "Hide X", "Quit X" — from qt_mac_applicationName,
        # which reads CFBundleName out of this file and only falls back to
        # the argv[0] name when there is no bundle. PyInstaller already sets
        # both from the bundle name; they are written out here so the one
        # string this milestone turns on is visible rather than implied.
        "CFBundleName": NAME,
        "CFBundleDisplayName": NAME,
        "CFBundleVersion": VERSION,
        # What System Settings > General > Language & Region reads to decide
        # whether this application can be given a language of its own. The
        # usual way to say it is an .lproj directory per language, which is
        # for applications whose strings macOS loads; ours are Qt catalogues
        # loaded by Qt, and this key is Apple's answer for exactly that case
        # — "the localizations handled manually by your app".
        #
        # Choosing one there writes an AppleLanguages list scoped to this
        # application, which is what gui.translations.offered() reads back
        # through QLocale.uiLanguages. The window's own Preferences overrule
        # it, and COMICTRANS_LANGUAGE overrules both.
        "CFBundleLocalizations": LANGUAGES,
        "CFBundleDevelopmentRegion": SOURCE_LANGUAGE,
        "NSHighResolutionCapable": True,
        # Pinned rather than left to the console flag above, because getting
        # it wrong is an application that launches with no Dock icon and no
        # menu bar, and nothing in this repository's tests can run a bundle
        # to find out.
        "LSBackgroundOnly": False,
        # Apple Silicon only, which is what this project targets and what
        # every other requirement here already assumes.
        "LSMinimumSystemVersion": "11.0",
        # No document types. macOS would then hand a double-clicked plan
        # file to the application through an Apple Event, which nothing here
        # listens for yet; claiming the type without handling it would make
        # a plan file open a window that ignores it.
    },
)
