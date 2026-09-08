from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from comictrans.errors import InputError, PlanError
from comictrans.model import Color, Geometry, Plan, PlanHeader, PlanImage, Region, TextCase
from comictrans.planfile import dumps, load_plan, loads, write_plan
from comictrans.planfile.schema import PLAN_VERSION, REGION_KEY_ORDER
from comictrans.util import sha256_file

from .conftest import make_plan, save_page


def _header() -> PlanHeader:
    return PlanHeader(
        version=PLAN_VERSION,
        generator="comictrans 0.1.0",
        created="2026-09-06T19:00:00Z",
        source_language="it",
        target_language="en",
        ocr_engine="apple-vision",
        font="Comic Sans MS",
        case=TextCase.UPPER,
        font_size_min_ratio=0.012,
        condense_min=0.9,
    )


def _region(**overrides: object) -> Region:
    base: dict[str, object] = {
        "id": "page-001-001",
        "image": "page-001.png",
        "order": 1,
        "geometry": Geometry.EXACT,
        "polygon": ((10, 10), (110, 10), (110, 60), (10, 60)),
        "fill_color": Color(253, 253, 250),
        "text_color": Color(27, 27, 27),
        "confidence": 0.931,
        "source_text": "NON CI POSSO\nCREDERE!",
        "translation": "",
        "notes": "",
    }
    base.update(overrides)
    return Region(**base)  # type: ignore[arg-type]


def _plan(*regions: Region, digests: dict[str, str] | None = None) -> Plan:
    return make_plan(_header(), regions or (_region(),), digests)


def test_round_trip_preserves_every_field() -> None:
    original = _plan(
        _region(
            translation="I CAN'T BELIEVE IT!",
            notes="check the accent",
            low_confidence=True,
            skip=True,
            font="Chalkboard SE",
            font_size=22,
            geometry=Geometry.APPROXIMATE,
        )
    )
    assert loads(dumps(original)) == original


def test_keys_are_written_in_schema_order() -> None:
    text = dumps(_plan(_region(low_confidence=True, skip=True, font="X", font_size=9)))
    region_block = text.split("regions:", 1)[1]
    positions = [region_block.find(f"{key}:") for key in REGION_KEY_ORDER]
    present = [p for p in positions if p >= 0]
    assert present == sorted(present)
    assert len(present) == len(REGION_KEY_ORDER)


def test_optional_keys_are_omitted_when_empty() -> None:
    regions = dumps(_plan()).split("regions:", 1)[1]
    assert "low_confidence" not in regions
    assert "skip:" not in regions
    assert "font_size" not in regions


def test_multiline_source_text_is_a_block_scalar_and_polygon_is_one_line() -> None:
    text = dumps(_plan())
    assert "source_text: |-" in text
    assert "polygon: [[10, 10], [110, 10], [110, 60], [10, 60]]" in text


def test_comments_survive_a_read(tmp_path: Path) -> None:
    path = tmp_path / "plan.yaml"
    path.write_text(dumps(_plan()) + "\n# my own note\n", encoding="utf-8")
    plan = load_plan(path, check_images=False)
    assert plan.regions[0].id == "page-001-001"


def test_unicode_survives_the_round_trip() -> None:
    plan = _plan(_region(source_text="PERCHÉ NON È QUÌ?", notes="città"))
    assert loads(dumps(plan)).regions[0].source_text == "PERCHÉ NON È QUÌ?"


def test_write_refuses_to_clobber_an_existing_plan(tmp_path: Path) -> None:
    path = tmp_path / "plan.yaml"
    write_plan(_plan(), path)
    with pytest.raises(InputError, match="already exists"):
        write_plan(_plan(), path)
    write_plan(_plan(_region(translation="OK")), path, force=True)
    assert load_plan(path, check_images=False).regions[0].translation == "OK"


def test_write_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    write_plan(_plan(), tmp_path / "plan.yaml")
    assert [p.name for p in tmp_path.iterdir()] == ["plan.yaml"]


def _line_of(text: str, needle: str) -> int:
    for number, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return number
    raise AssertionError(f"{needle!r} not in plan text")


def _mutate(find: str, replace: str) -> tuple[str, int]:
    text = dumps(_plan())
    assert find in text
    changed = text.replace(find, replace, 1)
    return changed, _line_of(changed, replace.rstrip().splitlines()[-1])


