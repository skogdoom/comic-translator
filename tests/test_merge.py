"""Carrying hand work across a re-extraction."""

from __future__ import annotations

from dataclasses import replace

import pytest

from comictrans.model import (
    Box,
    Color,
    Geometry,
    Plan,
    PlanHeader,
    PlanImage,
    Region,
    TextCase,
)
from comictrans.planfile.merge import has_hand_work, merge_plans
from comictrans.planfile.schema import PLAN_VERSION

from .conftest import make_plan


def _header() -> PlanHeader:
    return PlanHeader(
        version=PLAN_VERSION,
        generator="test",
        created="now",
        source_language="it",
        target_language="en",
        ocr_engine="fake",
        font="Comic Sans MS",
        case=TextCase.UPPER,
        font_size_min_ratio=0.012,
        condense_min=0.9,
    )


def _region(region_id: str, box: Box, source: str = "CIAO", image: str = "p.png") -> Region:
    return Region(
        id=region_id,
        image=image,
        order=1,
        geometry=Geometry.EXACT,
        polygon=box.as_polygon(),
        fill_color=Color(250, 250, 250),
        text_color=Color(20, 20, 20),
        confidence=0.9,
        source_text=source,
        translation=source,
    )


def _plan(*regions: Region) -> Plan:
    return make_plan(_header(), regions)


BOX = Box(100, 100, 300, 200)
NUDGED = Box(104, 98, 305, 203)  # the same balloon, detected a little differently


def test_an_edited_translation_survives_a_re_detection() -> None:
    old = replace(_region("page-001", BOX), translation="HELLO EVERYONE")
    fresh = _region("page-007", NUDGED)  # renumbered by a detection change

    merged, report = merge_plans(_plan(old), _plan(fresh))

    assert merged.regions[0].id == "page-007", "the fresh id is kept"
    assert merged.regions[0].translation == "HELLO EVERYONE"
    assert report.carried == ("page-007",)
    assert report.dropped == ()


def test_notes_skip_and_font_overrides_survive() -> None:
    old = replace(
        _region("page-001", BOX),
        notes="check the surname",
        skip=True,
        font="Chalkboard SE",
        font_size=26,
    )
    merged, _ = merge_plans(_plan(old), _plan(_region("page-001", NUDGED)))

    kept = merged.regions[0]
    assert kept.notes == "check the surname"
    assert kept.skip
    assert kept.font == "Chalkboard SE"
    assert kept.font_size == 26


def test_measured_values_come_from_the_fresh_run() -> None:
    # Re-extracting is how you pick up better detection; carrying the old
    # geometry or colours across would defeat the point.
    old = replace(_region("page-001", BOX, source="OLD OCR"), translation="EDITED")
    fresh = replace(
        _region("page-001", NUDGED, source="BETTER OCR"),
        fill_color=Color(1, 2, 3),
        confidence=0.42,
    )
    merged, _ = merge_plans(_plan(old), _plan(fresh))

    kept = merged.regions[0]
    assert kept.translation == "EDITED"
    assert kept.source_text == "BETTER OCR"
    assert kept.polygon == fresh.polygon
    assert kept.fill_color == Color(1, 2, 3)
    assert kept.confidence == 0.42


def test_a_seeded_translation_is_not_treated_as_hand_work() -> None:
    # extract seeds translation with source_text. Carrying that across would
    # pin the old OCR into the new plan and hide the better read.
    old = _region("page-001", BOX, source="OLD OCR")
    fresh = _region("page-001", NUDGED, source="BETTER OCR")

    merged, report = merge_plans(_plan(old), _plan(fresh))

    assert merged.regions[0].translation == "BETTER OCR"
    assert report.carried == ()


def test_a_deliberately_cleared_translation_is_hand_work() -> None:
    # Blanking a translation is how you tell apply to leave a balloon alone.
    old = replace(_region("page-001", BOX), translation="")
    merged, report = merge_plans(_plan(old), _plan(_region("page-001", NUDGED)))
    assert merged.regions[0].translation == ""
    assert report.carried == ("page-001",)


def test_hand_work_with_no_match_is_reported_as_lost() -> None:
    old = replace(_region("page-001", Box(10, 10, 60, 60)), translation="NOWHERE TO GO")
    merged, report = merge_plans(_plan(old), _plan(_region("page-001", BOX)))

    assert report.dropped == ("page-001",)
    assert report.added == ("page-001",)
    assert merged.regions[0].translation != "NOWHERE TO GO"


def test_an_untouched_region_with_no_match_is_not_reported_as_lost() -> None:
    old = _region("page-001", Box(10, 10, 60, 60))  # seeded only, never edited
    _, report = merge_plans(_plan(old), _plan(_region("page-001", BOX)))
    assert report.dropped == ()


def test_regions_are_matched_one_to_one() -> None:
    # Two old regions cannot both claim the same fresh one; the better overlap
    # wins and the loser is reported rather than silently merged in.
    near = replace(_region("near", BOX), translation="BEST MATCH")
    far = replace(_region("far", Box(120, 120, 320, 220)), translation="ALSO CLOSE")
    merged, report = merge_plans(_plan(near, far), _plan(_region("fresh", BOX)))

    assert merged.regions[0].translation == "BEST MATCH"
    assert report.dropped == ("far",)


