"""What ``validate`` finds in a plan, and what it deliberately does not."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from comictrans.model import Color, Geometry, Plan, PlanHeader, PlanImage, Region, TextCase
from comictrans.planfile import write_plan
from comictrans.planfile.schema import PLAN_VERSION
from comictrans.util import sha256_file
from comictrans.validate import Problem, validate_plan

from .conftest import make_plan, save_page

PAGE = "page-001.png"


def _header(**overrides: object) -> PlanHeader:
    base: dict[str, object] = {
        "version": PLAN_VERSION,
        "generator": "comictrans 0.1.0",
        "created": "2026-09-06T19:00:00Z",
        "source_language": "it",
        "target_language": "en",
        "ocr_engine": "apple-vision",
        "font": "Comic Sans MS",
        "case": TextCase.UPPER,
        "font_size_min_ratio": 0.012,
        "condense_min": 0.9,
    }
    base.update(overrides)
    return PlanHeader(**base)  # type: ignore[arg-type]


def _region(**overrides: object) -> Region:
    base: dict[str, object] = {
        "id": "page-001-001",
        "image": PAGE,
        "order": 1,
        "geometry": Geometry.EXACT,
        "polygon": ((10, 10), (110, 10), (110, 60), (10, 60)),
        "fill_color": Color(253, 253, 250),
        "text_color": Color(27, 27, 27),
        "confidence": 0.9,
        "source_text": "NON CI POSSO CREDERE!",
        "translation": "I CANNOT BELIEVE IT!",
    }
    base.update(overrides)
    return Region(**base)  # type: ignore[arg-type]


def _page(directory: Path, name: str = PAGE, size: tuple[int, int] = (200, 300)) -> Path:
    """A blank page ``size`` wide by ``size[1]`` tall, saved under ``name``."""
    width, height = size
    return save_page(np.full((height, width, 3), 255, dtype=np.uint8), directory / name)


def _written(tmp_path: Path, plan: Plan) -> Path:
    plan_path = tmp_path / "plan.yaml"
    write_plan(plan, plan_path, force=True)
    return plan_path


def _clean(tmp_path: Path, *regions: Region) -> Path:
    """A plan whose pages are all present and all hash as recorded."""
    image = _page(tmp_path)
    return _written(
        tmp_path, make_plan(_header(), regions or (_region(),), {PAGE: sha256_file(image)})
    )


def test_a_clean_plan_has_no_problems(tmp_path: Path, font_dir: Path) -> None:
    report = validate_plan(_clean(tmp_path))

    assert report.ok
    assert report.problems == ()
    assert (report.parsed, report.images, report.regions) == (True, 1, 1)
    assert report.fonts == ("Comic Sans MS",)


def test_every_problem_is_reported_rather_than_the_first(tmp_path: Path, font_dir: Path) -> None:
    # The whole difference between this and load_plan: four different things
    # wrong, and one run that names all four.
    image = _page(tmp_path)
    digest = sha256_file(image)
    plan_path = _written(
        tmp_path,
        make_plan(
            _header(font="Marker Felt"),  # a real font file, but with no bold face
            (_region(), _region(id="off-page", polygon=((10, 10), (900, 10), (900, 60)))),
            {PAGE: digest},
            extra_images=(PlanImage(name="page-002.png", sha256=digest),),
        ),
    )
    save_page(np.zeros((300, 200, 3), dtype=np.uint8), image)  # now hashes differently

    report = validate_plan(plan_path)

    assert not report.ok
    detail = "\n".join(str(problem) for problem in report.problems)
    assert "has changed since extract" in detail
    assert "source image not found" in detail
    assert "reaches past" in detail
    assert "bold" in detail
    assert len(report.problems) == 4, detail


def test_a_file_that_is_not_a_plan_is_the_only_thing_reported(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text("- just\n- a list\n", encoding="utf-8")

    report = validate_plan(plan_path)

    assert not report.parsed
    assert len(report.problems) == 1
    assert "mapping at the top level" in report.problems[0].detail
    assert (report.images, report.regions, report.fonts) == (0, 0, ())


def test_a_plan_file_that_is_not_there_is_reported_rather_than_raised(tmp_path: Path) -> None:
    report = validate_plan(tmp_path / "nothing.yaml")

    assert not report.ok
    assert "cannot read plan file" in report.problems[0].detail


def test_the_last_pixel_is_on_the_page_and_the_next_one_is_not(
    tmp_path: Path, font_dir: Path
) -> None:
    # The check the reader cannot make: it refuses a negative coordinate as
    # off the page and has no idea how wide the page is.
    inside = _region(polygon=((0, 0), (199, 0), (199, 299), (0, 299)))
    assert validate_plan(_clean(tmp_path, inside)).ok

    for corner in ((200, 0), (0, 300)):
        report = validate_plan(_clean(tmp_path, _region(polygon=((0, 0), corner, (10, 10)))))
        assert [problem.where for problem in report.problems] == ["page-001-001"]
        assert f"{corner[0]}, {corner[1]}" in report.problems[0].detail
        assert "200x300" in report.problems[0].detail


def test_a_region_names_its_own_font_and_that_one_is_checked_too(
    tmp_path: Path, font_dir: Path
) -> None:
    report = validate_plan(_clean(tmp_path, _region(font="Marker Felt")))

    assert [problem.where for problem in report.problems] == ["Marker Felt"]
    assert report.fonts == ("Marker Felt",)


def test_a_plan_with_no_regions_names_no_font_because_apply_resolves_none(
    tmp_path: Path, font_dir: Path
) -> None:
    # apply resolves a face per region, so a plan with nothing to letter
    # renders fine with a header font that would not resolve here. Refusing
    # it would be refusing something apply accepts.
    image = _page(tmp_path)
    plan = make_plan(
        _header(font="Nothing By This Name"),
        (),
        extra_images=(PlanImage(name=PAGE, sha256=sha256_file(image)),),
    )

    report = validate_plan(_written(tmp_path, plan))

    assert report.ok
    assert (report.images, report.regions, report.fonts) == (1, 0, ())


def test_nothing_is_said_about_what_only_a_render_could_know(
    tmp_path: Path, font_dir: Path
) -> None:
    # An untranslated region, a skipped one, and two polygons on top of each
    # other: all things apply and review report, none of them a reason this
    # run would fail.
    overlapping = (
        _region(translation=""),
        _region(id="second", polygon=((10, 10), (110, 10), (110, 60), (10, 60)), skip=True),
    )

    assert validate_plan(_clean(tmp_path, *overlapping)).ok


def test_the_same_font_named_twice_is_resolved_and_listed_once(
    tmp_path: Path, font_dir: Path
) -> None:
    report = validate_plan(
        _clean(tmp_path, _region(font="Marker Felt"), _region(id="second", font="Marker Felt"))
    )

    assert len(report.problems) == 1
    assert report.fonts == ("Marker Felt",)


def test_a_page_is_measured_from_its_header_rather_than_decoded(
    tmp_path: Path, font_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Cheap enough to do for every page of a chapter is the reason every
    # polygon can be checked at all.
    from PIL import Image

    decoded: list[str] = []
    original = Image.Image.load

    def watched(self: Image.Image) -> object:
        decoded.append(str(getattr(self, "filename", "")))
        return original(self)

    plan_path = _clean(tmp_path)
    monkeypatch.setattr(Image.Image, "load", watched)

    assert validate_plan(plan_path).ok
    assert decoded == []


def test_a_problem_that_already_names_its_page_does_not_say_it_twice() -> None:
    stutter = Problem("page-002.png", "page-002.png has changed since extract")
    assert str(stutter) == "page-002.png has changed since extract"

    plain = Problem("page-001-001", "polygon reaches past page-001.png (200x300): (900, 10)")
    assert str(plain) == "page-001-001: polygon reaches past page-001.png (200x300): (900, 10)"
