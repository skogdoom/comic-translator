"""Apple Vision adapter (``VNRecognizeTextRequest``).

This is the only place in the codebase that knows Vision's coordinate system:
normalised 0..1, origin bottom-left. It converts to page pixels with a
top-left origin before returning, and that convention holds everywhere after.

The whole module is import-guarded so the package remains importable — and the
test suite runnable — on machines without pyobjc.

**Off the main thread.** ``review`` runs extract on a worker thread, so this
adapter is called from one. ``performRequests_error_`` is synchronous and
Apple's own guidance is to run it off the main queue, so the request side is
fine; what a Python thread does not get for free is an autorelease pool.
PyObjC does not install one per thread, and without it every Objective-C
object autoreleased in here leaks for the life of the process — forty pages
of CGImages and Vision observations. :func:`recognize` therefore opens one
around each page, which is the right granularity anyway: the pool drains when
the page is done rather than when the chapter is.
"""

from __future__ import annotations

import io
import logging
from typing import Any

from PIL import Image

from ..config import OcrConfig
from ..errors import OcrUnavailableError
from ..imaging import PageImage
from ..model import Box
from .base import OcrLine

log = logging.getLogger(__name__)

_IMPORT_ERROR: str | None = None

try:  # pragma: no cover - platform dependent
    import objc
    import Quartz
    import Vision
    from Foundation import NSData
except ImportError as exc:  # pragma: no cover - platform dependent
    objc = None
    Quartz = None
    Vision = None
    NSData = None
    _IMPORT_ERROR = str(exc)


def available() -> bool:
    """True when Vision can actually be used on this machine."""
    return Vision is not None and Quartz is not None and objc is not None


def unavailable_reason() -> str:
    return _IMPORT_ERROR or ("available" if available() else "pyobjc Vision bindings missing")


def supported_languages() -> tuple[str, ...]:
    """The language tags this Mac's Vision reads, at the level extract asks for.

    Empty off a Mac. On one, **never run here**: this was written from
    Apple's documentation of ``supportedRecognitionLanguagesAndReturnError:``
    and PyObjC's convention of handing an error out-parameter back as a
    second result, and no machine this was built on could call it. The tags
    come back region-qualified, ``it-IT`` rather than ``it``, and are kept
    exactly as Vision gives them.
    """
    if not available():
        return ()
    try:  # pragma: no cover - platform dependent
        with objc.autorelease_pool():
            request = Vision.VNRecognizeTextRequest.alloc().init()
            request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
            languages, error = request.supportedRecognitionLanguagesAndReturnError_(None)
    except Exception:  # pragma: no cover - platform dependent
        log.warning("Vision would not list its languages", exc_info=True)
        return ()
    if error is not None:  # pragma: no cover - platform dependent
        log.warning("Vision would not list its languages: %s", error)
    return tuple(str(language) for language in languages or ())  # pragma: no cover


def _cg_image(page: PageImage) -> Any:
    """Wrap the decoded page as a CGImage.

    Re-encoding to PNG in memory avoids hand-rolling CGColorSpace and row
    stride plumbing, and keeps this adapter working on any pixel layout Pillow
    can produce. The source file itself is never reopened.
    """
    buffer = io.BytesIO()
    Image.fromarray(page.rgb).save(buffer, format="PNG")
    data = NSData.dataWithBytes_length_(buffer.getvalue(), len(buffer.getvalue()))
    source = Quartz.CGImageSourceCreateWithData(data, None)
    if source is None:
        raise OcrUnavailableError(f"Core Graphics could not decode {page.path}")
    image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
    if image is None:
        raise OcrUnavailableError(f"Core Graphics produced no image for {page.path}")
    return image


def _to_pixel_box(bounding_box: Any, width: int, height: int) -> Box:
    """Vision's normalised bottom-left rect -> integer pixel box, top-left origin."""
    origin = bounding_box.origin
    size = bounding_box.size
    left = origin.x * width
    right = (origin.x + size.width) * width
    # Flip the y axis: Vision measures up from the bottom edge.
    top = (1.0 - (origin.y + size.height)) * height
    bottom = (1.0 - origin.y) * height
    left_px, top_px = round(left), round(top)
    return Box(
        left=max(0, left_px),
        top=max(0, top_px),
        right=min(width, max(round(right), left_px + 1)),
        bottom=min(height, max(round(bottom), top_px + 1)),
    )


