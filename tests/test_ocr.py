from __future__ import annotations

import pytest

from comictrans.config import OcrConfig
from comictrans.errors import OcrUnavailableError
from comictrans.model import Box
from comictrans.ocr import get_recognizer
from comictrans.ocr.base import OcrLine, TextRecognizer
from comictrans.ocr.grouping import (
    median_line_height,
    sort_lines,
    utterance_confidence,
    utterance_text,
)
from comictrans.ocr.tesseract import TesseractRecognizer, tesseract_languages
from comictrans.ocr.vision import VisionRecognizer, _to_pixel_box


class _Rect:
    def __init__(self, x: float, y: float, w: float, h: float) -> None:
        self.origin = type("P", (), {"x": x, "y": y})()
        self.size = type("S", (), {"width": w, "height": h})()


def test_vision_normalised_bottom_left_becomes_pixel_top_left() -> None:
    # A box across the top of a 1000x2000 page: Vision reports it near y=1.
    box = _to_pixel_box(_Rect(0.1, 0.9, 0.5, 0.05), width=1000, height=2000)
    assert box == Box(left=100, top=100, right=600, bottom=200)


def test_vision_pixel_box_is_clamped_to_the_page() -> None:
    box = _to_pixel_box(_Rect(-0.1, -0.1, 1.4, 1.4), width=100, height=100)
    assert (box.left, box.top, box.right, box.bottom) == (0, 0, 100, 100)


def test_adapters_satisfy_the_recognizer_protocol() -> None:
    assert isinstance(VisionRecognizer(), TextRecognizer)
    assert isinstance(TesseractRecognizer(), TextRecognizer)


def test_tesseract_language_codes_map_from_bcp47() -> None:
    assert tesseract_languages(("it-IT",)) == "ita"
    assert tesseract_languages(("it-IT", "en-GB")) == "ita+eng"


def test_unknown_engine_is_an_error() -> None:
    with pytest.raises(OcrUnavailableError, match="unknown OCR engine"):
        get_recognizer(OcrConfig(engine="ocropus"))


def test_explicitly_named_engine_is_returned_without_probing() -> None:
    assert get_recognizer(OcrConfig(engine="tesseract")).name == "tesseract"
    assert get_recognizer(OcrConfig(engine="vision")).name == "apple-vision"


def test_auto_reports_both_backends_when_neither_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from comictrans.ocr import tesseract, vision

    monkeypatch.setattr(vision, "available", lambda: False)
    monkeypatch.setattr(tesseract, "available", lambda: False)
    with pytest.raises(OcrUnavailableError, match=r"Apple Vision:.*Tesseract:"):
        get_recognizer(OcrConfig(engine="auto"))


def test_auto_falls_back_to_tesseract(monkeypatch: pytest.MonkeyPatch) -> None:
    from comictrans.ocr import tesseract, vision

    monkeypatch.setattr(vision, "available", lambda: False)
    monkeypatch.setattr(tesseract, "available", lambda: True)
    assert get_recognizer(OcrConfig(engine="auto")).name == "tesseract"


def _line(text: str, left: int, top: int) -> OcrLine:
    return OcrLine(text=text, box=Box(left, top, left + 100, top + 30), confidence=0.9)


def test_lines_sort_top_to_bottom_then_left_to_right() -> None:
    lines = [_line("C", 100, 200), _line("B", 300, 100), _line("A", 100, 100)]
    assert [line.text for line in sort_lines(lines)] == ["A", "B", "C"]


def test_utterance_keeps_the_original_line_breaks() -> None:
    lines = [_line("NON CI POSSO", 100, 100), _line("CREDERE!", 100, 140)]
    assert utterance_text(lines) == "NON CI POSSO\nCREDERE!"


def test_utterance_drops_empty_lines() -> None:
    assert utterance_text([_line("  ", 100, 100), _line("CIAO", 100, 140)]) == "CIAO"


def test_region_confidence_is_the_worst_line() -> None:
    lines = [
        OcrLine("BUONA", Box(0, 0, 10, 10), 0.95),
        OcrLine("SERA", Box(0, 20, 10, 30), 0.31),
    ]
    assert utterance_confidence(lines) == pytest.approx(0.31)
    assert utterance_confidence([]) == 0.0


def test_median_line_height() -> None:
    assert median_line_height([_line("A", 0, 0), _line("B", 0, 40)]) == 30.0
    assert median_line_height([]) == 0.0
