"""Fitting a translation into a region's polygon.

Balloons are round, so line widths are measured against the polygon itself,
not its bounding box: for each line's vertical band the usable width is the
widest horizontal run that is inside the polygon on *every* row of that band.
Fitting to the bounding box instead is what pushes text into the corners of a
balloon and out through the curve.

The fit strategy is fixed and ordered:

1. shrink the font down to the minimum readable size,
2. then condense horizontally, never past the floor,
3. then fail and name the region.

Nothing overflows silently, and every region that needed condensing is
reported so it can be hand-tuned in the plan file.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol, cast

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import ImageFont

from .config import TypesetConfig
from .fonts import FontFace
from .markup import Token
from .model import Polygon, polygon_bounds

log = logging.getLogger(__name__)

MaskArray = NDArray[np.uint8]

HYPHEN = "-"


class Hyphenator(Protocol):
    """The slice of pyphen's API this module uses."""

    def iterate(self, word: str) -> Iterator[tuple[str, str]]:
        """Yield (head, tail) splits, longest head first."""
        ...


@dataclass(frozen=True, slots=True)
class PlacedRun:
    """A stretch of one weight, offset from the start of its line."""

    text: str
    bold: bool
    x: int


@dataclass(frozen=True, slots=True)
class PlacedLine:
    """One laid-out line, measured at natural (un-condensed) width."""

    runs: tuple[PlacedRun, ...]
    width: int
    top: int
    band_left: int
    band_right: int

    @property
    def band_width(self) -> int:
        return self.band_right - self.band_left


@dataclass(frozen=True, slots=True)
class Layout:
    """A successful fit."""

    lines: tuple[PlacedLine, ...]
    font_size: int
    condense: float
    line_height: int

    @property
    def condensed(self) -> bool:
        return self.condense < 1.0


@dataclass(frozen=True, slots=True)
class FitFailure:
    """Text that will not fit even at the minimum size and maximum condensing."""

    reason: str


def interior_mask(polygon: Polygon, height: int, width: int, cfg: TypesetConfig) -> MaskArray:
    """The polygon, inset so text does not sit against the balloon outline."""
    mask: MaskArray = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [np.array(polygon, dtype=np.int32)], 255)
    bounds = polygon_bounds(polygon)
    padding = round(min(bounds.width, bounds.height) * cfg.padding_ratio)
    if padding >= 1:
        size = max(3, (padding * 2 + 1) | 1)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        eroded = cast("MaskArray", cv2.erode(mask, kernel))
        if int(np.count_nonzero(eroded)) > 0:
            return eroded
    return mask


def band_span(mask: MaskArray, top: int, bottom: int) -> tuple[int, int] | None:
    """Widest horizontal run inside the mask across every row of a band.

    Requiring the run on every row is what keeps a line of text inside a
    curve: near the top of an ellipse the usable width is the narrow chord,
    not the widest one further down.
    """
    height = mask.shape[0]
    top, bottom = max(0, top), min(height, bottom)
    if bottom <= top:
        return None
    columns = mask[top:bottom].min(axis=0) > 0
    if not columns.any():
        return None

    best_start = best_length = 0
    start = -1
    for index, inside in enumerate(columns):
        if inside:
            if start < 0:
                start = index
        elif start >= 0:
            if index - start > best_length:
                best_start, best_length = start, index - start
            start = -1
    if start >= 0 and len(columns) - start > best_length:
        best_start, best_length = start, len(columns) - start
    if best_length <= 0:
        return None
    return best_start, best_start + best_length


class _Measurer:
    """Caches the two loaded faces and word widths for one font size."""

    def __init__(self, face: FontFace, size: int) -> None:
        self.regular = face.regular.load(size)
        # A face with no real bold never reaches here: fonts.resolve refuses
        # it rather than letting anything synthesise one.
        self.bold = face.bold.load(size) if face.bold is not None else self.regular
        self.space = self.regular.getlength(" ")
        self._widths: dict[tuple[str, bool], float] = {}

    def font(self, bold: bool) -> ImageFont.FreeTypeFont:
        return self.bold if bold else self.regular

    def width(self, text: str, bold: bool) -> float:
        key = (text, bold)
        cached = self._widths.get(key)
        if cached is None:
            cached = float(self.font(bold).getlength(text))
            self._widths[key] = cached
        return cached

    def token_width(self, token: Token) -> float:
        """A word's width, summed over its segments.

        Measuring segment by segment rather than as one string ignores
        kerning across a weight change, which is worth a fraction of a pixel
        and keeps placement and measurement in agreement.
        """
        return sum(self.width(segment.text, segment.bold) for segment in token.segments)


def _split_token(
    token: Token, measurer: _Measurer, budget: float, hyphenator: Hyphenator | None
) -> tuple[str, str] | None:
    """Break a too-long word, longest head first, keeping room for the hyphen.

    Only words of a single weight are hyphenated. Splitting across a markup
    boundary would have to divide the segments too, and a word that is part
    bold is short enough in practice that shrinking the font is the better
    answer than a wrong-weight hyphen.
    """
    if hyphenator is None or not token.uniform_weight:
        return None
    for head, tail in hyphenator.iterate(token.text):
        if measurer.width(head + HYPHEN, token.bold) <= budget:
            return head + HYPHEN, tail
    return None


