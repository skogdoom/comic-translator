"""Application settings: what a *new* thing starts from, and the window itself.

**A preference never overrides a plan value.** It fills in a blank when
something is created, and does that only. A "default font" that quietly won
over a plan's header would mean the same plan renders differently on two
machines, and re-runnability — edit a translation, run it again, and only
that text changes — is the property the whole two-pass design exists to have.
The header dialog edits *this* plan; this decides what a *new* one starts
from, and the two must not blur into each other.

That rule is why nothing here reaches an open document. Every field either
seeds a new plan's header (the extract dialog's), picks a run-wide setting
that is not part of a plan at all (the render dialog's erase strategy and
output format, both of which a region's own ``erase`` still beats), or is
about this application rather than any comic — ``language``, and
``experimental``. None of them is written into a plan that already exists.

No Qt. ``QSettings`` satisfies :class:`SettingsStore` structurally, so this
module is tested against a dictionary and the window hands it the real thing.

Everything is stored as a string, and an empty one always means "the
behaviour you would get without this setting": no default output directory,
the source language for OCR, the source image's own format, whatever font
``extract`` finds for itself. That keeps one rule instead of a scattering of
sentinels, and it round-trips through an INI file without the type guessing
that makes ``QSettings`` booleans a trap.

**A checkbox is stored under that rule too, not as a boolean.**
``experimental`` is ``""`` or ``"yes"``, and everything else a hand-edited
file might hold — ``false``, ``0``, ``no``, ``off`` — is not "on", so it
reads as off rather than as a non-empty string that happens to be truthy.
That is the trap the one-rule decision exists to avoid, and it only stays
avoided if the on-state is a named value rather than "anything at all".
:attr:`Preferences.experimental_on` is the question to ask.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Protocol

from ..config import (
    DEFAULT_ERASE_STRATEGY,
    DEFAULT_SOURCE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
)
from ..erase import STRATEGIES

PREFIX = "preferences/"
"""Namespace inside the same ``QSettings`` the window layout uses. Separate
from ``window/``, because one is a choice and the other is where you left a
dock."""

ENGINES = ("auto", "vision", "tesseract")
"""The values ``--ocr`` takes. A stored engine outside this set is a settings
file edited by hand or written by a future version, and falls back rather
than reaching ``get_recognizer`` as nonsense."""

FORMATS = ("", "png", "jpeg", "tiff")
"""``""`` is apply's own default: match the source, except JPEG becomes PNG."""

ON = "yes"
"""What a ticked checkbox is stored as. Off is ``""``, like every other
setting whose absence means "behave as though nobody said"."""

SWITCHES = ("", ON)
"""Everything a checkbox may be. See the module docstring on why a stored
``false`` must not read as on."""


class SettingsStore(Protocol):
    """The part of ``QSettings`` this module uses.

    Structural, so the tests can pass a dictionary wrapper and never build a
    ``QSettings`` — which would otherwise want a real application name and
    write to the config of whoever runs the suite.
    """

    def value(self, key: str, defaultValue: object = None) -> object: ...  # noqa: N803

    def setValue(self, key: str, value: object) -> None: ...  # noqa: N802


