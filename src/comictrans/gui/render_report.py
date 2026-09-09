"""An ``ApplyReport`` turned into rows a panel can list.

No Qt. The window shows a rendered run as a list of things worth a second
look, and *which* things those are is the same judgement the CLI's
end-of-run summary makes — so it is made once, here, and tested like any
other module rather than through a widget.

The order is by how much a row wants your attention: a page that was not
written at all, then a region whose text would not fit, then one with no
translation to draw, then the three quieter notes about text that did fit
but only just.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..apply import ApplyReport

PAGE_FAILED = "page failed"
DID_NOT_FIT = "did not fit"
NO_TRANSLATION = "no translation"
BELOW_MINIMUM = "below minimum size"
CONDENSED = "condensed"
NOT_TRANSLATED = "not translated"


@dataclass(frozen=True, slots=True)
class RenderRow:
    """One line of the report panel."""

    image: str
    region_id: str | None
    """``None`` for a whole page that failed, which is not one region's fault."""

    problem: str
    detail: str = ""

    @property
    def selectable(self) -> bool:
        """Whether activating this row can take you somewhere.

        A region row can: the window selects it. A page row names a page that
        was never written, and the reason is usually that its source image
        could not be read, so there is nothing to go and look at.
        """
        return self.region_id is not None


def rows_for(report: ApplyReport) -> tuple[RenderRow, ...]:
    """Everything in a run worth looking at again, most serious first."""
    rows: list[RenderRow] = [
        RenderRow(image=image, region_id=None, problem=PAGE_FAILED, detail=reason)
        for image, reason in report.page_failures
    ]
    rows.extend(
        RenderRow(image=image, region_id=o.region_id, problem=DID_NOT_FIT, detail=o.detail)
        for image, o in report.outcomes
        if o.failed
    )
    rows.extend(
        # The usual reason is that the field is empty, and the outcome says
        # so in the same words the problem column already uses. The other
        # one — a translation that is nothing but markup — is worth showing.
        RenderRow(
            image=image,
            region_id=o.region_id,
            problem=NO_TRANSLATION,
            detail="" if o.detail == NO_TRANSLATION else o.detail,
        )
        for image, o in report.outcomes
        if o.status == "skipped_empty"
    )
    rows.extend(
        RenderRow(
            image=image,
            region_id=o.region_id,
            problem=BELOW_MINIMUM,
            detail=f"{o.font_size}px",
        )
        for image, o in report.undersized
    )
    rows.extend(
        RenderRow(
            image=image,
            region_id=o.region_id,
            problem=CONDENSED,
            detail=f"{o.condense:.0%} of normal width",
        )
        for image, o in report.condensed
    )
    rows.extend(
        RenderRow(image=image, region_id=o.region_id, problem=NOT_TRANSLATED, detail="")
        for image, o in report.unedited
    )
    return tuple(rows)


def headline(report: ApplyReport, output: Path) -> str:
    """One line saying how the run went, for the top of the panel."""
    pages = len(report.pages_written)
    written = f"{pages} page{'' if pages == 1 else 's'} written to {output}"
    if report.cancelled:
        return f"cancelled — {written}"
    return written


def counts(report: ApplyReport) -> str:
    """The tallies under the headline, in the CLI summary's own order."""
    return (
        f"{report.rendered} rendered · "
        f"{report.skipped_empty} without a translation · "
        f"{report.skipped_flag} skipped · "
        f"{report.failed} did not fit"
    )


__all__ = [
    "BELOW_MINIMUM",
    "CONDENSED",
    "DID_NOT_FIT",
    "NOT_TRANSLATED",
    "NO_TRANSLATION",
    "PAGE_FAILED",
    "RenderRow",
    "counts",
    "headline",
    "rows_for",
]
