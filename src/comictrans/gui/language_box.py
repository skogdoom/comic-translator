"""A language field that shows a name and stores a code.

The plan records ``source_language: it``, and that stays: a code is what
pyphen, the recognisers and every other version of this tool read, and a plan
whose header said ``Italian`` would be a plan none of them could use. What
changes is only what the window shows — ``Italian (it)`` — and that a
language is picked from a list rather than typed from memory.

**The code is part of the name**, not left out of it. Qt names a language
from its first subtag and nothing else unless asked, so ``it`` and a
hand-typed Tesseract ``ita`` both come back as "Italian", and ``pt`` and
``pt-BR`` as "Portuguese" — measured. Four codes, two names; a field showing
only the name would show the same thing for plans that say different things.
With the code beside it, what is shown is what is stored.

**Nothing is rewritten.** A code this list does not have — a region-qualified
tag, a three-letter one, one nobody has heard of — is shown as whatever Qt
can say about it and handed back exactly as it came. One Qt cannot name at
all is shown as the code alone. That is the same promise ``font_box`` makes
about a font this machine does not have, for the same reason: a plan written
somewhere else must still open, and still say what it said.

Names are English whatever language the window is in. Qt can name a
language in English or in that language itself, and in nothing else —
measured — so a Swedish window cannot say *italienska* without a catalogue of
its own. The window-language field in preferences uses each language's own
name instead, and for a different reason; see ``preferences_dialog``'s
``language_name``, which is not this module's ``_name_of``.
"""

from __future__ import annotations

import re
from functools import cache

from PySide6.QtCore import QLocale, QSignalBlocker, Qt
from PySide6.QtWidgets import QComboBox, QWidget

_SUBTAG = re.compile(r"[-_]")
"""BCP-47 separates with a hyphen; pyphen and Qt's own locale names use an
underscore. Both are read, for naming only — the code is kept as typed."""

_LABEL = re.compile(r"(?P<name>.*) \((?P<code>[^()]+)\)")


def _qualifier(subtag: str) -> str:
    """A script or a territory, named — or nothing if Qt does not know it.

    Four letters is a script (``Hans``); two letters or three digits is a
    territory (``BR``, ``419``). Anything else is a variant or an extension,
    which Qt has no names for; the code in brackets still says it.
    """
    if len(subtag) == 4 and subtag.isalpha():
        script = QLocale.codeToScript(subtag)
        if script != QLocale.Script.AnyScript:
            return QLocale.scriptToString(script)
    elif (len(subtag) == 2 and subtag.isalpha()) or (len(subtag) == 3 and subtag.isdigit()):
        territory = QLocale.codeToTerritory(subtag)
        if territory != QLocale.Country.AnyTerritory:
            return QLocale.territoryToString(territory)
    return ""


def _name_of(code: str) -> str:
    """What Qt calls ``code``, in English, or ``""`` if it cannot say.

    Built from the tag's own subtags rather than from ``QLocale(code)``,
    because a locale fills in what the tag left out: ``QLocale("pt")`` is
    Brazilian, so asking it for a territory would name one the plan never
    mentioned.
    """
    first, *rest = _SUBTAG.split(code.strip())
    language = QLocale.codeToLanguage(first)
    if language in (QLocale.Language.AnyLanguage, QLocale.Language.C):
        return ""
    qualifiers = [named for named in (_qualifier(subtag) for subtag in rest) if named]
    return ", ".join([QLocale.languageToString(language), *qualifiers])


def language_label(code: str) -> str:
    """``it`` -> "Italian (it)". A code Qt cannot name is shown as itself."""
    code = code.strip()
    name = _name_of(code)
    return f"{name} ({code})" if name else code


@cache
def language_choices() -> tuple[tuple[str, str], ...]:
    """``(label, code)`` for every language with a two-letter code, by name.

    Every one Qt knows rather than a chosen few: a source language is
    whatever the comic was lettered in, and a short list would be a guess
    about which comics exist. There are 180; the field completes on any part
    of a name, so nobody scrolls them. Anything else — a three-letter code,
    a region — is typed, and kept.
    """
    rows: list[tuple[str, str]] = []
    for language in QLocale.Language:
        if language in (QLocale.Language.AnyLanguage, QLocale.Language.C):
            continue
        code = QLocale.languageToCode(language, QLocale.LanguageCodeType.ISO639Part1)
        if code:
            rows.append((language_label(code), code))
    return tuple(sorted(rows, key=lambda row: row[0].casefold()))


@cache
def _codes_by_name() -> dict[str, str]:
    """A bare name from the list, folded, to its code — for "italian" typed.

    No two languages in the list share a name — measured — so no name
    stands for two codes.
    """
    return {_name_of(code).casefold(): code for _label, code in language_choices()}


def code_for(text: str) -> str:
    """The code a field showing ``text`` holds.

    One of this module's own labels gives back its code, for any code and
    not only those in the list, so that a plan's ``pt-BR`` survives being
    shown as "Portuguese, Brazil (pt-BR)". A bare name from the list, typed
    rather than picked, is taken to mean that language. Anything else is a
    code somebody typed, and is kept exactly — a code is never corrected,
    only named.
    """
    typed = text.strip()
    match = _LABEL.fullmatch(typed)
    if match is not None and language_label(match["code"]).casefold() == typed.casefold():
        return match["code"].strip()
    return _codes_by_name().get(typed.casefold(), typed)


class LanguageBox(QComboBox):
    """An editable language picker: a name shown, a code stored."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # (label, code) as last shown by set_value; see value() for why.
        self._given = ("", "")

        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        # Sized from this list, which never changes, and not from the text
        # being shown — so the field is one width whatever language it holds,
        # and the rows beside it stay where they are. Qt's own default, and
        # what a field that grew with its value would lose.
        for label, code in language_choices():
            self.addItem(label, code)

        # A popup of every name containing what was typed, so "tal" offers
        # Italian: nobody needs to know which word a name starts with. Both
        # lines are needed, and the first is not cosmetic. Qt's default for a
        # combo is inline completion, which finishes what was typed as though
        # the match began with it — measured, "tal" becomes "talalan (ca)",
        # the tail of Catalan, and every one of those is an edit that writes
        # through. It is already case-insensitive, so that is left alone.
        completer = self.completer()
        if completer is not None:
            completer.setCompletionMode(completer.CompletionMode.PopupCompletion)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)

        line_edit = self.lineEdit()
        if line_edit is not None:
            line_edit.editingFinished.connect(self._show_label)

    def value(self) -> str:
        """The code shown, or ``""`` for an empty field.

        Until somebody edits the field, exactly the code it was given — even
        where reading the text back would say otherwise. A hand-written plan
        whose header says ``Italian`` is shown as "Italian", which typed
        would mean ``it``; handed back as ``it``, it would be a plan changed
        by being looked at.
        """
        label, code = self._given
        text = self.currentText()
        return code if text == label else code_for(text)

    def set_value(self, code: str) -> None:
        """Show ``code`` by name without writing it back out as an edit."""
        code = code.strip()
        self._given = (language_label(code), code)
        with QSignalBlocker(self):
            self.setCurrentText(self._given[0])

    def _show_label(self) -> None:
        """Once typing stops, name what was typed.

        ``it`` typed becomes "Italian (it)" when the field is left. The value
        does not change, and the signal is blocked, so this is not an edit —
        only the field catching up with what it already holds.
        """
        if language_label(self.value()) != self.currentText():
            self.set_value(self.value())


__all__ = ["LanguageBox", "code_for", "language_choices", "language_label"]
