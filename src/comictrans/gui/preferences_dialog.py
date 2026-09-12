"""What a new run starts from, and a standing reminder of what it does not.

Split the way the things it seeds are: what a *new plan* is written as, how
a run *writes pages*, and — last, because it is about this window rather than
any comic — which language the window speaks. Nothing here reaches a plan
that already exists, and the line at the top says so: a preferences dialog
that could quietly change an open document would be the one thing the
two-pass design cannot afford.

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

from PySide6.QtCore import QCoreApplication, QLocale, QSignalBlocker, Signal
from PySide6.QtGui import QPalette
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

from . import translations
from .extract_dialog import ENGINE_CHOICES
from .font_box import FontBox
from .preferences import Preferences
from .render_dialog import FORMAT_CHOICES, STRATEGY_CHOICES

WHAT_IT_IS = QCoreApplication.translate(
    "PreferencesDialog",
    "These fill in the Extract and Render dialogs when they open. "
    "They never change a plan you already have.",
)

SYSTEM_LANGUAGE = QCoreApplication.translate("PreferencesDialog", "the same as this Mac")
"""What an empty ``language`` means, said as what it does.

Named for the machine rather than "System Default" because the machine is
where the answer is: on macOS this window can be given its own language in
System Settings > General > Language & Region, and this field is the way to
say something different from whatever that ended up being.
"""

LANGUAGE_NOTE = QCoreApplication.translate(
    "PreferencesDialog", "takes effect the next time this application starts"
)
"""Said in the form rather than in an alert when the field changes.

The window's words are read once, as it is built — see ``translations`` —
so a language chosen here is the language of the *next* window. A note
standing under the field says that before the choice is made rather than
after, which is the difference between a rule and a surprise.
"""

FONT_DEFAULT = QCoreApplication.translate("PreferencesDialog", "(let extract choose)")
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
    """Air between the groups, as a row so the form owns the spacing."""
    return QLabel()


def language_name(code: str) -> str:
    """A language named in itself, for the field that picks one.

    *Svenska*, not *Swedish*: the only naming that helps somebody who opened
    this dialog because the window is in a language they cannot read.

    Qt names a *locale* rather than a language, and a bare code resolves to a
    default territory — measured: ``en`` comes back as "American English"
    while ``sv`` is "svenska". Where the name Qt gives is the language's own
    name with a qualifier in front, the qualifier goes: these catalogues are
    per language, and "American English" would be claiming something the
    catalogue does not say. Any other shape is left exactly as Qt gave it.
    """
    locale = QLocale(code)
    native = locale.nativeLanguageName()
    plain = QLocale.languageToString(locale.language())
    if native.endswith(plain) and native != plain:
        native = plain
    return native[:1].upper() + native[1:] if native else code


def _quieten(label: QLabel) -> QLabel:
    """A line of help rather than a field: the placeholder colour, as the
    render dialog's erase note uses. A palette, not a stylesheet, so it
    follows a light window and a dark one."""
    palette = label.palette()
    palette.setColor(
        QPalette.ColorRole.WindowText,
        palette.color(QPalette.ColorRole.PlaceholderText),
    )
    label.setPalette(palette)
    return label


RAR_NOTE = QCoreApplication.translate(
    "PreferencesDialog",
    "Only for opening a .cbr, and only needed when unrar is somewhere this "
    "application cannot see — which is usual, since an application opened "
    "from the Finder does not get the PATH a terminal has. unar, bsdtar and "
    "7z do as well. None of them ships with Comic Translator: unrar's licence "
    "is not one this project can pass on. CBZ and PDF need nothing.",
)
"""Why a field about somebody else's binary is in this dialog at all."""

DONE_TEXT = QCoreApplication.translate("PreferencesDialog", "Done")
"""What dismisses this dialog, said as what it does.

There is no ``StandardButton.Done``, so this is written out rather than
taken from Qt — see the note where the button is built.
"""


