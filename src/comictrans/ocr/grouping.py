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


MIN_WORD_LETTERS = 2
"""Letters needed for a token to count as a word.

Two, not more: a balloon holding one short exclamation is ordinary comic
lettering, and demanding a sentence would flag it. This only has to tell text
from things that are not text at all.
"""


def looks_like_text(text: str) -> bool:
    """True when this reads as language rather than as an artefact.

    OCR finds "text" in artwork — a window frame, an eye, the dots of a
    halftone screen — and returns things like ``(6``, ``o ©`` or ``<= 4``.
    Those regions are worth keeping in the plan file so they can be checked,
    but they should not be lettered back onto the page by default.

    The test is deliberately weak: one run of letters is enough. Anything
    stricter starts refusing real one-word balloons.
    """
    return any(
        sum(character.isalpha() for character in token) >= MIN_WORD_LETTERS
        for token in text.split()
    )


def utterance_confidence(lines: Sequence[OcrLine]) -> float:
    """Confidence for a whole region: the *worst* of its lines.

    Deliberately conservative. The flag exists to tell you where to look, and
    one badly-read line is enough of a reason to look.
    """
    if not lines:
        return 0.0
    return min(line.confidence for line in lines)