def test_regions_on_different_images_never_match() -> None:
    old = replace(_region("a", BOX, image="one.png"), translation="EDITED")
    fresh = _region("b", BOX, image="two.png")
    merged, report = merge_plans(_plan(old), _plan(fresh))

    assert merged.regions[0].translation != "EDITED"
    assert report.dropped == ("a",)


def test_barely_overlapping_regions_do_not_match() -> None:
    old = replace(_region("a", Box(100, 100, 300, 200)), translation="EDITED")
    sliver = _region("b", Box(290, 195, 490, 295))
    _, report = merge_plans(_plan(old), _plan(sliver))
    assert report.dropped == ("a",)


def test_the_overlap_threshold_is_configurable() -> None:
    old = replace(_region("a", Box(100, 100, 300, 200)), translation="EDITED")
    loose = _region("b", Box(180, 100, 380, 200))  # IoU 0.43
    assert merge_plans(_plan(old), _plan(loose))[1].dropped == ("a",)
    assert merge_plans(_plan(old), _plan(loose), min_iou=0.3)[1].carried == ("b",)


def test_the_fresh_header_is_kept() -> None:
    old = _plan(_region("a", BOX))
    fresh = make_plan(
        replace(_header(), font="Marker Felt", target_language="sv"),
        (_region("a", NUDGED),),
    )
    merged, _ = merge_plans(old, fresh)
    assert merged.header.font == "Marker Felt"
    assert merged.header.target_language == "sv"


@pytest.mark.parametrize(
    ("field", "value"),
    [("notes", "note"), ("skip", True), ("font", "X"), ("font_size", 12)],
)
def test_has_hand_work_covers_every_editable_field(field: str, value: object) -> None:
    plain = _region("a", BOX)
    assert not has_hand_work(plain)
    assert has_hand_work(replace(plain, **{field: value}))  # type: ignore[arg-type]


def test_the_pages_come_from_the_fresh_run() -> None:
    # Which files exist and what they hash to is measured, not hand work, so
    # a re-extraction's answer replaces the old plan's.
    gone = PlanImage(name="removed.png", sha256="a" * 64)
    blank = PlanImage(name="blank.png", sha256="b" * 64)
    old = make_plan(_header(), (_region("a", BOX),), extra_images=(gone,))
    fresh = make_plan(_header(), (_region("a", NUDGED),), extra_images=(blank,))

    merged, _ = merge_plans(old, fresh)

    assert merged.image_names() == ("p.png", "blank.png")


def test_re_extracting_keeps_a_hand_made_page_order() -> None:
    """The pages are measured again; the order they are in was a decision.

    ``extract`` lists pages in whatever order the directory scan produced,
    so without this a re-extraction would silently undo every drag someone
    did in the page list.
    """
    order = ("c.png", "a.png", "b.png")
    previous = make_plan(
        _header(),
        tuple(_region(f"r-{name}", Box(0, 0, 10, 10), image=name) for name in order),
    )
    fresh = make_plan(
        _header(),
        tuple(
            _region(f"r-{name}", Box(0, 0, 10, 10), image=name)
            for name in ("a.png", "b.png", "c.png")
        ),
    )

    merged, _report = merge_plans(previous, fresh)

    assert merged.image_names() == order
    assert [region.image for region in merged.regions] == list(order), (
        "regions follow the pages, or walking region by region and the page "
        "list would disagree about what comes next"
    )


def test_a_page_the_previous_plan_never_had_goes_after_the_ones_it_did() -> None:
    """A new page has no place in an order nobody has put it in yet."""
    previous = make_plan(
        _header(),
        (
            _region("r-b", Box(0, 0, 10, 10), image="b.png"),
            _region("r-a", Box(0, 0, 10, 10), image="a.png"),
        ),
    )
    fresh = make_plan(
        _header(),
        tuple(
            _region(f"r-{name[0]}", Box(0, 0, 10, 10), image=name)
            for name in ("a.png", "b.png", "new.png")
        ),
    )

    merged, _report = merge_plans(previous, fresh)

    assert merged.image_names() == ("b.png", "a.png", "new.png")


def test_re_extracting_still_takes_the_pages_themselves_from_the_fresh_run() -> None:
    """Order is carried; membership and hashes are measured, not carried."""
    previous = make_plan(
        _header(),
        (_region("r-gone", Box(0, 0, 10, 10), image="gone.png"),),
        digests={"gone.png": "0" * 64},
    )
    fresh = make_plan(
        _header(),
        (_region("r-a", Box(0, 0, 10, 10), image="a.png"),),
        digests={"a.png": "f" * 64},
    )

    merged, _report = merge_plans(previous, fresh)

    assert merged.image_names() == ("a.png",), "a page that is gone is gone"
    assert merged.sha256_for("a.png") == "f" * 64, "and the hash is this run's"
