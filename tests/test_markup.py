from __future__ import annotations

import pytest

from comictrans.markup import MarkupError, Run, parse_runs, plain_text, tokenize
from comictrans.model import TextCase


def test_plain_text_is_a_single_regular_run() -> None:
    assert parse_runs("CIAO A TUTTI") == (Run("CIAO A TUTTI", False),)


def test_double_asterisks_mark_bold() -> None:
    assert parse_runs("I **can't** believe it") == (
        Run("I ", False),
        Run("can't", True),
        Run(" believe it", False),
    )


def test_escaped_asterisk_is_literal() -> None:
    assert parse_runs(r"5 \* 2 = 10") == (Run("5 * 2 = 10", False),)


def test_unbalanced_emphasis_is_an_error_not_a_guess() -> None:
    with pytest.raises(MarkupError, match="unbalanced"):
        parse_runs("this **never closes")


def test_error_message_points_at_the_escape() -> None:
    with pytest.raises(MarkupError, match=r"literal asterisk"):
        parse_runs("2 ** 3")


def test_a_word_spanning_a_markup_boundary_stays_one_word() -> None:
    # The comma is not bold, but it must not be pushed onto its own token or
    # the renderer puts a space before it.
    tokens = tokenize("a **shout**, or not")
    assert [token.text for token in tokens] == ["a", "shout,", "or", "not"]
    assert [(s.text, s.bold) for s in tokens[1].segments] == [("shout", True), (",", False)]


def test_emphasis_inside_a_word() -> None:
    (token,) = tokenize("un**bel**ievable")
    assert token.text == "unbelievable"
    assert [(s.text, s.bold) for s in token.segments] == [
        ("un", False),
        ("bel", True),
        ("ievable", False),
    ]
    assert not token.uniform_weight


def test_uniform_weight_words_can_be_hyphenated() -> None:
    (plain,) = tokenize("extraordinary")
    (bold,) = tokenize("**extraordinary**")
    assert plain.uniform_weight and bold.uniform_weight
    assert bold.bold


def test_upper_case_applies_to_content_not_markers() -> None:
    tokens = tokenize("keep **calm** now", case=TextCase.UPPER)
    assert [t.text for t in tokens] == ["KEEP", "CALM", "NOW"]
    assert tokens[1].bold


def test_preserve_case_leaves_text_alone() -> None:
    assert [t.text for t in tokenize("Keep Calm", case=TextCase.PRESERVE)] == ["Keep", "Calm"]


def test_line_breaks_are_advisory_and_become_spaces() -> None:
    assert [t.text for t in tokenize("one\ntwo\n\nthree")] == ["one", "two", "three"]


def test_empty_and_markup_only_text_yield_no_tokens() -> None:
    assert tokenize("") == ()
    assert tokenize("****") == ()


def test_plain_text_strips_markup() -> None:
    assert plain_text("a **bold** word") == "a bold word"
    assert plain_text(r"\*literal\*") == "*literal*"


def test_token_replaced_keeps_weight() -> None:
    (token,) = tokenize("**hyphenate**")
    assert token.replaced("hyphen-").bold
