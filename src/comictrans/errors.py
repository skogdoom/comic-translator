"""Exception hierarchy.

Every error the CLI is expected to survive derives from :class:`ComictransError`.
Anything else escaping to the top level is a bug and gets a traceback.
"""

from __future__ import annotations

from pathlib import Path


class ComictransError(Exception):
    """Base class for expected, reportable failures."""


class InputError(ComictransError):
    """Bad CLI input: missing path, unreadable image, unsafe output directory."""


class PlanError(ComictransError):
    """A plan file is malformed.

    Carries the offending location so the message can point at a line the user
    can actually go and edit.
    """

    def __init__(self, message: str, *, path: Path | None = None, line: int | None = None) -> None:
        self.message = message
        self.path = path
        self.line = line
        super().__init__(str(self))

    def __str__(self) -> str:
        where = ""
        if self.path is not None:
            where = str(self.path)
            if self.line is not None:
                where = f"{where}:{self.line}"
        elif self.line is not None:
            where = f"line {self.line}"
        return f"{where}: {self.message}" if where else self.message


class OcrUnavailableError(ComictransError):
    """No usable OCR backend."""


class FontError(ComictransError):
    """A requested font (or its bold face) could not be resolved."""


class GuiUnavailableError(ComictransError):
    """PySide6 is not installed, or a display could not be opened."""
