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

from ..model import (
    Color,
    Geometry,
    Plan,
    PlanHeader,
    Polygon,
    Region,
    TextCase,
    polygon_is_simple,
)
from ..planfile import load_plan, write_plan
from ..planfile.schema import (
    CONDENSE_MIN_RANGE,
    FONT_SIZE_MIN_RATIO_RANGE,
    MIN_POLYGON_POINTS,
)
from ..util import slugify

_NON_EMPTY_HEADER_FIELDS = frozenset({"font", "source_language", "target_language"})

_HEADER_RANGES: dict[str, tuple[float, float]] = {
    "font_size_min_ratio": FONT_SIZE_MIN_RATIO_RANGE,
    "condense_min": CONDENSE_MIN_RANGE,
}
"""The reader's own limits, so an edit cannot outrun what will load again."""

UNDO_LIMIT = 500
"""How many edits back the history goes.

An entry is a tuple of pointers to regions that already exist, so the cost is
a few kilobytes each and the cap is about not growing without bound in a long
session rather than about memory being tight. Coalescing means an entry is one
act of typing, not one keystroke, so 500 is a long way back.
"""


MANUAL_CONFIDENCE = 1.0
"""What a hand-drawn region records where OCR would have put a score.

It is not a claim about a measurement: nothing measured this region, which
is what ``geometry: manual`` beside it says. The number has to be something
— the reader requires it and takes 0.0 to 1.0 — and 0.0 would mean the
opposite of the truth, flagging the region as a doubtful reading for as long
as it exists, when the reading is a person's own and there is nothing to
doubt. Read the pair together: manual geometry, so the confidence is the
person's, not the recogniser's.
"""

OVERLAP_BBOX_RATIO = 0.15
"""Share of the smaller region's bounding box that counts as an overlap.

The same threshold ``render._warn_about_overlaps`` uses at apply time, so a
region flagged here is exactly one apply would also warn about — never a
surprise the GUI invented and apply does not share.
"""


def validated_polygon(polygon: Polygon) -> Polygon:
    """A polygon the plan file reader will accept, or a ``ValueError`` saying why.

    Exactly the reader's own rules — at least three points, whole pixels, no
    negative coordinate, no edge crossing another — because an edit that got
    past this and into a saved plan would be a file the GUI could not reopen.
    The messages are written to be shown to whoever is dragging the shape.
    """
    points = tuple((round(x), round(y)) for x, y in polygon)
    if len(points) < MIN_POLYGON_POINTS:
        raise ValueError(f"a region needs at least {MIN_POLYGON_POINTS} corners")
    if any(x < 0 or y < 0 for x, y in points):
        raise ValueError("a corner cannot go off the top or left of the page")
    if not polygon_is_simple(points):
        # Covers a corner dragged onto its neighbour as well as a bow tie:
        # both leave edges touching, and neither is a shape apply could fill.
        raise ValueError("that shape crosses or folds over itself")
    return points


