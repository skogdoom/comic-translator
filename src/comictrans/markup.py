"""Emphasis markup inside a translation.

``**like this**`` renders bold. There is no italic: the spec is explicit that
emphasis is bold and the oblique face is never used, so there is nothing else
to parse. A literal asterisk is ``\\*``.

An unbalanced ``**`` is an error rather than a guess. Silently bolding the
rest of a balloon because of a typo is exactly the kind of quiet wrong output
the plan file exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ComictransError
from .model import TextCase

MARKER = "**"


class MarkupError(ComictransError):
    """A translation's emphasis markup is malformed."""


@dataclass(frozen=True, slots=True)
class Run:
    """A stretch of text at one weight."""

    text: str
    bold: bool


@dataclass(frozen=True, slots=True)
class Token:
    """One whitespace-delimited word.

    A word can span a markup boundary — ``**SHOUT**,`` is one word whose comma
    is not bold — so a token carries segments rather than a single weight.
    Splitting on runs first would put a space before that comma.
    """

    segments: tuple[Run, ...]

    @property
    def text(self) -> str:
        return "".join(segment.text for segment in self.segments)

    @property
    def uniform_weight(self) -> bool:
        """True when the whole word is one weight, so it can be hyphenated."""
        return len({segment.bold for segment in self.segments}) <= 1

    @property
    def bold(self) -> bool:
        return bool(self.segments) and self.segments[0].bold

    def replaced(self, text: str) -> Token:
        """The same word with different text, keeping its (single) weight."""
        return Token((Run(text, self.bold),))


def parse_runs(text: str) -> tuple[Run, ...]:
    """Split ``text`` into weighted runs, resolving escapes."""
    runs: list[Run] = []
    buffer: list[str] = []
    bold = False
    index = 0
    while index < len(text):
        if text[index] == "\\" and text.startswith(MARKER[0], index + 1):
            buffer.append(MARKER[0])
            index += 2
            continue
        if text.startswith(MARKER, index):
            runs.append(Run("".join(buffer), bold))
            buffer = []
            bold = not bold
            index += len(MARKER)
            continue
        buffer.append(text[index])
        index += 1

    if bold:
        raise MarkupError(
            f"unbalanced '{MARKER}' in translation: {text!r}. "
            f"Close the emphasis, or write '\\*' for a literal asterisk."
        )
    runs.append(Run("".join(buffer), bold))
    return tuple(run for run in runs if run.text)


def tokenize(text: str, *, case: TextCase = TextCase.PRESERVE) -> tuple[Token, ...]:
    """Words with their weight, cased for output.

    Line breaks in a translation are advisory: typeset reflows to fit the
    polygon, so they are treated as spaces.
    """
    characters: list[tuple[str, bool]] = []
    for run in parse_runs(text):
        content = run.text.upper() if case is TextCase.UPPER else run.text
        characters.extend((character, run.bold) for character in content)

    tokens: list[Token] = []
    segments: list[Run] = []
    buffer: list[str] = []
    weight = False

    def flush_segment() -> None:
        nonlocal buffer
        if buffer:
            segments.append(Run("".join(buffer), weight))
            buffer = []

    def flush_token() -> None:
        nonlocal segments
        flush_segment()
        if segments:
            tokens.append(Token(tuple(segments)))
            segments = []

    for character, bold in characters:
        if character.isspace():
            flush_token()
            continue
        if buffer and bold != weight:
            flush_segment()
        weight = bold
        buffer.append(character)
    flush_token()
    return tuple(tokens)


def plain_text(text: str) -> str:
    """The translation with its markup removed, for logs and messages."""
    return "".join(run.text for run in parse_runs(text))
