"""ComicInfo.xml, read liberally and written conservatively."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree

import pytest

from comictrans.comicinfo import (
    ComicInfo,
    ComicInfoError,
    comic_info_xml,
    differs,
    read_comic_info,
    with_comic_info,
)
from comictrans.model import PlanHeader, ReadingDirection, TextCase
from comictrans.planfile.schema import PLAN_VERSION

SCHEMA_ORDER = (
    "Title",
    "Series",
    "Number",
    "Count",
    "Volume",
    "AlternateSeries",
    "AlternateNumber",
    "AlternateCount",
    "Summary",
    "Notes",
    "Year",
    "Month",
    "Day",
    "Writer",
    "Penciller",
    "Inker",
    "Colorist",
    "Letterer",
    "CoverArtist",
    "Editor",
    "Translator",
    "Publisher",
    "Imprint",
    "Genre",
    "Tags",
    "Web",
    "PageCount",
    "LanguageISO",
    "Format",
    "BlackAndWhite",
    "Manga",
    "Characters",
    "Teams",
    "Locations",
    "ScanInformation",
    "StoryArc",
    "StoryArcNumber",
    "SeriesGroup",
    "AgeRating",
    "Pages",
    "CommunityRating",
    "MainCharacterOrTeam",
    "Review",
    "GTIN",
)
"""The element sequence of the Anansi project's ComicInfo v2.1 schema, copied
out of ``drafts/v2.1/ComicInfo.xsd``. An ``xs:sequence``, so a validating
reader holds a file to this order; v2.0's is the same less four fields."""

COMIC_RACK = b"""<?xml version="1.0" encoding="utf-8"?>
<ComicInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema"
           xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Title>The Long Night</Title>
  <Series>Dylan Dog</Series>
  <Number>12</Number>
  <Count>400</Count>
  <Volume>1</Volume>
  <Summary>An investigator of nightmares.</Summary>
  <Year>1987</Year>
  <Month>9</Month>
  <Writer>Tiziano Sclavi</Writer>
  <Penciller>Angelo Stano</Penciller>
  <Publisher>Sergio Bonelli Editore</Publisher>
  <PageCount>98</PageCount>
  <LanguageISO>it</LanguageISO>
  <Manga>No</Manga>
  <Pages>
    <Page Image="0" Type="FrontCover" ImageSize="512000" />
    <Page Image="1" ImageSize="498000" />
  </Pages>
</ComicInfo>
"""
"""Shaped like what ComicRack writes: namespace declarations on the root,
more fields than a plan holds, and a page list."""