@pytest.mark.parametrize(
    ("find", "replace", "message"),
    [
        ('    notes: ""', '    notes: ""\n    colour: red', "unknown key 'colour'"),
        ("\nfont: Comic Sans MS", "\nfnt: Comic Sans MS", "unknown key 'fnt'"),
        ("    geometry: exact", "    geometry: wobbly", "geometry must be one of"),
        ("    fill_color: '#fdfdfa'", "    fill_color: reddish", "fill_color"),
        ("    confidence: 0.931", "    confidence: 4", "confidence must be between"),
        ("    order: 1", "    order: 1.5", "order must be a whole number"),
        ("  - id: page-001-001", "  - id: ''", "id must not be empty"),
        (
            "    polygon: [[10, 10], [110, 10], [110, 60], [10, 60]]",
            "    polygon: [[10, 10], [110, 10]]",
            "at least 3 points",
        ),
        (
            "    polygon: [[10, 10], [110, 10], [110, 60], [10, 60]]",
            "    polygon: [[10, 10], [110, 60], [110, 10], [10, 60]]",
            "self-intersecting",
        ),
        (
            "    polygon: [[10, 10], [110, 10], [110, 60], [10, 60]]",
            "    polygon: [[10, 10], [110.5, 10], [110, 60], [10, 60]]",
            "whole pixels",
        ),
        (
            "    polygon: [[10, 10], [110, 10], [110, 60], [10, 60]]",
            "    polygon: [[10, 10], [-5, 10], [110, 60], [10, 60]]",
            "off the page",
        ),
    ],
)
def test_validation_errors_point_at_the_offending_line(
    find: str, replace: str, message: str
) -> None:
    text, expected_line = _mutate(find, replace)
    with pytest.raises(PlanError, match=message) as excinfo:
        loads(text)
    assert excinfo.value.line == expected_line


def test_missing_required_key_is_reported() -> None:
    text = dumps(_plan()).replace('    translation: ""\n', "")
    with pytest.raises(PlanError, match=r"missing required key.*translation"):
        loads(text)


def test_duplicate_region_ids_are_rejected() -> None:
    plan = _plan(_region(), _region(order=2))
    with pytest.raises(PlanError, match="duplicate region id"):
        loads(dumps(plan))


def test_unsupported_version_is_rejected() -> None:
    text = dumps(_plan()).replace(f"version: {PLAN_VERSION}", "version: 99")
    with pytest.raises(PlanError, match="unsupported plan version 99"):
        loads(text)


def test_yaml_syntax_error_carries_a_line_number() -> None:
    text = dumps(_plan()).replace("regions:", "regions: [")
    with pytest.raises(PlanError) as excinfo:
        loads(text)
    assert excinfo.value.line is not None


def test_empty_and_non_mapping_documents_are_rejected() -> None:
    with pytest.raises(PlanError, match="empty"):
        loads("")
    with pytest.raises(PlanError, match="mapping at the top level"):
        loads("- just\n- a list\n")


def test_image_hash_mismatch_is_an_error(tmp_path: Path) -> None:
    image = save_page(np.full((20, 20, 3), 255, dtype=np.uint8), tmp_path / "page-001.png")
    plan_path = tmp_path / "plan.yaml"
    write_plan(_plan(digests={"page-001.png": sha256_file(image)}), plan_path)
    load_plan(plan_path)  # matches, so this is fine

    save_page(np.zeros((20, 20, 3), dtype=np.uint8), image)
    with pytest.raises(PlanError, match="has changed since extract"):
        load_plan(plan_path)


