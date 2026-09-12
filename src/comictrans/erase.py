"""Removing the original lettering.

Everything here works from the plan file alone: the polygon, ``text_color``
and ``fill_color`` were all measured at extract time, so erase re-runs neither
detection nor OCR and gives the same result every time.

Glyph pixels are found by colour distance to ``text_color``, judged relative
to how far that sits from ``fill_color``. That ratio is scale-free, so a
white-on-black caption masks exactly as well as black-on-white.

**Everything here works inside a window around the region, not on the page.**
A balloon is a few per cent of a comic page, and the colour distance that
finds its lettering used to be computed for every pixel of the page and then
thrown away everywhere but the balloon. Measured on an eleven-megapixel page,
one call: ``rgb.astype(float32)`` 132MB, the subtraction another 132MB live
at the same time, and the norm over the colour axis peaking 220MB above
that — twice, because the ground mask distances the page a second time. 386MB
to erase a region covering 3.4% of the page.

The window is the polygon's bounding box grown by :func:`reach`, which is how
far outside itself the mask's own operations read. Everything inside the
polygon therefore comes out bit for bit as it did when the whole page was
computed, and a test holds that by erasing the same page twice — once with
the real window and once with one stretched to the whole page — and comparing
pixels.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Protocol, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from .config import EraseConfig
from .imaging import RgbArray
from .model import Color, Region

log = logging.getLogger(__name__)

MaskArray = NDArray[np.uint8]


class FillStrategy(Protocol):
    """How masked pixels get repainted.

    The hook the spec asks for: a cheap flat fill covers plain balloons, and
    anything heavier for textured art or borderless captions plugs in here
    without touching the masking or the caller.
    """

    name: str

    def fill(
        self,
        rgb: RgbArray,
        mask: MaskArray,
        region: Region,
        cfg: EraseConfig,
        *,
        page_height: int,
    ) -> RgbArray:
        """``rgb`` is the window around the region, not the page, and
        ``region`` carries a polygon already in the window's coordinates.

        ``page_height`` is the *page's*, because every ratio in
        :class:`EraseConfig` is a fraction of it — a radius worked out from
        the window's own height would change with the size of the balloon.
        """
        ...


def dilate_size(cfg: EraseConfig, page_height: int) -> int:
    """The kernel the ink mask is grown by, or 0 when it is not grown."""
    grow = round(cfg.dilate_ratio * page_height)
    return max(3, (grow * 2 + 1) | 1) if grow >= 1 else 0


def close_size(cfg: EraseConfig, page_height: int) -> int:
    """The kernel the ground mask is closed with."""
    return max(3, round(cfg.ground_close_ratio * page_height) | 1)


def inpaint_radius(cfg: EraseConfig, page_height: int) -> int:
    """How far :class:`InpaintFill` reads from around what it repaints."""
    return max(1, round(cfg.inpaint_radius_ratio * page_height))


def reach(cfg: EraseConfig, page_height: int) -> int:
    """How far outside the polygon an erase reads, in pixels.

    The sum of every operation that looks at a neighbour, not the largest of
    them: they are applied one after another, so their reaches add. A closing
    counts double — it dilates and then erodes, and each pass gathers from a
    kernel radius away.

    Read off the same functions the operations use, so a ratio changed in
    :class:`EraseConfig` moves the window with it. It is only ever a few tens
    of pixels against a balloon a few hundred across; there is nothing to be
    won by shaving it, and being wrong about it would change what a page
    renders as.
    """
    return (
        dilate_size(cfg, page_height) // 2
        + close_size(cfg, page_height)
        + inpaint_radius(cfg, page_height)
    )


@dataclass(frozen=True, slots=True)
class Window:
    """The rectangle of a page one region's erase looks at. Half-open."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    def of(self, array: NDArray[np.uint8]) -> NDArray[np.uint8]:
        """The part of ``array`` this covers — a view, so nothing is copied."""
        return array[self.top : self.bottom, self.left : self.right]

    def moved(self, region: Region) -> Region:
        """The same region with its polygon in this window's coordinates."""
        return replace(
            region,
            polygon=tuple((x - self.left, y - self.top) for x, y in region.polygon),
        )


