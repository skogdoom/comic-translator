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

DEFAULT_SOURCE_LANGUAGE = "it"
"""Language the pages are lettered in. Recorded in the plan file and used as
the OCR language unless one is given explicitly."""

DEFAULT_TARGET_LANGUAGE = "en"
"""Language the translations are written in. Recorded in the plan file and
used to pick a hyphenation dictionary."""

DEFAULT_LANGUAGES = (DEFAULT_SOURCE_LANGUAGE,)

DEFAULT_MAX_CONTOUR_AREA_RATIO = 0.25
"""A contour larger than this fraction of the page is a panel, not a balloon."""

DEFAULT_MIN_SOLIDITY = 0.55
"""Minimum contour area / convex hull area for a balloon.

Measured against the fixture pages: a burst balloon with a spiked outline
comes in at 0.64, and 0.80 rejected it. Lowering it to 0.55 changed nothing
else on any fixture — the guards that actually catch artwork escapes are the
area cap and the extent ratio, not solidity."""

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

    The two thresholds that actually catch artwork escapes on real pages are
    ``max_contour_area_ratio`` (a contour covering a quarter of the page is a
    panel) and ``max_extent_ratio`` (a contour spanning the page is a band of
    art). ``min_solidity`` is the loosest of the three on purpose: balloons
    are only roughly convex, and burst and scalloped outlines are not.
    """

    max_contour_area_ratio: float = DEFAULT_MAX_CONTOUR_AREA_RATIO
    """A contour larger than this fraction of the page is not a balloon."""

    min_contour_area_slack: float = 1.15
    """A contour must be at least this much larger than the text it holds."""

    min_solidity: float = DEFAULT_MIN_SOLIDITY
    """contour area / convex hull area. A burst balloon measures 0.64."""

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


DEFAULT_LINE_SPACING = 1.15


@dataclass(frozen=True, slots=True)
class TypesetConfig:
    """Fitting translated text into a region's polygon.

    The fit strategy is fixed by the spec and deliberately not reorderable:
    shrink to the minimum readable size, then condense horizontally to the
    floor, then fail and name the region. Never condense past the floor, and
    never overflow silently.
    """

    line_spacing: float = DEFAULT_LINE_SPACING
    padding_ratio: float = 0.05
    """Inset from the polygon edge, as a fraction of its smaller extent, so
    text does not sit against the balloon outline."""

    font_size_min_ratio: float = DEFAULT_FONT_SIZE_MIN_RATIO
    max_size_ratio: float = 0.6
    """Cap on font size as a fraction of the polygon's height."""

    condense_min: float = DEFAULT_CONDENSE_MIN
    condense_step: float = 0.02
    hyphenate: bool = True
    hyphenation_language: str = DEFAULT_TARGET_LANGUAGE
    """Which hyphenation dictionary to use. Apply sets this from the plan
    file's ``target_language``; a language pyphen has no dictionary for
    simply disables hyphenation, with a warning."""


DEFAULT_ERASE_STRATEGY = "flat"
"""Mask the glyphs and repaint them with the region's fill colour."""


@dataclass(frozen=True, slots=True)
class EraseConfig:
    """Removing the original lettering.

    Glyph pixels are found by colour distance to the region's recorded
    ``text_color``, relative to how far that sits from ``fill_color``. Both
    came from the source page at extract time, so this needs no detection and
    no OCR — apply stays deterministic.
    """

    strategy: str = DEFAULT_ERASE_STRATEGY
    """``flat`` fills masked pixels with the region's fill colour. ``inpaint``
    reconstructs them from the surrounding pixels, for textured balloons and
    borderless captions. ``polygon`` floods the whole interior."""

    glyph_threshold_ratio: float = 0.5
    """A pixel is ink when it is closer to ``text_color`` than this fraction
    of the distance between the text and fill colours. Scale-free, so it works
    for light-on-dark as well as the usual way round."""

    dilate_ratio: float = 0.0012
    """Grow the glyph mask by this fraction of image height, to catch the
    antialiased fringe a colour test leaves behind."""

    inpaint_radius_ratio: float = 0.003


@dataclass(frozen=True, slots=True)
class ApplyConfig:
    """Everything the apply pass needs."""

    typeset: TypesetConfig = field(default_factory=TypesetConfig)
    erase: EraseConfig = field(default_factory=EraseConfig)
