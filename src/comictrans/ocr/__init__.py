"""OCR stage: pixels in, text lines with pixel boxes out.

The stage is an interface with two adapters. Apple Vision is the real one;
Tesseract exists so the pipeline is runnable and testable off a Mac. Whichever
runs is recorded in the plan file header, because OCR quality is the thing
you will most often want to blame.
"""

from __future__ import annotations

import logging

from ..config import OcrConfig
from ..errors import OcrUnavailableError
from .base import OcrLine, TextRecognizer

log = logging.getLogger(__name__)

__all__ = [
    "OcrLine",
    "TextRecognizer",
    "get_recognizer",
    "installed_languages",
    "reads",
    "resolved_engine",
    "unread_languages",
]


def get_recognizer(config: OcrConfig) -> TextRecognizer:
    """Pick an OCR backend.

    ``auto`` prefers Apple Vision and falls back to Tesseract, loudly. Naming a
    backend explicitly makes its absence an error rather than a downgrade.

    Tesseract is asked here whether it has every language in ``config``, and
    refused if it has not — see :func:`tesseract.check_languages`. Every
    caller asks for a recogniser before it unpacks a chapter file, so a run
    Tesseract would refuse leaves nothing behind.
    """
    from . import tesseract, vision

    engine = config.engine.lower()
    if engine in {"vision", "apple-vision"}:
        return vision.VisionRecognizer()
    if engine == "tesseract":
        tesseract.check_languages(config.languages)
        return tesseract.TesseractRecognizer()
    if engine != "auto":
        raise OcrUnavailableError(f"unknown OCR engine {config.engine!r}")

    if vision.available():
        return vision.VisionRecognizer()

    reason = vision.unavailable_reason()
    if tesseract.available():
        log.warning(
            "Apple Vision unavailable (%s); falling back to Tesseract. "
            "OCR quality will be noticeably worse on comic lettering.",
            reason,
        )
        tesseract.check_languages(config.languages)
        return tesseract.TesseractRecognizer()

    raise OcrUnavailableError(
        f"no OCR backend available. Apple Vision: {reason}. "
        f"Tesseract: {tesseract.unavailable_reason()}."
    )


def resolved_engine(engine: str) -> str:
    """Which recogniser ``engine`` means here: ``vision``, ``tesseract`` or ``""``.

    ``auto`` is whichever :func:`get_recognizer` would pick; ``""`` is
    neither being available, or a name it does not know. Nothing is run.
    """
    from . import tesseract, vision

    name = engine.lower()
    if name in {"vision", "apple-vision"}:
        return "vision"
    if name == "tesseract":
        return "tesseract"
    if name != "auto":
        return ""
    if vision.available():
        return "vision"
    return "tesseract" if tesseract.available() else ""


def installed_languages(engine: str) -> tuple[str, ...]:
    """What ``engine`` can read on this machine, as language tags, in its order.

    Tesseract's list is translated from its data-file names into the tags the
    language fields hold; Vision's is its own. Empty when the recogniser is
    not here, which is not the same as having nothing installed, and a caller
    that says which is telling the truth.
    """
    from . import tesseract, vision

    resolved = resolved_engine(engine)
    if resolved == "vision":
        return vision.supported_languages()
    if resolved == "tesseract":
        return tesseract.installed_languages()
    return ()


def reads(engine: str, tag: str, installed: tuple[str, ...]) -> bool:
    """Whether ``tag`` is one of ``installed``, as ``engine`` would take it.

    For Tesseract, whether it comes to the same data file: ``it``, ``it-IT``
    and ``ita`` all do. For Vision, whether it has a tag Vision would be
    handed for it — see :func:`vision.vision_tag` — so ``it`` is read by a
    Vision listing ``it-IT``, and ``pt-PT`` is not by one listing ``pt-BR``.
    """
    from . import tesseract, vision

    wanted = tag.strip()
    if not wanted:
        return True
    if resolved_engine(engine) == "tesseract":
        name = tesseract.tesseract_name(wanted)
        return any(tesseract.tesseract_name(have) == name for have in installed)
    return vision.vision_tag(wanted, installed) is not None


def unread_languages(recognizer_name: str, languages: tuple[str, ...]) -> tuple[str, ...]:
    """Which of ``languages`` the recogniser that ran could not read.

    Vision's only: Tesseract refuses a language it has no file for, and the
    run is refused before it starts — see :func:`get_recognizer` — while
    Vision reads on with its own defaults and says nothing — see
    :func:`vision.vision_languages`.
    """
    from . import vision

    if recognizer_name != vision.VisionRecognizer.name:
        return ()
    return vision.vision_languages(languages, vision.supported_languages())[1]
