"""Carrying hand work across a re-extraction.

Region ids are positional, so any change to detection renumbers them and they
cannot be used to recognise a region between two runs. Geometry can: the same
balloon detected twice lands in very nearly the same place, so regions are
matched by how much their bounding boxes overlap.

Only what a person put there is carried — the translation where it was
actually edited, plus notes, skip and the font overrides. Everything measured
from the page (polygon, colours, confidence, the OCR text) comes from the
fresh run, which is the point of re-extracting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from ..model import Box, Plan, Region

log = logging.getLogger(__name__)

DEFAULT_MIN_IOU = 0.5


@dataclass(frozen=True, slots=True)
class MergeReport:
    """What survived a re-extraction, and what did not."""

    carried: tuple[str, ...]
    """Fresh region ids that received hand work from the previous plan."""

    dropped: tuple[str, ...]
    """Previous region ids holding hand work that found no home. Data loss."""

    added: tuple[str, ...]
    """Fresh region ids with no counterpart in the previous plan."""


def has_hand_work(region: Region) -> bool:
    """True when a person has touched this region.

    An edited translation counts, and so does a cleared one: blanking a
    translation is how you tell apply to leave a balloon alone, which is a
    decision worth keeping.
    """
    return (
        region.translation.strip() != region.source_text.strip()
        or bool(region.notes.strip())
        or region.skip
        or region.font is not None
        or region.font_size is not None
    )


def _iou(a: Box, b: Box) -> float:
    overlap = a.intersection(b)
    if overlap is None:
        return 0.0
    union = a.area + b.area - overlap.area
    return overlap.area / union if union > 0 else 0.0


def _pair_up(
    previous: tuple[Region, ...], fresh: tuple[Region, ...], min_iou: float
) -> dict[int, int]:
    """Match fresh regions to previous ones, best overlap first, one to one."""
    scored: list[tuple[float, int, int]] = []
    for new_index, new in enumerate(fresh):
        for old_index, old in enumerate(previous):
            if old.image != new.image:
                continue
            score = _iou(old.bounds, new.bounds)
            if score >= min_iou:
                scored.append((score, new_index, old_index))

    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    matched: dict[int, int] = {}
    taken: set[int] = set()
    for _, new_index, old_index in scored:
        if new_index in matched or old_index in taken:
            continue
        matched[new_index] = old_index
        taken.add(old_index)
    return matched


def merge_plans(
    previous: Plan, fresh: Plan, *, min_iou: float = DEFAULT_MIN_IOU
) -> tuple[Plan, MergeReport]:
    """Fold the hand work in ``previous`` into the freshly detected ``fresh``."""
    matched = _pair_up(previous.regions, fresh.regions, min_iou)

    regions: list[Region] = []
    carried: list[str] = []
    added: list[str] = []
    for index, new in enumerate(fresh.regions):
        old_index = matched.get(index)
        if old_index is None:
            added.append(new.id)
            regions.append(new)
            continue
        old = previous.regions[old_index]
        if not has_hand_work(old):
            regions.append(new)
            continue
        carried.append(new.id)
        regions.append(
            replace(
                new,
                # The seeded translation is not hand work; only keep one that
                # was actually changed, so fresh OCR still reaches the field.
                translation=(
                    old.translation
                    if old.translation.strip() != old.source_text.strip()
                    else new.translation
                ),
                notes=old.notes,
                skip=old.skip,
                font=old.font,
                font_size=old.font_size,
            )
        )

    taken = set(matched.values())
    dropped = tuple(
        region.id
        for index, region in enumerate(previous.regions)
        if index not in taken and has_hand_work(region)
    )
    for region_id in dropped:
        log.warning(
            "region %s from the previous plan has no match in this one; its "
            "translation and notes are lost",
            region_id,
        )

    # The fresh run's pages, not the old plan's: which files exist and what
    # they hash to is measured, like the polygons and the colours, and the
    # point of re-extracting is to measure it again.
    return Plan(header=fresh.header, images=fresh.images, regions=tuple(regions)), MergeReport(
        carried=tuple(carried), dropped=dropped, added=tuple(added)
    )
