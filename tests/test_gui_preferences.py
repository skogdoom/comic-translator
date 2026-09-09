"""Application defaults, decided without a window.

``QSettings`` satisfies the store protocol structurally, so everything here
runs against a dictionary: no application name, and nothing written into the
config of whoever runs the suite.
"""

from __future__ import annotations

from comictrans.config import (
    DEFAULT_ERASE_STRATEGY,
    DEFAULT_SOURCE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
)
from comictrans.gui.preferences import (
    DEFAULTS,
    PREFIX,
    Preferences,
    load_preferences,
    save_preferences,
)


class FakeStore:
    """The three lines of ``QSettings`` this module actually uses."""

    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = dict(values or {})

    def value(self, key: str, defaultValue: object = None) -> object:  # noqa: N803
        return self.values.get(key, defaultValue)

    def setValue(self, key: str, value: object) -> None:  # noqa: N802
        self.values[key] = value


def _filled() -> Preferences:
    return Preferences(
        source_language="ja",
        target_language="sv",
        ocr_languages="ja, en",
        ocr_engine="tesseract",
        font="Marker Felt",
        output_directory="/tmp/rendered",
        erase_strategy="inpaint",
        image_format="tiff",
        last_directory="/tmp/pages",
    )


def test_an_empty_store_gives_the_built_in_defaults() -> None:
    assert load_preferences(FakeStore()) == DEFAULTS
    assert DEFAULTS.source_language == DEFAULT_SOURCE_LANGUAGE
    assert DEFAULTS.target_language == DEFAULT_TARGET_LANGUAGE
    assert DEFAULTS.erase_strategy == DEFAULT_ERASE_STRATEGY
    assert DEFAULTS.font == "", "extract walks its own fallback chain"


def test_no_store_at_all_is_the_same_as_an_empty_one() -> None:
    """A window built without settings, as every widget test builds one."""
    assert load_preferences(None) == DEFAULTS
    save_preferences(None, _filled())  # and writes nothing, rather than raising


def test_every_field_round_trips() -> None:
    store = FakeStore()
    preferences = _filled()

    save_preferences(store, preferences)

    assert load_preferences(store) == preferences


def test_the_keys_are_namespaced_away_from_the_window_layout() -> None:
    store = FakeStore()
    save_preferences(store, _filled())

    assert all(key.startswith(PREFIX) for key in store.values)
    assert f"{PREFIX}source_language" in store.values


def test_one_unreadable_value_costs_that_field_and_no_other() -> None:
    """A settings file is hand-editable and outlives the version that wrote it."""
    store = FakeStore()
    save_preferences(store, _filled())
    store.values[f"{PREFIX}ocr_engine"] = "wishful"
    store.values[f"{PREFIX}erase_strategy"] = "scrub"
    store.values[f"{PREFIX}image_format"] = "bmp"

    loaded = load_preferences(store)

    assert loaded.ocr_engine == DEFAULTS.ocr_engine
    assert loaded.erase_strategy == DEFAULTS.erase_strategy
    assert loaded.image_format == DEFAULTS.image_format
    assert loaded.source_language == "ja", "the rest of the file still stands"
    assert loaded.font == "Marker Felt"


def test_a_value_that_is_not_even_a_string_falls_back() -> None:
    """QSettings hands back whatever the file held, which need not be text."""
    store = FakeStore({f"{PREFIX}source_language": 42, f"{PREFIX}font": None})

    loaded = load_preferences(store)

    assert loaded.source_language == DEFAULTS.source_language
    assert loaded.font == ""


def test_blank_is_stored_and_read_back_as_blank() -> None:
    """Empty means "the behaviour you get without this setting", everywhere."""
    store = FakeStore()
    save_preferences(store, Preferences(font="", image_format="", output_directory=""))

    loaded = load_preferences(store)

    assert loaded.font == ""
    assert loaded.image_format == ""
    assert loaded.output_directory == ""


def test_whitespace_around_a_hand_typed_value_is_not_a_value() -> None:
    store = FakeStore({f"{PREFIX}source_language": "  ", f"{PREFIX}target_language": " de "})

    loaded = load_preferences(store)

    assert loaded.source_language == DEFAULTS.source_language
    assert loaded.target_language == "de"


def test_preferences_are_frozen_so_the_shared_default_cannot_be_edited() -> None:
    import dataclasses

    assert dataclasses.is_dataclass(Preferences)
    assert Preferences.__dataclass_params__.frozen  # type: ignore[attr-defined]
