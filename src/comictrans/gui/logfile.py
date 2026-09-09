"""The application log, and the hooks that make sure things reach it.

``review`` configures logging the way the CLI does — a stream handler onto
stderr — and a window launched from Finder, or from a bundle, has no stderr
anyone will ever read. Every warning about a skipped page and every traceback
from a worker thread was being written and thrown away. This adds a file
alongside, in the same directory the crash traces go to, so a failure has
somewhere to be found afterwards.

It is a different file from the crash traces and stays one. That one is
written from a signal handler and must not contend with the locks this one
takes; it holds the last thing a dying process did, and this holds everything
a living one said.

**Nothing is ever sent anywhere.** A log here is a file on the user's own
disk that they may choose to attach to something. No network calls anywhere
in the pipeline is an invariant, and a stack trace is not an exception to it.

No Qt at import. The one part that needs it — Qt's own message handler —
imports inside the function, the same way ``cli`` defers to this package.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

log = logging.getLogger(__name__)

APPLICATION = "comictrans"
FILENAME = "review.log"

LOG_DIR_ENV = "COMICTRANS_LOG_DIR"
"""Overrides where the log and the crash traces go, the same escape hatch
``COMICTRANS_FONT_PATH`` is for fonts. It exists for the suite, which must
not write into the log directory of whoever runs it, and works for anyone who
wants their logs somewhere else."""

MAX_BYTES = 1_000_000
BACKUPS = 2
"""Three megabytes at worst. Unlike the crash file this takes a line per
page, so it is rotated properly rather than rolled once."""

FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
"""Richer than the CLI's stderr format, which stays exactly as it was: a
file is read long after the fact and has to say when and from where."""


def log_directory() -> Path:
    """Where diagnostics go, by the convention of the platform.

    macOS has one and it is not the one ``QStandardPaths`` would give: logs
    live in ``~/Library/Logs``, which is where Console.app reads and where
    anyone asked for a log will look. Elsewhere, the XDG state directory,
    which is the closest equivalent for something that is neither
    configuration nor a cache.
    """
    override = os.environ.get(LOG_DIR_ENV)
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / APPLICATION
    state = os.environ.get("XDG_STATE_HOME")
    root = Path(state) if state else Path.home() / ".local" / "state"
    return root / APPLICATION


_handler: RotatingFileHandler | None = None


def install(directory: Path | None = None) -> Path | None:
    """Start logging to a file as well as to stderr. Returns where, or ``None``.

    The stream handler the CLI set up keeps the level the CLI chose, and the
    file takes everything down to DEBUG — which is what makes "log the risky
    thing before doing it" worth doing, since a log that ends mid-page names
    the page even when nothing else can.

    Never raises. An unwritable home costs the log and nothing else; a
    diagnostic that stops the window from opening is worse than none.
    """
    global _handler
    path = (directory or log_directory()) / FILENAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
        )
    except OSError as exc:
        log.warning("no log file at %s: %s", path, exc)
        return None

    handler.setFormatter(logging.Formatter(FORMAT))
    handler.setLevel(logging.DEBUG)

    root = logging.getLogger()
    # Pin the existing handlers where the CLI put them before opening the
    # root level up, or -q would quietly start printing INFO to stderr.
    for existing in root.handlers:
        if existing.level == logging.NOTSET:
            existing.setLevel(root.level)
    root.setLevel(min(root.level, logging.DEBUG) if root.level else logging.DEBUG)

    uninstall()
    root.addHandler(handler)
    _handler = handler
    return path


def uninstall() -> None:
    """Drop the file handler. For tests, and for a second ``install``."""
    global _handler
    if _handler is not None:
        logging.getLogger().removeHandler(_handler)
        _handler.close()
        _handler = None


# -- what to do with what nobody caught --------------------------------

Notifier = Callable[[str], None]

_notify: Notifier | None = None


def set_notifier(notify: Notifier | None) -> None:
    """Tell the window when something went unhandled, so it can say so.

    An exception raised in a Qt slot is printed and the event loop carries
    on — the window survives with its state possibly half-updated, and what
    the user sees is a button that did nothing. Carrying on silently is the
    worst of the options, so the window puts a line up pointing at the log.
    """
    global _notify
    _notify = notify


def _report(where: str, exc: BaseException) -> None:
    log.error("unhandled exception in %s", where, exc_info=exc)
    if _notify is not None:
        try:
            _notify(f"{type(exc).__name__} in {where} — see the log")
        except Exception:
            log.exception("the notifier itself failed")


def install_hooks() -> None:
    """Route unhandled exceptions to the log, on every thread.

    Measured: PySide6 calls ``sys.excepthook`` for an exception raised inside
    a slot, whether the slot was invoked from C++ or queued through the event
    loop. So this one hook covers the whole of the case that otherwise only
    ever reached a terminal nobody had open.
    """

    def on_main(
        kind: type[BaseException], value: BaseException, traceback: TracebackType | None
    ) -> None:
        if issubclass(kind, KeyboardInterrupt):
            sys.__excepthook__(kind, value, traceback)
            return
        _report("the window", value)

    def on_thread(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is None:
            return
        name = args.thread.name if args.thread is not None else "a worker thread"
        _report(name, args.exc_value)

    sys.excepthook = on_main
    threading.excepthook = on_thread


def install_qt_message_handler() -> None:
    """Send Qt's own warnings to the log instead of the terminal.

    Likely the most informative of the hooks: Qt says a good deal about
    layouts, dangling objects and paint devices that nobody currently sees.
    Imported here rather than at module scope so this file stays Qt-free.
    """
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }
    qt = logging.getLogger("qt")

    def handle(mode: QtMsgType, _context: object, message: str) -> None:
        # A fatal one is followed by abort(), so this is the last chance to
        # write it down; the crash file catches where, this catches why.
        qt.log(levels.get(mode, logging.WARNING), "%s", message)

    qInstallMessageHandler(handle)


__all__ = [
    "APPLICATION",
    "BACKUPS",
    "FILENAME",
    "LOG_DIR_ENV",
    "MAX_BYTES",
    "install",
    "install_hooks",
    "install_qt_message_handler",
    "log_directory",
    "set_notifier",
    "uninstall",
]
