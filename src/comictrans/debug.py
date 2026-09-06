"""Debug overlays for the extract pass.

Detection is the part of this pipeline most likely to be wrong on a real scan,
and the failure is visual: a contour that escaped into the artwork looks fine
in a log line and obvious in an image. ``--debug-dir`` dumps what was found.

Overlays are debug artefacts, never output pages, and they always go in a
directory you name — never next to the source images.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .config import DetectConfig
from .detect import DetectedRegion
from .detect.contour import GrayArray, binarise
from .imaging import PageImage
from .model import Geometry

log = logging.getLogger(__name__)

EXACT_COLOR = (0, 200, 0)
APPROXIMATE_COLOR = (255, 140, 0)
BOX_COLOR = (0, 120, 255)


def _outline_width(page: PageImage) -> int:
    return max(2, page.height // 500)


def draw_regions(page: PageImage, regions: Sequence[DetectedRegion]) -> Image.Image:
    """Source page with polygons, OCR boxes, and reading-order labels drawn on."""
    canvas = Image.fromarray(page.rgb).convert("RGB")
    draw = ImageDraw.Draw(canvas)
    width = _outline_width(page)

    for index, region in enumerate(regions, start=1):
        color = EXACT_COLOR if region.geometry is Geometry.EXACT else APPROXIMATE_COLOR
        draw.polygon([tuple(point) for point in region.polygon], outline=color, width=width)
        for line in region.lines:
            box = line.box
            draw.rectangle(
                [box.left, box.top, box.right, box.bottom],
                outline=BOX_COLOR,
                width=max(1, width // 2),
            )
        bounds = region.bounds
        label = f"{index}{'' if region.geometry is Geometry.EXACT else '~'}"
        draw.text((bounds.left + 4, max(0, bounds.top - 16)), label, fill=color)
    return canvas


def draw_masks(page: PageImage, cfg: DetectConfig) -> Image.Image:
    """The two threshold polarities contour search actually ran on, side by side."""
    gray = cast("GrayArray", cv2.cvtColor(page.rgb, cv2.COLOR_RGB2GRAY))
    normal = binarise(gray, cfg, inverted=False)
    inverted = binarise(gray, cfg, inverted=True)
    combined = np.concatenate([normal, inverted], axis=1)
    return Image.fromarray(combined)


def dump(
    page: PageImage, regions: Sequence[DetectedRegion], cfg: DetectConfig, debug_dir: Path
) -> None:
    """Write the overlay and mask dumps for one page."""
    debug_dir.mkdir(parents=True, exist_ok=True)
    stem = page.path.stem
    regions_path = debug_dir / f"{stem}-regions.png"
    masks_path = debug_dir / f"{stem}-masks.png"
    draw_regions(page, regions).save(regions_path)
    draw_masks(page, cfg).save(masks_path)
    log.info("debug: wrote %s and %s", regions_path.name, masks_path.name)
