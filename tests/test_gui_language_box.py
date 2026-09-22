"""The language fields: a name in the window, a code in the file."""

from __future__ import annotations

from pathlib import Path

import pytest

from comictrans.model import PlanHeader, TextCase
from comictrans.planfile.schema import PLAN_VERSION

from .conftest import make_plan

pytest.importorskip("PySide6")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFormLayout, QWidget

from comictrans.gui.document import PlanDocument
from comictrans.gui.extract_dialog import ExtractDialog
from comictrans.gui.header_dialog import HeaderDialog
from comictrans.gui.language_box import LanguageBox, code_for, language_choices, language_label
from comictrans.gui.preferences import Preferences
from comictrans.gui.preferences_dialog import PreferencesDialog


def _document(source: str = "it", target: str = "en") -> PlanDocument:
    header = PlanHeader(
        version=PLAN_VERSION,
        generator="comictrans test",
        created="2026-09-22T12:00:00Z",
        source_language=source,
        target_language=target,
        ocr_engine="fake",
        font="Comic Sans MS",
        case=TextCase.UPPER,
        font_size_min_ratio=0.012,
        condense_min=0.9,
    )
    return PlanDocument(make_plan(header, ()), Path("comic-plan.yaml"))


def _box(code: str) -> LanguageBox:
    box = LanguageBox()
    box.set_value(code)
    return box


# -- naming -------------------------------------------------------------


def test_a_code_is_shown_by_name_and_handed_back_as_itself(qapp: object) -> None:
    box = _box("it")

    assert box.currentText() == "Italian (it)"
    assert box.value() == "it"


@pytest.mark.parametrize(
    ("first", "second"),
    [("it", "ita"), ("pt", "pt-BR"), ("zh-Hans", "zh-Hant")],
)
def test_codes_qt_would_name_alike_are_shown_apart(qapp: object, first: str, second: str) -> None:
    """Qt names a language from its first subtag, so each pair shares a name.

    ``it`` and a Tesseract ``ita`` are both "Italian"; ``pt`` and ``pt-BR``
    both "Portuguese". A field showing only that would show two different
    plans the same way.
    """
    one, other = _box(first), _box(second)

    assert one.currentText() != other.currentText()
    assert (one.value(), other.value()) == (first, second)


def test_a_qualifier_is_named_only_where_the_tag_has_one(qapp: object) -> None:
    """``QLocale("pt")`` is Brazilian; the plan said nothing of the kind."""
    assert language_label("pt") == "Portuguese (pt)"
    assert language_label("pt-BR") == "Portuguese, Brazil (pt-BR)"
    assert language_label("pt_BR") == "Portuguese, Brazil (pt_BR)", "pyphen's spelling"
    assert language_label("es-419") == "Spanish, Latin America (es-419)"
    assert language_label("zh-Hans") == "Chinese, Simplified Han (zh-Hans)"


@pytest.mark.parametrize("code", ["xx", "Italian", "de-1996", "sr_Latn", "es-419"])
def test_a_code_is_kept_exactly_however_little_can_be_said_of_it(qapp: object, code: str) -> None:
    """Never corrected, only named — including a name written where a code goes.

    ``Italian`` is what a hand-written plan might say. Typed, it would mean
    ``it``; given, it is handed back as it came, because a plan must not be
    changed by being looked at.
    """
    box = _box(code)

    assert box.value() == code
    box.lineEdit().editingFinished.emit()
    assert box.value() == code


def test_a_code_nobody_can_name_is_shown_as_itself(qapp: object) -> None:
    assert _box("xx").currentText() == "xx"


# -- typing and picking ---------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "code"),
    [("swedish", "sv"), ("Swedish (sv)", "sv"), ("swedish (SV)", "SV")],
)
def test_a_name_typed_rather_than_picked_means_that_language(
    qapp: object, typed: str, code: str
) -> None:
    """A label typed in the wrong case still reads back; its code is kept as typed."""
    box = _box("")

    box.setCurrentText(typed)

    assert box.value() == code


def test_picking_from_the_list_gives_the_code_not_the_label(qapp: object) -> None:
    box = _box("it")

    box.setCurrentIndex(box.findData("sv"))

    assert box.currentText() == "Swedish (sv)"
    assert box.value() == "sv"


def test_a_typed_code_is_named_when_typing_stops_without_being_an_edit(qapp: object) -> None:
    box = _box("")
    edits: list[str] = []
    box.currentTextChanged.connect(edits.append)

    box.setCurrentText("pt-BR")
    box.lineEdit().editingFinished.emit()

    assert box.currentText() == "Portuguese, Brazil (pt-BR)"
    assert box.value() == "pt-BR"
    assert edits == ["pt-BR"], "naming what was typed is not a second edit"


