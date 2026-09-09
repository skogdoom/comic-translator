from __future__ import annotations

from pathlib import Path

import pytest

from comictrans.errors import InputError, PlanError
from comictrans.gui.document import (
    MANUAL_CONFIDENCE,
    UNDO_LIMIT,
    PlanDocument,
    overlapping_region_ids,
    region_flags,
)
from comictrans.model import (
    Box,
    Color,
    Erase,
    Geometry,
    PlanHeader,
    PlanImage,
    Region,
    TextCase,
)
from comictrans.planfile import load_plan, write_plan
from comictrans.planfile.schema import PLAN_VERSION
from comictrans.util import sha256_file

from .conftest import (
    ART_DARK,
    BALLOON_WHITE,
    INK_BLACK,
    make_page_array,
    make_plan,
    save_page,
)

BALLOON = Box(80, 80, 520, 320)
TEXT_BOX = Box(140, 170, 460, 210)


def _header(**overrides: object) -> PlanHeader:
    base: dict[str, object] = {
        "version": PLAN_VERSION,
        "generator": "comictrans test",
        "created": "2026-09-07T12:00:00Z",
        "source_language": "it",
        "target_language": "en",
        "ocr_engine": "fake",
        "font": "Comic Sans MS",
        "case": TextCase.UPPER,
        "font_size_min_ratio": 0.012,
        "condense_min": 0.9,
    }
    base.update(overrides)
    return PlanHeader(**base)  # type: ignore[arg-type]


def _region(image: str, **overrides: object) -> Region:
    base: dict[str, object] = {
        "id": "page-001",
        "image": image,
        "order": 1,
        "geometry": Geometry.EXACT,
        "polygon": BALLOON.as_polygon(),
        "fill_color": Color(250, 250, 250),
        "text_color": Color(20, 20, 20),
        "confidence": 0.9,
        "source_text": "CIAO A TUTTI",
        "translation": "HELLO EVERYONE",
    }
    base.update(overrides)
    return Region(**base)  # type: ignore[arg-type]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A source page and a plan beside it, ready to open."""
    source = tmp_path / "pages"
    source.mkdir()
    image = save_page(
        make_page_array(
            (600, 400), ART_DARK, [("ellipse", BALLOON, BALLOON_WHITE, INK_BLACK, [TEXT_BOX])]
        ),
        source / "page-001.png",
    )
    plan_path = source / "comic-plan.yaml"
    plan = make_plan(
        _header(),
        (
            _region("page-001.png", id="page-001-001", order=1),
            _region(
                "page-001.png",
                id="page-001-002",
                order=2,
                polygon=Box(0, 0, 10, 10).as_polygon(),
                translation="",
            ),
        ),
        {"page-001.png": sha256_file(image)},
    )
    write_plan(plan, plan_path)
    return plan_path


def test_open_loads_the_plan_and_starts_clean(project: Path) -> None:
    doc = PlanDocument.open(project)
    assert doc.path == project
    assert not doc.dirty
    assert doc.images() == ("page-001.png",)
    assert len(doc.regions_for("page-001.png")) == 2


def test_a_page_with_no_regions_is_still_a_page_of_the_document() -> None:
    # The GUI lists every page the plan covers, not every page something was
    # found on: a blank one is where a missed balloon gets drawn by hand.
    blank = PlanImage(name="page-002.png", sha256="0" * 64)
    doc = PlanDocument(
        make_plan(_header(), (_apart(1),), extra_images=(blank,)), Path("comic-plan.yaml")
    )

    assert doc.images() == ("page-001.png", "page-002.png")
    assert doc.regions_for("page-002.png") == ()
    assert doc.summary("page-002.png").region_count == 0
    assert doc.summary("page-002.png").flagged_count == 0


