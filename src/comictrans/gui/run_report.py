"""A finished run turned into rows a panel can list.

Both passes end with a report, and both windows-full of it are the same
shape: a headline, a line of tallies, and a list of things worth a second
look. *Which* things those are is the same judgement each pass's
command-line summary makes — so it is made once, here, and tested like any
other module rather than through a widget.

The order in both is by how much a row wants your attention: what did not
happen at all first, then what happened but needs checking.

No widgets, but not no Qt: every word here is read off a panel, so it is
translated like the rest of the window. That is the whole of the dependency —
``QtCore``, for one bare ``QObject`` subclass that exists to name a catalogue
context — with no widget, no event loop and no ``QApplication`` in it, and
the tests still call these six functions directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject

from ..apply import ApplyReport
from ..extract import ExtractReport
from ..pack import archive_kind


class RunText(QObject):
    """A name for the catalogue, and nothing else.

    Every word below is ``RunText.tr(...)``. ``tr`` rather than
    ``QCoreApplication.translate``, which would be the obvious choice in a
    module with no widget in it, for a measured reason: ``lupdate`` marks no
    ``translate`` call as carrying plural forms, whatever its fourth
    argument, and drops the message outright when that argument is anything
    but a bare name — ``report.pages_read`` is enough to lose a string
    silently. Written as ``tr`` it is understood in every form. So the
    counting sentences have to be ``tr``, and the rest are ``tr`` to keep one
    mechanism in one file.

    Each call writes its English out in full for the same reason: ``lupdate``
    reads the source rather than running it, and sees nothing behind a helper.
    """

    @staticmethod
    def tr(text: str, disambiguation: str | None = None, n: int = -1) -> str:
        """What ``QObject.tr`` does, for a class nobody will ever instantiate.

        ``QObject.tr`` is an instance method to Python, so calling it on the
        class — which is what ``lupdate`` reads the context off — is a type
        error even though Qt answers it correctly. This says the same thing
        in a signature that is true, and keeps the context in one place
        rather than repeated at thirteen call sites.
        """
        return QCoreApplication.translate("RunText", text, disambiguation, n)


# What a render found.
PAGE_FAILED = RunText.tr("page failed")
DID_NOT_FIT = RunText.tr("did not fit")
NO_TRANSLATION = RunText.tr("no translation")
BELOW_MINIMUM = RunText.tr("below minimum size")
CONDENSED = RunText.tr("condensed")
NOT_TRANSLATED = RunText.tr("not translated")

# What an extract found.
COULD_NOT_READ = RunText.tr("could not read")
NO_REGIONS = RunText.tr("no regions found")
NOT_AN_IMAGE = RunText.tr("not read as a page")
LANGUAGE_NOT_READ = RunText.tr("language not read")

_EMPTY_TRANSLATION = "no translation"
"""What ``render`` writes as the detail for a region with nothing to letter.

The same words as ``NO_TRANSLATION`` and deliberately not the same string.
That one is this panel's, and is translated; this one comes off a
``RegionOutcome`` the pipeline built, and the pipeline is not translated —
see ``gui/translations``. Comparing the two would have matched in English
and quietly stopped the first time the window ran in anything else, leaving
the detail column repeating the problem column in two languages.
"""


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
            detail="" if o.detail == _EMPTY_TRANSLATION else o.detail,
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
            detail=RunText.tr("{0} of normal width").format(f"{o.condense:.0%}"),
        )
        for image, o in report.condensed
    )
    rows.extend(
        RunRow(image=image, region_id=o.region_id, problem=NOT_TRANSLATED)
        for image, o in report.unedited
    )
    return tuple(rows)


def render_headline(report: ApplyReport, output: Path) -> str:
    """One line saying how a render went, for the top of the panel.

    Cancelled or not, the count is part of one sentence rather than a prefix
    glued to a shared tail: a language that puts the number last, or inflects
    the noun for it, cannot be assembled from two halves translated apart.

    A chapter file gets its own three, because the count would otherwise be
    the only thing said about a run that left nothing at all: a stopped
    archive run has no partial form to keep, which is a promise worth saying
    out loud the once it applies rather than reporting as zero pages.
    """
    pages = len(report.pages_written)
    if archive_kind(output) is not None:
        if report.archive is not None:
            return RunText.tr("%n page(s) packed into {0}", None, pages).format(output)
        if report.cancelled:
            return RunText.tr(
                "cancelled — nothing written: {0} is one file, so it is packed "
                "only once every page is rendered"
            ).format(output.name)
        return RunText.tr(
            "nothing written: no page was rendered, so there was nothing to pack into {0}"
        ).format(output.name)
    if report.cancelled:
        return RunText.tr("cancelled — %n page(s) written to {0}", None, pages).format(output)
    return RunText.tr("%n page(s) written to {0}", None, pages).format(output)


def render_counts(report: ApplyReport) -> str:
    """The tallies under the headline, in the CLI summary's own order."""
    return RunText.tr(
        "{0} rendered · {1} without a translation · {2} skipped · {3} did not fit"
    ).format(report.rendered, report.skipped_empty, report.skipped_flag, report.failed)


# -- extracting --------------------------------------------------------


def extract_rows(report: ExtractReport) -> tuple[RunRow, ...]:
    """Everything in an extract worth looking at again, most serious first.

    Regions that merely need checking — approximate geometry, low
    confidence, text that did not read as language — are not listed here.
    The plan the run just wrote flags every one of them, the page list counts
    them, and Next Flagged Region walks them; a second copy in a panel would
    go stale the moment one was fixed. What is here is what the plan cannot
    tell you: a language the recogniser read without, first, because it is
    true of every page; then pages it does not cover, and pages it covers
    with nothing on.
    """
    rows: list[RunRow] = [
        RunRow(
            image="",
            problem=LANGUAGE_NOT_READ,
            detail=RunText.tr(
                "Apple Vision cannot read {0}, and read every page with its own defaults instead"
            ).format(language),
            in_plan=False,
        )
        for language in report.unread_languages
    ]
    rows.extend(
        RunRow(image=path.name, problem=COULD_NOT_READ, detail=reason, in_plan=False)
        for path, reason in report.failures
    )
    rows.extend(
        RunRow(
            image=path.name,
            problem=NO_REGIONS,
            detail=RunText.tr("nothing detected on this page"),
        )
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
        return RunText.tr(
            "cancelled after %n page(s) — {0} was not written", None, report.pages_read
        ).format(plan_path.name)
    return RunText.tr("%n page(s) read into {0}", None, report.pages_read).format(plan_path)


def extract_counts(report: ExtractReport) -> str:
    """The tallies under the headline, in the CLI summary's own order."""
    return RunText.tr(
        "%n region(s) · {0} low confidence · {1} approximate · {2} left unseeded",
        None,
        report.regions,
    ).format(report.low_confidence, report.approximate, report.artefacts + report.on_artwork)


__all__ = [
    "BELOW_MINIMUM",
    "CONDENSED",
    "COULD_NOT_READ",
    "DID_NOT_FIT",
    "LANGUAGE_NOT_READ",
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
