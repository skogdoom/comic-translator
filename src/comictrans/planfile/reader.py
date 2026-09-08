"""Reading and validating plan files.

Every failure carries the line you need to go and fix. A plan file is meant to
be hand-edited, which means typos are normal traffic, not exceptional — a
silent shrug or a bare KeyError would waste your time on every one of them.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError

from ..errors import PlanError
from ..model import (
    Color,
    Geometry,
    Plan,
    PlanHeader,
    PlanImage,
    Point,
    Polygon,
    Region,
    TextCase,
    polygon_is_simple,
)
from ..util import sha256_file
from .schema import (
    CONDENSE_MIN_RANGE,
    FONT_SIZE_MIN_RATIO_RANGE,
    IMAGE_KEYS,
    LEGACY_REGION_KEYS,
    MIN_POLYGON_POINTS,
    PLAN_VERSION,
    READABLE_VERSIONS,
    REGION_KEYS,
    REQUIRED_REGION_KEYS,
    TOP_LEVEL_KEYS,
)

_VALID_GEOMETRY = {str(value) for value in Geometry}
_VALID_CASE = {str(value) for value in TextCase}


def _line_of(node: Any, key: str | None = None) -> int | None:
    """1-based line of ``key`` within ``node``, or of the node itself."""
    lc = getattr(node, "lc", None)
    if lc is None:
        return None
    if key is not None:
        try:
            mark = lc.key(key)
        except (KeyError, AttributeError, TypeError):
            mark = None
        if mark is not None:
            return int(mark[0]) + 1
    line = getattr(lc, "line", None)
    return int(line) + 1 if line is not None else None


def _item_line(node: Any, index: int) -> int | None:
    lc = getattr(node, "lc", None)
    if lc is None:
        return None
    try:
        return int(lc.item(index)[0]) + 1
    except (KeyError, IndexError, AttributeError, TypeError):
        return _line_of(node)


class _Cursor:
    """A mapping being validated, plus where it came from."""

    def __init__(self, node: Any, path: Path | None, label: str) -> None:
        self.node = node
        self.path = path
        self.label = label

    def fail(self, message: str, key: str | None = None) -> PlanError:
        return PlanError(f"{self.label}: {message}", path=self.path, line=_line_of(self.node, key))

    def get(self, key: str) -> Any:
        if key not in self.node:
            raise PlanError(
                f"{self.label}: missing required key {key!r}",
                path=self.path,
                line=_line_of(self.node),
            )
        return self.node[key]

    def string(self, key: str, *, allow_empty: bool = True) -> str:
        value = self.get(key)
        if not isinstance(value, str):
            raise self.fail(f"{key} must be text, got {type(value).__name__}", key)
        if not allow_empty and not value.strip():
            raise self.fail(f"{key} must not be empty", key)
        return value

    def integer(self, key: str, *, minimum: int | None = None) -> int:
        value = self.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise self.fail(f"{key} must be a whole number, got {value!r}", key)
        if minimum is not None and value < minimum:
            raise self.fail(f"{key} must be >= {minimum}, got {value}", key)
        return value

    def number(self, key: str, *, minimum: float, maximum: float) -> float:
        value = self.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise self.fail(f"{key} must be a number, got {value!r}", key)
        if not minimum <= float(value) <= maximum:
            raise self.fail(f"{key} must be between {minimum} and {maximum}, got {value}", key)
        return float(value)

    def flag(self, key: str, default: bool = False) -> bool:
        if key not in self.node:
            return default
        value = self.node[key]
        if not isinstance(value, bool):
            raise self.fail(f"{key} must be true or false, got {value!r}", key)
        return value

    def color(self, key: str) -> Color:
        value = self.get(key)
        if not isinstance(value, str):
            raise self.fail(f"{key} must be a hex colour like '#ffffff'", key)
        try:
            return Color.from_hex(value)
        except ValueError as exc:
            raise self.fail(f"{key}: {exc}", key) from None

    def choice(self, key: str, allowed: set[str]) -> str:
        value = self.string(key)
        if value not in allowed:
            raise self.fail(
                f"{key} must be one of {', '.join(sorted(allowed))}, got {value!r}", key
            )
        return value

    def reject_unknown(self, allowed: frozenset[str]) -> None:
        for key in self.node:
            if key not in allowed:
                raise PlanError(
                    f"{self.label}: unknown key {key!r}. Valid keys: {', '.join(sorted(allowed))}",
                    path=self.path,
                    line=_line_of(self.node, str(key)),
                )


def _parse_polygon(cursor: _Cursor) -> Polygon:
    raw = cursor.get("polygon")
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise cursor.fail("polygon must be a list of [x, y] points", "polygon")
    if len(raw) < MIN_POLYGON_POINTS:
        raise cursor.fail(
            f"polygon needs at least {MIN_POLYGON_POINTS} points, got {len(raw)}", "polygon"
        )

    points: list[Point] = []
    for index, item in enumerate(raw):
        line = _item_line(raw, index)
        if not isinstance(item, Sequence) or isinstance(item, str) or len(item) != 2:
            raise PlanError(
                f"{cursor.label}: polygon point {index} must be [x, y], got {item!r}",
                path=cursor.path,
                line=line,
            )
        x, y = item
        if (
            isinstance(x, bool)
            or isinstance(y, bool)
            or not (isinstance(x, int) and isinstance(y, int))
        ):
            raise PlanError(
                f"{cursor.label}: polygon point {index} must be whole pixels, got {item!r}",
                path=cursor.path,
                line=line,
            )
        if x < 0 or y < 0:
            raise PlanError(
                f"{cursor.label}: polygon point {index} is off the page: {item!r}",
                path=cursor.path,
                line=line,
            )
        points.append((int(x), int(y)))

    polygon = tuple(points)
    if not polygon_is_simple(polygon):
        raise cursor.fail("polygon is self-intersecting or degenerate", "polygon")
    return polygon


def _parse_image(node: Any, index: int, path: Path | None) -> PlanImage:
    label = f"images[{index}]"
    if not hasattr(node, "keys"):
        raise PlanError(f"{label} must be a mapping, got {type(node).__name__}", path=path)

    cursor = _Cursor(node, path, label)
    cursor.reject_unknown(IMAGE_KEYS)
    missing = IMAGE_KEYS - set(node.keys())
    if missing:
        raise PlanError(
            f"{label}: missing required key(s) {', '.join(sorted(missing))}",
            path=path,
            line=_line_of(node),
        )
    return PlanImage(
        name=cursor.string("name", allow_empty=False),
        sha256=cursor.string("sha256", allow_empty=False),
    )


def _images_from_regions(raw_regions: Sequence[Any], path: Path | None) -> tuple[PlanImage, ...]:
    """Rebuild the images list a version 1 file does not carry.

    It has the same facts, spread across the regions: each one names its image
    and the hash that image had. First-seen order, which for a plan written by
    extract is the order the pages were read.
    """
    found: dict[str, str] = {}
    for index, node in enumerate(raw_regions):
        if not hasattr(node, "keys"):
            raise PlanError(f"region[{index}] must be a mapping", path=path)
        name, digest = node.get("image"), node.get("image_sha256")
        if not isinstance(name, str) or not isinstance(digest, str):
            raise PlanError(
                f"region[{index}]: a version 1 plan needs image and image_sha256",
                path=path,
                line=_line_of(node),
            )
        found.setdefault(name, digest)
    return tuple(PlanImage(name=name, sha256=digest) for name, digest in found.items())


def _parse_region(
    node: Any, index: int, path: Path | None, *, allowed_keys: frozenset[str]
) -> Region:
    label = f"region[{index}]"
    if not hasattr(node, "keys"):
        raise PlanError(f"{label} must be a mapping, got {type(node).__name__}", path=path)

    cursor = _Cursor(node, path, label)
    cursor.reject_unknown(allowed_keys)
    missing = REQUIRED_REGION_KEYS - set(node.keys())
    if missing:
        raise PlanError(
            f"{label}: missing required key(s) {', '.join(sorted(missing))}",
            path=path,
            line=_line_of(node),
        )

    region_id = cursor.string("id", allow_empty=False)
    cursor.label = f"region {region_id!r}"

    font_size = cursor.integer("font_size", minimum=1) if "font_size" in node else None
    font = cursor.string("font", allow_empty=False) if "font" in node else None

    return Region(
        id=region_id,
        image=cursor.string("image", allow_empty=False),
        order=cursor.integer("order", minimum=0),
        geometry=Geometry(cursor.choice("geometry", _VALID_GEOMETRY)),
        polygon=_parse_polygon(cursor),
        fill_color=cursor.color("fill_color"),
        text_color=cursor.color("text_color"),
        confidence=cursor.number("confidence", minimum=0.0, maximum=1.0),
        source_text=cursor.string("source_text"),
        translation=cursor.string("translation"),
        notes=cursor.string("notes") if "notes" in node else "",
        low_confidence=cursor.flag("low_confidence"),
        skip=cursor.flag("skip"),
        font=font,
        font_size=font_size,
    )


def _parse_header(node: Any, path: Path | None) -> PlanHeader:
    cursor = _Cursor(node, path, "header")
    version = cursor.integer("version", minimum=1)
    if version not in READABLE_VERSIONS:
        readable = ", ".join(str(v) for v in sorted(READABLE_VERSIONS))
        raise cursor.fail(
            f"unsupported plan version {version}; this build writes version "
            f"{PLAN_VERSION} and reads {readable}",
            "version",
        )
    return PlanHeader(
        # Always the current version in memory: a version 1 file is upgraded
        # as it is read, and saving it writes the upgraded form.
        version=PLAN_VERSION,
        generator=cursor.string("generator"),
        created=cursor.string("created"),
        source_language=cursor.string("source_language", allow_empty=False),
        target_language=cursor.string("target_language", allow_empty=False),
        ocr_engine=cursor.string("ocr_engine"),
        font=cursor.string("font", allow_empty=False),
        case=TextCase(cursor.choice("case", _VALID_CASE)),
        font_size_min_ratio=cursor.number(
            "font_size_min_ratio",
            minimum=FONT_SIZE_MIN_RATIO_RANGE[0],
            maximum=FONT_SIZE_MIN_RATIO_RANGE[1],
        ),
        condense_min=cursor.number(
            "condense_min", minimum=CONDENSE_MIN_RANGE[0], maximum=CONDENSE_MIN_RANGE[1]
        ),
    )


def loads(text: str, *, path: Path | None = None) -> Plan:
    """Parse and validate a plan from a string."""
    yaml = YAML(typ="rt")
    try:
        data = yaml.load(text)
    except MarkedYAMLError as exc:
        mark = exc.problem_mark
        raise PlanError(
            f"YAML syntax error: {exc.problem}",
            path=path,
            line=(mark.line + 1) if mark is not None else None,
        ) from None

    if data is None:
        raise PlanError("plan file is empty", path=path)
    if not hasattr(data, "keys"):
        raise PlanError("expected a mapping at the top level", path=path, line=1)

    root = _Cursor(data, path, "plan")
    root.reject_unknown(TOP_LEVEL_KEYS)
    header = _parse_header(data, path)
    written_version = data.get("version")

    raw_regions = data.get("regions")
    if raw_regions is None:
        raise PlanError("missing required key 'regions'", path=path, line=_line_of(data))
    if not isinstance(raw_regions, Sequence) or isinstance(raw_regions, str):
        raise PlanError("'regions' must be a list", path=path, line=_line_of(data, "regions"))

    images = _parse_images(data, raw_regions, written_version, path)

    # A version 1 region carries its image's hash; a version 2 one does not,
    # because the images list owns it now.
    allowed = LEGACY_REGION_KEYS if written_version == 1 else REGION_KEYS
    known_images = {image.name for image in images}

    regions: list[Region] = []
    seen: dict[str, int] = {}
    for index, item in enumerate(raw_regions):
        region = _parse_region(item, index, path, allowed_keys=allowed)
        if region.id in seen:
            raise PlanError(
                f"duplicate region id {region.id!r} (first seen at region[{seen[region.id]}])",
                path=path,
                line=_item_line(raw_regions, index),
            )
        if region.image not in known_images:
            raise PlanError(
                f"region {region.id!r}: image {region.image!r} is not in the plan's images",
                path=path,
                line=_item_line(raw_regions, index),
            )
        seen[region.id] = index
        regions.append(region)

    return Plan(header=header, images=images, regions=tuple(regions))


def _parse_images(
    data: Any, raw_regions: Sequence[Any], written_version: object, path: Path | None
) -> tuple[PlanImage, ...]:
    raw_images = data.get("images")
    if written_version == 1:
        if raw_images is not None:
            raise PlanError(
                "a version 1 plan does not have an 'images' list; set version: 2 to use one",
                path=path,
                line=_line_of(data, "images"),
            )
        return _images_from_regions(raw_regions, path)

    if raw_images is None:
        raise PlanError("missing required key 'images'", path=path, line=_line_of(data))
    if not isinstance(raw_images, Sequence) or isinstance(raw_images, str):
        raise PlanError("'images' must be a list", path=path, line=_line_of(data, "images"))

    images = tuple(_parse_image(item, index, path) for index, item in enumerate(raw_images))
    names: set[str] = set()
    for image in images:
        if image.name in names:
            raise PlanError(f"duplicate image {image.name!r} in 'images'", path=path)
        names.add(image.name)
    return images


def verify_images(plan: Plan, plan_path: Path) -> None:
    """Check that every referenced image exists and still hashes the same.

    A changed source image means the polygons in the plan no longer describe
    the pixels they were measured from. Rendering anyway would put text in the
    wrong place, so this is an error rather than a warning.
    """
    base = plan_path.parent
    for image in plan.images:
        resolved = (base / image.name).resolve()
        if not resolved.is_file():
            raise PlanError(
                f"source image not found: {image.name} (resolved to {resolved})", path=plan_path
            )
        actual = sha256_file(resolved)
        if image.sha256 != actual:
            raise PlanError(
                f"{image.name} has changed since extract "
                f"(expected {image.sha256[:12]}…, found {actual[:12]}…). "
                "Re-run extract, or restore the original image.",
                path=plan_path,
            )


def load_plan(path: Path, *, check_images: bool = True) -> Plan:
    """Load, validate, and optionally hash-check a plan file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanError(f"cannot read plan file: {exc}", path=path) from exc
    except UnicodeDecodeError as exc:
        raise PlanError(f"plan file is not valid UTF-8: {exc}", path=path) from exc

    plan = loads(text, path=path)
    if check_images:
        verify_images(plan, path)
    return plan
