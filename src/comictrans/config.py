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
"""Smallest comfortably legible glyph height, as a fraction of image height."""

DEFAULT_FONT_SIZE_FLOOR_RATIO = 0.006
"""Absolute floor on glyph height, as a fraction of image height.

Text that will not fit at the readable minimum even condensed drops below it
rather than failing outright: small lettering beats an empty balloon, and the
region is reported so it can be dealt with by hand. Half the readable minimum
by default."""

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

DEFAULT_COLOR_TOLERANCE = 24.0
"""Euclidean RGB distance still counted as the same fill colour."""

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

    color_segmentation: bool = True
    """Fall back to colour when a grey threshold cannot find a region.

    Runs only for text no contour claimed, and only around that text. Fixes
    the case where a balloon or caption box differs from its surroundings in
    hue but not in brightness — a flat tan caption on artwork, a white
    balloon over near-white art."""

    color_tolerance: float = DEFAULT_COLOR_TOLERANCE
    """Euclidean RGB distance from the seed colour still counted as the same
    fill. Measured: 16 to 28 all recover the same caption box, 40 starts to
    leak into neighbouring artwork."""

    max_color_text_ratio: float = 10.0
    """Largest a colour-segmented region may be relative to the text in it.

    A balloon is drawn to fit its lettering; a flat patch of artwork that
    happens to sit under a phantom OCR line is not. Applied only on the colour
    path: a traced contour is evidence of a drawn boundary, whereas matching
    pixels are only evidence of one colour, so the colour path has to be
    warier. Measured real regions run 2.1x to 6.7x, phantom ones 11x to 33x."""

    color_window_ratio: float = 4.0
    """How far around a line of text to search, in multiples of its own box."""

    max_hull_growth: float = 1.5
    """Cap on the convex hull fallback when growing a polygon to cover its own
    text, as a multiple of the shape it replaces. Beyond this the hull is
    swallowing something it should not — a balloon's tail, the gap between two
    disjoint pieces — and the original polygon is kept instead."""

    containment_tolerance_ratio: float = 0.004
    """How far outside a contour a text box may reach and still count as
    inside, as a fraction of image height.

    Lettering often grazes the balloon outline, and the traced polygon is the
    interior *inside* that outline, so a line's box can fall a pixel or two
    short. Measured misses on a real page: one line by 1px, another by 12px
    on a 3880px page. Without the tolerance those lines break away into their
    own region and one balloon's speech arrives split in two."""

    min_interior_uniformity: float = 0.6
    """A balloon interior is one flat colour; a panel is full of artwork.

    Measured as the fraction of interior pixels within
    ``uniformity_tolerance`` of the interior's median colour. This is the only
    guard that does not scale with the page, which is what makes it work on a
    six-panel page where every panel is small enough to pass the area and
    extent caps. Measured balloons and caption boxes score 0.75-0.97; panels
    score 0.00-0.23."""

    uniformity_tolerance: float = 24.0
    """Euclidean RGB distance counted as 'the same colour' for the above."""

    same_shape_containment: float = 0.8
    """Fraction of the smaller region's polygon that must lie inside the larger
    one for the two to count as the same balloon found twice.

    A balloon is traced on both threshold polarities — as its own light
    interior and as the hole inside its dark outline — and one of those traces
    can come back truncated, cut off partway down the balloon. Measured on a
    real page: two traces of one balloon, 98% of the smaller inside the larger,
    yet only 0.64 IoU, because the smaller stopped short of the tail. Keeping
    both splits one utterance across two regions and apply then typesets both
    into the same balloon, one on top of the other.

    Containment rather than IoU because that is the invariant that holds:
    balloons do not overlap, so one polygon sitting inside another means one
    balloon, however differently the two traces end."""

    same_shape_area_ratio: float = 0.55
    """How close in size the two must also be, smaller over larger.

    Containment alone says nothing about scale, and things that are not
    balloons do enclose things that are: on a screentoned page, halftone dots
    read as text and turn a whole panel into a region, with a real caption box
    sitting wholly inside it. Merging there would hand the caption the panel's
    outline and erase the artwork around it.

    Two traces of one shape stay close in size — measured at 0.99 for a pair
    found on opposite threshold polarities, and 0.66 for a trace truncated
    partway down its balloon. A caption inside a panel measured 0.20. The gap
    is wide but not endless: a trace cut off much past halfway is left alone,
    and apply warns that the two regions overlap."""

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

    artefact_uniformity: float = 0.75
    """How flat the ground under oversized lettering must be to believe it.

    Only ever asked of a region whose lettering already dwarfs the page's, so
    it separates a genuine display caption from artwork OCR read as text.
    Measured on the fixtures: real captions that tripped the size test scored
    0.83 to 0.95, the artwork 0.00 to 0.68.

    Higher than the detection guard of the same name because it answers a
    narrower question. That one tells a balloon from a whole panel; this one
    has already been told the lettering is wrong-sized and only asks whether
    anything is drawn underneath it."""


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
    font_size_floor_ratio: float = DEFAULT_FONT_SIZE_FLOOR_RATIO
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
