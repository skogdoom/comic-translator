"""What a finished run tells you, decided without a window.

The panel that shows this is a widget and is tested with the other widgets.
What is worth testing here is the judgement underneath it: which of a run's
outcomes are worth a second look, in what order, and what the line at the
top of the panel says.
"""

from __future__ import annotations

from pathlib import Path

from comictrans.apply import ApplyReport
from comictrans.gui.render_report import (
    BELOW_MINIMUM,
    CONDENSED,
    DID_NOT_FIT,
    NO_TRANSLATION,
    NOT_TRANSLATED,
    PAGE_FAILED,
    counts,
    headline,
    rows_for,
)
from comictrans.render import RegionOutcome

OUT = Path("/tmp/rendered")


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

    assert rows_for(report) == ()
    assert headline(report, OUT) == f"1 page written to {OUT}"


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

    assert [row.problem for row in rows_for(report)] == [
        PAGE_FAILED,
        DID_NOT_FIT,
        NO_TRANSLATION,
        BELOW_MINIMUM,
        CONDENSED,
        NOT_TRANSLATED,
    ], "a deliberate skip is a decision, not a problem, and is not listed"


def test_a_page_that_failed_names_no_region_to_go_to() -> None:
    report = _report(page_failures=[("page-009.png", "cannot read image")])
    (row,) = rows_for(report)

    assert row.image == "page-009.png"
    assert row.region_id is None
    assert not row.selectable, "there is no region to select; the page was never rendered"
    assert row.detail == "cannot read image"


def test_a_region_row_carries_the_region_to_select() -> None:
    report = _report(outcomes=[("page-003.png", RegionOutcome("overflow", "failed", "too long"))])
    (row,) = rows_for(report)

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
    plain, markup = rows_for(report)

    assert plain.detail == ""
    assert markup.detail == "translation is only markup"


def test_the_measured_numbers_reach_the_rows() -> None:
    report = _report(
        outcomes=[
            ("a.png", RegionOutcome("tiny", "rendered", font_size=9, undersized=True)),
            ("a.png", RegionOutcome("squeezed", "rendered", condense=0.85, font_size=30)),
        ]
    )
    small, squeezed = rows_for(report)

    assert small.detail == "9px"
    assert squeezed.detail == "85% of normal width"


def test_a_cancelled_run_says_so_and_still_counts_what_it_wrote() -> None:
    report = _report(cancelled=True, pages_written=[OUT / "a.png", OUT / "b.png"])

    assert headline(report, OUT) == f"cancelled — 2 pages written to {OUT}"


def test_the_counts_follow_the_command_line_summary() -> None:
    report = _report(
        outcomes=[
            ("a.png", RegionOutcome("one", "rendered", font_size=30)),
            ("a.png", RegionOutcome("two", "skipped_empty", "no translation")),
            ("a.png", RegionOutcome("three", "skipped_flag", "marked skip: true")),
            ("a.png", RegionOutcome("four", "failed", "too long")),
        ]
    )

    assert counts(report) == "1 rendered · 1 without a translation · 1 skipped · 1 did not fit"