def test_open_propagates_plan_errors(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("not: a: plan\n", encoding="utf-8")
    with pytest.raises(PlanError):
        PlanDocument.open(bad)


def test_region_looks_up_by_id_and_raises_for_unknown(project: Path) -> None:
    doc = PlanDocument.open(project)
    assert doc.region("page-001-001").source_text == "CIAO A TUTTI"
    with pytest.raises(KeyError):
        doc.region("no-such-region")


def test_source_path_resolves_against_the_plan_directory(project: Path) -> None:
    doc = PlanDocument.open(project)
    assert doc.source_path("page-001.png") == project.parent / "page-001.png"


def test_editing_a_field_marks_the_document_dirty_and_nothing_else(project: Path) -> None:
    doc = PlanDocument.open(project)
    updated = doc.set_translation("page-001-001", "CIAO EVERYONE")
    assert doc.dirty
    assert updated.translation == "CIAO EVERYONE"
    assert doc.region("page-001-001").translation == "CIAO EVERYONE"
    # Every other field, and every other region, is untouched.
    assert doc.region("page-001-001").notes == ""
    assert doc.region("page-001-002").translation == ""


def test_each_setter_touches_only_its_own_field(project: Path) -> None:
    doc = PlanDocument.open(project)
    doc.set_notes("page-001-001", "check this balloon")
    doc.set_skip("page-001-002", True)
    doc.set_font("page-001-001", "Chalkboard SE")
    doc.set_font_size("page-001-001", 42)

    region = doc.region("page-001-001")
    assert region.notes == "check this balloon"
    assert region.font == "Chalkboard SE"
    assert region.font_size == 42
    assert region.translation == "HELLO EVERYONE"  # unchanged
    assert doc.region("page-001-002").skip is True


def test_clearing_a_font_override_falls_back_to_none(project: Path) -> None:
    doc = PlanDocument.open(project)
    doc.set_font("page-001-001", "Chalkboard SE")
    doc.set_font("page-001-001", None)
    assert doc.region("page-001-001").font is None


def test_save_writes_back_to_the_opened_path(project: Path) -> None:
    doc = PlanDocument.open(project)
    doc.set_translation("page-001-001", "EDITED")
    doc.save()
    assert not doc.dirty
    reloaded = load_plan(project, check_images=False)
    assert reloaded.regions[0].translation == "EDITED"


def test_save_as_moves_the_document_to_the_new_path(project: Path, tmp_path: Path) -> None:
    doc = PlanDocument.open(project)
    doc.set_translation("page-001-001", "EDITED")
    target = tmp_path / "elsewhere" / "plan.yaml"
    doc.save_as(target)
    assert doc.path == target
    assert not doc.dirty
    assert load_plan(target, check_images=False).regions[0].translation == "EDITED"
    # The file it was opened from is untouched.
    assert load_plan(project, check_images=False).regions[0].translation == "HELLO EVERYONE"


def test_save_as_refuses_to_clobber_without_force(project: Path, tmp_path: Path) -> None:
    doc = PlanDocument.open(project)
    other = tmp_path / "already-exists.yaml"
    write_plan(make_plan(_header(), ()), other)
    with pytest.raises(InputError, match="already exists"):
        doc.save_as(other)
    assert doc.path == project, "a refused save must not move the document"

    doc.save_as(other, force=True)
    assert doc.path == other


def test_overlapping_region_ids_ignores_non_actionable_regions() -> None:
    # Two boxes that fully coincide, but one is skipped and one is unseeded:
    # neither is ever drawn, so they cannot overlap on the page.
    box = Box(0, 0, 100, 100)
    regions = [
        _region("p.png", id="a", polygon=box.as_polygon(), skip=True),
        _region("p.png", id="b", polygon=box.as_polygon(), translation=""),
        _region("p.png", id="c", polygon=box.as_polygon()),
        _region("p.png", id="d", polygon=box.as_polygon()),
    ]
    assert overlapping_region_ids(regions) == {"c", "d"}


def test_overlapping_region_ids_needs_real_overlap_not_a_touching_edge() -> None:
    regions = [
        _region("p.png", id="a", polygon=Box(0, 0, 100, 100).as_polygon()),
        _region("p.png", id="b", polygon=Box(100, 0, 200, 100).as_polygon()),
    ]
    assert overlapping_region_ids(regions) == frozenset()


def test_region_flags_separates_skipped_from_held_back() -> None:
    skipped = _region("p.png", id="a", translation="", skip=True)
    held_back = _region("p.png", id="b", translation="", skip=False)

    skipped_flags = region_flags(skipped, overlapping_ids=frozenset())
    held_back_flags = region_flags(held_back, overlapping_ids=frozenset())

    assert skipped_flags.skipped and not skipped_flags.held_back
    assert held_back_flags.held_back and not held_back_flags.skipped
    assert not skipped_flags.any, "a deliberate skip is not itself a problem"
    assert held_back_flags.any


def test_region_flags_reports_unedited_translations() -> None:
    same_as_source = _region("p.png", source_text="CIAO", translation="CIAO")
    edited = _region("p.png", source_text="CIAO", translation="HELLO")

    assert region_flags(same_as_source, overlapping_ids=frozenset()).unedited
    assert not region_flags(edited, overlapping_ids=frozenset()).unedited


def test_summary_counts_regions_and_flags_for_one_image(project: Path) -> None:
    doc = PlanDocument.open(project)
    summary = doc.summary("page-001.png")
    assert summary.region_count == 2
    # page-001-002 is empty and not skipped: it is held back.
    assert summary.flagged_count == 1


def _document(*regions: Region) -> PlanDocument:
    """A document built straight from regions: nothing to write or open."""
    return PlanDocument(make_plan(_header(), regions), Path("comic-plan.yaml"))


def _apart(index: int, **overrides: object) -> Region:
    """A clean region whose polygon overlaps no other one built this way."""
    left = index * 200
    overrides.setdefault("id", f"r{index}")
    return _region(
        "page-001.png",
        order=index,
        polygon=Box(left, 0, left + 100, 100).as_polygon(),
        **overrides,
    )


def test_ordered_ids_is_the_plans_own_order() -> None:
    doc = _document(_apart(1), _apart(2), _apart(3))
    assert doc.ordered_ids() == ("r1", "r2", "r3")


def test_adjacent_region_walks_the_whole_plan_not_just_one_page() -> None:
    doc = _document(
        _apart(1),
        _apart(2),
        _region("page-002.png", id="r3", polygon=Box(0, 0, 100, 100).as_polygon()),
    )
    assert doc.adjacent_region("r1", forward=True) == "r2"
    assert doc.adjacent_region("r2", forward=True) == "r3", "off the end of a page, onto the next"
    assert doc.adjacent_region("r3", forward=True) is None

    assert doc.adjacent_region("r3", forward=False) == "r2"
    assert doc.adjacent_region("r1", forward=False) is None


def test_adjacent_region_from_nowhere_is_the_first_one_or_the_last() -> None:
    doc = _document(_apart(1), _apart(2))
    assert doc.adjacent_region(None, forward=True) == "r1"
    assert doc.adjacent_region(None, forward=False) == "r2"


def test_adjacent_region_can_skip_to_the_next_one_worth_checking() -> None:
    doc = _document(_apart(1), _apart(2), _apart(3, translation=""), _apart(4))
    assert doc.adjacent_region("r1", forward=True, flagged_only=True) == "r3"
    assert doc.adjacent_region("r3", forward=True, flagged_only=True) is None
    # Without the filter the one in between is not skipped.
    assert doc.adjacent_region("r1", forward=True) == "r2"


def test_adjacent_region_is_nothing_for_an_unknown_id_or_an_empty_plan() -> None:
    assert _document(_apart(1)).adjacent_region("no-such-region", forward=True) is None
    assert _document().adjacent_region(None, forward=True) is None


def test_undo_steps_back_one_edit_and_redo_puts_it_back() -> None:
    doc = _document(_apart(1))
    doc.set_translation("r1", "FIRST")
    doc.set_skip("r1", True)

    assert doc.undo()
    assert doc.region("r1").skip is False
    assert doc.region("r1").translation == "FIRST", "only the last edit came back off"

    assert doc.undo()
    assert doc.region("r1").translation == "HELLO EVERYONE"
    assert not doc.can_undo
    assert not doc.undo(), "nothing left to step back to"

    assert doc.redo()
    assert doc.region("r1").translation == "FIRST"
    assert doc.redo()
    assert doc.region("r1").skip is True
    assert not doc.can_redo


def test_typing_into_one_field_is_one_undo_step() -> None:
    """The inspector writes a keystroke at a time; undo is not per character."""
    doc = _document(_apart(1))
    for text in ("H", "HE", "HEL", "HELL", "HELLO"):
        doc.set_translation("r1", text)

    doc.undo()

    assert doc.region("r1").translation == "HELLO EVERYONE", "the whole run came back off"
    assert not doc.can_undo


def test_a_different_field_starts_a_new_undo_step() -> None:
    doc = _document(_apart(1))
    doc.set_translation("r1", "TYPED")
    doc.set_notes("r1", "NOTED")

    doc.undo()

    assert doc.region("r1").notes == ""
    assert doc.region("r1").translation == "TYPED"


def test_moving_away_and_back_is_two_undo_steps() -> None:
    doc = _document(_apart(1))
    doc.set_translation("r1", "FIRST")
    doc.end_edit_run()
    doc.set_translation("r1", "FIRST AND SECOND")

    doc.undo()

    assert doc.region("r1").translation == "FIRST", "the two runs did not merge"


def test_setting_a_field_to_what_it_already_holds_is_not_an_edit() -> None:
    doc = _document(_apart(1))
    doc.set_translation("r1", doc.region("r1").translation)

    assert not doc.dirty
    assert not doc.can_undo, "an undo step that does nothing is worse than none"


def test_undoing_back_to_the_last_save_clears_the_dirty_marker(project: Path) -> None:
    doc = PlanDocument.open(project)
    doc.set_translation("page-001-001", "EDITED")
    assert doc.dirty

    doc.undo()
    assert not doc.dirty, "back to what is on disk"

    doc.redo()
    assert doc.dirty


def test_undo_history_survives_a_save_and_going_back_past_it_is_dirty(project: Path) -> None:
    doc = PlanDocument.open(project)
    doc.set_translation("page-001-001", "SAVED TEXT")
    doc.save()
    assert not doc.dirty

    assert doc.undo(), "a save is not the end of the history"
    assert doc.region("page-001-001").translation == "HELLO EVERYONE"
    assert doc.dirty, "the file still holds the saved text"

    doc.redo()
    assert not doc.dirty, "back to exactly what was written"


def test_an_edit_after_an_undo_drops_what_was_undone() -> None:
    doc = _document(_apart(1))
    doc.set_translation("r1", "FIRST")
    doc.undo()
    assert doc.can_redo

    doc.set_notes("r1", "SOMETHING ELSE")

    assert not doc.can_redo, "the redo branch is gone once history moves on"


def test_the_history_is_capped() -> None:
    doc = _document(_apart(1))
    for index in range(UNDO_LIMIT + 50):
        doc.set_notes("r1", f"note {index}")
        doc.end_edit_run()

    assert len(doc._undo) == UNDO_LIMIT


def test_header_setters_touch_only_their_own_field(project: Path) -> None:
    doc = PlanDocument.open(project)
    doc.set_header_font("Chalkboard SE")
    doc.set_header_case(TextCase.PRESERVE)
    doc.set_header_condense_min(0.8)
    doc.set_header_target_language("sv")

    header = doc.plan.header
    assert header.font == "Chalkboard SE"
    assert header.case is TextCase.PRESERVE
    assert header.condense_min == pytest.approx(0.8)
    assert header.target_language == "sv"
    assert header.source_language == "it", "untouched"
    assert doc.dirty


def test_a_header_edit_is_undone_like_any_other(project: Path) -> None:
    doc = PlanDocument.open(project)
    doc.set_header_font("Chalkboard SE")

    assert doc.undo()

    assert doc.plan.header.font == "Comic Sans MS"
    assert not doc.dirty


def test_a_header_edit_and_a_region_edit_are_separate_undo_steps(project: Path) -> None:
    """The run key tells them apart; no region id can be None."""
    doc = PlanDocument.open(project)
    doc.set_header_font("Chalkboard SE")
    doc.set_font("page-001-001", "Marker Felt")

    doc.undo()

    assert doc.region("page-001-001").font is None
    assert doc.plan.header.font == "Chalkboard SE", "the header edit is still a step back"


def test_typing_a_header_font_is_one_undo_step(project: Path) -> None:
    doc = PlanDocument.open(project)
    for text in ("C", "Ch", "Cha", "Chalkboard SE"):
        doc.set_header_font(text)

    doc.undo()

    assert doc.plan.header.font == "Comic Sans MS"
    assert not doc.can_undo


def test_the_header_refuses_a_value_the_reader_would_reject(project: Path) -> None:
    """A rejected edit beats a plan file that will not open again."""
    doc = PlanDocument.open(project)

    for call in (
        lambda: doc.set_header_font("   "),
        lambda: doc.set_header_source_language(""),
        lambda: doc.set_header_target_language(" "),
    ):
        with pytest.raises(ValueError, match="cannot be empty"):
            call()

    with pytest.raises(ValueError, match="between"):
        doc.set_header_condense_min(0.4)  # the schema floor is 0.5
    with pytest.raises(ValueError, match="between"):
        doc.set_header_font_size_min_ratio(1.5)

    assert not doc.dirty, "nothing was recorded by any of that"


def test_a_header_edit_that_changes_nothing_is_not_an_edit(project: Path) -> None:
    doc = PlanDocument.open(project)
    doc.set_header_font(doc.plan.header.font)
    assert not doc.dirty
    assert not doc.can_undo


def test_an_edited_header_still_loads_back(project: Path, tmp_path: Path) -> None:
    """The whole point of validating: what the GUI writes, the reader reads."""
    doc = PlanDocument.open(project)
    doc.set_header_font("Chalkboard SE")
    doc.set_header_case(TextCase.PRESERVE)
    doc.set_header_font_size_min_ratio(0.02)
    doc.set_header_condense_min(0.75)
    doc.save()

    reloaded = load_plan(project, check_images=False).header
    assert reloaded.font == "Chalkboard SE"
    assert reloaded.case is TextCase.PRESERVE
    assert reloaded.font_size_min_ratio == pytest.approx(0.02)
    assert reloaded.condense_min == pytest.approx(0.75)


def test_regions_using_header_font_counts_the_ones_without_an_override(project: Path) -> None:
    doc = PlanDocument.open(project)
    assert doc.regions_using_header_font() == 2

    doc.set_font("page-001-001", "Marker Felt")

    assert doc.regions_using_header_font() == 1


# -- reshaping ---------------------------------------------------------------

SQUARE = ((10, 10), (110, 10), (110, 110), (10, 110))


def test_setting_a_polygon_records_it_as_hand_drawn_geometry() -> None:
    doc = _document(_apart(1, geometry=Geometry.APPROXIMATE))

    region = doc.set_polygon("r1", SQUARE)

    assert region.polygon == SQUARE
    assert region.geometry is Geometry.MANUAL, "someone shaped this; nothing traced it"
    assert not doc.flags("r1").approximate, "'check this' is what has just been done"
    assert doc.dirty


def test_a_reshape_is_one_undo_step_that_takes_the_geometry_back_with_it() -> None:
    doc = _document(_apart(1, geometry=Geometry.APPROXIMATE))
    before = doc.region("r1").polygon
    doc.set_polygon("r1", SQUARE)

    assert doc.undo()

    assert doc.region("r1").polygon == before
    assert doc.region("r1").geometry is Geometry.APPROXIMATE
    assert not doc.can_undo


def test_two_drags_are_two_undo_steps() -> None:
    # Unlike typing, which coalesces: each drag is a separate act, and the
    # window ends the run after every one of them.
    doc = _document(_apart(1))
    doc.set_polygon("r1", SQUARE)
    doc.end_edit_run()
    doc.set_polygon("r1", ((20, 20), (120, 20), (120, 120), (20, 120)))

    doc.undo()

    assert doc.region("r1").polygon == SQUARE


def test_polygon_coordinates_are_rounded_to_whole_pixels() -> None:
    doc = _document(_apart(1))

    region = doc.set_polygon("r1", ((10.4, 10.6), (110.5, 10.0), (110.0, 110.0)))  # type: ignore[arg-type]

    assert region.polygon == ((10, 11), (110, 10), (110, 110))
    assert all(isinstance(value, int) for point in region.polygon for value in point)


@pytest.mark.parametrize(
    ("polygon", "message"),
    [
        (((10, 10), (110, 10)), "at least 3 corners"),
        (((-1, 10), (110, 10), (110, 110)), "off the top or left"),
        (((10, 10), (110, 110), (110, 10), (10, 110)), "crosses or folds"),
    ],
)
def test_a_polygon_the_reader_would_refuse_is_refused_here(
    polygon: tuple[tuple[int, int], ...], message: str
) -> None:
    # The trap this guards: a shape the GUI accepted and saved would be a
    # plan file the GUI itself could not reopen.
    doc = _document(_apart(1))
    before = doc.region("r1").polygon

    with pytest.raises(ValueError, match=message):
        doc.set_polygon("r1", polygon)

    assert doc.region("r1").polygon == before
    assert not doc.dirty, "a refused edit is not an edit"


def test_a_reshaped_region_saves_and_reopens(project: Path) -> None:
    # The whole point of validating the edit: what the GUI writes has to be
    # something the reader will take back.
    doc = PlanDocument.open(project)
    doc.set_polygon("page-001-001", ((5, 5), (60, 5), (60, 60), (5, 60)))
    doc.save()

    reopened = PlanDocument.open(project)

    assert reopened.region("page-001-001").polygon == ((5, 5), (60, 5), (60, 60), (5, 60))
    assert reopened.region("page-001-001").geometry is Geometry.MANUAL


# -- adding and deleting ------------------------------------------------------

WHITE = Color(255, 255, 255)
BLACK = Color(0, 0, 0)


def _added(doc: PlanDocument, image: str = "page-001.png", **overrides: object) -> Region:
    return doc.add_region(
        image,
        overrides.pop("polygon", SQUARE),  # type: ignore[arg-type]
        fill_color=WHITE,
        text_color=BLACK,
        **overrides,  # type: ignore[arg-type]
    )


def test_a_drawn_region_joins_the_plan_as_hand_drawn_and_unfilled() -> None:
    doc = _document(_apart(1))

    region = _added(doc)

    assert region.geometry is Geometry.MANUAL
    assert region.polygon == SQUARE
    assert (region.source_text, region.translation) == ("", "")
    assert doc.flags(region.id).held_back, "nothing to render until it is typed in"
    assert not doc.flags(region.id).low_confidence
    assert region.confidence == MANUAL_CONFIDENCE
    assert doc.dirty


def test_a_drawn_region_is_numbered_and_ordered_after_the_page_it_joins() -> None:
    doc = _document(_apart(1, id="page-001-001"), _apart(2, id="page-001-002"))

    region = _added(doc)

    assert region.id == "page-001-003", "the page's stem, then past its highest"
    assert region.order == 3
    assert doc.ordered_ids() == ("page-001-001", "page-001-002", "page-001-003")


def test_a_deleted_regions_number_is_not_handed_to_the_next_one() -> None:
    # An id is how a region is named in a report or a note. Reusing one makes
    # those quietly wrong, so the count goes forward even over a gap.
    doc = _document(_apart(1, id="page-001-001"), _apart(2, id="page-001-002"))
    doc.delete_region("page-001-002")

    region = _added(doc)

    assert region.id == "page-001-003"

    doc.delete_region("page-001-003")

    assert _added(doc).id == "page-001-004", "not back over the one just deleted"


def test_a_drawn_region_lands_with_its_own_page_not_at_the_end_of_the_plan() -> None:
    doc = _document(
        _apart(1),
        _region("page-002.png", id="r2", polygon=Box(0, 0, 100, 100).as_polygon()),
    )

    region = _added(doc, "page-001.png")

    assert doc.ordered_ids() == ("r1", region.id, "r2"), "reading order, not arrival order"


def test_a_page_with_no_regions_can_be_drawn_on() -> None:
    # What the images list in the plan is for: a page detection found nothing
    # on is still a page, and this is the region it never got.
    blank = PlanImage(name="page-002.png", sha256="0" * 64)
    doc = PlanDocument(
        make_plan(_header(), (_apart(1),), extra_images=(blank,)), Path("comic-plan.yaml")
    )

    region = _added(doc, "page-002.png")

    assert region.id == "page-002-001"
    assert region.order == 1
    assert doc.regions_for("page-002.png") == (region,)


def test_a_region_cannot_be_added_to_a_page_the_plan_does_not_have() -> None:
    doc = _document(_apart(1))

    with pytest.raises(ValueError, match="not a page in this plan"):
        _added(doc, "page-404.png")

    assert not doc.dirty


def test_a_drawn_region_is_validated_like_a_dragged_one() -> None:
    doc = _document(_apart(1))

    with pytest.raises(ValueError, match="crosses or folds"):
        _added(doc, polygon=((10, 10), (110, 110), (110, 10), (10, 110)))

    assert len(doc.plan.regions) == 1


def test_deleting_a_region_removes_it_and_undo_puts_it_back_where_it_was() -> None:
    doc = _document(_apart(1), _apart(2), _apart(3))

    removed = doc.delete_region("r2")

    assert removed.id == "r2"
    assert doc.ordered_ids() == ("r1", "r3")
    with pytest.raises(KeyError):
        doc.region("r2")

    assert doc.undo()
    assert doc.ordered_ids() == ("r1", "r2", "r3"), "back in its own place, not on the end"
    assert not doc.dirty


def test_adding_and_deleting_are_one_undo_step_each() -> None:
    doc = _document(_apart(1))
    added = _added(doc)
    doc.delete_region("r1")

    doc.undo()
    assert doc.ordered_ids() == ("r1", added.id)
    doc.undo()
    assert doc.ordered_ids() == ("r1",)
    assert not doc.can_undo


def test_the_source_text_and_the_colours_are_editable_fields_like_any_other() -> None:
    doc = _document(_apart(1))

    doc.set_source_text("r1", "CIAO A TUTTI")
    doc.set_fill_color("r1", Color(10, 20, 30))
    doc.set_text_color("r1", Color(200, 210, 220))

    assert doc.region("r1").source_text == "CIAO A TUTTI"
    assert doc.region("r1").fill_color == Color(10, 20, 30)
    assert doc.region("r1").text_color == Color(200, 210, 220)

    doc.undo()
    assert doc.region("r1").text_color != Color(200, 210, 220)


def test_a_drawn_region_saves_and_reopens(project: Path) -> None:
    doc = PlanDocument.open(project)
    added = doc.add_region(
        "page-001.png", SQUARE, fill_color=Color(1, 2, 3), text_color=Color(250, 251, 252)
    )
    doc.set_source_text(added.id, "CIAO")
    doc.save()

    reopened = PlanDocument.open(project).region(added.id)

    assert reopened.geometry is Geometry.MANUAL
    assert reopened.polygon == SQUARE
    assert (reopened.fill_color, reopened.text_color) == (Color(1, 2, 3), Color(250, 251, 252))
    assert reopened.source_text == "CIAO"
    assert reopened.confidence == MANUAL_CONFIDENCE


def test_a_drawn_region_asks_for_the_whole_of_itself_to_be_painted() -> None:
    # Otherwise the fill colour sampled for it would reach only lettering
    # that matches text_color, and a region drawn on artwork has none.
    doc = _document(_apart(1))

    region = _added(doc)

    assert region.erase is Erase.POLYGON


def test_how_a_region_is_erased_is_an_editable_field() -> None:
    doc = _document(_apart(1))
    assert doc.region("r1").erase is None, "detected regions follow the run's flag"

    doc.set_erase("r1", Erase.NONE)
    assert doc.region("r1").erase is Erase.NONE

    doc.end_edit_run()
    doc.set_erase("r1", Erase.POLYGON)

    doc.undo()
    assert doc.region("r1").erase is Erase.NONE
    doc.undo()
    assert doc.region("r1").erase is None


# -- merging ------------------------------------------------------------------

LEFT_HALF = ((10, 10), (110, 10), (110, 110), (10, 110))
RIGHT_HALF = ((60, 10), (160, 10), (160, 110), (60, 110))


def _halves(**overrides: object) -> PlanDocument:
    """One balloon traced as two overlapping regions, which is the case."""
    return _document(
        _region(
            "page-001.png",
            id="page-001-001",
            order=1,
            polygon=LEFT_HALF,
            source_text="NON CI POSSO",
            translation="I CAN'T",
        ),
        _region(
            "page-001.png",
            id="page-001-002",
            order=2,
            polygon=RIGHT_HALF,
            source_text="CREDERE!",
            translation="BELIEVE IT!",
            **overrides,
        ),
    )


def test_merging_keeps_the_earlier_region_and_covers_both_outlines() -> None:
    doc = _halves()

    merged = doc.merge_regions("page-001-001", "page-001-002")

    assert merged.id == "page-001-001", "the older name is the one that stays"
    assert merged.order == 1
    assert doc.ordered_ids() == ("page-001-001",)
    assert merged.geometry is Geometry.MANUAL, "a person decided this shape"
    assert set(merged.polygon) == {(10, 10), (160, 10), (160, 110), (10, 110)}
    assert merged.source_text == "NON CI POSSO\nCREDERE!"
    assert merged.translation == "I CAN'T\nBELIEVE IT!"


def test_merging_is_the_same_either_way_round() -> None:
    forwards = _halves().merge_regions("page-001-001", "page-001-002")
    backwards = _halves().merge_regions("page-001-002", "page-001-001")

    assert forwards == backwards, "which one you clicked first is not a decision"


def test_a_merge_takes_the_worse_confidence_and_the_flags_that_matter() -> None:
    doc = _halves(confidence=0.4, low_confidence=True, skip=True, font="Marker Felt")

    merged = doc.merge_regions("page-001-001", "page-001-002")

    assert merged.confidence == 0.4, "as good as its worse half"
    assert merged.low_confidence
    assert not merged.skip, "one half was to be lettered, so the merged one is"
    assert merged.font == "Marker Felt", "an override on either half is kept"


def test_merging_is_one_undo_step() -> None:
    doc = _halves()
    doc.merge_regions("page-001-001", "page-001-002")

    assert doc.undo()

    assert doc.ordered_ids() == ("page-001-001", "page-001-002")
    assert doc.region("page-001-001").polygon == LEFT_HALF
    assert not doc.dirty


def test_regions_that_do_not_overlap_are_not_merged() -> None:
    # A hull across two balloons would swallow the artwork between them, and
    # erase would then paint over it.
    doc = _document(_apart(1), _apart(2))

    with pytest.raises(ValueError, match="do not overlap"):
        doc.merge_regions("r1", "r2")

    assert len(doc.plan.regions) == 2
    assert not doc.dirty


def test_a_region_is_not_merged_with_itself_or_across_pages() -> None:
    doc = _document(
        _apart(1),
        _region("page-002.png", id="r2", polygon=Box(0, 0, 100, 100).as_polygon()),
    )

    with pytest.raises(ValueError, match="with itself"):
        doc.merge_regions("r1", "r1")
    with pytest.raises(ValueError, match="different pages"):
        doc.merge_regions("r1", "r2")


def test_a_merge_can_be_handed_colours_read_from_the_merged_shape() -> None:
    doc = _halves()

    merged = doc.merge_regions(
        "page-001-001",
        "page-001-002",
        fill_color=Color(1, 2, 3),
        text_color=Color(250, 251, 252),
    )

    assert (merged.fill_color, merged.text_color) == (Color(1, 2, 3), Color(250, 251, 252))
