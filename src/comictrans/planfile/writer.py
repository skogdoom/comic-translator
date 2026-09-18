"""Writing plan files.

Stable key ordering, UTF-8, and an atomic replace so an interrupted run cannot
leave a half-written plan on top of a file full of your translations.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import cast

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString, LiteralScalarString

from ..errors import InputError
from ..model import Plan, PlanHeader, PlanImage, Region
from .schema import (
    FILE_HEADER_COMMENT,
    HEADER_KEY_ORDER,
    IMAGE_KEY_ORDER,
    PLAN_VERSION,
    REGION_KEY_ORDER,
)


def _yaml() -> YAML:
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.allow_unicode = True
    yaml.width = 4096  # never wrap; wrapped lines are miserable to hand-edit
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _scalar_text(value: str) -> str:
    """Multi-line text becomes a block scalar; empty stays visibly a string."""
    if value == "":
        return cast("str", DoubleQuotedScalarString(""))
    if "\n" in value:
        # Strip-style (|-) so the text round-trips byte for byte.
        return cast("str", LiteralScalarString(value))
    return value


def _polygon_node(region: Region) -> CommentedSeq:
    """Polygons render in flow style: one readable line per region."""
    points = CommentedSeq()
    for x, y in region.polygon:
        point = CommentedSeq([int(x), int(y)])
        point.fa.set_flow_style()
        points.append(point)
    points.fa.set_flow_style()
    return points


def region_to_node(region: Region) -> CommentedMap:
    """One region as an ordered mapping, ready to dump."""
    values: dict[str, object] = {
        "id": region.id,
        "image": region.image,
        "order": region.order,
        "geometry": str(region.geometry),
        "polygon": _polygon_node(region),
        "fill_color": region.fill_color.to_hex(),
        "text_color": region.text_color.to_hex(),
        "confidence": region.confidence,
        "source_text": _scalar_text(region.source_text),
        "translation": _scalar_text(region.translation),
        "notes": _scalar_text(region.notes),
    }
    # Optional keys are emitted only when they carry information, so a plain
    # region stays a short, readable block.
    if region.low_confidence:
        values["low_confidence"] = True
    if region.skip:
        values["skip"] = True
    if region.erase is not None:
        values["erase"] = str(region.erase)
    if region.font is not None:
        values["font"] = region.font
    if region.font_size is not None:
        values["font_size"] = region.font_size

    node = CommentedMap()
    for key in REGION_KEY_ORDER:
        if key in values:
            node[key] = values[key]
    return node


def header_to_node(header: PlanHeader) -> CommentedMap:
    values: dict[str, object] = {
        # The number describes the shape of the file, and this writer only
        # knows how to write one shape, so it says so rather than repeating
        # whatever the header happens to hold. That is what upgrades a version
        # 1 plan: read it, save it, and the file on disk is the current form.
        "version": PLAN_VERSION,
        "generator": header.generator,
        "created": header.created,
        "source_language": header.source_language,
        "target_language": header.target_language,
        "ocr_engine": header.ocr_engine,
        "font": header.font,
        "case": str(header.case),
        "font_size_min_ratio": header.font_size_min_ratio,
        "condense_min": header.condense_min,
    }
    # Driven by the schema's own order, the way the regions and the images are
    # — the point of that module is that the reader and the writer cannot
    # disagree, and a key order written out again here is a second opinion. A
    # header field added there and forgotten here now raises rather than
    # quietly going unwritten.
    node = CommentedMap()
    for key in HEADER_KEY_ORDER:
        node[key] = values[key]
    return node


def image_to_node(image: PlanImage) -> CommentedMap:
    node = CommentedMap()
    values = {"name": image.name, "sha256": image.sha256}
    for key in IMAGE_KEY_ORDER:
        node[key] = values[key]
    return node


def plan_to_node(plan: Plan) -> CommentedMap:
    node = header_to_node(plan.header)
    node["images"] = CommentedSeq(image_to_node(image) for image in plan.images)
    regions = CommentedSeq(region_to_node(region) for region in plan.regions)
    node["regions"] = regions
    node.yaml_set_start_comment(FILE_HEADER_COMMENT)
    return node


def dumps(plan: Plan) -> str:
    """Serialise a plan to a YAML string."""
    stream = io.StringIO()
    _yaml().dump(plan_to_node(plan), stream)
    return stream.getvalue()


def write_plan(plan: Plan, path: Path, *, force: bool = False) -> None:
    """Write a plan file atomically.

    Refuses to clobber an existing plan unless ``force``: that file may hold
    hours of hand translation, and extract has no way to tell.
    """
    if path.exists() and not force:
        raise InputError(
            f"plan file already exists: {path}\n"
            "It may contain translations. Pass --force to overwrite it, or "
            "--plan to write somewhere else."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(dumps(plan), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
