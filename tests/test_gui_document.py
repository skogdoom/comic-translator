from __future__ import annotations

from pathlib import Path

import pytest

from comictrans.errors import InputError, PlanError
from comictrans.gui.document import (
    UNDO_LIMIT,
    PlanDocument,
    overlapping_region_ids,
    region_flags,
)
from comictrans.model import Box, Color, Geometry, Plan, PlanHeader, Region, TextCase
from comictrans.planfile import load_plan, write_plan
from comictrans.util import sha256_file

from .conftest import ART_DARK, BALLOON_WHITE, INK_BLACK, make_page_array, save_page

BALLOON = Box(80, 80, 520, 320)
TEXT_BOX = Box(140, 170, 460, 210)


def _header(**overrides: object) -> PlanHeader:
    base: dict[str, object] = {
        "version": 1,
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


def _region(image: str, digest: str, **overrides: object) -> Region:
    base: dict[str, object] = {
        "id": "page-001",
        "image": image,
        "image_sha256": digest,
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
    digest = sha256_file(image)
    plan = Plan(
        header=_header(),
        regions=(
            _region("page-001.png", digest, id="page-001-001", order=1),
            _region(
                "page-001.png",
                digest,
                id="page-001-002",
                order=2,
                polygon=Box(0, 0, 10, 10).as_polygon(),
                translation="",
            ),
        ),
    )
    write_plan(plan, plan_path)
    return plan_path


def test_open_loads_the_plan_and_starts_clean(project: Path) -> None:
    doc = PlanDocument.open(project)
    assert doc.path == project
    assert not doc.dirty
    assert doc.images() == ("page-001.png",)
    assert len(doc.regions_for("page-001.png")) == 2


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
    write_plan(Plan(header=_header(), regions=()), other)
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
        _region("p.png", "0" * 64, id="a", polygon=box.as_polygon(), skip=True),
        _region("p.png", "0" * 64, id="b", polygon=box.as_polygon(), translation=""),
        _region("p.png", "0" * 64, id="c", polygon=box.as_polygon()),
        _region("p.png", "0" * 64, id="d", polygon=box.as_polygon()),
    ]
    assert overlapping_region_ids(regions) == {"c", "d"}


def test_overlapping_region_ids_needs_real_overlap_not_a_touching_edge() -> None:
    regions = [
        _region("p.png", "0" * 64, id="a", polygon=Box(0, 0, 100, 100).as_polygon()),
        _region("p.png", "0" * 64, id="b", polygon=Box(100, 0, 200, 100).as_polygon()),
    ]
    assert overlapping_region_ids(regions) == frozenset()


def test_region_flags_separates_skipped_from_held_back() -> None:
    skipped = _region("p.png", "0" * 64, id="a", translation="", skip=True)
    held_back = _region("p.png", "0" * 64, id="b", translation="", skip=False)

    skipped_flags = region_flags(skipped, overlapping_ids=frozenset())
    held_back_flags = region_flags(held_back, overlapping_ids=frozenset())

    assert skipped_flags.skipped and not skipped_flags.held_back
    assert held_back_flags.held_back and not held_back_flags.skipped
    assert not skipped_flags.any, "a deliberate skip is not itself a problem"
    assert held_back_flags.any


def test_region_flags_reports_unedited_translations() -> None:
    same_as_source = _region("p.png", "0" * 64, source_text="CIAO", translation="CIAO")
    edited = _region("p.png", "0" * 64, source_text="CIAO", translation="HELLO")

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
    return PlanDocument(Plan(header=_header(), regions=regions), Path("comic-plan.yaml"))


def _apart(index: int, **overrides: object) -> Region:
    """A clean region whose polygon overlaps no other one built this way."""
    left = index * 200
    return _region(
        "page-001.png",
        "0" * 64,
        id=f"r{index}",
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
        _region("page-002.png", "1" * 64, id="r3", polygon=Box(0, 0, 100, 100).as_polygon()),
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
