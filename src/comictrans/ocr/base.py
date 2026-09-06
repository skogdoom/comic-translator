"""The OCR interface every adapter implements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..config import OcrConfig
from ..imaging import PageImage
from ..model import Box


@dataclass(frozen=True, slots=True)
class OcrLine:
    """One recognised line of text.

    ``box`` is already in page pixels with a top-left origin. Adapters convert
    at their own boundary; no backend's coordinate convention leaks past here.
    """

    text: str
    box: Box
    confidence: float


@runtime_checkable
class TextRecognizer(Protocol):
    """Pixels in, lines out. No grouping, no ordering, no geometry."""

    name: str

    def recognize(self, page: PageImage, config: OcrConfig) -> list[OcrLine]:
        """Recognise every text line on the page."""
        ...
