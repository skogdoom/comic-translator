"""How a long pass says where it has got to, and asks whether to stop.

Both pipeline passes loop over pages internally, which is what keeps "read
every page of a chapter" and "render every page of a plan" each a single
implementation the command line and the review window share. A caller that
wants to show progress therefore cannot write its own loop; it hands these
two callbacks in instead.

They live here rather than in either pass because both take them and neither
should have to import the other for a dataclass. Nothing else of ours is
imported, so this sits beside ``model`` at the bottom.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PageProgress:
    """Where a run has got to, handed over just before a page is worked on.

    Reported before rather than after, so a caller showing it has the name of
    the page currently being worked on rather than the last one that finished
    — which is the one a slow page makes you want to know.
    """

    index: int
    """Position in the run, counting from zero."""
    total: int
    image: str


ProgressCallback = Callable[[PageProgress], None]
CancelCheck = Callable[[], bool]
"""Asked between pages whether to stop. What a stopped run leaves behind is
each pass's own business: ``apply`` writes a page at a time and keeps the
whole ones, ``extract`` writes one file describing the whole chapter and
writes nothing at all."""


__all__ = ["CancelCheck", "PageProgress", "ProgressCallback"]
