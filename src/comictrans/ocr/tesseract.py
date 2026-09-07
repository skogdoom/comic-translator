"""Tesseract fallback adapter.

Exists so the pipeline runs and can be tested off a Mac. It is measurably
worse than Vision on comic lettering; whenever it runs, that fact is logged
and recorded in the plan file header.
"""

from __future__ import annotations

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

try:
    import pytesseract
except ImportError as exc:  # pragma: no cover - optional dependency
    pytesseract = None
    _IMPORT_ERROR = str(exc)

# BCP-47 prefix -> Tesseract's ISO 639-2 traineddata name.
_LANG_MAP = {
    "it": "ita",
    "en": "eng",
    "fr": "fra",
    "de": "deu",
    "es": "spa",
    "pt": "por",
    "nl": "nld",
}

_PSM_SPARSE = "--psm 11"
"""Sparse text: find as much text as possible in no particular order. Comic
pages are scattered balloons, not a column of prose."""


def available() -> bool:
    if pytesseract is None:
        return False
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return False
    return True


def unavailable_reason() -> str:
    if _IMPORT_ERROR is not None:
        return _IMPORT_ERROR
    if not available():
        return "pytesseract installed but the tesseract binary is not on PATH"
    return "available"


def tesseract_languages(languages: tuple[str, ...]) -> str:
    """BCP-47 tags to Tesseract traineddata names: ``('it-IT',)`` -> ``'ita'``.

    An unmapped language passes through unchanged, so a traineddata name given
    directly still works.
    """
    codes: list[str] = []
    for language in languages:
        prefix = language.split("-")[0].lower()
        code = _LANG_MAP.get(prefix, prefix)
        if code not in codes:
            codes.append(code)
    return "+".join(codes) or "eng"


def _group_words(data: dict[str, list[Any]], min_height: int) -> list[OcrLine]:
    """Fold Tesseract's word rows into lines, keyed by block/paragraph/line."""
    lines: dict[tuple[int, int, int], list[int]] = {}
    for index, text in enumerate(data["text"]):
        if not str(text).strip():
            continue
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            continue
        if confidence < 0:
            continue
        key = (
            int(data["block_num"][index]),
            int(data["par_num"][index]),
            int(data["line_num"][index]),
        )
        lines.setdefault(key, []).append(index)

    result: list[OcrLine] = []
    for indices in lines.values():
        words = [str(data["text"][i]).strip() for i in indices]
        left = min(int(data["left"][i]) for i in indices)
        top = min(int(data["top"][i]) for i in indices)
        right = max(int(data["left"][i]) + int(data["width"][i]) for i in indices)
        bottom = max(int(data["top"][i]) + int(data["height"][i]) for i in indices)
        if bottom - top < min_height:
            continue
        confidence = sum(float(data["conf"][i]) for i in indices) / len(indices) / 100.0
        result.append(
            OcrLine(
                text=" ".join(w for w in words if w),
                box=Box(left, top, right, bottom),
                confidence=max(0.0, min(1.0, confidence)),
            )
        )
    return result


class TesseractRecognizer:
    """Page-level sparse-text recognition via ``image_to_data``."""

    name = "tesseract"

    def recognize(self, page: PageImage, config: OcrConfig) -> list[OcrLine]:
        if not available():
            raise OcrUnavailableError(f"Tesseract unavailable: {unavailable_reason()}")

        data = pytesseract.image_to_data(
            Image.fromarray(page.rgb),
            lang=tesseract_languages(config.languages),
            config=_PSM_SPARSE,
            output_type=pytesseract.Output.DICT,
        )
        min_height = max(1, round(config.minimum_text_height_ratio * page.height))
        lines = _group_words(data, min_height)
        log.debug("tesseract: %d lines on %s", len(lines), page.path.name)
        return lines
