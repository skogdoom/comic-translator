"""The GUI's view-model: a loaded plan, its edits, and where they can be saved.

Free of Qt, and of anything beyond what ``planfile`` and ``model`` already
import — the same module dependency rule that lets ``planfile`` be imported
without dragging in Vision (see ``docs/ARCHITECTURE.md``) is why this layer
can be unit tested without a display, on any machine, in the same way the
rest of the pipeline is.

Every widget talks to a :class:`PlanDocument`, never to a ``Plan`` or the
filesystem directly. That is what lets "unsaved changes" be one true fact
instead of every widget guessing whether it touched something.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ..model import Geometry, Plan, Region
from ..planfile import load_plan, write_plan

OVERLAP_BBOX_RATIO = 0.15
"""Share of the smaller region's bounding box that counts as an overlap.

The same threshold ``render._warn_about_overlaps`` uses at apply time, so a
region flagged here is exactly one apply would also warn about — never a
surprise the GUI invented and apply does not share.
"""


def overlapping_region_ids(regions: Sequence[Region]) -> frozenset[str]:
    """Ids of regions that share enough bounding box to draw over each other.

    Over actionable regions only: a skipped region or one with no translation
    is never drawn, so it cannot actually overlap anything on the page,
    however its polygon happens to sit.
    """
    actionable = [r for r in regions if r.is_actionable]
    hit: set[str] = set()
    for index, first in enumerate(actionable):
        for second in actionable[index + 1 :]:
            shared = first.bounds.intersection(second.bounds)
            if shared is None:
                continue
            smaller = min(first.bounds.area, second.bounds.area)
            if smaller > 0 and shared.area / smaller > OVERLAP_BBOX_RATIO:
                hit.add(first.id)
                hit.add(second.id)
    return frozenset(hit)


@dataclass(frozen=True, slots=True)
class RegionFlags:
    """Why a region is worth a second look, as plain bools a badge can key off.

    ``skipped`` is not itself a problem — ``skip: true`` is a decision, not a
    defect, and is kept separate from ``held_back`` for exactly that reason.
    """

    approximate: bool
    """No clean balloon outline was found; this is a padded box. Check it."""

    low_confidence: bool
    held_back: bool
    """Empty translation and not skipped: extract left this for you to look
    at, and apply will leave the art untouched until you do."""

    unedited: bool
    """The translation is still the source text extract seeded. Not an error
    — a name or an exclamation can be a correct translation of itself — but
    worth knowing before you call a page done."""

    overlapping: bool
    skipped: bool

    @property
    def any(self) -> bool:
        return (
            self.approximate
            or self.low_confidence
            or self.held_back
            or self.unedited
            or self.overlapping
        )


def region_flags(region: Region, *, overlapping_ids: frozenset[str]) -> RegionFlags:
    return RegionFlags(
        approximate=region.geometry is Geometry.APPROXIMATE,
        low_confidence=region.low_confidence,
        held_back=not region.skip and not region.translation.strip(),
        unedited=region.is_untranslated,
        overlapping=region.id in overlapping_ids,
        skipped=region.skip,
    )


@dataclass(frozen=True, slots=True)
class ImageSummary:
    """Counts for one page, for the page list without opening it."""

    image: str
    region_count: int
    flagged_count: int


class PlanDocument:
    """A plan file open for editing.

    Wraps a ``Plan`` exactly as loaded from disk. Every mutation goes through
    one of the ``set_*`` methods, which is what makes ``dirty`` reliable: it
    is not "has anything in the widget tree changed", it is "has anything
    passed through here".
    """

    def __init__(self, plan: Plan, path: Path) -> None:
        self.plan = plan
        self.path = path
        self.dirty = False

    @classmethod
    def open(cls, path: Path, *, check_images: bool = True) -> PlanDocument:
        """Load a plan file read-only. Raises :class:`PlanError` on anything wrong."""
        return cls(load_plan(path, check_images=check_images), path)

    def images(self) -> tuple[str, ...]:
        return self.plan.images()

    def regions_for(self, image: str) -> tuple[Region, ...]:
        return self.plan.regions_for(image)

    def region(self, region_id: str) -> Region:
        for region in self.plan.regions:
            if region.id == region_id:
                return region
        raise KeyError(region_id)

    def ordered_ids(self) -> tuple[str, ...]:
        """Every region id, in the plan's own order.

        Not grouped by page a second time: ``extract`` writes regions page by
        page and in reading order within a page, so walking the plan straight
        through is walking the comic. A hand-edited plan that interleaves
        pages walks the way it is written, which is the honest answer — the
        file's order is what ``apply`` uses too.
        """
        return tuple(region.id for region in self.plan.regions)

    def adjacent_region(
        self, region_id: str | None, *, forward: bool, flagged_only: bool = False
    ) -> str | None:
        """The next region past this one, or ``None`` at the end of the plan.

        ``region_id`` of ``None`` starts from before the first region going
        forward, and past the last going back, so "next" from nowhere is the
        first region rather than nothing.
        """
        ids = self.ordered_ids()
        if not ids:
            return None
        if region_id is None:
            start = -1 if forward else len(ids)
        elif region_id in ids:
            start = ids.index(region_id)
        else:
            return None

        step = 1 if forward else -1
        for index in range(start + step, len(ids) if forward else -1, step):
            if not flagged_only or self.flags(ids[index]).any:
                return ids[index]
        return None

    def source_path(self, image: str) -> Path:
        """Where an image lives on disk, resolved against the plan's own directory.

        The same one-liner as ``apply.source_for``, duplicated rather than
        imported: ``apply`` pulls in ``imaging``, and with it Pillow and
        OpenCV, which is exactly what this module exists to stay free of —
        see the module dependency rule in docs/ARCHITECTURE.md.
        """
        return (self.path.parent / image).resolve()

    def overlapping_ids(self, image: str) -> frozenset[str]:
        return overlapping_region_ids(self.regions_for(image))

    def flags(self, region_id: str) -> RegionFlags:
        region = self.region(region_id)
        return region_flags(region, overlapping_ids=self.overlapping_ids(region.image))

    def summary(self, image: str) -> ImageSummary:
        regions = self.regions_for(image)
        overlapping = overlapping_region_ids(regions)
        flagged = sum(1 for r in regions if region_flags(r, overlapping_ids=overlapping).any)
        return ImageSummary(image=image, region_count=len(regions), flagged_count=flagged)

    def _update(self, region_id: str, **changes: object) -> Region:
        """Replace one field on one region, in place in the plan, and mark dirty.

        Not validated beyond what ``Region`` itself enforces (its fields carry
        no invariants of their own) — the schema is enforced once, at load
        time, by the plan file reader. A hand-typed ``font_size`` of ``0``
        would be caught there on the next load, the same as if you had typed
        it into the YAML by hand.
        """
        updated = replace(self.region(region_id), **changes)  # type: ignore[arg-type]
        self.plan = replace(
            self.plan,
            regions=tuple(
                updated if region.id == region_id else region for region in self.plan.regions
            ),
        )
        self.dirty = True
        return updated

    def set_translation(self, region_id: str, translation: str) -> Region:
        return self._update(region_id, translation=translation)

    def set_notes(self, region_id: str, notes: str) -> Region:
        return self._update(region_id, notes=notes)

    def set_skip(self, region_id: str, skip: bool) -> Region:
        return self._update(region_id, skip=skip)

    def set_font(self, region_id: str, font: str | None) -> Region:
        """``None`` clears the override, falling back to the plan header's font."""
        return self._update(region_id, font=font)

    def set_font_size(self, region_id: str, font_size: int | None) -> Region:
        """``None`` clears the override, back to automatic fitting."""
        return self._update(region_id, font_size=font_size)

    def save(self) -> None:
        """Write back to the file this document was opened from."""
        write_plan(self.plan, self.path, force=True)
        self.dirty = False

    def save_as(self, path: Path, *, force: bool = False) -> None:
        """Write to a different path. Refuses to clobber unless ``force``.

        Mirrors ``write_plan``'s own refusal, rather than deciding for the
        caller: the GUI asks before overwriting, the CLI's ``extract`` asks
        for ``--force`` — the decision belongs one layer up either way.
        """
        write_plan(self.plan, path, force=force)
        self.path = path
        self.dirty = False