def test_missing_source_image_is_an_error(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.yaml"
    write_plan(_plan(), plan_path)
    with pytest.raises(PlanError, match="source image not found"):
        load_plan(plan_path)


def test_plan_error_message_includes_path_and_line() -> None:
    error = PlanError("bad thing", path=Path("/tmp/plan.yaml"), line=7)
    assert str(error) == "/tmp/plan.yaml:7: bad thing"


# Written out in full rather than derived from dumps(), because this is the
# one shape the writer can no longer produce: a hash on every region and no
# images list. It is what a plan written before the schema change looks like.
VERSION_1_PLAN = """\
version: 1
generator: comictrans 0.1.0
created: '2026-09-06T19:00:00Z'
source_language: it
target_language: en
ocr_engine: apple-vision
font: Comic Sans MS
case: upper
font_size_min_ratio: 0.012
condense_min: 0.9
regions:
  - id: page-001-001
    image: page-001.png
    image_sha256: aaaa
    order: 1
    geometry: exact
    polygon: [[10, 10], [110, 10], [110, 60], [10, 60]]
    fill_color: '#fdfdfa'
    text_color: '#1b1b1b'
    confidence: 0.9
    source_text: CIAO
    translation: HELLO
  - id: page-002-001
    image: page-002.png
    image_sha256: bbbb
    order: 1
    geometry: exact
    polygon: [[10, 10], [110, 10], [110, 60], [10, 60]]
    fill_color: '#fdfdfa'
    text_color: '#1b1b1b'
    confidence: 0.9
    source_text: CIAO
    translation: HELLO
"""


def test_a_version_1_plan_is_upgraded_as_it_is_read() -> None:
    plan = loads(VERSION_1_PLAN)

    assert plan.header.version == PLAN_VERSION, "in memory it is a current plan"
    assert [(i.name, i.sha256) for i in plan.images] == [
        ("page-001.png", "aaaa"),
        ("page-002.png", "bbbb"),
    ]
    assert plan.sha256_for("page-002.png") == "bbbb"


def test_saving_a_version_1_plan_writes_the_current_shape() -> None:
    text = dumps(loads(VERSION_1_PLAN))

    assert f"version: {PLAN_VERSION}" in text
    assert "image_sha256" not in text, "the hash lives in the images list now"
    assert loads(text) == loads(VERSION_1_PLAN), "and reads back as the same plan"


def test_a_version_1_region_must_still_carry_its_hash() -> None:
    text = VERSION_1_PLAN.replace("    image_sha256: aaaa\n", "")
    with pytest.raises(PlanError, match="needs image and image_sha256"):
        loads(text)


def test_a_version_1_plan_may_not_carry_an_images_list() -> None:
    text = VERSION_1_PLAN.replace(
        "regions:", "images:\n  - name: page-001.png\n    sha256: aaaa\nregions:"
    )
    with pytest.raises(PlanError, match="version 1 plan does not have an 'images' list"):
        loads(text)


def test_a_current_plan_may_not_carry_a_region_hash() -> None:
    text = dumps(_plan()).replace(
        "    image: page-001.png\n", "    image: page-001.png\n    image_sha256: aaaa\n"
    )
    with pytest.raises(PlanError, match="unknown key 'image_sha256'"):
        loads(text)


def test_a_page_with_no_regions_survives_the_round_trip() -> None:
    blank = PlanImage(name="page-002.png", sha256="bbbb")
    original = make_plan(_header(), (_region(),), extra_images=(blank,))

    plan = loads(dumps(original))

    assert plan == original
    assert plan.image_names() == ("page-001.png", "page-002.png")
    assert plan.regions_for("page-002.png") == ()


def test_a_region_naming_an_image_the_plan_does_not_list_is_rejected() -> None:
    text = dumps(_plan()).replace("    image: page-001.png", "    image: page-009.png")
    with pytest.raises(PlanError, match="is not in the plan's images"):
        loads(text)


def test_a_duplicate_image_is_rejected() -> None:
    text = dumps(_plan()).replace("regions:", "  - name: page-001.png\n    sha256: aaaa\nregions:")
    with pytest.raises(PlanError, match="duplicate image"):
        loads(text)


def test_a_current_plan_needs_an_images_list() -> None:
    text = dumps(_plan())
    without = text[: text.index("images:")] + text[text.index("regions:") :]
    with pytest.raises(PlanError, match="missing required key 'images'"):
        loads(without)


def test_manual_geometry_round_trips() -> None:
    plan = _plan(_region(geometry=Geometry.MANUAL))
    assert "geometry: manual" in dumps(plan)
    assert loads(dumps(plan)).regions[0].geometry is Geometry.MANUAL


def test_a_page_with_no_regions_is_hash_checked_too(tmp_path: Path) -> None:
    # The point of the images list: in version 1 a page nothing was found on
    # had nowhere to record its hash, so a change to it went unnoticed.
    image = save_page(np.full((20, 20, 3), 255, dtype=np.uint8), tmp_path / "page-002.png")
    blank = PlanImage(name="page-002.png", sha256=sha256_file(image))
    plan_path = tmp_path / "plan.yaml"
    save_page(np.full((20, 20, 3), 255, dtype=np.uint8), tmp_path / "page-001.png")
    write_plan(
        make_plan(
            _header(),
            (_region(),),
            {"page-001.png": sha256_file(tmp_path / "page-001.png")},
            extra_images=(blank,),
        ),
        plan_path,
    )
    load_plan(plan_path)  # both match

    save_page(np.zeros((20, 20, 3), dtype=np.uint8), image)
    with pytest.raises(PlanError, match=r"page-002\.png has changed since extract"):
        load_plan(plan_path)
