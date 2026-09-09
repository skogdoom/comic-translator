"""What a finished run tells you, decided without a window.

The panel that shows this is a widget and is tested with the other widgets.
What is worth testing here is the judgement underneath it: which of a run's
outcomes are worth a second look, in what order, and what the line at the
top of the panel says. Both passes end in the same panel, so both are here.
"""

from __future__ import annotations

from pathlib import Path

from comictrans.apply import ApplyReport
from comictrans.extract import ExtractReport
from comictrans.gui.run_report import (
    BELOW_MINIMUM,
    CONDENSED,
    COULD_NOT_READ,
    DID_NOT_FIT,
    NO_REGIONS,
    NO_TRANSLATION,
    NOT_AN_IMAGE,
    NOT_TRANSLATED,
    PAGE_FAILED,
    extract_counts,
    extract_headline,
    extract_rows,
    render_counts,
    render_headline,
    render_rows,
)
from comictrans.render import RegionOutcome

OUT = Path("/tmp/rendered")


# -- rendering ---------------------------------------------------------


def _report(**overrides: object) -> ApplyReport:
    base: dict[str, object] = {
        "pages_written": [OUT / "page-001.png"],
        "outcomes": [],
        "page_failures": [],
    }
    base.update(overrides)
    return ApplyReport(**base)  # type: ignore[arg-type]


def test_a_run_with_nothing_wrong_lists_nothing() -> None:
    report = _report(outcomes=[("page-001.png", RegionOutcome("a", "rendered", font_size=30))])

    assert render_rows(report) == ()
    assert render_headline(report, OUT) == f"1 page written to {OUT}"


def test_every_kind_of_problem_is_listed_worst_first() -> None:
    report = _report(
        page_failures=[("page-009.png", "cannot read image")],
        outcomes=[
            ("page-001.png", RegionOutcome("fits", "rendered", font_size=30)),
            ("page-001.png", RegionOutcome("squeezed", "rendered", condense=0.82, font_size=30)),
            ("page-002.png", RegionOutcome("tiny", "rendered", font_size=8, undersized=True)),
            ("page-002.png", RegionOutcome("blank", "skipped_empty", "no translation")),
            ("page-003.png", RegionOutcome("overflow", "failed", "does not fit")),
            ("page-003.png", RegionOutcome("copied", "rendered", font_size=30, unedited=True)),
            ("page-004.png", RegionOutcome("deliberate", "skipped_flag", "marked skip: true")),
        ],
    )

    assert [row.problem for row in render_rows(report)] == [
        PAGE_FAILED,
        DID_NOT_FIT,
        NO_TRANSLATION,
        BELOW_MINIMUM,
        CONDENSED,
        NOT_TRANSLATED,
    ], "a deliberate skip is a decision, not a problem, and is not listed"


def test_a_page_that_failed_names_no_region_to_go_to() -> None:
    report = _report(page_failures=[("page-009.png", "cannot read image")])
    (row,) = render_rows(report)

    assert row.image == "page-009.png"
    assert row.region_id is None
    assert not row.selectable, "there is no region to select; the page was never rendered"
    assert row.detail == "cannot read image"


def test_a_region_row_carries_the_region_to_select() -> None:
    report = _report(outcomes=[("page-003.png", RegionOutcome("overflow", "failed", "too long"))])
    (row,) = render_rows(report)

    assert row.selectable
    assert (row.image, row.region_id) == ("page-003.png", "overflow")


def test_the_usual_missing_translation_does_not_repeat_itself() -> None:
    """The outcome's detail and the problem column would say the same thing."""
    report = _report(
        outcomes=[
            ("a.png", RegionOutcome("blank", "skipped_empty", "no translation")),
            ("a.png", RegionOutcome("marked-up", "skipped_empty", "translation is only markup")),
        ]
    )
    plain, markup = render_rows(report)

    assert plain.detail == ""
    assert markup.detail == "translation is only markup"


