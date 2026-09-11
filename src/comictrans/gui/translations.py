"""Which language the window speaks, and where its words come from.

**Three places say which, and they are asked in this order:**
``COMICTRANS_LANGUAGE``, the language picked in the window's own settings,
then the machine's — which on macOS includes the per-application language in
System Settings > General > Language & Region, because :func:`offered` asks
Qt for the preference *list* rather than the system locale. Anything named
and not translated falls back to English rather than to the next place down.

**GUI chrome only.** The menus, the dialogs, the status bar, the hint line.
Not the command line, which is a large surface for a different audience; not
plan file content, which is the comic rather than the interface; and not what
the pipeline says when something goes wrong — an unresolvable font or an
output directory inside the source tree produces the same sentence here and
on the command line, and one of them being translated would make them two
sentences to keep in step for no one's benefit.

**The translator is installed before the window is built, and that is
load-bearing.** Some of what the window says lives in module-level constants,
evaluated once when their module is imported; a translator installed after
that import leaves them in English for the life of the process. ``gui.app``
imports the widget modules from inside ``run()`` rather than at the top of
the file — which it already did, for its own reasons — so installing here,
first, is what makes those constants translatable at all. A test pins the
ordering, because nothing about the import would look wrong if it moved.

Qt's own strings — the "Don't Save" on an alert, the Open panel's furniture —
come from a second translator loaded from Qt's catalogue, so a window in
Swedish does not answer in half English.

**English has a catalogue too, holding nothing but plurals.** Qt counts for
you — ``%n page(s)`` comes back as ``3 page(s)`` with no translator loaded —
but it cannot inflect the noun beside the number, and English is the one
language here nobody would otherwise translate into. ``comictrans_en.ts`` is
built with ``lupdate -pluralonly``, so it holds those messages and no others,
and ``lrelease`` drops anything left unfinished: every string that is not a
plural falls straight through to the English in the source. Measured, not
assumed — see ``resources/translations/recompile.py``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator

log = logging.getLogger(__name__)

_current = "en"
"""Set by :func:`install`, read by :func:`current`. The default is what an
uninstalled process speaks, which is the source language by definition."""

_installed: list[QTranslator] = []
"""What :func:`install` put in Qt's chain last time, so it can take it out.

Installing is a once-per-process thing in the application, and this makes no
difference there. It is here because a second call otherwise *adds* a
translator over the first — Qt consults them newest first — leaving a process
speaking a language nothing asked for, which is a bug waiting for the first
caller who installs twice and a real nuisance in a test suite.
"""

TRANSLATION_DIR = Path(__file__).parent / "resources" / "translations"
"""Compiled ``.qm`` catalogues, beside the ``.ts`` they are built from."""

PREFIX = "comictrans"
"""``comictrans_sv.qm``, and so on. The stem Qt matches a locale against."""

LANGUAGE_ENV = "COMICTRANS_LANGUAGE"
"""Force a language, whatever anything else says.

For seeing a translation on a machine that is not set to that language,
which is most of the machines anybody checks one on. Same shape as
``COMICTRANS_FONT_PATH`` and ``COMICTRANS_LOG_DIR``, and first in the order
below for the same reason: it is the hatch, and a hatch that something else
could overrule would be no use for checking anything.
"""

SOURCE_LANGUAGE = "en"
"""The language the source strings are written in, and the fallback.

