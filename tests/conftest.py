"""Shared fixtures.

The synthetic pages here are deliberately crude but structurally honest: a
balloon is a filled shape with an outline, and "glyphs" are drawn as marks
with gaps between them, because ink covering a minority of a text box is what
the colour sampler relies on to tell text from balloon.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator, Sequence
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from comictrans.config import OcrConfig
from comictrans.imaging import PageImage
from comictrans.model import Box
from comictrans.ocr.base import OcrLine

ART_LIGHT = (200, 200, 200)
ART_DARK = (90, 90, 90)
BALLOON_WHITE = (250, 250, 250)
INK_BLACK = (20, 20, 20)

_MARK_WIDTH = 6
_MARK_PITCH = 18


def draw_glyph_marks(draw: ImageDraw.ImageDraw, box: Box, color: tuple[int, int, int]) -> None:
    """Fill a text box with ink marks that leave gaps, the way real text does."""
    for x in range(box.left, box.right - _MARK_WIDTH, _MARK_PITCH):
        draw.rectangle([x, box.top, x + _MARK_WIDTH, box.bottom - 1], fill=color)


def make_page_array(
    size: tuple[int, int],
    background: tuple[int, int, int],
    balloons: Sequence[tuple[str, Box, tuple[int, int, int], tuple[int, int, int], Sequence[Box]]],
) -> np.ndarray:
    """Render a synthetic page.

    Each balloon is ``(kind, bounds, fill, ink, text_boxes)`` where ``kind`` is
    ``"ellipse"``, ``"rect"``, or ``"none"`` (glyphs drawn straight onto art).
    """
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    for kind, bounds, fill, ink, text_boxes in balloons:
        shape = [bounds.left, bounds.top, bounds.right, bounds.bottom]
        if kind == "ellipse":
            draw.ellipse(shape, fill=fill, outline=ink, width=3)
        elif kind == "rect":
            draw.rectangle(shape, fill=fill, outline=ink, width=3)
        for box in text_boxes:
            draw_glyph_marks(draw, box, ink)
    return np.asarray(image, dtype=np.uint8)


def save_page(array: np.ndarray, path: Path, **info: object) -> Path:
    Image.fromarray(array).save(path, **info)
    return path


def lines_for(boxes: Sequence[Box], text: Sequence[str], confidence: float = 0.9) -> list[OcrLine]:
    return [
        OcrLine(text=value, box=box, confidence=confidence)
        for box, value in zip(boxes, text, strict=True)
    ]


class FakeRecognizer:
    """A TextRecognizer that replays canned lines, keyed by filename."""

    name = "fake"

    def __init__(self, by_filename: dict[str, list[OcrLine]]) -> None:
        self.by_filename = by_filename
        self.calls: list[Path] = []

    def recognize(self, page: PageImage, config: OcrConfig) -> list[OcrLine]:
        self.calls.append(page.path)
        return list(self.by_filename.get(page.path.name, []))


@pytest.fixture
def balloon_page() -> tuple[np.ndarray, list[Box]]:
    """A white speech balloon on dark art, with two lines of text in it."""
    boxes = [Box(160, 140, 360, 164), Box(160, 180, 340, 204)]
    array = make_page_array(
        (600, 800),
        ART_DARK,
        [("ellipse", Box(120, 100, 420, 260), BALLOON_WHITE, INK_BLACK, boxes)],
    )
    return array, boxes


@pytest.fixture
def inverted_caption_page() -> tuple[np.ndarray, list[Box]]:
    """A white-on-black caption box on light art."""
    boxes = [Box(160, 140, 360, 164)]
    array = make_page_array(
        (600, 800),
        ART_LIGHT,
        [("rect", Box(130, 110, 400, 200), INK_BLACK, BALLOON_WHITE, boxes)],
    )
    return array, boxes


@pytest.fixture
def borderless_page() -> tuple[np.ndarray, list[Box]]:
    """Text sitting directly on artwork, with nothing enclosing it."""
    boxes = [Box(160, 140, 360, 164)]
    array = make_page_array(
        (600, 800), ART_LIGHT, [("none", Box(0, 0, 1, 1), ART_LIGHT, INK_BLACK, boxes)]
    )
    return array, boxes


FONT_ROOTS = (
    Path("/System/Library/Fonts"),  # macOS, including Supplemental/
    Path("/Library/Fonts"),
    Path("/usr/share/fonts"),  # Linux
)

# Known regular/bold pairs, macOS first since that is the target platform.
FONT_PAIRS = (
    ("Arial.ttf", "Arial Bold.ttf"),
    ("Verdana.ttf", "Verdana Bold.ttf"),
    ("Georgia.ttf", "Georgia Bold.ttf"),
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf"),
    ("FreeSans.ttf", "FreeSansBold.ttf"),
)

_BOLD_SUFFIXES = (" Bold", "-Bold", "bd")


def _find_font_pair() -> tuple[Path, Path] | None:
    """A regular/bold TTF pair from the host system, for font-resolution tests.

    No font ships with this repository, so the tests borrow one. The named
    pairs cover macOS and the usual Linux distributions; the sibling scan is
    the fallback for anything else.
    """
    roots = [root for root in FONT_ROOTS if root.is_dir()]
    for regular_name, bold_name in FONT_PAIRS:
        for root in roots:
            regular = next(root.rglob(regular_name), None)
            bold = next(root.rglob(bold_name), None)
            if regular is not None and bold is not None:
                return regular, bold

    for root in roots:
        for regular in sorted(root.rglob("*.ttf")):
            for suffix in _BOLD_SUFFIXES:
                bold = regular.with_name(f"{regular.stem}{suffix}{regular.suffix}")
                if bold.is_file():
                    return regular, bold
    return None


@pytest.fixture
def font_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A fake macOS font directory on COMICTRANS_FONT_PATH.

    Real system fonts are copied under Apple's filenames so resolution can be
    exercised off a Mac. Nothing is bundled with the tool; if the host has no
    usable font pair, these tests skip.
    """
    pair = _find_font_pair()
    if pair is None:
        searched = ", ".join(str(root) for root in FONT_ROOTS)
        pytest.skip(f"no regular/bold TTF pair found under {searched}")
    regular, bold = pair
    directory = tmp_path / "fonts"
    directory.mkdir()
    shutil.copy(regular, directory / "Comic Sans MS.ttf")
    shutil.copy(bold, directory / "Comic Sans MS Bold.ttf")
    shutil.copy(regular, directory / "Marker Felt.ttf")  # regular only, no bold
    monkeypatch.setenv("COMICTRANS_FONT_PATH", str(directory))
    yield directory
