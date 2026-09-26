"""ComicInfo.xml: what a chapter file says about itself, read in and written out.

The file ComicRack invented, which sits at the root of a CBZ or a CBR and
which every comic reader and library looks for. It is a de-facto standard: the
Anansi project keeps a schema for it, two versions of which differ only by
fields nobody here uses, and readers in the wild ignore whatever they do not
recognise. So the rule is the old one — **liberal about what is read,
conservative about what is written** — and each half is below.

**Reading takes what the plan header can hold, and nothing else.** Series,
title, volume, number, year, publisher, writer and reading direction go into
the header; the language is kept aside, for ``extract`` to compare with the
one it was given rather than to replace it (see :attr:`ComicInfo.language`).
Everything else — the summary, the artists, the page list — has no field in a
plan to go into, and is not carried. Being liberal means:

- element names are matched without regard to case or a namespace prefix,
  since both vary between the tools that write this file;
- a field that does not make sense is dropped and the rest are kept — a
  ``Year`` that is not a year costs the year, not the file;
- whitespace inside a value is folded to single spaces, because every field
  it lands in is one line in the plan header dialog.

**Writing puts in what the plan says, and nothing it had to fill in.** One
element per fact the header holds, in the order the schema's sequence lists
them, and none for a fact it does not hold. Two of the schema's types are
narrower than the header's, and each is written only where it fits:

- ``Volume`` is an integer in the schema, and free text in the plan. A volume
  that is not a whole number is left out rather than written as text: Komga,
  for one, maps the file onto a class whose volume is an integer, and
  discards the whole file on any exception while mapping it
  (``ComicInfoProvider.getComicInfo``) — so text there puts the series and
  everything else at the mercy of how it parses a number.
- ``Manga`` is the only element that states a reading direction, and it does
  so as a side effect of saying the book is a manga: ``YesAndRightToLeft``
  means both. Right to left is written as that. Left to right is written as
  nothing, because ``No`` — the only other value that could say it — says
  "not a manga", which the plan does not know; and left to right is what
  every reader does with a book that does not say. Reading is the other way
  round and takes ``No`` as left to right, as Komga does.

``LanguageISO`` is the target language, since what is packed is the
translation.

**No document type is read at all.** The one XML attack a file of this size
still carries is entity expansion — a few hundred bytes declaring an entity
that expands into gigabytes — and entities are declared in a document type.
ComicInfo.xml has no use for one, so a file that begins one is refused
before anything in it is declared, rather than trusting the parser's own
limits to hold. :data:`MAX_BYTES` bounds everything else, and is checked
against what the archive claims before anything is decompressed, as a page's
size is.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, fields, replace
from xml.etree import ElementTree
from xml.parsers import expat

from .model import PlanHeader, ReadingDirection
from .ocr.tesseract import tesseract_name
from .planfile.schema import YEAR_RANGE

log = logging.getLogger(__name__)

FILENAME = "ComicInfo.xml"
"""What the file is called, at the root of the archive.

Written exactly so, because a reader may look for it by exact name — Komga
does. Read without regard to case, because not every writer spells it so."""

MAX_BYTES = 1024 * 1024
"""How large a ComicInfo.xml may say it unpacks to before it is not read.

A megabyte. The largest thing in one is the page list, one element per page:
built in ComicRack's own shape, with every page's size and dimensions and a
thousand-byte summary beside it, a thousand pages came to 81KB. So this is
twelve times a thousand-page chapter's, and still a size nobody minds holding
in memory while it is parsed."""

_WHOLE_NUMBER = re.compile(r"[0-9]{1,9}")
"""A volume the schema's ``xs:int`` holds: digits only, and few enough of them
that no reader overflows on it."""

_NOT_XML = re.compile(r"[^\t\n\r\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")
"""Characters XML 1.0 cannot carry at all, not even escaped. A plan file can
hold one — YAML escapes anything — and one in a value would make the whole
file unreadable to every reader, which is worse than losing the character."""

_UNSET = "-1"
"""What the schema gives ``Volume`` and ``Year`` when they are not set, and
some writers therefore write out. It means nothing is known."""


class ComicInfoError(ValueError):
    """A ComicInfo.xml that could not be read at all; the message says why."""


@dataclass(frozen=True, slots=True)
class ComicInfo:
    """What a chapter's ComicInfo.xml says, as far as a plan can hold it.

    Empty and ``None`` mean the file did not say — or said something that
    was not one — exactly as they do on :class:`PlanHeader`, whose fields
    these are.
    """

    series: str = ""
    title: str = ""
    volume: str = ""
    number: str = ""
    year: int | None = None
    publisher: str = ""
    writer: str = ""
    reading_direction: ReadingDirection | None = None

    language: str = ""
    """The language the file says the book is in, as it wrote it.

    Not a header field, and deliberately not put into one: the language a
    chapter is read in is chosen before it is unpacked, and recognition runs
    in that one. A plan claiming a language its text was not read in would
    be wrong in a way nothing would notice. So this is compared with it
    instead — see :func:`differs` — and the difference is reported."""

    def stated(self) -> tuple[str, ...]:
        """The names of the header fields this says something about, in order."""
        return tuple(
            each.name
            for each in fields(self)
            if each.name != "language" and getattr(self, each.name) not in ("", None)
        )


def _local(name: str) -> str:
    """An element's name without a namespace prefix, folded."""
    return name.rpartition(":")[2].casefold()


