"""Small helpers with no domain knowledge."""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from pathlib import Path

_DIGITS = re.compile(r"(\d+)")
_NON_SLUG = re.compile(r"[^a-z0-9]+")

_HASH_CHUNK = 1 << 20


def natural_key(name: str) -> tuple[tuple[int, str | int], ...]:
    """Sort key putting ``page2`` before ``page10``.

    Digit runs compare numerically, everything else case-insensitively. The
    leading tag keeps ints and strs out of the same comparison.
    """
    parts: list[tuple[int, str | int]] = []
    for chunk in _DIGITS.split(name):
        if not chunk:
            continue
        if chunk.isdigit():
            parts.append((0, int(chunk)))
        else:
            parts.append((1, chunk.casefold()))
    return tuple(parts)


def slugify(text: str) -> str:
    """ASCII, lowercase, hyphen-separated. Used for region ids."""
    normalised = unicodedata.normalize("NFKD", text)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    slug = _NON_SLUG.sub("-", ascii_only.lower()).strip("-")
    return slug or "page"


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file's bytes. Opens read-only and never seeks past EOF."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def is_within(child: Path, parent: Path) -> bool:
    """True if ``child`` is ``parent`` or lives underneath it.

    Both paths are resolved first, so symlinks and ``..`` cannot be used to
    sneak an output directory into the source tree.
    """
    child_resolved = child.resolve()
    parent_resolved = parent.resolve()
    return child_resolved == parent_resolved or parent_resolved in child_resolved.parents


def relative_posix(path: Path, start: Path) -> str:
    """``path`` relative to ``start`` as a POSIX string, with ``..`` if needed.

    Plan files store image paths this way so a plan directory can be moved
    wholesale without rewriting it.
    """
    return Path(os.path.relpath(path.resolve(), start.resolve())).as_posix()
