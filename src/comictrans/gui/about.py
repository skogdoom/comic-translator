"""What the About dialog says: version, author, licence, libraries.

Free of Qt, like ``document.py`` and ``preview.py``, so the awkward part —
working out what is actually installed — is tested without a display.

Everything here is read from the installed distribution's own metadata at
run time rather than written down a second time. A hardcoded library list is
wrong the first time a resolve moves a version, and a hardcoded version is
wrong the first time someone bumps one of the two places it used to live.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, metadata, requires, version

from .. import __version__

DISTRIBUTION = "comictrans"

_REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9._-]+")
"""The name at the head of a requirement, before any version or marker.

Requirements arrive as ``"pillow>=10.3"`` or
``"pyside6>=6.7; extra == 'gui'"``. Matching the name rather than parsing the
whole grammar keeps ``packaging`` out of the runtime dependencies for the
sake of one field — and these are this project's own requirements, not
arbitrary ones off the internet.
"""


@dataclass(frozen=True, slots=True)
class Library:
    """One installed dependency, as the About dialog lists it."""

    name: str
    version: str


def package_version() -> str:
    """The version of comictrans, from the installed distribution.

    Falls back to ``comictrans.__version__`` when there is no distribution to
    ask — running straight from a source tree that was never installed. The
    two cannot disagree: hatch reads the version out of that same module.
    """
    try:
        return version(DISTRIBUTION)
    except PackageNotFoundError:
        return __version__


def _field(name: str, default: str) -> str:
    try:
        return metadata(DISTRIBUTION).get(name) or default
    except PackageNotFoundError:
        return default


def author() -> str:
    return _field("Author", "unknown")


def summary() -> str:
    return _field("Summary", "")


def licence() -> str:
    """The licence identifier. The full text is in ``LICENSE``.

    Deliberately the identifier and not the text. Copying the text into a
    string here would be a second copy to drift out of step with the file,
    and reading the file at run time works in a source checkout but not in a
    wheel or an application bundle, which is exactly where an About dialog
    gets read.
    """
    return _field("License", "MIT")


def libraries() -> tuple[Library, ...]:
    """The declared dependencies that are actually installed, with versions.

    Declared rather than transitive: these are the libraries this tool chose,
    and the list stays the same size as the project rather than growing to
    whatever the resolver dragged in. Ones that are absent are left out
    entirely — the optional extras, and the pyobjc packages that only exist
    on macOS, are not libraries this run is using.
    """
    try:
        declared = requires(DISTRIBUTION) or []
    except PackageNotFoundError:
        return ()

    found: dict[str, Library] = {}
    for requirement in declared:
        match = _REQUIREMENT_NAME.match(requirement)
        if match is None:
            continue
        name = match.group()
        if name in found:
            continue
        try:
            found[name] = Library(name=name, version=version(name))
        except PackageNotFoundError:
            continue
    return tuple(sorted(found.values(), key=lambda library: library.name.lower()))
