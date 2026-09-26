"""Running a pipeline pass off the UI thread.

Both passes are seconds of work per page — apply in Pillow, numpy and
OpenCV, extract in OCR and detection on top of those — and either would
freeze the window for the length of a chapter, with no progress and no way
out. Both therefore run on a worker thread and report back through signals.

Each job is deliberately a thin harness around the pass's own top-level
function rather than a loop of its own. The plan-to-pages and pages-to-plan
loops stay where the command line uses them, and a job only supplies the two
callbacks they take.

Nothing here touches the document, the canvas, or any other widget. A render
job is handed a frozen :class:`Plan` and hands back an :class:`ApplyReport`,
which is what makes it safe to run beside a window whose plan is still being
edited: the plan it renders is the one it was given. An extract job is handed
paths and hands back an :class:`ExtractReport`; the plan it writes did not
exist when it started.

**Why these subclass QThread.** The usual advice is to move a worker object
onto a thread instead, and it is good advice when the worker has slots for
things to call while it runs — a thread that only executes ``run()`` has no
event loop to deliver them to. These have nothing to receive: they are told
to stop through a :class:`threading.Event`, not a slot. Overriding ``run()``
therefore costs nothing and buys the thing the alternative gets wrong here,
which is that ``wait()`` returns when the work is done rather than when
somebody remembers to quit an event loop that was only ever idling.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from ..apply import ApplyReport, apply_plan
from ..config import (
    DEFAULT_SOURCE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
    ApplyConfig,
    ExtractConfig,
)
from ..errors import ComictransError
from ..extract import ExtractReport, extract, read_region
from ..fonts import resolve
from ..imaging import PageImage
from ..model import Plan, Polygon, TextCase
from ..ocr import get_recognizer
from ..planfile import write_plan
from ..progress import CancelCheck, PageProgress, ProgressCallback
from ..render import RenderCancelled
from ..sources import is_container, unpack
from .preview import Preview, PreviewRequest, render_preview

log = logging.getLogger(__name__)


class RunJob(QThread):
    """One pipeline pass, from start to report.

    ``progressed`` arrives once per page, before that page is worked on.
    Exactly one of ``completed`` and ``failed`` follows, and after either the
    job is done and can be dropped. All three are delivered in the window's
    thread, because that is where their receivers live.

    Subclasses implement :meth:`work` and nothing else.
    """

    progressed = Signal(int, int, str)
    """``(index, total, image)`` — index counts from zero."""

    completed = Signal(object)
    """The pass's own report object, whether or not the run was cancelled.

    Not called ``finished``: ``QThread`` already has a signal by that name,
    meaning the thread has stopped, which is a different event and one worth
    keeping.
    """

    failed = Signal(str)
    """The run could not be done at all: an unresolvable font, no OCR
    backend, an output directory inside the source tree."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cancel = threading.Event()
        self.setObjectName(f"comictrans-{type(self).__name__.lower()}")

    def cancel(self) -> None:
        """Ask for the run to stop after the page it is on.

        Not during one: a page takes a second at worst, and stopping cleanly
        between them is what lets each pass say something true about what it
        left behind.
        """
        self._cancel.set()

    @property
    def cancelling(self) -> bool:
        return self._cancel.is_set()

    def work(self, progress: ProgressCallback, should_cancel: CancelCheck) -> object:
        """Do the pass and return its report. Runs on the worker thread."""
        raise NotImplementedError

    def run(self) -> None:
        """The worker thread. Everything here runs off the UI thread."""
        try:
            report = self.work(self._on_page, self._cancel.is_set)
        except ComictransError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            # A bug, not an expected failure. On the command line one of
            # these would print a traceback and stop; on a worker thread
            # there is no top level to reach, so it is logged with its
            # traceback and reported, rather than leaving a window that does
            # nothing and never says why.
            log.exception("%s failed", type(self).__name__)
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.completed.emit(report)

    def _on_page(self, progress: PageProgress) -> None:
        self.progressed.emit(progress.index, progress.total, progress.image)


