"""The OCR languages field: typed codes, a menu of what is installed, names."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QFormLayout, QWidget

from comictrans import ocr
from comictrans.gui.extract_dialog import ExtractDialog
from comictrans.gui.ocr_languages import OcrLanguagesField
from comictrans.gui.preferences import Preferences
from comictrans.gui.preferences_dialog import PreferencesDialog

INSTALLED = {
    "tesseract": ("en", "it", "zh-Hant", "jpn_vert"),
    "vision": ("it-IT", "en-US", "pt-BR"),
}
"""What each recogniser says it reads, for these tests: this machine's own
answer depends on what is installed on it."""


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Recognisers that answer from ``INSTALLED``; yields who was asked."""
    calls: list[str] = []

    def installed(engine: str) -> tuple[str, ...]:
        calls.append(engine)
        return INSTALLED.get(engine, ())

    resolved = {"auto": "vision", "vision": "vision", "tesseract": "tesseract"}
    monkeypatch.setattr(ocr, "installed_languages", installed)
    monkeypatch.setattr(ocr, "resolved_engine", lambda engine: resolved.get(engine, ""))
    yield calls


def _menu(field: OcrLanguagesField) -> list[tuple[str, bool, bool]]:
    """``(text, ticked, enabled)`` for each entry, as the menu opens."""
    field._menu.aboutToShow.emit()
    return [(a.text(), a.isChecked(), a.isEnabled()) for a in field._menu.actions()]


def _names(field: OcrLanguagesField) -> str:
    """The whole line read back — the tooltip, since the label may elide."""
    return field._names.toolTip()


# -- the menu -------------------------------------------------------------


def test_the_menu_offers_what_the_recogniser_has_by_name(qapp: object, asked: list[str]) -> None:
    field = OcrLanguagesField("it", "tesseract")

    assert _menu(field) == [
        ("Chinese, Traditional Han (zh-Hant)", False, True),
        ("English (en)", False, True),
        ("Italian (it)", True, False),
        ("Japanese (jpn_vert)", False, True),
    ], "sorted by name; what is listed already is ticked and cannot be added twice"


def test_a_language_listed_another_way_is_still_ticked(qapp: object, asked: list[str]) -> None:
    """``ita`` is Italian to Tesseract; ``it`` is Italian to an ``it-IT`` Vision."""
    field = OcrLanguagesField("ita", "tesseract")
    assert ("Italian (it)", True, False) in _menu(field)

    field.set_engine("vision")
    field.set_text("it")
    assert ("Italian, Italy (it-IT)", True, False) in _menu(field)


def test_picking_from_the_menu_adds_to_the_end_and_is_an_edit(
    qapp: object, asked: list[str]
) -> None:
    field = OcrLanguagesField("it", "tesseract")
    edits: list[str] = []
    field.changed.connect(lambda: edits.append(field.text()))

    _menu(field)
    english = next(a for a in field._menu.actions() if a.text() == "English (en)")
    english.trigger()

    assert field.languages() == ("it", "en")
    assert edits == ["it, en"]


def test_showing_a_list_is_not_an_edit(qapp: object, asked: list[str]) -> None:
    field = OcrLanguagesField("", "tesseract")
    edits: list[str] = []
    field.changed.connect(lambda: edits.append(field.text()))

    field.set_text("it, en")

    assert (field.languages(), edits) == (("it", "en"), [])


def test_the_menu_asks_the_recogniser_now_chosen(qapp: object, asked: list[str]) -> None:
    field = OcrLanguagesField("", "tesseract")
    field.set_engine("vision")

    assert [text for text, _ticked, _enabled in _menu(field)] == [
        "English, United States (en-US)",
        "Italian, Italy (it-IT)",
        "Portuguese, Brazil (pt-BR)",
    ]