def test_typing_part_of_a_name_offers_the_language(qapp: object) -> None:
    """Typing "tal" offers Italian: nobody should need to know how a name starts.

    Typed as keys rather than set as a prefix, because the completer's model
    filters the same way whatever its mode, and it is the mode that decides
    whether anything is offered: inline completion, Qt's default, shows
    nothing for a match in the middle of a name.
    """
    box = _box("")
    box.show()
    box.activateWindow()
    box.lineEdit().setFocus()
    QApplication.processEvents()

    QTest.keyClicks(box.lineEdit(), "tal")
    QApplication.processEvents()

    completer = box.completer()
    assert completer is not None
    popup = completer.popup()
    try:
        assert popup is not None and popup.isVisible(), "nothing was offered"
        model = completer.completionModel()
        offered = [model.index(row, 0).data() for row in range(model.rowCount())]
        assert "Italian (it)" in offered
    finally:
        if popup is not None:
            popup.hide()
        box.hide()


def test_the_list_is_every_two_letter_language_by_name(qapp: object) -> None:
    choices = language_choices()
    labels = [label for label, _code in choices]
    codes = [code for _label, code in choices]

    assert labels == sorted(labels, key=str.casefold)
    assert all(len(code) == 2 for code in codes)
    assert len(set(codes)) == len(codes)
    assert {("Italian (it)", "it"), ("English (en)", "en"), ("Swedish (sv)", "sv")} <= set(choices)
    assert all(code_for(label) == code for label, code in choices), "every label reads back"


def test_the_field_is_one_width_whatever_it_holds(qapp: object) -> None:
    """The Region panel moved with its content once; this field must not.

    One field, laid out the way macOS lays out a form — each field at its own
    size hint — and given one language after another, which is what the
    header dialog does when an undo lands. Its actual width rather than its
    hint, because a field can grow through its minimum width without its hint
    moving at all, and that is how ``font_box`` fits a long name.
    """
    box = LanguageBox()
    form = QFormLayout()
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
    form.addRow("language", box)
    holder = QWidget()
    holder.setLayout(form)
    holder.show()
    widths = set()
    try:
        for code in ("", "it", "zh-Hans", "xx"):
            box.set_value(code)
            holder.adjustSize()
            QApplication.processEvents()
            widths.add(box.width())
    finally:
        holder.hide()

    assert len(widths) == 1, widths


# -- the dialogs ----------------------------------------------------------


def test_the_header_dialog_writes_the_code_of_a_language_picked_by_name(
    qapp: object, font_dir: Path
) -> None:
    document = _document()
    dialog = HeaderDialog(document)

    dialog._target_language.setCurrentIndex(dialog._target_language.findData("sv"))

    assert document.plan.header.target_language == "sv"


def test_the_header_dialog_changes_nothing_by_showing_a_code_it_cannot_name(
    qapp: object, font_dir: Path
) -> None:
    document = _document(source="ita", target="Italian")
    dialog = HeaderDialog(document)

    assert dialog._source_language.currentText() == "Italian (ita)"
    dialog._source_language.lineEdit().editingFinished.emit()
    dialog._target_language.lineEdit().editingFinished.emit()

    assert not document.dirty
    assert document.plan.header.source_language == "ita"
    assert document.plan.header.target_language == "Italian"


def test_the_header_dialog_shows_an_undone_language_by_name(qapp: object, font_dir: Path) -> None:
    document = _document()
    dialog = HeaderDialog(document)
    dialog._source_language.setCurrentText("de")

    document.undo()
    dialog.repopulate()

    assert dialog._source_language.currentText() == "Italian (it)"
    assert document.plan.header.source_language == "it"


def test_preferences_store_the_code_of_a_language_picked_by_name(qapp: object) -> None:
    dialog = PreferencesDialog(Preferences(), None)

    dialog._source_language.setCurrentIndex(dialog._source_language.findData("ja"))
    dialog._target_language.setCurrentText("Swedish (sv)")

    assert dialog.preferences().source_language == "ja"
    assert dialog.preferences().target_language == "sv"


def test_preferences_show_what_they_hold_by_name(qapp: object) -> None:
    dialog = PreferencesDialog(Preferences(source_language="ja", target_language="pt-BR"), None)

    assert dialog._source_language.currentText() == "Japanese (ja)"
    assert dialog._target_language.currentText() == "Portuguese, Brazil (pt-BR)"

    dialog.repopulate(Preferences(source_language="fr"))

    assert dialog._source_language.currentText() == "French (fr)"


def test_extract_asks_for_the_code_of_a_language_picked_by_name(
    qapp: object, tmp_path: Path
) -> None:
    dialog = ExtractDialog(None, None)
    dialog._source_language.setCurrentIndex(dialog._source_language.findData("fr"))
    dialog._target_language.setCurrentText("Portuguese, Brazil (pt-BR)")

    request = dialog.request()

    assert request.source_language == "fr"
    assert request.target_language == "pt-BR"
    assert request.config.ocr.languages == ("fr",), "the recogniser is handed the code too"
