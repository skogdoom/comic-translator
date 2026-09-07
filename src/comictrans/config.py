"""Tunables.

Everything here is a plain dataclass built from CLI flags and passed down
explicitly; no module-level mutable state, so a test can dial one knob without
touching the rest of the pipeline.

Ratios rather than pixel constants throughout: a 300 dpi scan and a 600 dpi
scan of the same page should detect the same regions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_FONT_SIZE_MIN_RATIO = 0.012
"""Smallest legible glyph height, as a fraction of image height."""

DEFAULT_CONDENSE_MIN = 0.9
"""Hard floor on horizontal condensing. Never lower this by accident."""

DEFAULT_CONFIDENCE_THRESHOLD = 0.5
"""Below this, a region is still written out but flagged ``low_confidence``."""

DEFAULT_LANGUAGES = ("it-IT",)

DEFAULT_MAX_CONTOUR_AREA_RATIO = 0.25
"""A contour larger than this fraction of the page is a panel, not a balloon."""

DEFAULT_MIN_SOLIDITY = 0.80
"""Minimum contour area / convex hull area for a balloon."""

DEFAULT_MAX_EXTENT_RATIO = 0.75
"""Largest share of the page a region may span in either direction."""


@dataclass(frozen=True, slots=True)
class OcrConfig:
    """OCR stage settings."""

    languages: tuple[str, ...] = DEFAULT_LANGUAGES
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    engine: str = "auto"
    """``auto`` tries Apple Vision then Tesseract; or name one explicitly."""

    minimum_text_height_ratio: float = 0.004
    """Discard OCR boxes shorter than this; they are scan speckle, not text."""


@dataclass(frozen=True, slots=True)
class DetectConfig:
    """Balloon-contour search and region assembly.

    The two thresholds that matter most on real scans are
    ``max_contour_area_ratio`` (stops a broken balloon outline from selecting
    the whole panel) and ``min_solidity`` (a balloon is convex-ish; a panel
    full of art is not).
    """

    max_contour_area_ratio: float = DEFAULT_MAX_CONTOUR_AREA_RATIO
    """A contour larger than this fraction of the page is not a balloon."""

    min_contour_area_slack: float = 1.15
    """A contour must be at least this much larger than the text it holds."""

    min_solidity: float = DEFAULT_MIN_SOLIDITY
    """contour area / convex hull area."""

    max_fill_ratio: float = 0.90
    """Text must not fill more than this share of its balloon."""

    max_extent_ratio: float = DEFAULT_MAX_EXTENT_RATIO
    """A balloon must not span more than this share of the page in either
    direction. Area alone does not catch a band of flat artwork that happens
    to hold a caption — a sand or sky band is thin enough to pass the area
    test while running the full width of the page. Measured balloons sit at
    0.25-0.45; escapes sit near 0.9."""

    approx_epsilon_ratio: float = 0.004
    """approxPolyDP epsilon as a fraction of contour perimeter."""

    max_polygon_points: int = 64
    fallback_margin_ratio: float = 0.008
    """Margin added around the union of OCR boxes when no contour is found."""

    line_gap_ratio: float = 1.6
    """Max vertical gap between lines of one utterance, in line heights."""

    order_band_ratio: float = 0.6
    """Reading-order rows: regions whose tops differ by less than this many
    median region heights count as the same row and sort left-to-right."""

    close_kernel_ratio: float = 0.002
    """Morphological close before contour search, as a fraction of page height.
    Bridges the gaps halftone screening punches in a balloon outline."""


@dataclass(frozen=True, slots=True)
class ExtractConfig:
    """Everything the extract pass needs."""

    ocr: OcrConfig = field(default_factory=OcrConfig)
    detect: DetectConfig = field(default_factory=DetectConfig)
    font_size_min_ratio: float = DEFAULT_FONT_SIZE_MIN_RATIO
    condense_min: float = DEFAULT_CONDENSE_MIN
