"""The plan file contract: keys, order, defaults, and the header comment.

Kept in one place so the reader and writer can never disagree about what a
valid plan file looks like.
"""

from __future__ import annotations

PLAN_VERSION = 3

READABLE_VERSIONS: frozenset[int] = frozenset({1, 2, 3})
"""Versions the reader accepts, as against the one it writes.

Version 2 moved each page's hash out of every region and into a top-level
``images`` list, and added ``manual`` geometry. A version 1 file carries the
same information — every region names its image and its hash — so it is
upgraded on the way in rather than refused. Refusing would orphan every plan
written before the change, translations and all.

Version 3 added the optional region key ``erase``. An older file simply does
not have it and reads unchanged, which is why — unlike the images list, whose
two shapes are mutually exclusive — the key is accepted whatever version a
file claims: a hand-edited plan that gained one without its number being
bumped is not worth refusing."""

MIN_POLYGON_POINTS = 3
"""Fewest points a polygon may have.

Here rather than in the reader alone because the review GUI edits polygons
and has to refuse exactly what the reader would: a shape it accepted and
wrote out would be a plan file the GUI itself could not reopen.
"""

FONT_SIZE_MIN_RATIO_RANGE = (0.0001, 1.0)
CONDENSE_MIN_RANGE = (0.5, 1.0)
"""What the header's two fit limits may be set to.

Named here rather than written into the reader, because the review GUI edits
these and has to offer exactly the range the reader will accept. A widget
that allowed one value more than the reader does would write a plan file it
could not then reopen.
"""

HEADER_KEY_ORDER: tuple[str, ...] = (
    "version",
    "generator",
    "created",
    "source_language",
    "target_language",
    "ocr_engine",
    "font",
    "case",
    "font_size_min_ratio",
    "condense_min",
)

IMAGE_KEY_ORDER: tuple[str, ...] = ("name", "sha256")
IMAGE_KEYS: frozenset[str] = frozenset(IMAGE_KEY_ORDER)

TOP_LEVEL_KEYS: frozenset[str] = frozenset({*HEADER_KEY_ORDER, "images", "regions"})

# Editable prose last, so the bits you actually touch sit together at the
# bottom of each region block.
REGION_KEY_ORDER: tuple[str, ...] = (
    "id",
    "image",
    "order",
    "geometry",
    "polygon",
    "fill_color",
    "text_color",
    "erase",
    "confidence",
    "low_confidence",
    "skip",
    "font",
    "font_size",
    "source_text",
    "translation",
    "notes",
)

REQUIRED_REGION_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "image",
        "order",
        "geometry",
        "polygon",
        "fill_color",
        "text_color",
        "confidence",
        "source_text",
        "translation",
    }
)

REGION_KEYS: frozenset[str] = frozenset(REGION_KEY_ORDER)

LEGACY_REGION_KEYS: frozenset[str] = REGION_KEYS | {"image_sha256"}
"""What a version 1 region may carry: the current keys plus the hash that
moved to the images list."""

FILE_HEADER_COMMENT = """\
comictrans plan file. Hand-edit this, then run: comictrans apply

  translation   what gets drawn. Seeded with the source text so you can edit
                it in place; apply reports any left identical to source_text.
                Empty means "leave this balloon alone", reported as skipped.
  skip: true    deliberately leave the art untouched, no warning.
  notes         yours; comictrans never reads or rewrites it.
  **bold**      emphasis. Renders as bold, never italic.
  polygon       pixels, origin top-left. Editing it moves the erase and
                typeset area; apply never re-runs detection.
  geometry      'approximate' means no clean balloon outline was found and
                this is a padded box around the text. Check those.
  erase         what gets painted over before the translation goes on:
                none, flat (the lettering), polygon (all of it), inpaint.
                Omitted means whatever apply's --erase says. fill_color is
                the colour it paints with, so under flat it reaches only
                the old lettering.
  images        every page this plan covers and the hash it had when extract
                read it, including pages no text was found on. Apply copies
                those through unchanged.

font, case and sizes in the header apply to every region; a region may
override font and font_size on its own.
"""