It has a catalogue, but only for plurals — see the module docstring. Every
other English string is the one in the source, which is why a missing
``comictrans_en.qm`` costs a few inflections rather than the window.
"""


def available() -> tuple[str, ...]:
    """Every language a catalogue ships for, sorted. English included."""
    if not TRANSLATION_DIR.is_dir():
        return ()
    return tuple(
        sorted(path.stem.removeprefix(f"{PREFIX}_") for path in TRANSLATION_DIR.glob("*.qm"))
    )


def _bare(tag: str) -> str:
    """``sv_SE``, ``sv-SE`` and ``sv`` all come back as ``sv``.

    The window is translated per language, not per country: there is no
    answer this tool gives that differs between Sweden and Finland.
    """
    return tag.strip().replace("-", "_").split("_")[0].lower()


def offered() -> tuple[str, ...]:
    """What the machine's own language settings ask for, best first.

    ``uiLanguages`` rather than ``name``, and that is the whole of what makes
    the macOS per-application language work: setting one in System Settings >
    General > Language & Region writes an ``AppleLanguages`` list scoped to
    that application, which Qt reports here in order. The single ``name()``
    only ever describes the *system* locale, so an application asked for
    Swedish on an English Mac would have gone on speaking English.

    It is a list because a preference list is what a person actually has:
    Swedish, then English, then whatever else. The first one there is a
    catalogue for wins.
    """
    return _codes(*QLocale.system().uiLanguages(), QLocale.system().name())


def _codes(*tags: str) -> tuple[str, ...]:
    """Language tags as bare codes, in order, without repeats or blanks.

    Split out from :func:`offered` to be testable: a machine set to one
    language — which is every machine this suite runs on — cannot tell a list
    of preferences from a single locale by looking at the result.
    """
    codes: list[str] = []
    for tag in tags:
        code = _bare(tag)
        if code and code not in codes:
            codes.append(code)
    return tuple(codes)


def preferred(chosen: str = "") -> tuple[str, ...]:
    """Every language to try, best first, from all three places one is said.

    In order: ``COMICTRANS_LANGUAGE``, the language picked in the window's
    own settings, then the machine's. Each of the first two is one answer
    rather than a list — somebody who names a language has named it, and
    falling through to the system's when there is no catalogue for it would
    be answering a different question. English is behind all of them.
    """
    forced = os.environ.get(LANGUAGE_ENV, "").strip()
    if forced:
        return (_bare(forced),)
    if chosen.strip():
        return (_bare(chosen),)
    return offered()


def wanted(chosen: str = "") -> str:
    """The language asked for, which is not always the one that loads."""
    asked = preferred(chosen)
    return asked[0] if asked else SOURCE_LANGUAGE


def resolved(chosen: str = "") -> str:
    """The language a window opened right now would actually speak.

    What :func:`install` settles on, without installing anything — so the
    window can ask whether a language just chosen in its settings differs
    from the one it is running in, and say a restart is needed only when it
    does. Picking Swedish on a Mac already running the window in Swedish
    changes nothing and should not claim to.
    """
    return next((code for code in preferred(chosen) if code in available()), SOURCE_LANGUAGE)


def current() -> str:
    """The language the window is actually speaking.

    ``wanted()`` is what the machine asked for; this is what it got, which
    differs whenever there is no catalogue for the first. The guide is picked
    by this one — a window in English because Swedish is not translated yet
    should not open a Swedish guide.
    """
    return _current


def install(app: QCoreApplication, chosen: str = "") -> str:
    """Load the catalogues for :func:`preferred` and return the language used.

    ``chosen`` is the language picked in the window's own settings, empty for
    "whatever this machine is set to". It is read out of the stored
    preferences by ``gui.app`` and handed in rather than read here, so that
    this module stays the one that knows about languages and that one stays
    the one that knows about settings.

    Returns ``"en"`` when there is nothing else to load, which is not a
    failure: English is the source language, and its own catalogue carries
    only the plurals the source cannot spell.

    Both translators are parented to the application deliberately. A
    ``QTranslator`` that goes out of scope is removed from Qt's chain by its
    own destructor, and a window that reverted to English a moment after
    opening would be a puzzling bug to go looking for.
    """
    global _current
    _current = SOURCE_LANGUAGE
    while _installed:
        app.removeTranslator(_installed.pop())

    asked = preferred(chosen)
    language = resolved(chosen)
    if language not in asked:
        log.info("no catalogue for %s; falling back to %s", ", ".join(asked), SOURCE_LANGUAGE)

    ours = QTranslator(app)
    if not ours.load(f"{PREFIX}_{language}", str(TRANSLATION_DIR)):
        log.warning("could not load the %s translation from %s", language, TRANSLATION_DIR)
        return SOURCE_LANGUAGE
    app.installTranslator(ours)
    _installed.append(ours)

    # Qt's own, for the strings Qt draws rather than we do. Missing is not
    # worth a word: the window is translated either way, and only Qt's own
    # furniture stays English.
    theirs = QTranslator(app)
    catalogue = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if theirs.load(QLocale(language), "qtbase", "_", catalogue):
        app.installTranslator(theirs)
        _installed.append(theirs)
    else:
        log.debug("no Qt catalogue for %s in %s", language, catalogue)

    log.info("interface language: %s", language)
    _current = language
    return language


__all__ = [
    "LANGUAGE_ENV",
    "PREFIX",
    "SOURCE_LANGUAGE",
    "TRANSLATION_DIR",
    "available",
    "current",
    "install",
    "offered",
    "preferred",
    "resolved",
    "wanted",
]
