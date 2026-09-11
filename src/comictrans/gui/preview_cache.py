"""The last rendered page, kept so that looking at it twice costs one render.

Toggling between the overlay and the rendered page is the common gesture, and
until this existed it cost a full render each way: measured, three toggles of
an eleven-megapixel page with nothing edited between them ran three renders
and thirty-four seconds. None of that work was new.

**One entry, and the reason is measured too.** A retained preview holds its
image — 33MB for a page that size — so a cache of every page in a chapter is
a standing cost in hundreds of megabytes, under a render whose transient peak
is already 540MB. What one entry buys is the gesture that repeats; what more
would buy is returning to a page previewed earlier and untouched since, which
is rarer by a long way.

**What the key is, and why not the plan.** A request carries the whole
``Plan``, so keying on it would miss the moment anything on any other page
changed — which during a review is most edits. A page's render reads its own
regions, the header the styles come from, and the file on disk. That is the
key, and nothing else is in it.

The failure this must not have is the quiet one: showing an old render as
though it were current. Everything else in the preview path fails loudly. So
the key is exact and dull — no heuristics, no "probably unchanged", no
expiry — and anything it cannot see is handled by throwing the cache away
rather than by guessing. Fonts are the case in point: ``resolve_styles``
reads the filesystem, so installing a font changes what a plan renders as
without changing the plan. Rescan Fonts drops the cache for that reason.

No Qt, like ``document`` and ``preview``, so what counts as the same page is
tested without a display.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path

from ..model import PlanHeader, PlanImage, Region
from .preview import Preview, PreviewRequest, source_path


@dataclass(frozen=True, slots=True)
class PreviewKey:
    """Everything one page's render depends on, and nothing else.

    Frozen and hashable all the way down — ``PlanHeader``, ``Region`` and
    ``PlanImage`` are frozen dataclasses of scalars and tuples — so equality
    is what it looks like: the same values mean the same page.
    """

    header: PlanHeader
    """Where the styles and the typesetting limits come from."""

    regions: tuple[Region, ...]
    """This page's regions only. A region on another page cannot change what
    this one renders as, and a key that said otherwise would miss on nearly
    every edit somebody makes."""

    recorded: PlanImage | None
    """What the plan says this page is, hash and all. ``None`` for an image
    the plan does not list, which is a plan the window would not have opened."""

    stamp: tuple[int, int] | None
    """``(mtime_ns, size)`` of the file on disk, or ``None`` if it could not
    be read. The plan's hash is what the page was when it was opened; this is
    what it is now. A page replaced while the window is open is a different
    page, and re-reading it is what every uncached render already did.

    One ``stat`` per lookup, immediately before a call that would otherwise
    read the whole file — not the per-entry cost that kept a stat out of the
    recent-files menu."""

    @classmethod
    def of(cls, request: PreviewRequest) -> PreviewKey:
        stamp: tuple[int, int] | None = None
        with contextlib.suppress(OSError):
            info = Path(source_path(request.plan_path, request.image)).stat()
            stamp = (info.st_mtime_ns, info.st_size)
        recorded = next(
            (entry for entry in request.plan.images if entry.name == request.image), None
        )
        return cls(
            header=request.plan.header,
            regions=request.plan.regions_for(request.image),
            recorded=recorded,
            stamp=stamp,
        )


class PreviewCache:
    """The last rendered page, and what it was rendered from."""

    def __init__(self) -> None:
        self._key: PreviewKey | None = None
        self._preview: Preview | None = None

    def get(self, request: PreviewRequest) -> Preview | None:
        """The render for this request, if the held one is still it."""
        if self._key is not None and self._key == PreviewKey.of(request):
            return self._preview
        return None

    def put(self, request: PreviewRequest, preview: Preview) -> None:
        """Hold this render, dropping whichever one was held before.

        The key is taken again rather than passed in from the lookup: a
        render takes seconds, and the answer being stored describes the file
        as it is now, not as it was when somebody asked.
        """
        self._key = PreviewKey.of(request)
        self._preview = preview

    def clear(self) -> None:
        """Let go, for anything the key cannot see. See the module docstring."""
        self._key = None
        self._preview = None

    @property
    def holding(self) -> bool:
        """Whether there is anything to hit."""
        return self._preview is not None


__all__ = ["PreviewCache", "PreviewKey"]