@dataclass(frozen=True, slots=True)
class Preferences:
    """What new runs start from. Every field is a string; empty means unset."""

    # -- what a new plan starts as -------------------------------------
    source_language: str = DEFAULT_SOURCE_LANGUAGE
    target_language: str = DEFAULT_TARGET_LANGUAGE
    ocr_languages: str = ""
    """Comma-separated, as the extract dialog takes them. Empty is the
    source language, which is what ``--lang`` defaults to."""

    ocr_engine: str = "auto"
    font: str = ""
    """Recorded in a new plan's header. Empty lets ``extract`` walk its own
    fallback chain, which is the right default and was, until now, the only
    answer available from the window."""

    # -- chapter files -------------------------------------------------
    unrar_tool: str = ""
    """Where ``unrar`` is, for opening a .cbr. Empty looks on ``PATH``.

    One of the two settings here that are about this machine rather than
    about comics, and it exists because an application opened from the Finder
    does not inherit the shell's ``PATH``: a Homebrew ``unrar`` is on it for
    the command line and invisible to the window. Nothing else needs it — CBZ
    and PDF are read with no help — and no tool ships with comictrans,
    because unrar's licence is not one an MIT project can redistribute.
    """

    rar_tool: str = ""
    """Where ``rar`` is, for writing a .cbr. Empty looks on ``PATH``.

    A separate field from the one above rather than the same path used both
    ways, because they are not the same program: ``unrar`` only ever reads,
    and no amount of asking will make it write an archive. Somebody who has
    licensed WinRAR has both; somebody who installed unrar from Homebrew has
    only the reading half, and this field left empty is how that says so.
    """

    # -- how a run writes pages ----------------------------------------
    output_directory: str = ""
    """Empty means the render dialog's own suggestion, beside the pages."""

    erase_strategy: str = DEFAULT_ERASE_STRATEGY
    image_format: str = ""

    # -- the application itself ----------------------------------------
    experimental: str = ""
    """Whether unfinished work is switched on. ``ON`` or empty, and empty is
    what a fresh installation gets.

    What it gates today is the Plugins menu, and with it everything under
    ``plugins.py``: the window builds that menu hidden and only this shows
    it, so a fresh installation reads nothing off disk and imports no
    third-party code until somebody asks for it by name. It was here before
    there was anything to gate, so that the first feature that needed it
    would find a flag and a settled place in the window rather than have one
    added in a hurry beside it. Ask :attr:`experimental_on`."""

    language: str = ""
    """The interface language, empty for whatever this machine asks for.

    A bare code — ``sv`` — matching a catalogue in
    ``resources/translations/``. Not validated here, which would mean this
    module knowing what ships: the dialog offers only what does, and
    ``gui.translations`` falls back to English for anything it cannot load.
    """

    # -- where the file dialogs start ----------------------------------
    last_directory: str = ""
    """Remembered, not chosen: it is where you last opened or read from, so
    it is kept here but never shown in the preferences dialog. Nobody wants
    to type a last directory."""

    @property
    def experimental_on(self) -> bool:
        """Whether unfinished work is switched on, as a yes or a no.

        A property rather than a stored field, so it is not a preference in
        its own right: ``fields()`` does not see it, which is what keeps
        loading, saving and the round-trip test working off one list.
        """
        return self.experimental == ON


DEFAULTS = Preferences()
"""What a window with no settings behind it uses, and what an unreadable or
missing value falls back to. Safe to share: the class is frozen."""


def _clean(value: object) -> str:
    """A stored value as a string, whatever ``QSettings`` handed back."""
    return value.strip() if isinstance(value, str) else ""


def load_preferences(store: SettingsStore | None) -> Preferences:
    """Read preferences, falling back field by field rather than all at once.

    One unrecognised value does not throw the rest away. A settings file is
    hand-editable and outlives the version that wrote it, so a stale engine
    name costs that one field and nothing else.
    """
    if store is None:
        return DEFAULTS
    values: dict[str, str] = {}
    for field in fields(Preferences):
        stored = _clean(store.value(f"{PREFIX}{field.name}", ""))
        values[field.name] = stored or str(getattr(DEFAULTS, field.name))
    return _validated(Preferences(**values))


def _validated(preferences: Preferences) -> Preferences:
    """Put any field that names something unknown back to its default."""
    changes: dict[str, str] = {}
    if preferences.ocr_engine not in ENGINES:
        changes["ocr_engine"] = DEFAULTS.ocr_engine
    if preferences.erase_strategy not in STRATEGIES:
        changes["erase_strategy"] = DEFAULTS.erase_strategy
    if preferences.image_format not in FORMATS:
        changes["image_format"] = DEFAULTS.image_format
    if preferences.experimental not in SWITCHES:
        changes["experimental"] = DEFAULTS.experimental
    return replace(preferences, **changes) if changes else preferences


def save_preferences(store: SettingsStore | None, preferences: Preferences) -> None:
    """Write every field. A window built without settings persists nothing."""
    if store is None:
        return
    for field in fields(Preferences):
        store.setValue(f"{PREFIX}{field.name}", getattr(preferences, field.name))


__all__ = [
    "DEFAULTS",
    "ENGINES",
    "FORMATS",
    "ON",
    "PREFIX",
    "SWITCHES",
    "Preferences",
    "SettingsStore",
    "load_preferences",
    "save_preferences",
]
