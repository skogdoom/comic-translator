"""Entry point for ``comictrans review``.

Two layers of "is this actually usable", not one. ``PySide6`` importing
successfully only means the wheel is installed — it does not touch a
platform's windowing libraries, which is a separate, deeper failure this
module hit for itself during development (a container with PySide6 installed
but no ``libEGL``). So ``available()`` is cheap and answers the first
question; the second is only answered by actually trying to open a window,
inside :func:`run`.

Nothing above this module imports PySide6 at all, which is what lets
``comictrans`` stay usable — and its test suite runnable — on a machine
without it. ``cli.py`` imports this module lazily, the same way it defers to
``ocr.vision`` and ``ocr.tesseract``, and this module in turn only imports
the widget modules (which do need PySide6 at their own top level) from
inside :func:`run`, after :func:`available` has already been checked.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import GuiUnavailableError

if TYPE_CHECKING:  # this module has to import without PySide6 — see ``available``
    from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)

_ORGANIZATION = "comictrans"
_APPLICATION = "review"
"""Where ``QSettings`` keeps what this tool remembers between sessions.

Four things, and no more: the window layout, the preferences a new run
starts from, the list of plans opened lately, and what each installed plugin
is set to (``plugin_settings``, under ``plugins/``). What a review *means*
is not among them — that lives in the plan file, which is the thing you can
read, diff and hand to someone else.

