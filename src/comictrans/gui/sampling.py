"""Colours for a region drawn by hand, measured from the page.

The one part of the review GUI that reads pixels rather than the plan file.
That is allowed here and nowhere downstream: the invariant is that *apply*
runs no detection, so that a plan file is the whole of what it needs. Before
a region exists, though, its ``fill_color`` and ``text_color`` have to come
from somewhere, and measuring them is better than guessing — a white-on-black
caption is not the unusual case, it is a page turn away.

Kept out of ``gui.document`` deliberately: that module is free of numpy and
OpenCV so it can be tested and reasoned about as a view-model. This one is
where the GUI's dependency on both is declared.
"""

from __future__ import annotations

import numpy as np

from ..detect.color import sample_colors
from ..imaging import PageImage
from ..model import Box, Color, Point, Polygon, polygon_bounds

SAMPLE_RADIUS = 1
"""Half-width of the square a picked colour is taken from, in page pixels.

A single pixel is what the pointer is on, but the pointer is on a scaled,
antialiased view of the page: land one pixel inside a balloon's outline and
you get the outline's grey. The median of a 3x3 square is the colour of the
thing you clicked, not of the edge you nearly clicked.
"""


TEXT_SAMPLE_SHARE = 0.6
"""How much of an outline's width and height the ink is looked for in.

Detection hands the sampler the OCR line boxes; a hand-drawn region has
none, so a box in the middle of the outline stands in for them. The middle,
because that is where lettering sits and because the edges of a hand-drawn
outline are exactly where it strays onto the artwork: measured on the
synthetic balloon fixture, offering the full bounding box of a rectangle
drawn around an *ellipse* returns the dark art in its corners as the text
colour (90, 90, 90) rather than the lettering's (20, 20, 20).
"""


def _ink_box(polygon: Polygon) -> Box:
    """The middle of a polygon's bounding box, where the lettering will be."""
    bounds = polygon_bounds(polygon)
    inset_x = round(bounds.width * (1.0 - TEXT_SAMPLE_SHARE) / 2)
    inset_y = round(bounds.height * (1.0 - TEXT_SAMPLE_SHARE) / 2)
    middle = Box(
        bounds.left + inset_x,
        bounds.top + inset_y,
        bounds.right - inset_x,
        bounds.bottom - inset_y,
    )
    # A region small enough that the inset eats it is one where the whole of
    # it is the middle.
    return middle if middle.width > 0 and middle.height > 0 else bounds


def sample_region_colors(page: PageImage, polygon: Polygon) -> tuple[Color, Color]:
    """``(fill_color, text_color)`` for a hand-drawn outline on this page.

    The same sampler ``extract`` uses, so a hand-drawn region's colours are
    arrived at the way every other region's were. Otsu splits what is inside
    the ink box into ink and ground: on a balloon with lettering in it that
    finds the lettering, and on an empty one the split is meaningless and the
    sampler's own contrast floor falls back to black or white — which is what
    a balloon you are about to letter wants anyway.
    """
    return sample_colors(page.rgb, polygon, (_ink_box(polygon),), page_height=page.height)


def color_at(page: PageImage, point: Point) -> Color:
    """The colour of the page around a point, for picking one by eye."""
    x, y = point
    height, width = page.rgb.shape[0], page.rgb.shape[1]
    left, right = max(0, x - SAMPLE_RADIUS), min(width, x + SAMPLE_RADIUS + 1)
    top, bottom = max(0, y - SAMPLE_RADIUS), min(height, y + SAMPLE_RADIUS + 1)
    patch = page.rgb[top:bottom, left:right]
    if patch.size == 0:
        return Color(255, 255, 255)
    median = np.median(patch.reshape(-1, 3).astype(np.float64), axis=0)
    return Color.from_rgb((int(median[0]), int(median[1]), int(median[2])))