def window_for(
    region: Region, cfg: EraseConfig, shape: tuple[int, ...], page_height: int
) -> Window:
    """The polygon's bounding box, grown by :func:`reach` and clipped to the page."""
    grown = reach(cfg, page_height)
    xs = [x for x, _ in region.polygon]
    ys = [y for _, y in region.polygon]
    height, width = shape[0], shape[1]
    return Window(
        left=max(0, min(xs) - grown),
        top=max(0, min(ys) - grown),
        right=min(width, max(xs) + 1 + grown),
        bottom=min(height, max(ys) + 1 + grown),
    )


def polygon_mask(region: Region, height: int, width: int) -> MaskArray:
    mask: MaskArray = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [np.array(region.polygon, dtype=np.int32)], 255)
    return mask


def _distance(rgb: RgbArray, color: Color) -> NDArray[np.float32]:
    target = np.array(color.as_tuple(), dtype=np.float32)
    return cast(
        "NDArray[np.float32]",
        np.linalg.norm(rgb.astype(np.float32) - target, axis=2),
    )


def _ground_mask(
    rgb: RgbArray, region: Region, separation: float, cfg: EraseConfig, *, page_height: int
) -> MaskArray:
    """Where the region's own flat background is, with the lettering filled in.

    A pixel belongs to the ground when it is nearer ``fill_color`` than
    ``glyph_threshold_ratio`` of the way to ``text_color`` — the same
    scale-free test the ink mask uses, read from the other end. Lettering
    leaves holes, and closing by more than a stroke width fills them back in,
    so what comes out is the flat area *including* the text sitting on it.

    What it excludes is the balloon's own ink outline, which is the point. The
    polygon can reach past that outline: a polygon is grown until it covers
    every line of text assigned to it, and it grows by union with the lines'
    bounding boxes, whose corners stick out beyond the glyphs they were added
    for. Where such a corner crosses the outline, a mask bounded only by the
    polygon treats the outline as lettering and the flat fill repaints it,
    cutting a notch out of the balloon.
    """
    near_fill = _distance(rgb, region.fill_color) < separation * cfg.glyph_threshold_ratio
    ground = near_fill.astype(np.uint8) * 255
    size = close_size(cfg, page_height)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    return cast("MaskArray", cv2.morphologyEx(ground, cv2.MORPH_CLOSE, kernel))


def glyph_mask(rgb: RgbArray, region: Region, cfg: EraseConfig, *, page_height: int) -> MaskArray:
    """Mask of the original lettering inside a region.

    Bounded by two things, not one: the region's polygon, and the flat ground
    the lettering sits on. The polygon alone is not enough — see
    :func:`_ground_mask`.

    Dilated by ``dilate_ratio`` to take the antialiased fringe with it — a
    pure colour test leaves a halo of half-ink pixels that reads as a ghost of
    the old lettering once the new text is drawn over it.
    """
    height, width = rgb.shape[0], rgb.shape[1]
    inside = polygon_mask(region, height, width)

    separation = float(
        np.linalg.norm(
            np.array(region.text_color.as_tuple(), dtype=np.float32)
            - np.array(region.fill_color.as_tuple(), dtype=np.float32)
        )
    )
    if separation <= 0.0:
        # Extract guarantees the two colours differ; belt and braces.
        return np.zeros((height, width), dtype=np.uint8)

    ink = _distance(rgb, region.text_color) < separation * cfg.glyph_threshold_ratio
    mask = cv2.bitwise_and((ink.astype(np.uint8) * 255), inside)

    size = dilate_size(cfg, page_height)
    if size:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        mask = cv2.dilate(mask, kernel)
    mask = cv2.bitwise_and(mask, inside)

    # A floor, not a judgement call. Closing the lettering back into the
    # ground needs a kernel wider than a glyph's stroke; if it is not, the
    # lettering stays a hole, the ground excludes all of it, and the region
    # would come back unerased with the translation drawn over the top. That
    # is a worse failure than the notched outline this guards against, so a
    # result this empty means the ground could not be read and the mask
    # stands unconstrained.
    #
    # Measured over the thirteen fixtures the constraint keeps 95% of a
    # region's mask, and half of it where a polygon steps out over the
    # outline. A ground that failed to close keeps none at all.
    ground = _ground_mask(rgb, region, separation, cfg, page_height=page_height)
    constrained = cast("MaskArray", cv2.bitwise_and(mask, ground))
    if int(np.count_nonzero(constrained)) < int(np.count_nonzero(mask)) // 20:
        log.debug("%s: ground could not be read; erasing without it", region.id)
        return cast("MaskArray", mask)
    return constrained


