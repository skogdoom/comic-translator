"""Plan file read, write, and validate.

Imports no image library and no OCR backend on purpose: the review GUI's
document layer and any downstream tooling can depend on this package alone.
"""

from __future__ import annotations

from .merge import MergeReport, merge_plans
from .reader import image_problems, load_plan, loads, verify_images
from .schema import PLAN_VERSION, REGION_KEY_ORDER
from .writer import dumps, write_plan

__all__ = [
    "PLAN_VERSION",
    "REGION_KEY_ORDER",
    "MergeReport",
    "dumps",
    "image_problems",
    "load_plan",
    "loads",
    "merge_plans",
    "verify_images",
    "write_plan",
]