They are kept under separate keys rather than in one blob, because they are
wanted and unwanted separately: Reset Layout puts the docks back without
touching the preferences, Clear Menu empties the history without touching
either, and turning experimental features off leaves a plugin's settings
where they were for when it is turned back on.
"""

_IMPORT_ERROR: str | None

try:  # pragma: no cover - depends on whether the `gui` extra is installed
    import PySide6  # noqa: F401
except ImportError as exc:  # pragma: no cover
    _IMPORT_ERROR = str(exc)
else:
    _IMPORT_ERROR = None


def available() -> bool:
    """True if PySide6 itself is installed. Says nothing about a display."""
    return _IMPORT_ERROR is None


def unavailable_reason() -> str:
    return _IMPORT_ERROR or "PySide6 is not installed"


def close_down(window: QWidget) -> None:
    """Destroy the window now, rather than leaving it to the interpreter.

    **This is what a crash report asked for.** A review session that had been
    quit segfaulted on the way out, and the macOS report named the path
    exactly: ``Py_FinalizeEx`` runs the ``atexit`` handlers, PySide's is
    ``runCleanupFunctions`` → ``destroyQCoreApplication``, and that walks
    every Python-wrapped ``QObject`` still alive and destroys it —
    ``visitAllPyObjects(destructionVisitor)``. It reached a ``QDialog``,
    destroyed it, and the dialog's own destructor walked into a child that
    had already been freed: ``EXC_BAD_ACCESS`` at ``0x39``, with no Python
    frame on the stack because by then there was no Python left to be on it.

    The order that walk destroys things in is not ours to choose. What is
    ours is whether anything is left for it to walk, so the window goes
    here, deterministically, while the interpreter is still running and Qt
    can tear its children down in the order it knows. ``shiboken6.delete``
    rather than ``del`` or ``deleteLater``: the first cannot work, because a
    window is the centre of a knot of reference cycles and is collected by
    the cycle collector rather than by a refcount reaching zero; the second
    only posts an event, and the event loop it needs has already returned.

    Safe to call twice, and safe on a window whose C++ half is already gone.
    """
    import shiboken6

    if shiboken6.isValid(window):
        shiboken6.delete(window)


def run(plan_path: Path | None = None) -> int:
    """Open the review window, on a plan file if one was named. Blocks until closed.

    With no plan file it opens empty, and stays that way until asked. It used
    to put the Open dialog up immediately, on the grounds that there was
    nothing else to do in an empty window — which stopped being true when
    extract moved into the window. An empty window is now the front door to
    two things, and a modal dialog in front of one of them is in the way of
    the other. The status bar names both.

    Diagnostics are turned on first, before anything can go wrong. A window
    launched from Finder has no stderr at all, so both halves go to files:
    :mod:`comictrans.gui.logfile` for what a running window says and for
    anything nobody caught, and :mod:`comictrans.gui.crash` for the traceback
    of a process that dies outright.

    Raises :class:`GuiUnavailableError` rather than letting an import error or
    a platform-plugin failure escape as something unreadable — both are things
    a user can plausibly fix (install the extra; install the missing system
    library) if the message says so.
    """
    if not available():
        raise GuiUnavailableError(
            "the review GUI needs PySide6, which is not installed "
            f"({unavailable_reason()}). Install it with: uv sync --extra gui"
        )

    # Before the QApplication, so anything that goes wrong while Qt is
    # starting up has somewhere to go too. None of this raises: an unwritable
    # home costs the diagnostics and the window opens either way.
    from . import logfile
    from .crash import enable as enable_crash_traces

    written = logfile.install()
    logfile.install_hooks()
    traces = enable_crash_traces()
    if written is not None:
        log.info("log file: %s", written)
    if traces is not None:
        log.info("crash traces: %s", traces)

    from PySide6.QtWidgets import QApplication

    from . import about

    # Set before Qt starts so nothing derives them from argv[0] instead.
    # These are what Qt itself reads: applicationDisplayName titles windows
    # that do not title themselves, applicationName is the fallback key for
    # a QSettings built without explicit ones. Neither is what macOS reads —
    # see below.
    QApplication.setApplicationName(about.NAME)
    QApplication.setApplicationDisplayName(about.NAME)

    try:
        # The name in argv[0], not the path this was launched by, because on
        # macOS that string becomes the application menu.
        #
        # Qt titles "About X", "Hide X" and "Quit X" from
        # qt_mac_applicationName(), which reads CFBundleName out of the
        # bundle's Info.plist and falls back to the basename of argv[0].
        # QCoreApplicationPrivate::appName does the same. Neither consults
        # setApplicationName at all, so no amount of setting it moves that
        # menu: run from `.venv/bin/comictrans` those items read
        # "comictrans", which is the basename and nothing else.
        #
        # Qt does not need argv[0] to be a path. applicationFilePath comes
        # from the OS — measured, it reports the real interpreter either way
        # — and this project never asks for it or for applicationDirPath.
        # Every argument after the first is dropped here regardless, so the
        # command line is not being taken away from anyone.
        #
        # The bold title beside the Apple menu is a different thing again:
        # that is the process, which is the interpreter, and only a real
        # bundle changes it. The bundle sets CFBundleName and gets both.
        app = QApplication.instance() or QApplication([about.NAME])
    except Exception as exc:  # Qt's own platform-plugin failures vary by OS and are not typed
        raise GuiUnavailableError(
            f"PySide6 is installed but could not open a display: {exc}. On Linux this "
            "usually means a system Qt/X11 library is missing; on macOS it should just work."
        ) from exc

    # Now that Qt exists, its own warnings can be routed too — the most
    # informative of the three hooks, and the only one that needs it running.
    logfile.install_qt_message_handler()

    # Before the widget modules are imported, and that is the whole reason
    # they are imported down here rather than at the top of this file. Some
    # of what the window says lives in module-level constants, evaluated once
    # at import; a translator installed after that leaves them in English for
    # the life of the process. A test pins this ordering.
    #
    # The settings are read here rather than by the window, because the
    # language is one of them and the window is what a language has to be
    # chosen before. The same object is handed to the window below, so there
    # is one of them and not two.
    from PySide6.QtCore import QSettings

    from . import translations
    from .preferences import load_preferences

    settings = QSettings(_ORGANIZATION, _APPLICATION)
    language = translations.install(app, load_preferences(settings).language)

    # Set on the application rather than the window, so every window and
    # dialog this process opens inherits it. On macOS the Dock reads the
    # bundle instead, where the .icns tools/build_app.py renders answers for
    # it — this is what shows before there is a bundle, and everywhere that
    # is not a Mac.
    from . import icons

    # Static on QGuiApplication rather than a call on the instance, which
    # `QApplication.instance()` types as the QCoreApplication that has no
    # window to put an icon on.
    QApplication.setWindowIcon(icons.app_icon())

    from .main_window import MainWindow

    window = MainWindow(plan_path, settings=settings)
    window.show()
    log.debug("window open in %s", language)
    code = app.exec()
    close_down(window)
    return code