class FlatFill:
    """Repaint masked pixels with the region's recorded fill colour."""

    name = "flat"

    def fill(
        self,
        rgb: RgbArray,
        mask: MaskArray,
        region: Region,
        cfg: EraseConfig,
        *,
        page_height: int,
    ) -> RgbArray:
        out = rgb.copy()
        out[mask > 0] = np.array(region.fill_color.as_tuple(), dtype=np.uint8)
        return out


class PolygonFill:
    """Flood the whole polygon interior with the fill colour.

    Guaranteed to leave no trace of the old lettering, and the right choice
    for a plain balloon. It also destroys anything else inside the polygon, so
    it is wrong for a region flagged ``geometry: approximate``.
    """

    name = "polygon"

    def fill(
        self,
        rgb: RgbArray,
        mask: MaskArray,
        region: Region,
        cfg: EraseConfig,
        *,
        page_height: int,
    ) -> RgbArray:
        out = rgb.copy()
        interior = polygon_mask(region, rgb.shape[0], rgb.shape[1])
        out[interior > 0] = np.array(region.fill_color.as_tuple(), dtype=np.uint8)
        return out


class NoFill:
    """Paint nothing at all: the translation goes straight onto the page.

    For a region whose background is the artwork and has to stay that way — a
    sound effect, a caption lettered over a panel. There is nothing to
    reconstruct and nothing to flatten, so ``fill_color`` goes unused.
    """

    name = "none"

    def fill(
        self,
        rgb: RgbArray,
        mask: MaskArray,
        region: Region,
        cfg: EraseConfig,
        *,
        page_height: int,
    ) -> RgbArray:
        return rgb.copy()


class InpaintFill:
    """Reconstruct masked pixels from their surroundings.

    For textured balloons and borderless captions, where a flat patch of
    ``fill_color`` would read as a hole in the artwork.
    """

    name = "inpaint"

    def fill(
        self,
        rgb: RgbArray,
        mask: MaskArray,
        region: Region,
        cfg: EraseConfig,
        *,
        page_height: int,
    ) -> RgbArray:
        # The page's height, not this array's. They were the same number when
        # a fill was handed the whole page; now that it is handed a window,
        # only one of them keeps the radius a property of the page.
        return cast(
            "RgbArray",
            cv2.inpaint(rgb.copy(), mask, inpaint_radius(cfg, page_height), cv2.INPAINT_TELEA),
        )


STRATEGIES: dict[str, FillStrategy] = {
    strategy.name: strategy for strategy in (FlatFill(), PolygonFill(), InpaintFill(), NoFill())
}

_NEEDS_NO_MASK = frozenset({PolygonFill.name, NoFill.name})
"""Strategies that ignore the glyph mask, so nothing is amiss when it is empty."""


def get_strategy(name: str) -> FillStrategy:
    try:
        return STRATEGIES[name]
    except KeyError:
        raise ValueError(
            f"unknown erase strategy {name!r}; expected one of {', '.join(sorted(STRATEGIES))}"
        ) from None


def erase(rgb: RgbArray, region: Region, cfg: EraseConfig, *, page_height: int) -> RgbArray:
    """Remove a region's original lettering, leaving the rest of the page alone.

    The region's own ``erase`` wins over the run's ``--erase`` flag, the same
    way its ``font`` wins over the header's: which of these is right is a fact
    about one balloon — a flat one, a textured one, a sound effect that must
    keep the art behind it — not about a chapter.

    The page comes back whole and the original is not touched, which is what
    ``render_page`` chains one region to the next on. Only the window is
    computed: the copy is made, the strategy paints the window, and the rest
    of the page is the copy it already was.
    """
    strategy = get_strategy(str(region.erase) if region.erase is not None else cfg.strategy)
    if strategy.name == NoFill.name:
        return rgb.copy()  # no mask to compute; nothing is painted

    window = window_for(region, cfg, rgb.shape, page_height)
    if window.empty:  # a polygon entirely off the page; nothing to paint
        log.debug("region %s: its polygon does not meet the page", region.id)
        return rgb.copy()

    local = window.moved(region)
    inside = window.of(rgb)
    mask = glyph_mask(inside, local, cfg, page_height=page_height)
    if int(np.count_nonzero(mask)) == 0 and strategy.name not in _NEEDS_NO_MASK:
        log.debug("region %s: nothing matched the recorded text colour", region.id)

    out = rgb.copy()
    window.of(out)[:] = strategy.fill(inside, mask, local, cfg, page_height=page_height)
    return out