def test_the_menu_says_when_there_is_nothing_to_offer(
    qapp: object, asked: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    field = OcrLanguagesField("", "neither")
    assert _menu(field) == [("no recogniser here to ask", False, False)]

    monkeypatch.setitem(INSTALLED, "tesseract", ())
    field.set_engine("tesseract")
    assert _menu(field) == [("nothing installed for Tesseract", False, False)]


def test_each_recogniser_is_asked_once(qapp: object, asked: list[str]) -> None:
    """Asking Tesseract runs it; once per field is enough."""
    field = OcrLanguagesField("it", "tesseract")
    for text in ("it, en", "it, en, sv", "sv"):
        field.line_edit().setText(text)
    _menu(field)
    _menu(field)
    field.set_engine("vision")
    field.set_engine("tesseract")

    assert asked == ["tesseract", "vision"]


# -- reading it back ------------------------------------------------------


def test_the_codes_are_read_back_as_names_in_order(qapp: object, asked: list[str]) -> None:
    field = OcrLanguagesField("it, en", "tesseract")

    assert _names(field) == "Italian, then English"

    field.line_edit().setText("en, it")
    assert _names(field) == "English, then Italian", "the order is the point"


def test_a_language_the_recogniser_lacks_is_said_to_be_missing(
    qapp: object, asked: list[str]
) -> None:
    """Kept and named, as a font this machine lacks is kept and marked."""
    field = OcrLanguagesField("it, sv", "tesseract")

    assert _names(field) == "Italian, then Swedish, which Tesseract does not have"
    assert field.languages() == ("it", "sv"), "never dropped"

    field.set_engine("vision")
    assert _names(field) == "Italian, then Swedish, which Apple Vision does not have"


def test_nothing_is_said_missing_with_no_recogniser_to_ask(qapp: object, asked: list[str]) -> None:
    """Every language missing would be true of the machine, not the languages."""
    field = OcrLanguagesField("it, sv", "neither")

    assert _names(field) == "Italian, then Swedish"


def test_the_field_is_one_width_however_many_languages_it_lists(
    qapp: object, asked: list[str]
) -> None:
    """Names read back must not push the form wide, as the region line once did."""
    field = OcrLanguagesField("", "tesseract")
    form = QFormLayout()
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
    form.addRow("OCR languages", field)
    holder = QWidget()
    holder.setLayout(form)
    holder.show()
    widths = set()
    try:
        for text in ("", "it", "it, en, sv, pt-BR, zh-Hant, jpn_vert, de, fr, es, nl"):
            field.set_text(text)
            holder.adjustSize()
            QApplication.processEvents()
            widths.add(field.width())
    finally:
        holder.hide()

    assert len(widths) == 1, widths


# -- the dialogs ----------------------------------------------------------


def test_extract_follows_its_recogniser_and_asks_for_the_codes(
    qapp: object, asked: list[str]
) -> None:
    dialog = ExtractDialog(preferences=Preferences(ocr_languages="it, sv", ocr_engine="tesseract"))
    assert "Tesseract does not have" in _names(dialog._languages)

    dialog._engine.setCurrentIndex(dialog._engine.findData("vision"))
    assert "Apple Vision does not have" in _names(dialog._languages)

    assert dialog.request().config.ocr.languages == ("it", "sv")


def test_preferences_write_the_list_through_and_follow_the_recogniser(
    qapp: object, asked: list[str]
) -> None:
    dialog = PreferencesDialog(Preferences(ocr_languages="it", ocr_engine="tesseract"), None)
    field = dialog._ocr_languages
    saved: list[str] = []
    dialog.changed.connect(lambda: saved.append(dialog.preferences().ocr_languages))

    _menu(field)
    next(a for a in field._menu.actions() if a.text() == "English (en)").trigger()
    assert saved == ["it, en"], "an edit the window hears of, and so saves"

    dialog._engine.setCurrentIndex(dialog._engine.findData("vision"))
    assert ("Italian, Italy (it-IT)", True, False) in _menu(field)

    dialog.repopulate(Preferences(ocr_languages="sv", ocr_engine="tesseract"))
    assert _names(field) == "Swedish, which Tesseract does not have"
