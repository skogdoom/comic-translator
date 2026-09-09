"""A finished run turned into rows a panel can list.

No Qt. Both passes end with a report, and both windows-full of it are the
same shape: a headline, a line of tallies, and a list of things worth a
second look. *Which* things those are is the same judgement each pass's
command-line summary makes — so it is made once, here, and tested like any
other module rather than through a widget.

The order in both is by how much a row wants your attention: what did not
happen at all first, then what happened but needs checking.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..apply import ApplyReport
from ..extract import ExtractReport

# What a render found.
PAGE_FAILED = "page failed"
DID_NOT_FIT = "did not fit"
NO_TRANSLATION = "no translation"
BELOW_MINIMUM = "below minimum size"
CONDENSED = "condensed"
NOT_TRANSLATED = "not translated"

# What an extract found.
COULD_NOT_READ = "could not read"
NO_REGIONS = "no regions found"
NOT_AN_IMAGE = "not read as a page"


@dataclass(frozen=True, slots=True)
class RunRow:
    """One line of the run panel."""

    image: str
    region_id: str | None = None
    """The region to select when this row is chosen, if it names one."""

    problem: str = ""
    detail: str = ""
    in_plan: bool = True
    """Whether ``image`` is a page the plan actually holds.

    False for an input that was skipped or failed before it could be read:
    there is no page to go and look at, so the row is inert.
    """

    @property
    def selectable(self) -> bool:
        """Whether activating this row can take you somewhere.

        A region row selects that region. A page row selects that page, which
        is what you want for a page that came back with nothing on it. A row
        for something that never made it into the plan takes you nowhere.
        """
        return self.in_plan


# -- rendering ---------------------------------------------------------


def render_rows(report: ApplyReport) -> tuple[RunRow, ...]:
    """Everything in a render worth looking at again, most serious first."""
    rows: list[RunRow] = [
        RunRow(image=image, problem=PAGE_FAILED, detail=reason, in_plan=False)
        for image, reason in report.page_failures
    ]
    rows.extend(
        RunRow(image=image, region_id=o.region_id, problem=DID_NOT_FIT, detail=o.detail)
        for image, o in report.outcomes
        if o.failed
    )
    rows.extend(
        # The usual reason is that the field is empty, and the outcome says
        # so in the same words the problem column already uses. The other
        # one — a translation that is nothing but markup — is worth showing.
        RunRow(
            image=image,
            region_id=o.region_id,
            problem=NO_TRANSLATION,
            detail="" if o.detail == NO_TRANSLATION else o.detail,
        )
        for image, o in report.outcomes
        if o.status == "skipped_empty"
    )
    rows.extend(
        RunRow(
            image=image,
            region_id=o.region_id,
            problem=BELOW_MINIMUM,
            detail=f"{o.font_size}px",
        )
        for image, o in report.undersized
    )
    rows.extend(
        RunRow(
            image=image,
            region_id=o.region_id,
            problem=CONDENSED,
            detail=f"{o.condense:.0%} of normal width",
        )
        for image, o in report.condensed
    )
    rows.extend(
        RunRow(image=image, region_id=o.region_id, problem=NOT_TRANSLATED)
        for image, o in report.unedited
    )
    return tuple(rows)


def render_headline(report: ApplyReport, output: Path) -> str:
    """One line saying how a render went, for the top of the panel."""
    pages = len(report.pages_written)
    written = f"{pages} page{'' if pages == 1 else 's'} written to {output}"
    if report.cancelled:
        return f"cancelled — {written}"
    return written


def render_counts(report: ApplyReport) -> str:
    """The tallies under the headline, in the CLI summary's own order."""
    return (
        f"{report.rendered} rendered · "
        f"{report.skipped_empty} without a translation · "
        f"{report.skipped_flag} skipped · "
        f"{report.failed} did not fit"
    )


# -- extracting --------------------------------------------------------


def extract_rows(report: ExtractReport) -> tuple[RunRow, ...]:
    """Everything in an extract worth looking at again, most serious first.

    Regions that merely need checking — approximate geometry, low
    confidence, text that did not read as language — are not listed here.
    The plan the run just wrote flags every one of them, the page list counts
    them, and Next Flagged Region walks them; a second copy in a panel would
    go stale the moment one was fixed. What is here is what the plan cannot
    tell you: pages it does not cover, and pages it covers with nothing on.
    """
    rows: list[RunRow] = [
        RunRow(image=path.name, problem=COULD_NOT_READ, detail=reason, in_plan=False)
        for path, reason in report.failures
    ]
    rows.extend(
        RunRow(image=path.name, problem=NO_REGIONS, detail="nothing detected on this page")
        for path in report.empty_pages
    )
    rows.extend(
        RunRow(image=path.name, problem=NOT_AN_IMAGE, detail=reason, in_plan=False)
        for path, reason in report.skipped_inputs
    )
    return tuple(rows)


def extract_headline(report: ExtractReport, plan_path: Path) -> str:
    """One line saying how an extract went, for the top of the panel."""
    if report.cancelled:
        return f"cancelled after {report.pages_read} page(s) — {plan_path.name} was not written"
    pages = report.pages_read
    return f"{pages} page{'' if pages == 1 else 's'} read into {plan_path}"


def extract_counts(report: ExtractReport) -> str:
    """The tallies under the headline, in the CLI summary's own order."""
    return (
        f"{report.regions} region{'' if report.regions == 1 else 's'} · "
        f"{report.low_confidence} low confidence · "
        f"{report.approximate} approximate · "
        f"{report.artefacts + report.on_artwork} left unseeded"
    )


__all__ = [
    "BELOW_MINIMUM",
    "CONDENSED",
    "COULD_NOT_READ",
    "DID_NOT_FIT",
    "NOT_AN_IMAGE",
    "NOT_TRANSLATED",
    "NO_REGIONS",
    "NO_TRANSLATION",
    "PAGE_FAILED",
    "RunRow",
    "extract_counts",
    "extract_headline",
    "extract_rows",
    "render_counts",
    "render_headline",
    "render_rows",
]
