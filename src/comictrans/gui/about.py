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

NAME = "Comic Translator"
"""What the application calls itself: the window title, the About box.

A different fact from ``DISTRIBUTION``, which is what to ask the package
metadata about and stays `comictrans` — that is the name on PyPI and the
name of the command. ``comictrans review`` is neither of these: it is how
this window is opened, and the CLI keeps it exactly as it is.

Everything that shows a name reads it from here, so this constant is the
whole of what it takes to rename the application.
"""

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
    """The version of comictrans, from the module that defines it.

    Not from the installed distribution, which is a *copy* made when the
    package was installed and can be older than the code beside it. This
    asked the metadata first until an application bundle shipped reporting
    0.1.0 while the same build's crash log banner — which reads
    ``__version__`` — said 1.0.0. Reproduced afterwards in three lines: edit
    ``__version__``, import without reinstalling, and the two disagree.

    An editable install is where it bites. Hatch derives the distribution's
    version from this module at install time, so the copy is right when it is
    made and stale from the next edit until something reinstalls; a build
    that happens in between carries the stale one into the bundle, where
    there is nothing to reinstall it.

    There is no case the other way round. The metadata is derived from this
    string, so it is never more current than the string, and the About box
    should not be the only place in the application reporting a different
    version from the log.
    """
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
