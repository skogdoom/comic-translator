"""Running the apply pass off the UI thread.

Rendering a chapter is a second or so of work per page, all of it in Pillow,
numpy and OpenCV. Doing it in the window's own thread would freeze the window
for the length of the run, with no progress and no way out, so it happens on
a worker thread and reports back through signals.

This is deliberately a thin harness around :func:`apply_plan` rather than a
loop of its own — the plan-to-pages loop stays in ``apply``, where the CLI
uses it too, and this only supplies the two callbacks it takes.

Nothing here touches the document, the canvas, or any other widget. A job is
handed a frozen :class:`Plan` and hands back an :class:`ApplyReport`, which
is what makes it safe to run beside a window whose plan is still being
edited: the plan it renders is the one it was given.

**Why this subclasses QThread.** The usual advice is to move a worker object
onto a thread instead, and it is good advice when the worker has slots for
things to call while it runs — a thread that only executes ``run()`` has no
event loop to deliver them to. This one has nothing to receive: it is told to
stop through a :class:`threading.Event`, not a slot. Overriding ``run()``
therefore costs nothing and buys the thing the alternative gets wrong here,
which is that ``wait()`` returns when the work is done rather than when
somebody remembers to quit an event loop that was only ever idling.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from ..apply import PageProgress, apply_plan
from ..config import ApplyConfig
from ..errors import ComictransError
from ..model import Plan

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RenderRequest:
    """Everything one run needs, settled before the thread starts.

    Frozen, and holding a frozen ``Plan``: once a run is under way there is
    nothing about it left for the window to change, which is why editing the
    document while it renders cannot affect what lands on disk.
    """

    plan: Plan
    plan_path: Path
    output: Path
    config: ApplyConfig
    image_format: str | None = None
    force: bool = False

    @property
    def total(self) -> int:
        return len(self.plan.image_names())


class RenderJob(QThread):
    """One render, from start to report.

    ``progressed`` arrives once per page, before that page is rendered.
    Exactly one of ``completed`` and ``failed`` follows, and after either the
    job is done and can be dropped. All three are delivered in the window's
    thread, because that is where their receivers live.
    """

    progressed = Signal(int, int, str)
    """``(index, total, image)`` — index counts from zero."""

    completed = Signal(object)
    """An :class:`ApplyReport`, whether or not the run was cancelled.

    Not called ``finished``: ``QThread`` already has a signal by that name,
    meaning the thread has stopped, which is a different event and one worth
    keeping.
    """

    failed = Signal(str)
    """The run could not be done at all: an unresolvable font, say."""

    def __init__(self, request: RenderRequest, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.request = request
        self._cancel = threading.Event()
        self.setObjectName("comictrans-render")

    def cancel(self) -> None:
        """Ask for the run to stop after the page it is on.

        Not during one: a half-written page is not something to leave on
        disk, and a page takes a second at worst.
        """
        self._cancel.set()

    @property
    def cancelling(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:
        """The worker thread. Everything here runs off the UI thread."""
        request = self.request
        try:
            report = apply_plan(
                request.plan,
                request.plan_path,
                request.output,
                request.config,
                image_format=request.image_format,
                force=request.force,
                progress=self._on_page,
                should_cancel=self._cancel.is_set,
            )
        except ComictransError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            # A bug, not an expected failure. On the command line one of
            # these would print a traceback and stop; on a worker thread
            # there is no top level to reach, so it is logged with its
            # traceback and reported, rather than leaving a window that
            # renders nothing and never says why.
            log.exception("render failed")
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.completed.emit(report)

    def _on_page(self, progress: PageProgress) -> None:
        self.progressed.emit(progress.index, progress.total, progress.image)


__all__ = ["RenderJob", "RenderRequest"]