# -- rendering ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RenderRequest:
    """Everything one render needs, settled before the thread starts.

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

    rar_tool: str = ""
    """Where the ``rar`` compressor is, for writing a .cbr. Empty looks at
    ``COMICTRANS_RAR`` and then ``PATH``. Ignored for every other output —
    a directory and a .cbz need nothing."""

    @property
    def total(self) -> int:
        return len(self.plan.image_names())


class RenderJob(RunJob):
    """``apply_plan`` on a worker thread. Completes with an ``ApplyReport``."""

    def __init__(self, request: RenderRequest, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.request = request

    def work(self, progress: ProgressCallback, should_cancel: CancelCheck) -> ApplyReport:
        request = self.request
        return apply_plan(
            request.plan,
            request.plan_path,
            request.output,
            request.config,
            image_format=request.image_format,
            rar_tool=request.rar_tool,
            force=request.force,
            progress=progress,
            should_cancel=should_cancel,
        )


# -- extracting --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExtractRequest:
    """Everything one extract needs, settled before the thread starts."""

    source: Path
    """A directory of pages, one image, or a chapter file to unpack first."""

    plan_path: Path
    config: ExtractConfig
    case: TextCase = TextCase.UPPER
    source_language: str = DEFAULT_SOURCE_LANGUAGE
    target_language: str = DEFAULT_TARGET_LANGUAGE
    font: str | None = None
    """``None`` walks the fallback chain, exactly as ``extract`` with no
    ``--font`` does. The header font is editable in the window afterwards."""

    force: bool = False
    """Overwrite an existing plan file, discarding everything in it."""

    unrar_tool: str = ""
    """Where ``unrar`` is, for reading a .cbr. Empty looks on ``PATH``, which
    an application opened from the Finder barely has — hence the preference.

    Named for the tool rather than the format because the other direction
    needs a different binary: ``unrar`` cannot write an archive whatever it
    is asked to, so :class:`RenderRequest` carries ``rar_tool`` and this
    carries ``unrar_tool``, and neither will do the other's job."""

    pages: tuple[Path, ...] = field(default_factory=tuple)
    """The images the run will read, as the dialog counted them. Carried so
    the panel can show a total before the first page is opened; the run
    itself collects its own inputs."""

    @property
    def total(self) -> int:
        return len(self.pages)


