"""OCR stage: pixels in, text lines with pixel boxes out.

The stage is an interface with two adapters. Apple Vision is the real one;
Tesseract exists so the pipeline is runnable and testable off a Mac. Whichever
runs is recorded in the plan file header, because OCR quality is the thing
you will most often want to blame.
"""

from __future__ import annotations

import logging

from ..config import OcrConfig
from ..errors import OcrUnavailableError
from .base import OcrLine, TextRecognizer

log = logging.getLogger(__name__)

__all__ = ["OcrLine", "TextRecognizer", "get_recognizer"]


def get_recognizer(config: OcrConfig) -> TextRecognizer:
    """Pick an OCR backend.

    ``auto`` prefers Apple Vision and falls back to Tesseract, loudly. Naming a
    backend explicitly makes its absence an error rather than a downgrade.
    """
    from . import tesseract, vision

    engine = config.engine.lower()
    if engine in {"vision", "apple-vision"}:
        return vision.VisionRecognizer()
    if engine == "tesseract":
        return tesseract.TesseractRecognizer()
    if engine != "auto":
        raise OcrUnavailableError(f"unknown OCR engine {config.engine!r}")

    if vision.available():
        return vision.VisionRecognizer()

    reason = vision.unavailable_reason()
    if tesseract.available():
        log.warning(
            "Apple Vision unavailable (%s); falling back to Tesseract. "
            "OCR quality will be noticeably worse on comic lettering.",
            reason,
        )
        return tesseract.TesseractRecognizer()

    raise OcrUnavailableError(
        f"no OCR backend available. Apple Vision: {reason}. "
        f"Tesseract: {tesseract.unavailable_reason()}."
    )
