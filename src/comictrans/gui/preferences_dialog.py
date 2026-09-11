"""What a new run starts from, and a standing reminder of what it does not.

Split the way the two things it seeds are: what a *new plan* is written as,
and how a run *writes pages*. Nothing here reaches a plan that already
exists, and the line at the top says so — a preferences dialog that could
quietly change an open document would be the one thing the two-pass design
cannot afford.

Fields write straight through as they are edited, like the header dialog and
the region inspector, so there is nothing to apply and nothing to cancel.
That is also what macOS expects of a Preferences window: no OK button, and
Cmd+, to open it. Undo does not reach here, and does not need to — a
preference only seeds the next dialog, which shows you what it seeded and
lets you change it there.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .extract_dialog import ENGINE_CHOICES
from .font_box import FontBox
from .preferences import Preferences
from .render_dialog import FORMAT_CHOICES, STRATEGY_CHOICES

WHAT_IT_IS = (
    "These fill in the Extract and Render dialogs when they open. "
    "They never change a plan you already have."
)

FONT_DEFAULT = "(let extract choose)"
"""What an unset font means here. Not "(plan default)": there is no plan in
this dialog, and what happens instead is that ``extract`` walks its own
fallback chain and records whichever family it found."""


def _section(title: str) -> QLabel:
    """A group heading: bold, so it does not read as another field label."""
    label = QLabel(title)
    font = label.font()
    font.setBold(True)
    label.setFont(font)
    return label


def _spacer() -> QLabel:
    """Air between the two groups, as a row so the form owns the spacing."""
    return QLabel()


DONE_TEXT = "Done"
"""What dismisses this dialog, said as what it does.

