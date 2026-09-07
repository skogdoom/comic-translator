"""Apple Vision adapter (``VNRecognizeTextRequest``).

This is the only place in the codebase that knows Vision's coordinate system:
normalised 0..1, origin bottom-left. It converts to page pixels with a
top-left origin before returning, and that convention holds everywhere after.

The whole module is import-guarded so the package remains importable — and the
test suite runnable — on machines without pyobjc.
"""

from __future__ import annotations

import io
import logging
from typing import Any

from PIL import Image

from ..config import OcrConfig
from ..errors import OcrUnavailableError
from ..imaging import PageImage
from ..model import Box
from .base import OcrLine

log = logging.getLogger(__name__)

_IMPORT_ERROR: str | None = None

try:  # pragma: no cover - platform dependent
    import Quartz
    import Vision
    from Foundation import NSData
except ImportError as exc:  # pragma: no cover - platform dependent
    Quartz = None
    Vision = None
    NSData = None
    _IMPORT_ERROR = str(exc)


def available() -> bool:
    """True when Vision can actually be used on this machine."""
    return Vision is not None and Quartz is not None


def unavailable_reason() -> str:
    return _IMPORT_ERROR or ("available" if available() else "pyobjc Vision bindings missing")


def _cg_image(page: PageImage) -> Any:
    """Wrap the decoded page as a CGImage.

    Re-encoding to PNG in memory avoids hand-rolling CGColorSpace and row
    stride plumbing, and keeps this adapter working on any pixel layout Pillow
    can produce. The source file itself is never reopened.
    """
    buffer = io.BytesIO()
    Image.fromarray(page.rgb).save(buffer, format="PNG")
    data = NSData.dataWithBytes_length_(buffer.getvalue(), len(buffer.getvalue()))
    source = Quartz.CGImageSourceCreateWithData(data, None)
    if source is None:
        raise OcrUnavailableError(f"Core Graphics could not decode {page.path}")
    image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
    if image is None:
        raise OcrUnavailableError(f"Core Graphics produced no image for {page.path}")
    return image


def _to_pixel_box(bounding_box: Any, width: int, height: int) -> Box:
    """Vision's normalised bottom-left rect -> integer pixel box, top-left origin."""
    origin = bounding_box.origin
    size = bounding_box.size
    left = origin.x * width
    right = (origin.x + size.width) * width
    # Flip the y axis: Vision measures up from the bottom edge.
    top = (1.0 - (origin.y + size.height)) * height
    bottom = (1.0 - origin.y) * height
    left_px, top_px = round(left), round(top)
    return Box(
        left=max(0, left_px),
        top=max(0, top_px),
        right=min(width, max(round(right), left_px + 1)),
        bottom=min(height, max(round(bottom), top_px + 1)),
    )


class VisionRecognizer:
    """``VNRecognizeTextRequest`` at accurate level, in the source language."""

    name = "apple-vision"

    def recognize(self, page: PageImage, config: OcrConfig) -> list[OcrLine]:
        if not available():
            raise OcrUnavailableError(f"Apple Vision unavailable: {unavailable_reason()}")

        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setRecognitionLanguages_(list(config.languages))
        request.setUsesLanguageCorrection_(True)
        request.setMinimumTextHeight_(config.minimum_text_height_ratio)

        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
            _cg_image(page), None
        )
        ok, error = handler.performRequests_error_([request], None)
        if not ok:
            raise OcrUnavailableError(f"Vision request failed on {page.path}: {error}")

        lines: list[OcrLine] = []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            candidate = candidates[0]
            text = str(candidate.string()).strip()
            if not text:
                continue
            lines.append(
                OcrLine(
                    text=text,
                    box=_to_pixel_box(observation.boundingBox(), page.width, page.height),
                    confidence=float(candidate.confidence()),
                )
            )
        log.debug("vision: %d lines on %s", len(lines), page.path.name)
        return lines
