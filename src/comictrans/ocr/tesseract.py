"""Tesseract fallback adapter.

Exists so the pipeline runs and can be tested off a Mac. It is measurably
worse than Vision on comic lettering; whenever it runs, that fact is logged
and recorded in the plan file header.
"""

from __future__ import annotations

import logging
import re
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

TESSERACT_NAMES: dict[str, str] = {
    "af": "afr",
    "am": "amh",
    "ar": "ara",
    "as": "asm",
    "az": "aze",
    "be": "bel",
    "bg": "bul",
    "bn": "ben",
    "bo": "bod",
    "br": "bre",
    "bs": "bos",
    "ca": "cat",
    "co": "cos",
    "cs": "ces",
    "cy": "cym",
    "da": "dan",
    "de": "deu",
    "dv": "div",
    "dz": "dzo",
    "el": "ell",
    "en": "eng",
    "eo": "epo",
    "es": "spa",
    "et": "est",
    "eu": "eus",
    "fa": "fas",
    "fi": "fin",
    "fo": "fao",
    "fr": "fra",
    "fy": "fry",
    "ga": "gle",
    "gd": "gla",
    "gl": "glg",
    "gu": "guj",
    "he": "heb",
    "hi": "hin",
    "hr": "hrv",
    "ht": "hat",
    "hu": "hun",
    "hy": "hye",
    "id": "ind",
    "is": "isl",
    "it": "ita",
    "iu": "iku",
    "ja": "jpn",
    "jv": "jav",
    "ka": "kat",
    "kk": "kaz",
    "km": "khm",
    "kn": "kan",
    "ko": "kor",
    "ky": "kir",
    "la": "lat",
    "lb": "ltz",
    "lo": "lao",
    "lt": "lit",
    "lv": "lav",
    "mi": "mri",
    "mk": "mkd",
    "ml": "mal",
    "mn": "mon",
    "mr": "mar",
    "ms": "msa",
    "mt": "mlt",
    "my": "mya",
    "ne": "nep",
    "nl": "nld",
    "oc": "oci",
    "or": "ori",
    "pa": "pan",
    "pl": "pol",
    "ps": "pus",
    "pt": "por",
    "qu": "que",
    "ro": "ron",
    "ru": "rus",
    "sa": "san",
    "sd": "snd",
    "si": "sin",
    "sk": "slk",
    "sl": "slv",
    "sq": "sqi",
    "sr": "srp",
    "su": "sun",
    "sv": "swe",
    "sw": "swa",
    "ta": "tam",
    "te": "tel",
    "tg": "tgk",
    "th": "tha",
    "ti": "tir",
    "to": "ton",
    "tr": "tur",
    "tt": "tat",
    "ug": "uig",
    "uk": "ukr",
    "ur": "urd",
    "uz": "uzb",
    "vi": "vie",
    "yi": "yid",
    "yo": "yor",
}
"""A two-letter language code -> the Tesseract data file for that language.

Generated rather than written from memory. Of the 162 ``tesseract-ocr-*``
packages Debian lists (measured 2026-09), 112 are plain language files and
106 of those are languages Qt can name — every one of them named by that
language's ISO 639-2/T code, checked name by name — and these are the 101
whose language also has a two-letter code. ``tests/test_ocr.py`` checks every entry against Qt again
wherever Qt is installed. The other five — ``ceb``, ``chr``, ``fil``,
``grc``, ``syr`` — have no two-letter code, and are typed as themselves.

Before this there were seven entries, so ``sv``, the source language of a
Swedish chapter, reached Tesseract as ``sv`` and was refused; the file is
``swe``.
"""

_BY_QUALIFIER = {
    ("zh", "hans"): "chi_sim",
    ("zh", "hant"): "chi_tra",
    ("zh", "tw"): "chi_tra",
    ("zh", "hk"): "chi_tra",
    ("zh", "mo"): "chi_tra",
    ("sr", "latn"): "srp_latn",
    ("az", "cyrl"): "aze_cyrl",
    ("uz", "cyrl"): "uzb_cyrl",
}
"""Where the script or the region picks a different file.

Chinese is two files, one per script, and Taiwan, Hong Kong and Macao write
the traditional one. Serbian, Azerbaijani and Uzbek each have a second
script beside the one their plain file reads."""

_NOT_BY_CODE = {"zh": "chi_sim", "no": "nor", "nb": "nor", "nn": "nor"}
"""The files not named by ISO 639-2/T. Tesseract's Chinese is ``chi_``, the
bibliographic code, and has no plain file, so unqualified Chinese is the
simplified one, as Qt's own default for ``zh`` is. There is one Norwegian,
``nor``, which Qt does not name at all, for all three codes."""

_TAGS = {name: code for code, name in {**TESSERACT_NAMES, **_NOT_BY_CODE}.items()} | {
    "nor": "no",
    "chi_sim": "zh-Hans",
    "chi_tra": "zh-Hant",
    "srp_latn": "sr-Latn",
    "aze_cyrl": "az-Cyrl",
    "uzb_cyrl": "uz-Cyrl",
}
"""The other way: a data file -> the tag the language fields hold."""

_NOT_LANGUAGES = frozenset({"osd", "equ"})
"""Installed, but not a language: orientation and script detection, and
equations. Script models are left out too — they list as ``script/Latin``."""

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


def tesseract_name(tag: str) -> str:
    """The Tesseract data file for a language tag: ``sv`` -> ``swe``.

    A tag is read the way a BCP-47 one is written, hyphens or underscores:
    ``zh-Hant`` and ``pt_BR`` both work. Anything whose first part is not two
    letters is taken to be Tesseract's own name already — ``chi_sim``,
    ``ita_old``, ``jpn_vert``, ``ceb`` — and passes through unchanged, as a
    two-letter code this table lacks does, for Tesseract to refuse by name.
    """
    raw = tag.strip()
    primary, *rest = (part.lower() for part in re.split(r"[-_]", raw))
    if len(primary) != 2:
        return raw.split("-")[0].lower()
    for qualifier in rest:
        if (primary, qualifier) in _BY_QUALIFIER:
            return _BY_QUALIFIER[(primary, qualifier)]
    return _NOT_BY_CODE.get(primary) or TESSERACT_NAMES.get(primary, primary)


def tesseract_languages(languages: tuple[str, ...]) -> str:
    """Language tags to Tesseract's ``-l`` argument: ``('it-IT', 'en')`` -> ``'ita+eng'``.

    In order, each once: two tags for the same file are one language to
    Tesseract.
    """
    names = [name for name in (tesseract_name(tag) for tag in languages) if name]
    return "+".join(dict.fromkeys(names)) or "eng"


def installed_languages() -> tuple[str, ...]:
    """What this machine's Tesseract has data for, as the tags the fields hold.

    ``ita`` comes back as ``it``, ``chi_tra`` as ``zh-Hant``; a file with no
    tag of its own — ``ita_old``, ``jpn_vert``, ``ceb`` — as its own name,
    which :func:`tesseract_name` hands back unchanged. Empty when Tesseract is
    not here to ask. Asking runs the binary: about 12ms warm, measured, and
    650ms the first time on a cold disk.
    """
    if not available():
        return ()
    try:
        names = pytesseract.get_languages(config="")
    except Exception:  # a binary that answers --version but not this
        log.warning("tesseract would not list its languages", exc_info=True)
        return ()
    tags = (
        _TAGS.get(name, name)
        for name in (str(name) for name in names)
        if name not in _NOT_LANGUAGES and "/" not in name
    )
    return tuple(dict.fromkeys(tags))


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