class ExtractJob(RunJob):
    """``extract`` on a worker thread, then the plan file it produced.

    The recogniser and the font are resolved here rather than in the dialog,
    on this thread rather than the window's. Both can fail — no OCR backend,
    no resolvable font — and failing here means the window hears about it the
    same way it hears about any other reason a run could not happen.

    **A cancelled run writes nothing.** A render stopped part-way leaves
    whole pages, each one exactly what a complete run would have written for
    it. A plan file has no such partial form: it names the images it covers,
    so half of one is a file that claims a chapter it never read. Pages
    unpacked from a chapter file before it stopped are the exception that
    proves it: they are copies of what is in the chapter, not a claim about
    anything, and running it again continues from them.

    **A chapter file is unpacked here**, after the font and the recogniser
    and before anything else, which is where the command line does it too:
    unpacking is the first thing this writes, and a run that was going to
    fail for want of a font should fail before it has left a folder behind.
    """

    unpacked = Signal(int)
    """How many pages came out of a chapter file, once they all have.

    The dialog could not count them without reading the chapter on every
    keystroke, so a run over one starts with no total — this is the panel's
    cue to say what it is now reading.
    """

    def __init__(self, request: ExtractRequest, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.request = request

    def work(self, progress: ProgressCallback, should_cancel: CancelCheck) -> ExtractReport:
        request = self.request
        face = resolve(request.font)
        recognizer = get_recognizer(request.config.ocr)
        log.info("OCR backend: %s", recognizer.name)

        source = request.source
        comic_info = None
        if is_container(source):
            chapter = unpack(
                source,
                unrar_tool=request.unrar_tool,
                progress=progress,
                should_cancel=should_cancel,
            )
            if chapter.cancelled:
                return ExtractReport(cancelled=True)
            source = chapter.directory
            comic_info = chapter.comic_info
            self.unpacked.emit(len(chapter.pages))

        plan, report = extract(
            source,
            request.plan_path,
            recognizer,
            face.family,
            request.config,
            case=request.case,
            source_language=request.source_language,
            target_language=request.target_language,
            comic_info=comic_info,
            progress=progress,
            should_cancel=should_cancel,
        )
        if not report.cancelled:
            write_plan(plan, request.plan_path, force=request.force)
        return report


# -- reading one region ------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegionTextRequest:
    """One region to read, settled before the thread starts.

    It carries the page the window already has open rather than a path to
    load again: this is a per-region action, and 230MB and a third of a
    second to decode the same eleven-megapixel page for every balloon is the
    cost this milestone exists to avoid on the recognition side. A
    ``PageImage`` is frozen and nothing reads one destructively, so handing
    it to a thread is the same promise ``RenderRequest`` makes with a
    ``Plan``.

    The region is named as well as outlined because the answer has to find
    its way back to it: the window may be looking at something else by the
    time a recogniser is done.
    """

    page: PageImage
    polygon: Polygon
    config: ExtractConfig
    region_id: str
    image: str


class RegionTextJob(RunJob):
    """``read_region`` on a worker thread. Completes with the text it read.

    One unit of work, no page loop, and nothing to cancel: a recogniser is
    handed a crop and either comes back or does not. What makes it a job
    rather than a call is that it is seconds, on a window that has to stay
    live — and that a recogniser can be missing, which is a failure to
    report rather than an exception on the UI thread.
    """

    def __init__(self, request: RegionTextRequest, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.request = request

    def work(
        self,
        progress: ProgressCallback,  # noqa: ARG002 - nothing to report from one crop
        should_cancel: CancelCheck,  # noqa: ARG002 - and nothing to stop half way
    ) -> str:
        request = self.request
        recognizer = get_recognizer(request.config.ocr)
        log.info("OCR backend: %s", recognizer.name)
        return read_region(request.page, request.polygon, recognizer, request.config)


# -- previewing --------------------------------------------------------


class PreviewJob(RunJob):
    """``render_preview`` on a worker thread. Completes with a ``Preview``.

    **It stops inside the page, which the other two jobs do not.** They stop
    between pages, because a chapter has more coming; a preview is one page,
    so stopping at all means stopping part-way through one. That is safe here
    and nowhere else: a half-rendered preview is discarded rather than shown,
    where a half-written page would be a file somebody keeps. ``apply`` passes
    neither hook to ``render_page`` and so cannot reach either behaviour.

    ``progressed`` carries regions rather than pages — ``(done, total,
    image)`` counting regions erased. Erasing is 80% of a preview's time,
    measured, so a bar following it follows the wait.

    Completes with ``None`` rather than a ``Preview`` when it was stopped.
    Not ``failed``: nothing failed.
    """

    def __init__(self, request: PreviewRequest, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.request = request

    def work(self, progress: ProgressCallback, should_cancel: CancelCheck) -> Preview | None:
        request = self.request

        def on_region(done: int, total: int) -> None:
            progress(PageProgress(index=done, total=total, image=request.image))

        try:
            return render_preview(
                request.plan,
                request.plan_path,
                request.image,
                on_region=on_region,
                should_cancel=should_cancel,
            )
        except RenderCancelled:
            log.debug("preview of %s cancelled", request.image)
            return None


__all__ = [
    "ExtractJob",
    "ExtractRequest",
    "PreviewJob",
    "PreviewRequest",
    "RegionTextJob",
    "RegionTextRequest",
    "RenderJob",
    "RenderRequest",
    "RunJob",
]
