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

    Fatal-signal traces are turned on first — see :mod:`comictrans.gui.crash`.
    A segfault or a ``qFatal`` abort kills the process outright, and a window
    launched from Finder has no stderr for it to be reported on, so the trace
    goes to a file instead.

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

    # Before the QApplication, so a fatal signal raised while Qt is starting
    # up has somewhere to go too. Never raises; returns None if it could not
    # open the file, and the window opens either way.
    from .crash import enable as enable_crash_traces

    traces = enable_crash_traces()
    if traces is not None:
        log.info("crash traces: %s", traces)

    from PySide6.QtWidgets import QApplication

    try:
        app = QApplication.instance() or QApplication(sys.argv[:1])
    except Exception as exc:  # Qt's own platform-plugin failures vary by OS and are not typed
        raise GuiUnavailableError(
            f"PySide6 is installed but could not open a display: {exc}. On Linux this "
            "usually means a system Qt/X11 library is missing; on macOS it should just work."
        ) from exc

    from PySide6.QtCore import QSettings

    from .main_window import MainWindow

    window = MainWindow(plan_path, settings=QSettings(_ORGANIZATION, _APPLICATION))
    window.show()
    return app.exec()
