"""Wrapped help text that is never allocated less height than it needs."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QFormLayout, QLabel, QLineEdit, QWidget

from comictrans.gui.note import Note


def _sized(note: Note, width: int) -> Note:
    """A note actually laid out at ``width``.

    Shown, because a hidden widget is not sent a resize event and the whole
    point of this class is what it does when it is given one.
    """
    note.show()
    note.resize(width, 10)
    QApplication.processEvents()
    return note


LONG = (
    "Only for opening a .cbr, and only needed when unrar is somewhere this "
    "application cannot see — which is usual, since an application opened "
    "from the Finder does not get the PATH a terminal has."
)


def test_a_narrow_note_asks_for_the_height_that_width_costs(qapp: object) -> None:
    """The defect this exists for, stated as a number.

    A plain wrapped ``QLabel`` answers for a width it chose itself, so a
    layout that gives it a narrower one allocates too little and the last
    line is cut off. This one answers for the width it was given.
    """
    plain = QLabel(LONG)
    plain.setWordWrap(True)
    plain.show()
    plain.resize(160, 10)
    QApplication.processEvents()
    assert plain.minimumHeight() < plain.heightForWidth(160), (
        "the label Qt gives you would be happy with less than it needs"
    )

    note = _sized(Note(LONG), 160)

    assert note.minimumHeight() == note.needed_height(160)
    assert note.minimumHeight() > note.needed_height(600), "and narrower costs more lines"


def test_the_height_follows_the_width_it_is_given(qapp: object) -> None:
    note = _sized(Note(LONG), 160)
    narrow = note.minimumHeight()

    _sized(note, 600)

    assert note.minimumHeight() < narrow, "fewer lines when there is more room"
    assert note.minimumHeight() == note.needed_height(600)


def test_new_text_is_measured_at_the_width_already_in_hand(qapp: object) -> None:
    note = _sized(Note("short"), 200)
    short = note.minimumHeight()

    note.setText(LONG)

    assert note.minimumHeight() > short
    assert note.minimumHeight() == note.needed_height(200)


def test_emptying_it_gives_the_space_back(qapp: object) -> None:
    note = _sized(Note(LONG), 200)
    assert note.minimumHeight() > 0

    note.setText("")

    assert note.minimumHeight() == 0, "a note with nothing to say takes no room"


def test_it_measures_what_qt_measures(qapp: object) -> None:
    """The one thing taking the measurement by hand has to earn.

    ``heightForWidth`` is unusable here because it is clamped to the minimum
    this class sets — but the number it gives a label that has no minimum yet
    is the right answer, so that is what this is held to.
    """
    for text in ("short", LONG, "repaint the original lettering in the fill colour"):
        note = Note(text)
        fresh = QLabel(text)
        fresh.setWordWrap(True)
        fresh.setContentsMargins(note.contentsMargins())
        for width in (120, 226, 406, 520, 900):
            assert note.needed_height(width) == fresh.heightForWidth(width), (width, text)


def test_the_minimum_does_not_latch_once_it_has_been_set(qapp: object) -> None:
    """The defect the hand measurement exists to avoid, pinned.

    Asking ``heightForWidth`` here would answer with the minimum already set
    rather than with the text, so a note that had once been narrow would
    never give the space back.
    """
    note = _sized(Note(LONG), 160)
    tall = note.minimumHeight()

    _sized(note, 900)

    assert note.minimumHeight() < tall
    assert note.heightForWidth(900) == note.minimumHeight(), (
        "Qt's own answer is the clamped one, which is why it is not asked"
    )


def test_a_note_with_no_width_yet_does_not_guess(qapp: object) -> None:
    """Before the first layout pass there is no width to answer for."""
    note = Note(LONG)
    note.resize(0, 0)
    note.refit()

    assert note.minimumHeight() == 0


@pytest.mark.parametrize(
    "policy",
    [
        QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint,
        QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow,
    ],
)
def test_a_form_cannot_squeeze_one_below_its_text(
    qapp: object, policy: QFormLayout.FieldGrowthPolicy
) -> None:
    """Both growth policies, because macOS defaults to the other one.

    ``FieldsStayAtSizeHint`` is what ``QMacStyle`` uses and nothing else
    does, so a form tested only here is a form tested in the one arrangement
    the bug did not appear in.
    """
    holder = QWidget()
    form = QFormLayout(holder)
    form.setFieldGrowthPolicy(policy)
    form.addRow("a field", QLineEdit())
    note = Note(LONG)
    form.addRow("", note)
    holder.show()
    holder.resize(320, holder.minimumSizeHint().height())
    QApplication.processEvents()

    assert note.height() >= note.needed_height(note.width()), "the note is not cut off"
    assert holder.height() >= note.height()