def test_the_measured_numbers_reach_the_rows() -> None:
    report = _report(
        outcomes=[
            ("a.png", RegionOutcome("tiny", "rendered", font_size=9, undersized=True)),
            ("a.png", RegionOutcome("squeezed", "rendered", condense=0.85, font_size=30)),
        ]
    )
    small, squeezed = render_rows(report)

    assert small.detail == "9px"
    assert squeezed.detail == "85% of normal width"


def test_a_cancelled_run_says_so_and_still_counts_what_it_wrote() -> None:
    report = _report(cancelled=True, pages_written=[OUT / "a.png", OUT / "b.png"])

    assert render_headline(report, OUT) == f"cancelled — 2 pages written to {OUT}"


def test_the_counts_follow_the_command_line_summary() -> None:
    report = _report(
        outcomes=[
            ("a.png", RegionOutcome("one", "rendered", font_size=30)),
            ("a.png", RegionOutcome("two", "skipped_empty", "no translation")),
            ("a.png", RegionOutcome("three", "skipped_flag", "marked skip: true")),
            ("a.png", RegionOutcome("four", "failed", "too long")),
        ]
    )

    assert render_counts(report) == (
        "1 rendered · 1 without a translation · 1 skipped · 1 did not fit"
    )


# -- extracting --------------------------------------------------------

PLAN = Path("/tmp/pages/comic-plan.yaml")


def _extract_report(**overrides: object) -> ExtractReport:
    base: dict[str, object] = {"pages_read": 3, "regions": 7}
    base.update(overrides)
    return ExtractReport(**base)  # type: ignore[arg-type]


def test_a_clean_extract_lists_nothing() -> None:
    report = _extract_report()

    assert extract_rows(report) == ()
    assert extract_headline(report, PLAN) == f"3 pages read into {PLAN}"


def test_an_extract_lists_what_the_plan_cannot_tell_you_worst_first() -> None:
    report = _extract_report(
        failures=[(Path("/tmp/pages/page-002.png"), "cannot read image: truncated")],
        empty_pages=[Path("/tmp/pages/page-004.png")],
        skipped_inputs=[(Path("/tmp/pages/notes.txt"), "unsupported extension .txt")],
        low_confidence=2,
        approximate=1,
    )
    rows = extract_rows(report)

    assert [row.problem for row in rows] == [COULD_NOT_READ, NO_REGIONS, NOT_AN_IMAGE]
    assert [row.image for row in rows] == ["page-002.png", "page-004.png", "notes.txt"]
    assert not any(row.region_id for row in rows), "an extract reports pages, not regions"


def test_regions_that_merely_need_checking_are_left_to_the_plan() -> None:
    """The page list counts them and Next Flagged Region walks them.

    A second copy in the panel would go stale the moment one was fixed.
    """
    report = _extract_report(low_confidence=4, approximate=3, artefacts=2, on_artwork=1)

    assert extract_rows(report) == ()
    assert extract_counts(report) == (
        "7 regions · 4 low confidence · 3 approximate · 3 left unseeded"
    )


def test_a_page_with_nothing_on_it_is_still_a_page_to_go_and_look_at() -> None:
    report = _extract_report(empty_pages=[Path("/tmp/pages/page-004.png")])
    (row,) = extract_rows(report)

    assert row.selectable, "the plan holds it, so the window can select it"
    assert row.region_id is None


def test_something_that_never_made_it_into_the_plan_goes_nowhere() -> None:
    report = _extract_report(
        failures=[(Path("/tmp/pages/broken.png"), "cannot read image")],
        skipped_inputs=[(Path("/tmp/pages/notes.txt"), "unsupported extension .txt")],
    )
    broken, notes = extract_rows(report)

    assert not broken.selectable
    assert not notes.selectable


def test_a_cancelled_extract_says_the_plan_was_not_written() -> None:
    report = _extract_report(pages_read=1, cancelled=True)

    assert extract_headline(report, PLAN) == (
        f"cancelled after 1 page(s) — {PLAN.name} was not written"
    )