def _header(**overrides: object) -> PlanHeader:
    base = PlanHeader(
        version=PLAN_VERSION,
        generator="comictrans test",
        created="2026-09-26T12:00:00Z",
        source_language="it",
        target_language="en",
        ocr_engine="fake",
        font="Comic Sans MS",
        case=TextCase.UPPER,
        font_size_min_ratio=0.012,
        condense_min=0.9,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def _elements(document: bytes) -> list[tuple[str, str]]:
    root = ElementTree.fromstring(document)
    assert root.tag == "ComicInfo"
    return [(child.tag, child.text or "") for child in root]


# -- reading ---------------------------------------------------------------


def test_what_a_plan_can_hold_is_read_and_the_rest_is_not() -> None:
    assert read_comic_info(COMIC_RACK) == ComicInfo(
        series="Dylan Dog",
        title="The Long Night",
        volume="1",
        number="12",
        year=1987,
        publisher="Sergio Bonelli Editore",
        writer="Tiziano Sclavi",
        reading_direction=ReadingDirection.LEFT_TO_RIGHT,
        language="it",
    )


def test_names_are_read_whatever_their_case_or_prefix() -> None:
    """Both vary between the tools that write this file."""
    document = b"""<ci:comicinfo xmlns:ci="urn:example">
      <ci:SERIES>Tex</ci:SERIES><number>7</number><languageiso>it</languageiso>
    </ci:comicinfo>"""

    assert read_comic_info(document) == ComicInfo(series="Tex", number="7", language="it")


@pytest.mark.parametrize(
    ("manga", "direction"),
    [
        ("YesAndRightToLeft", ReadingDirection.RIGHT_TO_LEFT),
        ("yesandrighttoleft", ReadingDirection.RIGHT_TO_LEFT),
        ("No", ReadingDirection.LEFT_TO_RIGHT),
        # "Yes" says it is a manga and nothing about which way it reads.
        ("Yes", None),
        ("Unknown", None),
        ("Sideways", None),
    ],
)
def test_the_reading_direction_is_read_from_manga(
    manga: str, direction: ReadingDirection | None
) -> None:
    document = f"<ComicInfo><Manga>{manga}</Manga></ComicInfo>".encode()

    assert read_comic_info(document).reading_direction is direction


def test_a_field_that_makes_no_sense_costs_that_field_and_not_the_file() -> None:
    document = b"""<ComicInfo>
      <Series>Tex</Series><Year>nineteen</Year><Volume>-1</Volume>
    </ComicInfo>"""

    assert read_comic_info(document) == ComicInfo(series="Tex")


@pytest.mark.parametrize("year", ["-1", "19", "20255", "1987.5", ""])
def test_a_year_is_a_four_digit_year_or_nothing(year: str) -> None:
    """``-1`` is the schema's own "not set"; the rest are typos the plan
    header would refuse, and reading one in would make a plan it cannot
    write back."""
    document = f"<ComicInfo><Year>{year}</Year></ComicInfo>".encode()

    assert read_comic_info(document).year is None


def test_a_volume_is_read_as_the_text_it_is() -> None:
    """The schema says a number and the plan holds text; reading is liberal."""
    document = b"<ComicInfo><Volume>2018</Volume><Number>1.5</Number></ComicInfo>"

    assert read_comic_info(document) == ComicInfo(volume="2018", number="1.5")


def test_whitespace_inside_a_value_is_folded_to_single_spaces() -> None:
    """Every field it lands in is one line in the plan header dialog."""
    document = b"<ComicInfo><Series>\n    Dylan\n    Dog  </Series></ComicInfo>"

    assert read_comic_info(document).series == "Dylan Dog"


def test_the_first_value_given_is_the_one_read() -> None:
    document = b"""<ComicInfo>
      <Series></Series><Series>Tex</Series><Series>Zagor</Series>
    </ComicInfo>"""

    assert read_comic_info(document).series == "Tex"


def test_only_the_roots_own_children_are_read() -> None:
    """A nested element with a familiar name is some other field's inside."""
    document = b"""<ComicInfo>
      <Pages><Series>not the series</Series></Pages>
      <Title>Kept<Series>not the series either</Series></Title>
    </ComicInfo>"""

    assert read_comic_info(document) == ComicInfo(title="Kept")


def test_utf_16_with_its_mark_is_read() -> None:
    document = "<?xml version='1.0' encoding='utf-16'?><ComicInfo><Series>Été</Series></ComicInfo>"

    assert read_comic_info(document.encode("utf-16")).series == "Été"


@pytest.mark.parametrize(
    ("document", "reason"),
    [
        (b"", "not well-formed XML"),
        (b"<ComicInfo><Series>Tex</ComicInfo>", "not well-formed XML"),
        (b"Series: Tex", "not well-formed XML"),
        (b"<Book><Series>Tex</Series></Book>", "its root element is <Book>, not <ComicInfo>"),
    ],
)
def test_a_file_that_is_not_one_is_refused_with_the_reason(document: bytes, reason: str) -> None:
    with pytest.raises(ComicInfoError, match=reason):
        read_comic_info(document)


def test_a_document_type_is_refused_before_its_entities_expand() -> None:
    """A billion laughs: ten levels of ten, 3GB of "lol" from 700 bytes."""
    levels = ['<!ENTITY lol0 "lol">'] + [
        f'<!ENTITY lol{n} "{f"&lol{n - 1};" * 10}">' for n in range(1, 10)
    ]
    document = (
        f"<?xml version='1.0'?><!DOCTYPE ComicInfo [{''.join(levels)}]>"
        "<ComicInfo><Series>&lol9;</Series></ComicInfo>"
    ).encode()

    with pytest.raises(ComicInfoError, match="declares a document type"):
        read_comic_info(document)


def test_an_entity_naming_a_file_is_refused_and_the_file_never_read(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("the contents of somebody's file")
    document = (
        f'<?xml version="1.0"?><!DOCTYPE ComicInfo [<!ENTITY x SYSTEM "{secret.as_uri()}">]>'
        "<ComicInfo><Series>&x;</Series></ComicInfo>"
    ).encode()

    with pytest.raises(ComicInfoError, match="declares a document type"):
        read_comic_info(document)


# -- into a header ---------------------------------------------------------


def test_what_the_file_states_goes_into_the_header_and_nothing_else_moves() -> None:
    header = _header(series="typed by hand", publisher="also typed")
    info = ComicInfo(series="Tex", year=1948, language="it")

    filled = with_comic_info(header, info)

    assert filled == replace(header, series="Tex", year=1948), (
        "the publisher it did not state is left alone, and its language is not the plan's to take"
    )


def test_stated_names_the_header_fields_it_says_something_about() -> None:
    info = ComicInfo(series="Tex", year=1948, reading_direction=ReadingDirection.RIGHT_TO_LEFT)

    assert info.stated() == ("series", "year", "reading_direction")
    assert ComicInfo(language="it").stated() == ()


@pytest.mark.parametrize(
    ("stated", "read_in", "expected"),
    [
        ("ja", ("it",), True),
        ("ja", ("it", "ja"), False),
        ("jpn", ("ja",), False),
        ("en-US", ("en",), False),
        ("EN", ("en",), False),
        ("zh-Hant", ("zh-Hans",), True),
        ("", ("it",), False),
        ("  ", ("it",), False),
    ],
)
def test_two_languages_differ_when_the_recogniser_reads_them_differently(
    stated: str, read_in: tuple[str, ...], expected: bool
) -> None:
    assert differs(stated, read_in) is expected


# -- writing ---------------------------------------------------------------


def test_every_fact_the_header_holds_is_written_in_the_schemas_order() -> None:
    header = _header(
        series="Dylan Dog",
        title="The Long Night",
        volume="1",
        number="12",
        year=1987,
        publisher="Sergio Bonelli Editore",
        writer="Tiziano Sclavi",
        reading_direction=ReadingDirection.RIGHT_TO_LEFT,
    )

    written = _elements(comic_info_xml(header))

    assert written == [
        ("Title", "The Long Night"),
        ("Series", "Dylan Dog"),
        ("Number", "12"),
        ("Volume", "1"),
        ("Year", "1987"),
        ("Writer", "Tiziano Sclavi"),
        ("Publisher", "Sergio Bonelli Editore"),
        ("LanguageISO", "en"),
        ("Manga", "YesAndRightToLeft"),
    ]
    positions = [SCHEMA_ORDER.index(tag) for tag, _text in written]
    assert positions == sorted(positions)


def test_a_header_that_says_nothing_about_the_comic_writes_only_its_language() -> None:
    """The one fact every plan holds: what language the translation is in."""
    assert _elements(comic_info_xml(_header())) == [("LanguageISO", "en")]


@pytest.mark.parametrize("direction", [ReadingDirection.LEFT_TO_RIGHT, None])
def test_only_right_to_left_is_written_as_a_reading_direction(
    direction: ReadingDirection | None,
) -> None:
    """Left to right could only be said as "not a manga", which is not known."""
    tags = [tag for tag, _text in _elements(comic_info_xml(_header(reading_direction=direction)))]

    assert "Manga" not in tags


@pytest.mark.parametrize("volume", ["3", "03", "2018", "123456789"])
def test_a_volume_that_is_a_whole_number_is_written(volume: str) -> None:
    assert ("Volume", volume) in _elements(comic_info_xml(_header(volume=volume)))


@pytest.mark.parametrize(
    "volume", ["Vol. 3", "3.5", "-2", "III", "1234567890", "\N{FULLWIDTH DIGIT THREE}"]
)
def test_a_volume_the_schemas_integer_cannot_hold_is_left_out_and_said(
    volume: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="comictrans.comicinfo"):
        document = comic_info_xml(_header(volume=volume))

    assert "Volume" not in [tag for tag, _text in _elements(document)]
    assert f"volume {volume!r} is not written" in caplog.text


def test_markup_in_a_value_is_escaped_and_what_xml_cannot_carry_is_dropped() -> None:
    header = _header(series="Tom & Jerry <Classics>", title="Bell\x07 and\x0b tab\tkept")

    document = comic_info_xml(header)

    assert read_comic_info(document) == ComicInfo(
        series="Tom & Jerry <Classics>", title="Bell and tab kept", language="en"
    )


def test_what_is_written_is_read_back_as_the_header_it_came_from() -> None:
    header = _header(
        series="Dylan Dog",
        title="The Long Night",
        volume="1",
        number="12",
        year=1987,
        publisher="Sergio Bonelli Editore",
        writer="Tiziano Sclavi",
        reading_direction=ReadingDirection.RIGHT_TO_LEFT,
    )

    info = read_comic_info(comic_info_xml(header))

    assert with_comic_info(_header(), info) == header
    assert info.language == header.target_language


def test_left_to_right_goes_out_as_nothing_and_comes_back_unstated() -> None:
    """The one field the round trip does not keep, and why is above."""
    header = _header(series="Tex", reading_direction=ReadingDirection.LEFT_TO_RIGHT)

    info = read_comic_info(comic_info_xml(header))

    assert info.reading_direction is None
    assert info.series == "Tex"
