"""The catalogues the window speaks from, and the traps in building them.

Three things are worth a test here and they fail in three different ways.

A ``.qm`` older than its ``.ts`` ships a window still speaking the last
translation, with nothing wrong anywhere to see. A counting message that
``lupdate`` did not mark as one is a sentence that will say "1 sidor" in
every language with a plural, including this one — and the mistake that
causes it is a call spelled ``QCoreApplication.translate`` instead of ``tr``,
which reads identically. And a translator installed after a widget module is
imported leaves that module's constants in English for the life of the
process, which no amount of looking at the window will explain.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from string import Formatter
from types import ModuleType
from typing import Any
from xml.sax.saxutils import unescape as _unescape

import pytest

pytest.importorskip("PySide6")

from comictrans.gui import translations


def _recompile() -> ModuleType:
    """The build script, imported from where it lives — beside the ``.ts``.

    It is a script rather than a module of the package, so there is no
    import path to it; this is the same reason it cannot import
    ``translations`` and why the two agree by test rather than by import.
    """
    path = translations.TRANSLATION_DIR / "recompile.py"
    spec = importlib.util.spec_from_file_location("recompile", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


recompile = _recompile()

MESSAGE = re.compile(r'<message( numerus="yes")?>(.*?)</message>', re.S)
SOURCE = re.compile(r"<source>(.*?)</source>", re.S)


def unescape(text: str) -> str:
    """``&apos;`` is not one of the three entities Python unescapes by default."""
    return _unescape(text, {"&apos;": "'", "&quot;": '"'})


def _messages(catalogue: Path) -> list[tuple[bool, str, str]]:
    """``(counts, source, body)`` for every message in a ``.ts``."""
    text = catalogue.read_text(encoding="utf-8")
    found = []
    for match in MESSAGE.finditer(text):
        source = SOURCE.search(match.group(2))
        assert source is not None, catalogue
        found.append((bool(match.group(1)), source.group(1), match.group(2)))
    return found


def test_every_compiled_catalogue_is_current() -> None:
    """The check ``recompile.py --check`` makes, made by the suite as well.

    A ``.ts`` edited and not recompiled is invisible: the window keeps
    speaking the last compiled version and nothing says so.
    """
    assert recompile.check() == 0


def test_the_two_source_languages_agree() -> None:
    """``recompile`` cannot import ``translations``; a test can read both."""
    assert recompile.SOURCE_LANGUAGE == translations.SOURCE_LANGUAGE


@pytest.mark.parametrize("catalogue", recompile.catalogues(), ids=lambda path: path.name)
def test_a_counting_message_carries_its_plural_forms(catalogue: Path) -> None:
    """``%n`` and ``numerus="yes"`` go together, or the count is wrong.

    Measured, and the reason this is a test rather than a convention:
    ``lupdate`` marks no ``QCoreApplication.translate`` call as carrying
    plural forms whatever its arguments, and drops the message outright when
    the count is an attribute rather than a bare name. Both failures are
    silent — the first ships one form for every number, the second ships the
    English.
    """
    for counts, source, _body in _messages(catalogue):
        assert counts == ("%n" in source), source


@pytest.mark.parametrize("catalogue", recompile.catalogues(), ids=lambda path: path.name)
def test_a_translation_takes_the_same_placeholders_as_its_source(catalogue: Path) -> None:
    """A window crashes on a translation that invents a placeholder.

    The sentences here are filled in with ``str.format`` after Qt has
    substituted ``%n``, and ``str.format`` raises on a field the caller did
    not pass — so ``{2}`` in a translation of a two-placeholder sentence is a
    traceback in somebody's status bar, in one language only, at whatever
    moment that sentence comes up. A stray ``{`` is the same. Cheap to check
    here and unpleasant to find there.
    """
    fields = Formatter().parse

    for _counts, source, body in _messages(catalogue):
        wanted = {name for _text, name, _spec, _conv in fields(unescape(source)) if name}
        for form in re.findall(r"<(?:numerusform|translation)>(.*?)</", body, re.S):
            got = {name for _text, name, _spec, _conv in fields(unescape(form)) if name}
            assert got == wanted, f"{source!r} -> {form!r}"


def test_the_english_catalogue_is_plurals_and_nothing_else() -> None:
    """English is the source language, so only its inflections are here.

    ``lupdate -pluralonly`` keeps it that way. Every one of them is
    translated, because an unfinished English plural is exactly the
    ``1 page(s)`` the catalogue exists to stop.
    """
    catalogue = recompile.HERE / f"comictrans_{translations.SOURCE_LANGUAGE}.ts"
    messages = _messages(catalogue)
    assert messages, catalogue
    for counts, source, body in messages:
        assert counts, source
        assert 'type="unfinished"' not in body, source


def test_english_says_one_page_rather_than_one_page_s(qapp: object) -> None:
    """What the English catalogue buys, in the words a window shows."""
    from comictrans.apply import ApplyReport
    from comictrans.gui.run_report import render_headline

    out = Path("/tmp/rendered")
    one = ApplyReport(pages_written=[out / "a.png"], outcomes=[], page_failures=[])
    two = ApplyReport(pages_written=[out / "a.png", out / "b.png"], outcomes=[], page_failures=[])

    assert render_headline(one, out) == f"1 page written to {out}"
    assert render_headline(two, out) == f"2 pages written to {out}"


def test_a_translated_window_says_a_translated_thing(qapp: Any) -> None:
    """Swedish, loaded the way the application loads it.

    Installed and taken out again inside the test: a translator stays in the
    application's chain until it is removed, and every later test in this
    session asserts English.
    """
    from PySide6.QtCore import QCoreApplication, QTranslator

    swedish = QTranslator()
    assert swedish.load(f"{translations.PREFIX}_sv", str(translations.TRANSLATION_DIR))
    qapp.installTranslator(swedish)
    try:
        assert QCoreApplication.translate("RunPanel", "Cancel") == "Avbryt"
        counted = QCoreApplication.translate("RunText", "%n page(s) written to {0}", None, 2)
        assert counted == "2 sidor skrivna till {0}"
    finally:
        qapp.removeTranslator(swedish)

    assert QCoreApplication.translate("RunPanel", "Cancel") == "Cancel"


def test_the_translator_goes_in_before_the_widgets_are_imported() -> None:
    """The ordering in ``gui.app.run`` that makes constants translatable.

    Nothing about the import would look wrong if it moved, and moving it
    costs every module-level string in the window — ``inspector``'s flag
    names, ``main_window``'s two preview labels, the guide's title — without
    costing anything that would fail.
    """
    source = Path(translations.__file__).with_name("app.py").read_text(encoding="utf-8")
    run = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "run"
    )

    installed_at = None
    imported_at = None
    for node in ast.walk(run):
        if (
            installed_at is None
            and isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "install"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "translations"
        ):
            installed_at = node.lineno
        if (
            imported_at is None
            and isinstance(node, ast.ImportFrom)
            and node.module == "main_window"
        ):
            imported_at = node.lineno

    assert installed_at is not None, "gui.app.run no longer installs a translator"
    assert imported_at is not None, "gui.app.run no longer imports the window"
    assert installed_at < imported_at


def test_the_language_asked_for_is_the_environment_then_the_system(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(translations.LANGUAGE_ENV, "sv_SE")
    assert translations.wanted() == "sv"

    monkeypatch.setenv(translations.LANGUAGE_ENV, "pt-BR")
    assert translations.wanted() == "pt"

    monkeypatch.delenv(translations.LANGUAGE_ENV)
    assert translations.wanted()  # whatever this machine says, it says something


def test_three_places_say_which_language_and_they_are_asked_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The environment, then this window's own setting, then the machine's.

    The middle one is the point: it is what makes the language switchable
    from inside the window at all, and it has to beat the machine's answer
    or choosing anything but the system language would do nothing.
    """
    monkeypatch.setenv(translations.LANGUAGE_ENV, "sv")
    assert translations.preferred("pt") == ("sv",)

    monkeypatch.delenv(translations.LANGUAGE_ENV)
    assert translations.preferred("pt-BR") == ("pt",)

    assert translations.preferred("") == translations.offered()
    assert translations.preferred("   ") == translations.offered()


def test_what_the_machine_asks_for_is_a_list_of_bare_codes() -> None:
    """``uiLanguages``, not ``name``, is what carries a macOS per-app choice.

    Choosing a language for this application in System Settings writes an
    ``AppleLanguages`` list scoped to it; the system *locale* does not
    change, so a window reading ``QLocale.system().name()`` alone would go on
    speaking the Mac's language and the bundle's own setting would do
    nothing.

    What this can check is the list-building, which is why that half is a
    function of its own: every machine the suite runs on is set to one
    language, so the result of :func:`offered` looks the same either way.
    """
    assert translations._codes("sv-SE", "en_GB", "sv", "", "EN") == ("sv", "en")

    codes = translations.offered()
    assert codes, "the machine always asks for something"
    assert len(set(codes)) == len(codes), codes


def test_a_language_with_no_catalogue_falls_back_to_english(
    qapp: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(translations.LANGUAGE_ENV, "xx")

    assert translations.install(qapp) == translations.SOURCE_LANGUAGE
    assert translations.current() == translations.SOURCE_LANGUAGE


def test_the_window_setting_is_what_actually_loads(
    qapp: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: a stored choice, through ``install``, to a Swedish word.

    Installed and put back inside the test. ``install`` removes what it
    installed last time, which is what makes that possible and what stops a
    second call from stacking a language over the one before it.
    """
    from PySide6.QtCore import QCoreApplication

    monkeypatch.delenv(translations.LANGUAGE_ENV, raising=False)
    try:
        assert translations.install(qapp, "sv") == "sv"
        assert translations.current() == "sv"
        assert QCoreApplication.translate("RunPanel", "Cancel") == "Avbryt"
    finally:
        monkeypatch.setenv(translations.LANGUAGE_ENV, translations.SOURCE_LANGUAGE)
        translations.install(qapp)

    assert QCoreApplication.translate("RunPanel", "Cancel") == "Cancel"


def test_the_preferences_dialog_offers_what_ships_and_nothing_else(qapp: object) -> None:
    """The field a language is chosen in, and the one row that is not one."""
    from comictrans.gui.preferences_dialog import SYSTEM_LANGUAGE, PreferencesDialog

    rows = PreferencesDialog.language_choices()

    assert rows[0] == (SYSTEM_LANGUAGE, "")
    assert [code for _label, code in rows[1:]] == list(translations.available())
    # Named in themselves, which is the only naming that helps somebody who
    # opened this dialog because the window is in a language they cannot read.
    # "English" rather than the "American English" Qt names a bare ``en``
    # locale: these catalogues are per language, not per country.
    assert dict(rows)["Svenska"] == "sv"
    assert dict(rows)["English"] == "en"


def test_the_catalogues_that_ship_are_the_ones_compiled() -> None:
    """``available`` reads the directory the application reads."""
    compiled = {
        path.stem.removeprefix(f"{translations.PREFIX}_")
        for path in translations.TRANSLATION_DIR.glob("*.qm")
    }

    assert set(translations.available()) == compiled
    assert translations.SOURCE_LANGUAGE in compiled