def _fold(tag: str) -> str:
    return tag.strip().casefold().replace("_", "-")


def vision_tag(tag: str, supported: tuple[str, ...]) -> str | None:
    """Vision's own spelling of ``tag``, or ``None`` if it reads no such thing.

    The tag itself, in any case and with either separator; or, for a bare
    language with no region or script, the first tag Vision lists for that
    language: ``it`` is ``it-IT``. A tag with a region or a script matches
    only itself — ``pt-PT`` is not ``pt-BR``.
    """
    wanted = _fold(tag)
    if not wanted:
        return None
    for have in supported:
        if _fold(have) == wanted:
            return have
    # Only a bare language can match here: no language part has a hyphen in it.
    return next((have for have in supported if _fold(have).split("-")[0] == wanted), None)


def vision_languages(
    languages: tuple[str, ...], supported: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(what to hand Vision, what it does not read)``, in order.

    **Vision does not refuse a language it does not read; it ignores it** —
    reported from a Mac, where a source language Vision lacks ran with no
    error and read the page with Vision's own defaults. So nothing is left to
    it: each tag is handed over in Vision's own spelling, which is what makes
    a bare ``it`` certain to mean Italian whether or not Vision would have
    matched it, and one it lacks is not handed over at all but said.

    With nothing to check against — the list could not be had — the tags go
    through as they came, and nothing is said to be unread.
    """
    if not supported:
        return tuple(languages), ()
    handed: list[str] = []
    unread: list[str] = []
    for tag in languages:
        found = vision_tag(tag, supported)
        if found is None:
            unread.append(tag)
        elif found not in handed:
            handed.append(found)
    return tuple(handed), tuple(unread)


class VisionRecognizer:
    """``VNRecognizeTextRequest`` at accurate level, in the source language."""

    name = "apple-vision"

    def __init__(self) -> None:
        # Asked once per run, not once per page; see languages_for.
        self._supported: tuple[str, ...] | None = None
        self._said: set[str] = set()

    def languages_for(self, languages: tuple[str, ...]) -> tuple[str, ...]:
        """What to hand Vision for ``languages``, saying once what it cannot read."""
        if self._supported is None:
            self._supported = supported_languages()
        handed, unread = vision_languages(languages, self._supported)
        fresh = [tag for tag in unread if tag not in self._said]
        if fresh:
            self._said.update(fresh)
            log.warning(
                "Apple Vision cannot read %s; it reads with its own defaults instead",
                ", ".join(fresh),
            )
        return handed

    def recognize(self, page: PageImage, config: OcrConfig) -> list[OcrLine]:
        if not available():
            raise OcrUnavailableError(f"Apple Vision unavailable: {unavailable_reason()}")
        # One pool per page — see the note on threads in the module docstring.
        with objc.autorelease_pool():
            return self._recognize(page, config)

    def _recognize(self, page: PageImage, config: OcrConfig) -> list[OcrLine]:
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setRecognitionLanguages_(list(self.languages_for(config.languages)))
        request.setUsesLanguageCorrection_(True)
        request.setMinimumTextHeight_(config.minimum_text_height_ratio)

        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
            _cg_image(page), None
        )
        ok, error = handler.performRequests_error_([request], None)
        if not ok:
            raise OcrUnavailableError(f"Vision request failed on {page.path}: {error}")

        lines: list[OcrLine] = []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            candidate = candidates[0]
            text = str(candidate.string()).strip()
            if not text:
                continue
            lines.append(
                OcrLine(
                    text=text,
                    box=_to_pixel_box(observation.boundingBox(), page.width, page.height),
                    confidence=float(candidate.confidence()),
                )
            )
        log.debug("vision: %d lines on %s", len(lines), page.path.name)
        return lines
