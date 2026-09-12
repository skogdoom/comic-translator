"""Everything about a plan that can be checked without rendering it.

For the moment before a long run: a chapter of forty pages takes minutes to
render and fails in the first second if a font will not resolve, so this
answers "would ``apply`` get through this?" in the time it takes to hash the
images.

**It reports every problem it finds rather than the first.** That is the
whole difference between this and loading a plan to render it. ``load_plan``
raises on the first thing wrong, which is right when something is about to
use the result; here, a chapter with three missing pages and two unresolvable
fonts should say so once rather than over five runs.

**What it accepts is what ``apply`` accepts**, and it stays that way by
calling the same functions rather than by agreeing with them. The schema is
``load_plan``; the pages are ``planfile.image_problems``, which is what
``verify_images`` raises the first of; the fonts are ``fonts.resolve`` with
the precedence ``apply.resolve_styles`` uses. A check written here in the
same spirit as one of those would be a second opinion, and a second opinion
is what a validator must not be.

**What it does not check is anything that needs pixels drawn.** Whether a
translation fits its balloon, whether it is still the source text, whether
two polygons overlap — all of those are answers ``apply`` and ``review``
already give, and they need the render this exists to run before. What is
here is what fails a run outright.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import ComictransError, PlanError
from .fonts import resolve
from .imaging import page_size
from .model import Plan
from .planfile import image_problems, load_plan


@dataclass(frozen=True, slots=True)
class Problem:
    """One thing wrong with a plan, and the part of it that is wrong.

    ``where`` is what a person would go and look at — a page's filename, a
    region's id, a font's name, or the plan itself — so that problems of the
    same kind read as a list rather than as repetition.
    """

    where: str
    detail: str

    def __str__(self) -> str:
        # Several of these details are the reader's own messages, printed
        # unchanged so that the two commands cannot drift apart, and some of
        # those already open with the name of the page they are about.
        # Prefixing that again would only stutter.
        if self.detail.startswith(self.where):
            return self.detail
        return f"{self.where}: {self.detail}"


@dataclass(frozen=True, slots=True)
class ValidateReport:
    """What one plan turned out to be, and everything wrong with it."""

    plan_path: Path
    problems: tuple[Problem, ...] = ()

    parsed: bool = False
    """Whether the file turned out to be a plan at all. When it did not,
    every count below is zero because nothing was read, not because the plan
    is empty — so a summary should say the one problem and stop."""

    images: int = 0
    regions: int = 0
    fonts: tuple[str, ...] = ()
    """Every font the plan names, resolved or not, sorted."""

    @property
    def ok(self) -> bool:
        return not self.problems


def _font_names(plan: Plan) -> tuple[str, ...]:
    """Every distinct font a render would have to resolve, in plan order.

    The precedence ``apply.resolve_styles`` uses, minus the ``--font``
    override this command does not take: a region's own font, else the
    header's. Both are names: the reader refuses an empty one, and a header
    font is not optional, so there is no unnamed case to stand in for.

    A plan with no regions names no fonts, even though its header still
    carries one. That is not an oversight: ``resolve_styles`` walks regions
    too, so a plan with nothing to letter renders fine with a header font
    that would not resolve, and a validator that refused it would be
    refusing something ``apply`` accepts.
    """
    seen: list[str] = []
    for region in plan.regions:
        family = region.font or plan.header.font
        if family not in seen:
            seen.append(family)
    return tuple(seen)


def _font_problems(plan: Plan) -> list[Problem]:
    """Every font the plan names that this machine cannot render with.

    The failure this command is really for. A plan can be perfectly
    well-formed, name every page correctly, hash clean, and still refuse
    every region because the font it names has no bold face here — which
    nothing in the schema can catch, being a fact about the machine rather
    than about the file.
    """
    found: list[Problem] = []
    for family in _font_names(plan):
        try:
            resolve(family)
        except ComictransError as exc:
            found.append(Problem(where=family, detail=str(exc)))
    return found


def _page_sizes(plan: Plan, plan_path: Path) -> dict[str, tuple[int, int]]:
    """``(width, height)`` per page, read from the file's header.

    ``imaging.page_size`` decodes nothing, so this is a few bytes a page
    rather than the megabytes a decode would be — which is what makes
    checking every polygon against its own page cheap enough to do here at
    all. A page it cannot open is simply left out: ``image_problems`` has
    already said that the file is missing or unreadable, and saying it twice
    in different words is what this module exists not to do.
    """
    sizes: dict[str, tuple[int, int]] = {}
    base = plan_path.parent
    for image in plan.images:
        size = page_size(base / image.name)
        if size is not None:
            sizes[image.name] = size
    return sizes


def _bounds_problems(plan: Plan, plan_path: Path) -> list[Problem]:
    """Every polygon that reaches past the page it is drawn on.

    The reader refuses a negative coordinate as "off the page" and cannot
    refuse the other three edges, having no idea how big the page is. This
    is that rule finished, and it is the one check here the reader could
    never make: the polygon and the image are both valid, and only together
    are they wrong.
    """
    sizes = _page_sizes(plan, plan_path)
    found: list[Problem] = []
    for region in plan.regions:
        size = sizes.get(region.image)
        if size is None:
            continue
        width, height = size
        over = [f"({x}, {y})" for x, y in region.polygon if x >= width or y >= height]
        if over:
            found.append(
                Problem(
                    where=region.id,
                    detail=f"polygon reaches past {region.image} ({width}x{height}): "
                    f"{', '.join(over[:4])}{' …' if len(over) > 4 else ''}",
                )
            )
    return found


def validate_plan(path: Path) -> ValidateReport:
    """Check one plan every way it can be checked without rendering it.

    A plan that will not parse ends there: everything below reads the object
    the reader would have produced, and there is nothing useful to say about
    the fonts of a file that is not a plan yet. Past that point every check
    runs, whatever the ones before it found.
    """
    try:
        plan = load_plan(path, check_images=False)
    except PlanError as exc:
        return ValidateReport(plan_path=path, problems=(Problem(where=path.name, detail=str(exc)),))

    problems: list[Problem] = [
        Problem(where=name, detail=message) for name, message in image_problems(plan, path)
    ]
    problems += _bounds_problems(plan, path)
    problems += _font_problems(plan)

    return ValidateReport(
        plan_path=path,
        problems=tuple(problems),
        parsed=True,
        images=len(plan.images),
        regions=len(plan.regions),
        fonts=tuple(sorted(_font_names(plan))),
    )


__all__ = ["Problem", "ValidateReport", "validate_plan"]
