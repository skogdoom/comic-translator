from __future__ import annotations

import pytest

from comictrans.config import OcrConfig
from comictrans.errors import OcrUnavailableError
from comictrans.model import Box
from comictrans.ocr import get_recognizer, installed_languages, reads, resolved_engine
from comictrans.ocr.base import OcrLine, TextRecognizer
from comictrans.ocr.grouping import (
    median_line_height,
    sort_lines,
    utterance_confidence,
    utterance_text,
)
from comictrans.ocr.tesseract import (
    TESSERACT_NAMES,
    TesseractRecognizer,
    tesseract_languages,
    tesseract_name,
)
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


@pytest.mark.parametrize(
    ("tag", "name"),
    [
        ("sv", "swe"),  # refused as "sv" before this table: the file is swe
        ("ja", "jpn"),
        ("pt_BR", "por"),  # pyphen's spelling of a tag
        ("zh", "chi_sim"),
        ("zh-Hans", "chi_sim"),
        ("zh-Hant", "chi_tra"),
        ("zh-TW", "chi_tra"),
        ("sr", "srp"),
        ("sr-Latn", "srp_latn"),
        ("sr_Latn", "srp_latn"),
        ("az-Cyrl", "aze_cyrl"),
        ("uz-Cyrl", "uzb_cyrl"),
        ("nb", "nor"),
        ("nn", "nor"),
        ("no", "nor"),
    ],
)
def test_a_tag_reaches_tesseract_as_the_file_it_has(tag: str, name: str) -> None:
    assert tesseract_name(tag) == name


@pytest.mark.parametrize("given", ["chi_sim", "Chi_Sim", "ita_old", "jpn_vert", "ceb", "xx"])
def test_a_name_that_is_not_a_two_letter_tag_goes_through_as_given(given: str) -> None:
    """Tesseract's own names, typed as themselves, and a code nobody knows.

    The last for Tesseract to refuse by name, rather than for this to guess.
    """
    assert tesseract_name(given) == given.lower()


def test_each_file_is_asked_for_once_in_the_order_given() -> None:
    assert tesseract_languages(("it", "ita", "it-IT", "en", "en-GB")) == "ita+eng"
    assert tesseract_languages(()) == "eng"


def test_the_table_is_qts_own_three_letter_codes() -> None:
    """Generated from Qt once; checked against it wherever Qt is installed.

    Tesseract names its plain language files by ISO 639-2/T — all 106 that
    Qt can name, measured against Debian's list — so a table disagreeing
    with Qt's ISO 639-2/T is a table that was edited by hand.
    """
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QLocale

    part2t = QLocale.LanguageCodeType.ISO639Part2T
    disagree = {
        code: (name, QLocale.languageToCode(QLocale.codeToLanguage(code), part2t))
        for code, name in TESSERACT_NAMES.items()
        if QLocale.languageToCode(QLocale.codeToLanguage(code), part2t) != name
    }
    assert disagree == {}
    assert len(TESSERACT_NAMES) == 101