def _wrap(
    tokens: tuple[Token, ...],
    spans: list[tuple[int, int] | None],
    measurer: _Measurer,
    condense: float,
    hyphenator: Hyphenator | None,
) -> list[tuple[list[PlacedRun], float, tuple[int, int]]] | None:
    """Greedy wrap into fixed bands. None when the text does not fit them."""
    pending = list(tokens)
    lines: list[tuple[list[PlacedRun], float, tuple[int, int]]] = []

    for span in spans:
        if not pending:
            break
        if span is None:
            return None
        left, right = span
        # Budget is natural width: condensing shrinks the rendered line, so it
        # buys proportionally more room at the same font size.
        budget = (right - left) / condense

        runs: list[PlacedRun] = []
        cursor = 0.0
        while pending:
            token = pending[0]
            lead = measurer.space if runs else 0.0
            width = measurer.token_width(token)
            if cursor + lead + width <= budget:
                offset = cursor + lead
                for segment in token.segments:
                    runs.append(PlacedRun(segment.text, segment.bold, round(offset)))
                    offset += measurer.width(segment.text, segment.bold)
                cursor += lead + width
                pending.pop(0)
                continue
            if runs:
                break  # try the next line
            split = _split_token(token, measurer, budget, hyphenator)
            if split is None:
                return None  # a single word will not fit this band at all
            head, tail = split
            runs.append(PlacedRun(head, token.bold, 0))
            cursor = measurer.width(head, token.bold)
            pending[0] = token.replaced(tail)
            break

        if not runs:
            return None
        lines.append((runs, cursor, span))

    return None if pending else lines


def _attempt(
    tokens: tuple[Token, ...],
    mask: MaskArray,
    size: int,
    condense: float,
    face: FontFace,
    cfg: TypesetConfig,
    hyphenator: Hyphenator | None,
) -> Layout | None:
    """Try one font size and condense factor."""
    rows = np.nonzero(mask.max(axis=1) > 0)[0]
    if rows.size == 0:
        return None
    top_limit, bottom_limit = int(rows[0]), int(rows[-1]) + 1
    available = bottom_limit - top_limit

    measurer = _Measurer(face, size)
    line_height = max(1, round(size * cfg.line_spacing))

    for count in range(1, available // line_height + 1):
        block = count * line_height
        block_top = top_limit + (available - block) // 2
        spans = [
            band_span(mask, block_top + index * line_height, block_top + (index + 1) * line_height)
            for index in range(count)
        ]
        wrapped = _wrap(tokens, spans, measurer, condense, hyphenator)
        if wrapped is None:
            continue
        lines = tuple(
            PlacedLine(
                runs=tuple(runs),
                width=round(width),
                top=block_top + index * line_height,
                band_left=span[0],
                band_right=span[1],
            )
            for index, (runs, width, span) in enumerate(wrapped)
        )
        return Layout(lines=lines, font_size=size, condense=condense, line_height=line_height)
    return None


def _hyphenator(cfg: TypesetConfig) -> Hyphenator | None:
    if not cfg.hyphenate:
        return None
    try:
        import pyphen

        return cast("Hyphenator", pyphen.Pyphen(lang=cfg.hyphenation_language))
    except (ImportError, OSError, KeyError) as exc:  # pragma: no cover - dictionary missing
        log.warning("hyphenation unavailable for %s: %s", cfg.hyphenation_language, exc)
        return None


def layout_text(
    tokens: tuple[Token, ...],
    polygon: Polygon,
    face: FontFace,
    cfg: TypesetConfig,
    *,
    page_width: int,
    page_height: int,
    fixed_size: int | None = None,
) -> Layout | FitFailure:
    """Fit ``tokens`` into ``polygon``, or explain why they will not go.

    ``fixed_size`` comes from a region's ``font_size`` override: that size is
    used as given rather than searched, because overriding it means asking for
    it. Condensing still applies, so the text cannot overflow either way.
    """
    if not tokens:
        return FitFailure("no text to place")

    mask = interior_mask(polygon, page_height, page_width, cfg)
    if int(np.count_nonzero(mask)) == 0:
        return FitFailure("polygon encloses no pixels")

    hyphenator = _hyphenator(cfg)
    minimum = max(1, round(cfg.font_size_min_ratio * page_height))

    if fixed_size is not None:
        smallest = fixed_size
        attempt = _attempt(tokens, mask, fixed_size, 1.0, face, cfg, hyphenator)
        if attempt is not None:
            return attempt
    else:
        bounds = polygon_bounds(polygon)
        largest = max(minimum, round(bounds.height * cfg.max_size_ratio))
        best = _search(tokens, mask, minimum, largest, face, cfg, hyphenator)
        if best is not None:
            return best
        smallest = minimum

    # Only now, at the smallest size allowed, start condensing.
    condense = 1.0 - cfg.condense_step
    while condense >= cfg.condense_min - 1e-9:
        attempt = _attempt(tokens, mask, smallest, round(condense, 4), face, cfg, hyphenator)
        if attempt is not None:
            return attempt
        condense -= cfg.condense_step

    return FitFailure(
        f"will not fit at {smallest}px even condensed to "
        f"{cfg.condense_min:.0%}; shorten the translation, enlarge the polygon, "
        f"or lower font_size_min_ratio"
    )


def _search(
    tokens: tuple[Token, ...],
    mask: MaskArray,
    minimum: int,
    largest: int,
    face: FontFace,
    cfg: TypesetConfig,
    hyphenator: Hyphenator | None,
) -> Layout | None:
    """Largest size that fits, by binary search.

    Fit is treated as monotonic in size: if a size fits, every smaller one
    does. Line breaking makes that not quite true at the margins, so the
    result is the largest size the search proved, not necessarily the global
    maximum — a pixel of conservatism, never an overflow.
    """
    best: Layout | None = None
    low, high = minimum, largest
    while low <= high:
        middle = (low + high) // 2
        attempt = _attempt(tokens, mask, middle, 1.0, face, cfg, hyphenator)
        if attempt is not None:
            best = attempt
            low = middle + 1
        else:
            high = middle - 1
    return best