There is no ``StandardButton.Done``, so this is written out rather than
taken from Qt — see the note where the button is built.
"""


class PreferencesDialog(QDialog):
    """Application defaults, written through as they are edited."""

    changed = Signal()
    """Something was edited. The window saves and keeps its own copy."""

    def __init__(self, preferences: Preferences, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # "Settings", the name macOS has used since 13, and the name the
        # menu item that opens this carries. A window whose title does not
        # match the command that opened it is one more thing to work out.
        self.setWindowTitle("Settings")
        self._preferences = preferences

        self._source_language = QLineEdit(preferences.source_language)
        self._target_language = QLineEdit(preferences.target_language)
        self._ocr_languages = QLineEdit(preferences.ocr_languages)
        self._ocr_languages.setPlaceholderText("same as the source language")
        self._engine = QComboBox()
        for label, value in ENGINE_CHOICES:
            self._engine.addItem(label, value)
        self._engine.setCurrentIndex(self._engine.findData(preferences.ocr_engine))

        # allow_default, because "no font recorded here" is a real answer and
        # the one this shipped with. An unresolvable name is marked rather
        # than swapped, the same as everywhere else a font is named.
        self._font = FontBox(allow_default=True, default_text=FONT_DEFAULT)
        self._font.set_value(preferences.font or None)

        self._output = QLineEdit(preferences.output_directory)
        self._output.setPlaceholderText("beside the pages")
        choose = QPushButton("Choose…")
        choose.setAutoDefault(False)
        choose.clicked.connect(self._on_choose_output)
        output_row = QHBoxLayout()
        output_row.setContentsMargins(0, 0, 0, 0)
        output_row.addWidget(self._output, 1)
        output_row.addWidget(choose)
        output_widget = QWidget()
        output_widget.setLayout(output_row)

        self._erase = QComboBox()
        for label, value, _description in STRATEGY_CHOICES:
            self._erase.addItem(label, value)
        self._erase.setCurrentIndex(self._erase.findData(preferences.erase_strategy))

        self._format = QComboBox()
        for format_label, image_format in FORMAT_CHOICES:
            # "" rather than None for "match the source": a preference is a
            # string everywhere, and QSettings hands None back as an empty
            # one anyway. The render dialog turns it back into None.
            self._format.addItem(format_label, image_format or "")
        self._format.setCurrentIndex(self._format.findData(preferences.image_format))

        heading = QLabel(WHAT_IT_IS)
        heading.setWordWrap(True)

        # One form for both groups, not one each: two form layouts size their
        # label columns separately, so "write pages to" and "pages are
        # lettered in" would put their fields at different places down the
        # same dialog. The group headings are spanning rows inside it.
        form = QFormLayout()
        form.addRow(_section("a new plan starts as"))
        form.addRow("pages are lettered in", self._source_language)
        form.addRow("translating into", self._target_language)
        form.addRow("OCR languages", self._ocr_languages)
        form.addRow("recogniser", self._engine)
        form.addRow("font", self._font)
        form.addRow(_spacer())
        form.addRow(_section("rendering pages"))
        form.addRow("write pages to", output_widget)
        form.addRow("erase", self._erase)
        form.addRow("format", self._format)

        # "Done", not "Close". Every field here has written itself through
        # by the time this is pressed, so there is nothing being closed
        # away and nothing to discard — and "Close" reads like a button
        # that might be doing one of those. Save and Cancel would say it
        # even more wrongly: there is no pending edit for either to act on.
        #
        # Not a Qt standard button, because there is no Done among them.
        # That costs the free translation ``StandardButton.Close`` came
        # with, so this string is one of ours for 4.9 to pick up.
        #
        # AcceptRole rather than RejectRole: finishing with a form is the
        # affirmative answer, and it is what puts Return on the button.
        # Escape still closes, as it does for any dialog, and means the
        # same thing here as Done does.
        buttons = QDialogButtonBox()
        buttons.addButton(DONE_TEXT, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addSpacing(10)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addWidget(buttons)
        self.setMinimumWidth(520)

        self._source_language.textChanged.connect(self._commit)
        self._target_language.textChanged.connect(self._commit)
        self._ocr_languages.textChanged.connect(self._commit)
        self._engine.currentIndexChanged.connect(self._commit)
        self._font.currentTextChanged.connect(self._commit)
        self._output.textChanged.connect(self._commit)
        self._erase.currentIndexChanged.connect(self._commit)
        self._format.currentIndexChanged.connect(self._commit)

    # -- the value -------------------------------------------------------

    def preferences(self) -> Preferences:
        """What the fields currently say, over what was passed in.

        ``last_directory`` is carried through untouched: it is remembered
        rather than chosen, and this dialog never shows it.
        """
        return replace(
            self._preferences,
            source_language=self._source_language.text().strip(),
            target_language=self._target_language.text().strip(),
            ocr_languages=self._ocr_languages.text().strip(),
            ocr_engine=str(self._engine.currentData()),
            font=self._font.value() or "",
            output_directory=self._output.text().strip(),
            erase_strategy=str(self._erase.currentData()),
            image_format=str(self._format.currentData()),
        )

    def _commit(self) -> None:
        self._preferences = self.preferences()
        self.changed.emit()

    def _on_choose_output(self) -> None:
        start = self._output.text().strip() or str(Path.home())
        name = QFileDialog.getExistingDirectory(self, "Render Into", start)
        if name:
            self._output.setText(name)

    def repopulate(self, preferences: Preferences) -> None:
        """Show these values without writing them back out as an edit."""
        self._preferences = preferences
        with ExitStack() as blockers:
            for widget in (
                self._source_language,
                self._target_language,
                self._ocr_languages,
                self._engine,
                self._font,
                self._output,
                self._erase,
                self._format,
            ):
                blockers.enter_context(QSignalBlocker(widget))
            self._source_language.setText(preferences.source_language)
            self._target_language.setText(preferences.target_language)
            self._ocr_languages.setText(preferences.ocr_languages)
            self._engine.setCurrentIndex(self._engine.findData(preferences.ocr_engine))
            self._font.set_value(preferences.font or None)
            self._output.setText(preferences.output_directory)
            self._erase.setCurrentIndex(self._erase.findData(preferences.erase_strategy))
            self._format.setCurrentIndex(self._format.findData(preferences.image_format))


__all__ = ["DONE_TEXT", "FONT_DEFAULT", "WHAT_IT_IS", "PreferencesDialog"]
