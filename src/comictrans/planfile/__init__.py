"""Plan file read, write, and validate.

Imports no image library and no OCR backend on purpose: the future review GUI
and any downstream tooling can depend on this package alone.
"""

from __future__ import annotations

from .reader import load_plan, loads, verify_images
from .schema import PLAN_VERSION, REGION_KEY_ORDER
from .writer import dumps, write_plan

__all__ = [
    "PLAN_VERSION",
    "REGION_KEY_ORDER",
    "dumps",
    "load_plan",
    "loads",
    "verify_images",
    "write_plan",
]