def test_what_tesseract_has_comes_back_as_tags_that_find_it_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whatever is installed, the tag it is listed as is the tag that reaches it.

    Everything in the table, the files named otherwise, and the ones with no
    tag of their own. Not a language — orientation detection, equations,
    script models — is left out.
    """
    from comictrans.ocr import tesseract

    files = [
        *TESSERACT_NAMES.values(),
        *("nor", "chi_sim", "chi_tra", "srp_latn", "aze_cyrl", "uzb_cyrl"),
        *("ita_old", "jpn_vert", "ceb"),
    ]
    monkeypatch.setattr(tesseract, "available", lambda: True)
    monkeypatch.setattr(
        tesseract.pytesseract,
        "get_languages",
        lambda config="": [*files, "osd", "equ", "script/Latin"],
    )

    tags = tesseract.installed_languages()

    assert [tesseract_name(tag) for tag in tags] == files
    assert tags[:2] == ("af", "am"), "tags, not the files' own names"
    assert "zh-Hant" in tags and "sr-Latn" in tags and "no" in tags


def test_nothing_is_installed_where_tesseract_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    from comictrans.ocr import tesseract

    monkeypatch.setattr(tesseract, "available", lambda: False)

    assert tesseract.installed_languages() == ()


def test_automatic_asks_whichever_recogniser_it_would_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from comictrans.ocr import tesseract, vision

    monkeypatch.setattr(vision, "supported_languages", lambda: ("it-IT",))
    monkeypatch.setattr(tesseract, "installed_languages", lambda: ("it", "en"))
    monkeypatch.setattr(tesseract, "available", lambda: True)

    monkeypatch.setattr(vision, "available", lambda: True)
    assert (resolved_engine("auto"), installed_languages("auto")) == ("vision", ("it-IT",))
    monkeypatch.setattr(vision, "available", lambda: False)
    assert (resolved_engine("auto"), installed_languages("auto")) == ("tesseract", ("it", "en"))
    monkeypatch.setattr(tesseract, "available", lambda: False)
    assert (resolved_engine("auto"), installed_languages("auto")) == ("", ())


@pytest.mark.parametrize(
    ("engine", "tag", "installed", "expected"),
    [
        ("tesseract", "it-IT", ("it",), True),  # the same file
        ("tesseract", "ita", ("it",), True),
        ("tesseract", "sv", ("it",), False),
        ("tesseract", "zh-Hant", ("zh-Hans",), False),  # a different file
        ("vision", "it", ("it-IT",), True),  # a bare language, any region of it
        ("vision", "pt-PT", ("pt-BR",), False),  # but not another region
        ("vision", "PT_br", ("pt-BR",), True),
        ("vision", "sv", ("it-IT",), False),
        ("vision", "", (), True),
    ],
)
def test_whether_a_recogniser_reads_a_language(
    engine: str, tag: str, installed: tuple[str, ...], expected: bool
) -> None:
    assert reads(engine, tag, installed) is expected


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


@pytest.mark.parametrize(
    "text",
    [
        "NON CI POSSO CREDERE!",
        "STOP!",  # a one-word balloon is ordinary comic lettering
        "NO",
        "PERCHÉ?",  # accents count as letters
        "私は",  # ... in any script python knows about
        "a b cd",
    ],
)
def test_real_lettering_reads_as_text(text: str) -> None:
    from comictrans.ocr.grouping import looks_like_text

    assert looks_like_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "(6",  # a face, read as text
        "o ©",  # an eye
        "<= 4 \\ \\ 7 \\/",  # dashes of a balloon outline
        "_",
        "/",
        "",
        "   ",
        "0 0 0 0 0 0",  # halftone dots
        "!!! ... ?",
    ],
)
def test_artefacts_do_not_read_as_text(text: str) -> None:
    from comictrans.ocr.grouping import looks_like_text

    assert not looks_like_text(text)


VISION = ("en-US", "it-IT", "pt-BR", "zh-Hans", "zh-Hant")


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("it", "it-IT"),  # a bare language: the tag Vision lists for it
        ("it-IT", "it-IT"),
        ("IT_it", "it-IT"),  # any case, either separator, in Vision's spelling
        ("zh", "zh-Hans"),  # the first Vision lists
        ("zh-Hant", "zh-Hant"),
        ("pt-PT", None),  # a region is itself, not another region
        ("sv", None),
        ("", None),
    ],
)
def test_a_tag_is_handed_to_vision_in_its_own_spelling(tag: str, expected: str | None) -> None:
    from comictrans.ocr.vision import vision_tag

    assert vision_tag(tag, VISION) == expected


def test_what_vision_cannot_read_is_held_back_and_said() -> None:
    from comictrans.ocr.vision import vision_languages

    assert vision_languages(("it", "sv", "it-IT", "en", "xx"), VISION) == (
        ("it-IT", "en-US"),
        ("sv", "xx"),
    )


def test_with_nothing_to_check_against_the_tags_go_through_as_they_came() -> None:
    """Vision's list could not be had: better its old behaviour than none."""
    from comictrans.ocr.vision import vision_languages

    assert vision_languages(("it", "sv"), ()) == (("it", "sv"), ())


def test_the_vision_adapter_asks_once_and_says_each_language_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from comictrans.ocr import vision

    asked: list[int] = []
    monkeypatch.setattr(vision, "supported_languages", lambda: (asked.append(1), VISION)[1])
    recognizer = VisionRecognizer()

    with caplog.at_level("WARNING", logger="comictrans.ocr.vision"):
        for _page in range(3):
            handed = recognizer.languages_for(("it", "sv"))

    assert handed == ("it-IT",)
    assert asked == [1], "one run, one question"
    said = [r.getMessage() for r in caplog.records if "cannot read" in r.getMessage()]
    assert said == ["Apple Vision cannot read sv; it reads with its own defaults instead"]


def test_only_vision_is_said_to_read_without_a_language(monkeypatch: pytest.MonkeyPatch) -> None:
    from comictrans.ocr import unread_languages, vision

    monkeypatch.setattr(vision, "supported_languages", lambda: VISION)

    assert unread_languages("apple-vision", ("it", "sv")) == ("sv",)
    assert unread_languages("tesseract", ("it", "sv")) == ()