class PreferencesDialog(QDialog):
    """Application defaults, written through as they are edited."""

    changed = Signal()
    """Something was edited. The window saves and keeps its own copy."""

    @staticmethod
    def language_choices() -> tuple[tuple[str, str], ...]:
        """``(label, code)`` for the field: this machine's answer, then ours.

        Each language is named in itself — *svenska*, not *Swedish* — which
        is what every other language picker does and the only naming that
        works for somebody who has opened this dialog because the window is
        in a language they cannot read. Qt supplies the names; a code it has
        no name for is shown as the code.
        """
        rows = [(SYSTEM_LANGUAGE, "")]
        for code in translations.available():
            rows.append((language_name(code), code))
        return tuple(rows)

    def __init__(self, preferences: Preferences, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # The name the menu item that opens this carries, which on macOS is
        # "Preferences" whatever that action's text says — Qt titles the
        # merged item itself. A window whose title does not match the command
        # that opened it is one more thing to work out.
        self.setWindowTitle(self.tr("Preferences"))
        self._preferences = preferences

        self._source_language = QLineEdit(preferences.source_language)
        self._target_language = QLineEdit(preferences.target_language)
        self._ocr_languages = QLineEdit(preferences.ocr_languages)
        self._ocr_languages.setPlaceholderText(self.tr("same as the source language"))
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
        self._output.setPlaceholderText(self.tr("beside the pages"))
        choose = QPushButton(self.tr("Choose…"))
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

        self._rar_tool = QLineEdit(preferences.rar_tool)
        self._rar_tool.setPlaceholderText(self.tr("found on PATH"))
        choose_rar = QPushButton(self.tr("Choose…"))
        choose_rar.setAutoDefault(False)
        choose_rar.clicked.connect(self._on_choose_rar_tool)
        rar_row = QHBoxLayout()
        rar_row.setContentsMargins(0, 0, 0, 0)
        rar_row.addWidget(self._rar_tool, 1)
        rar_row.addWidget(choose_rar)
        rar_widget = QWidget()
        rar_widget.setLayout(rar_row)
        self._rar_note = _quieten(QLabel(RAR_NOTE))
        self._rar_note.setWordWrap(True)

        self._language = QComboBox()
        for label, code in self.language_choices():
            self._language.addItem(label, code)
        self._language.setCurrentIndex(self._language_index(preferences.language))
        self._language_note = _quieten(QLabel(LANGUAGE_NOTE))
        self._language_note.setWordWrap(True)

        heading = QLabel(WHAT_IT_IS)
        heading.setWordWrap(True)

        # One form for both groups, not one each: two form layouts size their
        # label columns separately, so "write pages to" and "pages are
        # lettered in" would put their fields at different places down the
        # same dialog. The group headings are spanning rows inside it.
        form = QFormLayout()
        form.addRow(_section(self.tr("a new plan starts as")))
        form.addRow(self.tr("pages are lettered in"), self._source_language)
        form.addRow(self.tr("translating into"), self._target_language)
        form.addRow(self.tr("OCR languages"), self._ocr_languages)
        form.addRow(self.tr("recogniser"), self._engine)
        form.addRow(self.tr("font"), self._font)
        form.addRow(_spacer())
        form.addRow(_section(self.tr("reading a .cbr")))
        form.addRow(self.tr("unrar is at"), rar_widget)
        form.addRow("", self._rar_note)
        form.addRow(_spacer())
        form.addRow(_section(self.tr("rendering pages")))
        form.addRow(self.tr("write pages to"), output_widget)
        form.addRow(self.tr("erase"), self._erase)
        form.addRow(self.tr("format"), self._format)
        form.addRow(_spacer())
        form.addRow(_section(self.tr("this window")))
        form.addRow(self.tr("language"), self._language)
        form.addRow("", self._language_note)

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
        self._rar_tool.textChanged.connect(self._commit)
        self._erase.currentIndexChanged.connect(self._commit)
        self._format.currentIndexChanged.connect(self._commit)
        self._language.currentIndexChanged.connect(self._commit)

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
            rar_tool=self._rar_tool.text().strip(),
            erase_strategy=str(self._erase.currentData()),
            image_format=str(self._format.currentData()),
            language=str(self._language.currentData()),
        )

    def _on_choose_rar_tool(self) -> None:
        """Pick the binary itself, not a folder: it is one file somewhere.

        Starting where the field points, so somebody correcting a path does
        not start over from the top of the disk.
        """
        start = self._rar_tool.text().strip() or "/usr/local/bin"
        name, _filter = QFileDialog.getOpenFileName(self, self.tr("Where unrar Is"), start)
        if name:
            self._rar_tool.setText(name)

    def _language_index(self, code: str) -> int:
        """Where ``code`` sits in the field, or the machine's own answer.

        A settings file naming a language that no longer ships — edited by
        hand, or written by a version that had one this does not — shows as
        the default rather than as an empty box. It loads that way too.
        """
        found = self._language.findData(code)
        return found if found >= 0 else 0

    def _commit(self) -> None:
        self._preferences = self.preferences()
        self.changed.emit()

    def _on_choose_output(self) -> None:
        start = self._output.text().strip() or str(Path.home())
        name = QFileDialog.getExistingDirectory(self, self.tr("Render Into"), start)
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
                self._language,
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
            self._language.setCurrentIndex(self._language_index(preferences.language))


__all__ = [
    "DONE_TEXT",
    "FONT_DEFAULT",
    "LANGUAGE_NOTE",
    "SYSTEM_LANGUAGE",
    "WHAT_IT_IS",
    "PreferencesDialog",
    "language_name",
]