def _refuse_doctype(*_args: object) -> None:
    raise ComicInfoError(
        "it declares a document type, which ComicInfo.xml has no use for and "
        "which is how an XML file is made to expand without limit"
    )


def _elements(data: bytes) -> dict[str, str]:
    """The root's direct children, by folded name, first non-empty value each."""
    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = _refuse_doctype
    depth = 0
    root = ""
    current = ""
    text: list[str] = []
    found: dict[str, str] = {}

    def start(name: str, _attributes: dict[str, str]) -> None:
        nonlocal depth, root, current
        depth += 1
        if depth == 1:
            root = name
        elif depth == 2:
            current = _local(name)
            text.clear()

    def end(_name: str) -> None:
        nonlocal depth
        if depth == 2:
            value = " ".join("".join(text).split())
            if value and current not in found:
                found[current] = value
        depth -= 1

    def characters(data: str) -> None:
        if depth == 2:
            text.append(data)

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = characters
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise ComicInfoError(f"it is not well-formed XML ({exc})") from exc
    if _local(root) != "comicinfo":
        raise ComicInfoError(f"its root element is <{root}>, not <ComicInfo>")
    return found


def _year(text: str) -> int | None:
    try:
        year = int(text)
    except ValueError:
        return None
    low, high = YEAR_RANGE
    return year if low <= year <= high else None


def _direction(text: str) -> ReadingDirection | None:
    folded = text.casefold()
    if folded == "yesandrighttoleft":
        return ReadingDirection.RIGHT_TO_LEFT
    if folded == "no":
        return ReadingDirection.LEFT_TO_RIGHT
    return None


def read_comic_info(data: bytes) -> ComicInfo:
    """What ``data``, a ComicInfo.xml, says. :class:`ComicInfoError` if nothing.

    A file that is XML with a ``ComicInfo`` root is read, however little of
    it makes sense; one that is not either is refused, with the reason.
    """
    found = _elements(data)
    volume = found.get("volume", "")
    return ComicInfo(
        series=found.get("series", ""),
        title=found.get("title", ""),
        volume="" if volume == _UNSET else volume,
        number=found.get("number", ""),
        year=_year(found.get("year", "")),
        publisher=found.get("publisher", ""),
        writer=found.get("writer", ""),
        reading_direction=_direction(found.get("manga", "")),
        language=found.get("languageiso", ""),
    )


def with_comic_info(header: PlanHeader, info: ComicInfo) -> PlanHeader:
    """``header`` with every detail ``info`` states put in it.

    Only what the file states: a field it says nothing about keeps what the
    header had. The language is not among them — see
    :attr:`ComicInfo.language`.
    """
    return replace(header, **{name: getattr(info, name) for name in info.stated()})


def differs(stated: str, read_in: Sequence[str]) -> bool:
    """Whether a file's language is none of the ones a chapter was read in.

    ``read_in`` is what the recogniser was asked for — the OCR languages, or
    the source language when none were given — since that is the choice a
    wrong language spoils. The same language to the recogniser is the same
    language here: ``ja`` and ``jpn`` are one, and so are ``en`` and
    ``en-US``, while ``zh-Hans`` and ``zh-Hant`` are two, because they are
    read with two different models. Nothing stated is no difference.
    """
    if not stated.strip():
        return False
    return tesseract_name(stated) not in {tesseract_name(language) for language in read_in}


def _written(text: str) -> str:
    return _NOT_XML.sub("", text).strip()


def comic_info_xml(header: PlanHeader) -> bytes:
    """The ComicInfo.xml for a chapter this plan describes, as UTF-8.

    An element for each fact the header holds and none for what it leaves
    empty, in the schema's order. See the module docstring for the two that
    are written only where the schema's type holds them.
    """
    volume = header.volume.strip()
    if volume and not _WHOLE_NUMBER.fullmatch(volume):
        log.warning(
            "volume %r is not written into %s: the format holds a volume only as a whole number",
            volume,
            FILENAME,
        )
    rows: list[tuple[str, str]] = [
        ("Title", header.title),
        ("Series", header.series),
        ("Number", header.number),
        ("Volume", volume if _WHOLE_NUMBER.fullmatch(volume) else ""),
        ("Year", "" if header.year is None else str(header.year)),
        ("Writer", header.writer),
        ("Publisher", header.publisher),
        ("LanguageISO", header.target_language),
        (
            "Manga",
            "YesAndRightToLeft"
            if header.reading_direction is ReadingDirection.RIGHT_TO_LEFT
            else "",
        ),
    ]
    root = ElementTree.Element("ComicInfo")
    for element, value in rows:
        text = _written(value)
        if text:
            ElementTree.SubElement(root, element).text = text
    ElementTree.indent(root)
    document: bytes = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    return document + b"\n"


__all__ = [
    "FILENAME",
    "MAX_BYTES",
    "ComicInfo",
    "ComicInfoError",
    "comic_info_xml",
    "differs",
    "read_comic_info",
    "with_comic_info",
]
