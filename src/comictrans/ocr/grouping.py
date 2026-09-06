"""Turning loose OCR lines into one utterance per region.

Line breaks from the source are *preserved* in the plan file. They are OCR
truth, they show you the shape of the original balloon while you translate,
and the apply pass reflows freely anyway — so nothing downstream depends on
them. De-hyphenating or re-wrapping here would throw away information for no
gain.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from statistics import median

from .base import OcrLine


def median_line_height(lines: Iterable[OcrLine]) -> float:
    """Median box height, or 0.0 for an empty run."""
    heights = [line.box.height for line in lines if line.box.height > 0]
    return float(median(heights)) if heights else 0.0


def sort_lines(lines: Sequence[OcrLine], band: float | None = None) -> list[OcrLine]:
    """Reading order within one region: top to bottom, then left to right.

    ``band`` is the vertical tolerance within which two lines count as the same
    row; it defaults to half a line height. Without it, two lines that start
    within a pixel of each other sort on scan noise.
    """
    if not lines:
        return []
    tolerance = band if band is not None else max(1.0, median_line_height(lines) * 0.5)
    return sorted(lines, key=lambda line: (round(line.box.top / tolerance), line.box.left))


def utterance_text(lines: Sequence[OcrLine]) -> str:
    """Join a region's lines into one utterance, keeping the original breaks."""
    return "\n".join(line.text.strip() for line in sort_lines(lines) if line.text.strip())


def utterance_confidence(lines: Sequence[OcrLine]) -> float:
    """Confidence for a whole region: the *worst* of its lines.

    Deliberately conservative. The flag exists to tell you where to look, and
    one badly-read line is enough of a reason to look.
    """
    if not lines:
        return 0.0
    return min(line.confidence for line in lines)