def _numbers_used(plan: Plan) -> dict[str, int]:
    """Highest ``page-001-004`` style number per page stem in a plan."""
    highest: dict[str, int] = {}
    for region in plan.regions:
        stem, _, suffix = region.id.rpartition("-")
        if stem and suffix.isdigit():
            highest[stem] = max(highest.get(stem, 0), int(suffix))
    return highest


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
    """A plan file open for editing, with its undo history.

    Wraps a ``Plan`` exactly as loaded from disk. Every mutation goes through
    one of the ``set_*`` methods, which is what makes ``dirty`` reliable: it
    is not "has anything in the widget tree changed", it is "has anything
    passed through here".

    **Undo is a stack of whole plans, not a stack of operations.** ``Plan``
    is frozen and holds a tuple of frozen ``Region``s, and every edit already
    builds a new one, so the plan as it stood before an edit *is* the undo
    entry — a new tuple of pointers, not a copy of anything. The point of
    doing it this way is what it costs to extend: an operation that adds,
    deletes or reshapes a region needs no undo code of its own, because it
    goes through the same place and leaves the same kind of entry behind.
    """

    def __init__(self, plan: Plan, path: Path) -> None:
        self.plan = plan
        self.path = path
        self._clean = plan
        self._undo: list[Plan] = []
        self._redo: list[Plan] = []
        self._run: tuple[str | None, str] | None = None
        self._allocated = _numbers_used(plan)
        """Highest region number seen per page since this was opened.

        Seeded from the file and only ever raised, so that deleting the last
        region on a page and drawing another does not hand the old one's name
        to the new one. Session-scoped, because a plan file cannot record the
        ids that are no longer in it: reopen the file and the number is free
        again."""

    @property
    def dirty(self) -> bool:
        """Whether the plan differs from the one last written to disk.

        Identity against the saved plan rather than a flag that only ever
        goes true, so undoing back to the last save clears the marker
        honestly. Retyping the same text by hand does not: that is a
        different object and a different edit, and it is what every other
        editor does too.
        """
        return self.plan is not self._clean

    @classmethod
    def open(cls, path: Path, *, check_images: bool = True) -> PlanDocument:
        """Load a plan file read-only. Raises :class:`PlanError` on anything wrong."""
        return cls(load_plan(path, check_images=check_images), path)

    def images(self) -> tuple[str, ...]:
        return self.plan.image_names()

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
        """Replace fields on one region, in place in the plan, and record it.

        Not validated beyond what ``Region`` itself enforces (its fields carry
        no invariants of their own) — the schema is enforced once, at load
        time, by the plan file reader. A hand-typed ``font_size`` of ``0``
        would be caught there on the next load, the same as if you had typed
        it into the YAML by hand. The exceptions are the two edits that could
        write a file the reader would refuse: the header, and a polygon, both
        of which validate before they get here.

        Usually one field. A caller passing two is saying they are one edit —
        a polygon and the geometry that describes it — and the first names
        the undo run.
        """
        current = self.region(region_id)
        updated = replace(current, **changes)  # type: ignore[arg-type]
        if updated == current:
            # Nothing changed, so there is nothing to undo. Without this a
            # field re-set to the value it already held would leave an undo
            # step that appears to do nothing when taken.
            return current

        field = next(iter(changes))
        self._record(
            replace(
                self.plan,
                regions=tuple(
                    updated if region.id == region_id else region for region in self.plan.regions
                ),
            ),
            run=(region_id, field),
        )
        return updated

    def _record(self, plan: Plan, *, run: tuple[str | None, str] | None) -> None:
        """Move to ``plan``, pushing the current one onto the undo stack.

        ``run`` identifies what is being edited, as region and field, with a
        region of ``None`` meaning the header — which no region id can
        collide with, since the reader refuses an empty one.

        Consecutive edits carrying the same ``run`` are the same act of
        typing and collapse into a single undo step. Without that, every
        keystroke would be its own, since that is how the inspector writes
        them.
        """
        if run is None or run != self._run:
            self._undo.append(self.plan)
            del self._undo[: max(0, len(self._undo) - UNDO_LIMIT)]
        self._redo.clear()
        self._run = run
        self.plan = plan

    def end_edit_run(self) -> None:
        """Break the current run, so the next edit starts a new undo step.

        Called when the selection moves. Typing into one region, going to
        look at another and coming back is two acts however identical the
        field, and undo should not swallow the first with the second.
        """
        self._run = None

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        """Step back one edit. False when there is nothing left to undo."""
        if not self._undo:
            return False
        self._redo.append(self.plan)
        self.plan = self._undo.pop()
        self._run = None
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self.plan)
        self.plan = self._redo.pop()
        self._run = None
        return True

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

    def set_polygon(self, region_id: str, polygon: Polygon) -> Region:
        """Reshape a region, and record that a person shaped it.

        Validated, unlike the text fields and like the header: the reader
        refuses a self-intersecting or degenerate polygon at load time, so an
        edit that skipped this could write a plan the GUI itself could not
        reopen. ``ValueError`` says which rule it broke, in words meant for
        whoever is dragging the shape.

        The geometry becomes ``manual`` in the same step, because it is no
        longer what its old value claims: nothing traced this outline and no
        OCR box bounded it. It also clears the ``approximate`` flag, which
        says "check this" — which is precisely what has just been done.
        """
        return self._update(region_id, polygon=validated_polygon(polygon), geometry=Geometry.MANUAL)

    def set_source_text(self, region_id: str, source_text: str) -> Region:
        """The text as it stands on the page.

        For a detected region this is what OCR read; for a hand-drawn one
        there was no reading and this is the only way it gets any. Either
        way it is what ``apply`` measures "same as source" against, and the
        plan file has been hand-editable since milestone 1 — the inspector
        offering the same field is not a new licence, just a nearer one.
        """
        return self._update(region_id, source_text=source_text)

    def set_fill_color(self, region_id: str, color: Color) -> Region:
        """What erase paints the region with before the translation goes on."""
        return self._update(region_id, fill_color=color)

    def set_text_color(self, region_id: str, color: Color) -> Region:
        return self._update(region_id, text_color=color)

    # -- adding and deleting ---------------------------------------------

    def _next_region_id(self, image: str) -> str:
        """A fresh id for a page, in the shape extract writes: ``page-001-004``.

        Numbered past the highest the page has ever reached rather than into
        the first gap: an id is how a region is named in a report, in a note
        to yourself, in a commit message. Handing a deleted region's name to
        a different one makes those quietly wrong.
        """
        stem = slugify(Path(image).stem)
        highest = self._allocated.get(stem, 0)
        for region in self.plan.regions:
            prefix, _, suffix = region.id.rpartition("-")
            if prefix == stem and suffix.isdigit():
                highest = max(highest, int(suffix))
        taken = {region.id for region in self.plan.regions}
        while True:
            highest += 1
            candidate = f"{stem}-{highest:03d}"
            if candidate not in taken:
                self._allocated[stem] = highest
                return candidate

    def _insertion_index(self, image: str) -> int:
        """Where a new region on ``image`` goes: last on its page, pages in order.

        The plan's order is reading order — ``apply`` and every "next region"
        step walk it straight through — so a region added to page 3 belongs
        with page 3's, not at the end of the chapter behind page 40's.
        """
        rank = {name: index for index, name in enumerate(self.plan.image_names())}
        here = rank[image]
        for index, region in enumerate(self.plan.regions):
            if rank[region.image] > here:
                return index
        return len(self.plan.regions)

    def add_region(
        self,
        image: str,
        polygon: Polygon,
        *,
        fill_color: Color,
        text_color: Color,
        source_text: str = "",
        translation: str = "",
    ) -> Region:
        """Put a new region on a page, drawn by hand rather than detected.

        No OCR: ``review`` never reads a page for text, so ``source_text``
        is whatever the person typed, and empty until they do. That leaves
        the region held back — no translation, not skipped — which is exactly
        what it is until it has been filled in.
        """
        if image not in self.plan.image_names():
            raise ValueError(f"{image} is not a page in this plan")
        region = Region(
            id=self._next_region_id(image),
            image=image,
            order=max((r.order for r in self.regions_for(image)), default=0) + 1,
            geometry=Geometry.MANUAL,
            polygon=validated_polygon(polygon),
            fill_color=fill_color,
            text_color=text_color,
            confidence=MANUAL_CONFIDENCE,
            source_text=source_text,
            translation=translation,
        )
        index = self._insertion_index(image)
        regions = self.plan.regions
        self._record(
            replace(self.plan, regions=(*regions[:index], region, *regions[index:])), run=None
        )
        return region

    def delete_region(self, region_id: str) -> Region:
        """Remove a region from the plan, and hand it back.

        A delete is a delete, not a ``skip: true`` in disguise: the region is
        gone from the file the next time it is saved, the same as deleting
        its block by hand. Undo covers it while the session lasts, which is
        the same safety net every other edit here gets.
        """
        region = self.region(region_id)
        self._record(
            replace(self.plan, regions=tuple(r for r in self.plan.regions if r.id != region_id)),
            run=None,
        )
        return region

    # -- the header ------------------------------------------------------

    def regions_using_header_font(self) -> int:
        """How many regions have no font of their own and follow the header."""
        return sum(1 for region in self.plan.regions if region.font is None)

    def _update_header(self, **changes: object) -> PlanHeader:
        """Replace one field on the header, recording it like any other edit.

        Validated, unlike the region setters. A region's fields carry no
        invariants of their own, but the header's do — an empty font or a
        condense floor below the schema's would write a plan file the reader
        then refuses to open, which is a worse outcome than a rejected edit.
        """
        field = next(iter(changes))
        value = changes[field]
        if field in _NON_EMPTY_HEADER_FIELDS and not str(value).strip():
            raise ValueError(f"header {field} cannot be empty")
        if field in _HEADER_RANGES:
            low, high = _HEADER_RANGES[field]
            if not isinstance(value, int | float) or not low <= float(value) <= high:
                raise ValueError(f"header {field} must be between {low} and {high}, got {value!r}")

        updated = replace(self.plan.header, **changes)  # type: ignore[arg-type]
        if updated == self.plan.header:
            return self.plan.header
        self._record(replace(self.plan, header=updated), run=(None, field))
        return updated

    def set_header_font(self, font: str) -> PlanHeader:
        """The font every region without an override of its own is drawn in."""
        return self._update_header(font=font.strip())

    def set_header_case(self, case: TextCase) -> PlanHeader:
        return self._update_header(case=case)

    def set_header_font_size_min_ratio(self, ratio: float) -> PlanHeader:
        return self._update_header(font_size_min_ratio=ratio)

    def set_header_condense_min(self, condense_min: float) -> PlanHeader:
        return self._update_header(condense_min=condense_min)

    def set_header_source_language(self, language: str) -> PlanHeader:
        return self._update_header(source_language=language.strip())

    def set_header_target_language(self, language: str) -> PlanHeader:
        """Also picks the hyphenation dictionary used when text is fitted."""
        return self._update_header(target_language=language.strip())

    def save(self) -> None:
        """Write back to the file this document was opened from."""
        write_plan(self.plan, self.path, force=True)
        self._mark_saved()

    def _mark_saved(self) -> None:
        """This plan is now what is on disk. Undo history survives a save.

        Stepping back past a save is allowed and leaves the document dirty
        again, which is honest: the file still holds what was written.
        """
        self._clean = self.plan
        self._run = None

    def save_as(self, path: Path, *, force: bool = False) -> None:
        """Write to a different path. Refuses to clobber unless ``force``.

        Mirrors ``write_plan``'s own refusal, rather than deciding for the
        caller: the GUI asks before overwriting, the CLI's ``extract`` asks
        for ``--force`` — the decision belongs one layer up either way.
        """
        write_plan(self.plan, path, force=force)
        self.path = path
        self._mark_saved()
