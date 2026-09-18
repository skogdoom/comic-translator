"""Somewhere for a hard crash to leave a trace.

An exception in a Qt slot is printed and the event loop carries on; that is
noisy but survivable. A segfault, or the abort ``qFatal`` raises when Qt gives
up, is neither: the process dies, the window vanishes, and a review session
launched from Finder has no stderr for any of it to have gone to. Without
this, nothing at all is left behind.

:mod:`faulthandler` is the only thing that helps. It installs handlers for the
fatal signals and writes a Python traceback — the exact line, on every thread
— from inside the signal handler, which is why it works when nothing else
does. Measured: ``SIGSEGV`` (exit 139) and ``SIGABRT`` (exit 134) both caught,
both written to the file rather than a terminal.

This is deliberately not the application log, which is :mod:`.logfile` and a
different thing with different rules: this file is written from a signal
handler and must not contend with the logging module's locks, and it holds
only the last thing the process did before dying.

No Qt, and it shares :func:`comictrans.gui.logfile.log_directory` with the
application log so the two files sit side by side: whoever finds one finds
the other.
"""

from __future__ import annotations

import faulthandler
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from .. import __version__
from .logfile import log_directory

log = logging.getLogger(__name__)

FILENAME = "review-crash.log"

MAX_BYTES = 512 * 1024
"""When the file passes this, it is rolled to ``.old`` and started again.

A traceback is a few hundred bytes and a session banner less, so this is
thousands of launches. Two files, never more: enough to still have the crash
after launching the application again to look for it, which is exactly what
somebody does.
"""


_file: IO[str] | None = None
"""Held for the life of the process.

``faulthandler`` writes to the file *descriptor*, so letting the object be
collected would close the fd out from under a signal handler that only runs
when things are already going badly.
"""


def _roll(path: Path) -> None:
    try:
        if path.is_file() and path.stat().st_size > MAX_BYTES:
            path.replace(path.with_suffix(".log.old"))
    except OSError:  # a full or read-only disk is not worth failing over
        pass


def enable(directory: Path | None = None) -> Path | None:
    """Start writing fatal-signal traces to a file. Returns where, or ``None``.

    Never raises. A diagnostic that stops the window from opening is worse
    than no diagnostic, so an unwritable home directory, a sandbox, or a full
    disk costs the traces and nothing else.
    """
    global _file
    path = (directory or log_directory()) / FILENAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _roll(path)
        handle = path.open("a", encoding="utf-8", buffering=1)
    except OSError as exc:
        log.warning("no crash log at %s: %s", path, exc)
        return None

    # A banner per launch, so a trace can be told from the session it
    # belongs to, and a session with nothing after it can be told from one
    # that ended in a traceback.
    started = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    handle.write(f"\n--- comictrans {__version__} review started {started} ---\n")
    handle.flush()

    if _file is not None:
        _file.close()
    _file = handle
    faulthandler.enable(file=handle, all_threads=True)
    return path


def disable() -> None:
    """Stop, and let the file go. For tests, and for a second ``enable``."""
    global _file
    faulthandler.disable()
    if _file is not None:
        _file.close()
        _file = None


__all__ = ["FILENAME", "MAX_BYTES", "disable", "enable"]
