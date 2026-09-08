from __future__ import annotations

import numpy as np
import pytest

from comictrans.config import EraseConfig
from comictrans.erase import STRATEGIES, erase, get_strategy, glyph_mask, polygon_mask
from comictrans.model import Box, Color, Geometry, Region

PAGE = 400


def _region(fill: Color, text: Color, polygon: object = None) -> Region:
    return Region(
        id="page-001",
        image="page.png",
        order=1,
        geometry=Geometry.EXACT,
        polygon=polygon or Box(100, 100, 300, 300).as_polygon(),  # type: ignore[arg-type]
        fill_color=fill,
        text_color=text,
        confidence=0.9,
        source_text="CIAO",
        translation="HELLO",
    )


def _page(
    background: tuple[int, int, int], balloon: tuple[int, int, int], ink: tuple[int, int, int]
) -> np.ndarray:
    page = np.full((PAGE, PAGE, 3), background, dtype=np.uint8)
    page[100:300, 100:300] = balloon
    page[190:210, 130:270] = ink  # a bar of lettering
    return page


def test_glyph_mask_finds_the_lettering_and_nothing_else() -> None:
    page = _page((60, 160, 60), (250, 250, 250), (20, 20, 20))
    region = _region(Color(250, 250, 250), Color(20, 20, 20))
    mask = glyph_mask(page, region, EraseConfig(), page_height=PAGE)

    assert mask[200, 200] > 0, "ink is masked"
    assert mask[150, 200] == 0, "clean balloon is not"
    assert mask[50, 50] == 0, "artwork outside the polygon is never touched"


def test_glyph_mask_works_for_light_text_on_a_dark_balloon() -> None:
    page = _page((200, 200, 200), (20, 20, 20), (250, 250, 250))
    region = _region(Color(20, 20, 20), Color(250, 250, 250))
    mask = glyph_mask(page, region, EraseConfig(), page_height=PAGE)

    assert mask[200, 200] > 0
    assert mask[150, 200] == 0


def _outlined_balloon(stroke: int) -> np.ndarray:
    """A white balloon with its own dark outline, on dark artwork."""
    page = np.full((PAGE, PAGE, 3), (60, 160, 60), dtype=np.uint8)
    page[100:300, 100:300] = (20, 20, 20)  # the outline
    page[104:296, 104:296] = (250, 250, 250)  # the interior inside it
    page[190 : 190 + stroke, 130:270] = (20, 20, 20)  # a line of lettering
    return page


def test_glyph_mask_leaves_the_balloon_outline_alone() -> None:
    # A polygon is grown until it covers every line of text assigned to it,
    # and it grows by union with the lines' bounding boxes, whose corners
    # stick out past the glyphs. Where a corner crosses the balloon's own
    # outline, a mask bounded only by the polygon takes the outline for
    # lettering, and the flat fill repaints it — measured on a real page as
    # notches cut out of two balloons.
    page = _outlined_balloon(stroke=3)
    # The balloon's interior with one step-out over the outline, which is the
    # shape covering a line of text produces: a union with its bounding box.
    steps_out = (
        (104, 104),
        (150, 104),
        (150, 98),
        (250, 98),
        (250, 104),
        (296, 104),
        (296, 296),
        (104, 296),
    )
    region = _region(Color(250, 250, 250), Color(20, 20, 20), steps_out)

    mask = glyph_mask(page, region, EraseConfig(), page_height=PAGE)

    assert mask[191, 200] > 0, "the lettering is still erased"
    assert mask[102, 200] == 0, "the balloon's own outline is not"
    assert mask[200, 102] == 0, "nor is it down the side"


def test_glyph_mask_keeps_erasing_when_the_ground_cannot_be_read() -> None:
    # The ground is worked out by closing the lettering back into the flat
    # fill, which needs a kernel wider than a glyph's stroke. Where the stroke
    # is wider than that, the lettering stays a hole and the ground excludes
    # it. Erasing nothing would leave the original text under the translation,
    # which is worse than the notch the ground test exists to prevent, so the
    # mask falls back to the polygon alone.
    page = _outlined_balloon(stroke=40)
    region = _region(Color(250, 250, 250), Color(20, 20, 20))

    mask = glyph_mask(page, region, EraseConfig(), page_height=PAGE)

    assert mask[200, 200] > 0, "lettering must still be erased"


def test_glyph_mask_never_extends_past_the_polygon() -> None:
    page = _page((20, 20, 20), (250, 250, 250), (20, 20, 20))
    region = _region(Color(250, 250, 250), Color(20, 20, 20))
    mask = glyph_mask(page, region, EraseConfig(dilate_ratio=0.05), page_height=PAGE)
    outside = polygon_mask(region, PAGE, PAGE) == 0
    assert not mask[outside].any(), "dilation leaked outside the balloon"


def test_flat_fill_repaints_the_lettering_and_leaves_the_art() -> None:
    page = _page((60, 160, 60), (250, 250, 250), (20, 20, 20))
    region = _region(Color(250, 250, 250), Color(20, 20, 20))
    out = erase(page, region, EraseConfig(strategy="flat"), page_height=PAGE)

    assert tuple(out[200, 200]) == (250, 250, 250), "lettering gone"
    assert tuple(out[50, 50]) == (60, 160, 60), "artwork untouched"
    assert out.shape == page.shape


def test_erase_does_not_modify_its_input() -> None:
    page = _page((60, 160, 60), (250, 250, 250), (20, 20, 20))
    before = page.copy()
    erase(page, _region(Color(250, 250, 250), Color(20, 20, 20)), EraseConfig(), page_height=PAGE)
    assert np.array_equal(page, before)


def test_polygon_fill_floods_the_whole_interior() -> None:
    page = _page((60, 160, 60), (250, 250, 250), (20, 20, 20))
    region = _region(Color(255, 0, 0), Color(20, 20, 20))
    out = erase(page, region, EraseConfig(strategy="polygon"), page_height=PAGE)

    assert tuple(out[200, 200]) == (255, 0, 0)
    assert tuple(out[110, 110]) == (255, 0, 0), "the whole interior, not just glyphs"
    assert tuple(out[50, 50]) == (60, 160, 60)


def test_inpaint_reconstructs_rather_than_flat_filling() -> None:
    page = _page((60, 160, 60), (250, 250, 250), (20, 20, 20))
    region = _region(Color(250, 250, 250), Color(20, 20, 20))
    out = erase(page, region, EraseConfig(strategy="inpaint"), page_height=PAGE)

    assert tuple(out[50, 50]) == (60, 160, 60)
    assert int(out[200, 200].min()) > 200, "the dark bar is gone"


def test_identical_fill_and_text_colors_mask_nothing() -> None:
    page = _page((60, 160, 60), (250, 250, 250), (20, 20, 20))
    region = _region(Color(250, 250, 250), Color(250, 250, 250))
    mask = glyph_mask(page, region, EraseConfig(), page_height=PAGE)
    assert int(np.count_nonzero(mask)) == 0


def test_every_strategy_is_reachable_by_name() -> None:
    assert set(STRATEGIES) == {"flat", "polygon", "inpaint"}
    for name in STRATEGIES:
        assert get_strategy(name).name == name


def test_unknown_strategy_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown erase strategy"):
        get_strategy("magic")
