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
import sys
from pathlib import Path

from ..errors import GuiUnavailableError

log = logging.getLogger(__name__)

_ORGANIZATION = "comictrans"
_APPLICATION = "review"
"""Where ``QSettings`` keeps the window layout between sessions.

The only state this tool holds outside a plan file, and it is deliberately
nothing but layout: what a review *means* lives in the plan, which is the
file you can read, diff and hand to someone else.
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

    # Before Qt is started, and that is the whole point of the placement.
    #
    # macOS titles its application menu — "About X", "Hide X", "Quit X" —
    # once, in QCocoaMenuLoader's init, which runs while QApplication is
    # being constructed. The name it uses is qt_mac_applicationName():
    # CFBundleName from the bundle's Info.plist if there is one, and
    # otherwise the name derived from argv[0]. Setting the application name
    # after construction changes nothing there, because those titles have
    # already been built — which is why "About comictrans" outlived a
    # rename of everything this window titles for itself.
    #
    # Unbundled, this is the fix. Bundled, CFBundleName wins and 4.10 has
    # to set it to the same string; the roadmap says so.
    #
    # Safe to set because nothing in this project reads applicationName:
    # the one real QSettings names its organisation and application
    # outright, and the log directory comes from logfile.APPLICATION, a
    # literal. A test asserts neither moves.
    QApplication.setApplicationName(about.NAME)
    QApplication.setApplicationDisplayName(about.NAME)

    try:
        app = QApplication.instance() or QApplication(sys.argv[:1])
    except Exception as exc:  # Qt's own platform-plugin failures vary by OS and are not typed
        raise GuiUnavailableError(
            f"PySide6 is installed but could not open a display: {exc}. On Linux this "
            "usually means a system Qt/X11 library is missing; on macOS it should just work."
        ) from exc

    # Now that Qt exists, its own warnings can be routed too — the most
    # informative of the three hooks, and the only one that needs it running.
    logfile.install_qt_message_handler()

    # Set on the application rather than the window, so every window and
    # dialog this process opens inherits it. On macOS the Dock reads the
    # bundle instead, which is milestone 4.10's business — this is what shows
    # before there is a bundle, and everywhere that is not a Mac.
    from . import icons

    # Static on QGuiApplication rather than a call on the instance, which
    # `QApplication.instance()` types as the QCoreApplication that has no
    # window to put an icon on.
    QApplication.setWindowIcon(icons.app_icon())

    from PySide6.QtCore import QSettings

    from .main_window import MainWindow

    window = MainWindow(plan_path, settings=QSettings(_ORGANIZATION, _APPLICATION))
    window.show()
    return app.exec()
