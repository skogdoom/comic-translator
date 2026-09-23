"""What a new run starts from, and a standing reminder of what it does not.

Split the way the things it seeds are: what a *new plan* is written as, how
a run *writes pages*, and — last, because it is about this window rather than
any comic — which language the window speaks. Nothing here reaches a plan
that already exists, and the line at the top says so: a preferences dialog
that could quietly change an open document would be the one thing the
two-pass design cannot afford.

Fields write straight through as they are edited, like the header dialog and
the region inspector, so there is nothing to apply and nothing to cancel —
the font excepted, which waits for a whole name, as it does in both of them.
That is also what macOS expects of a Preferences window: no OK button, and
Cmd+, to open it. Undo does not reach here, and does not need to — a
preference only seeds the next dialog, which shows you what it seeded and
lets you change it there.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLocale, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QGuiApplication, QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import erase_choices, translations
from .extract_dialog import ENGINE_CHOICES
from .font_box import FontBox
from .language_box import LanguageBox
from .note import Note
from .ocr_languages import OcrLanguagesField
from .preferences import ON, Preferences
from .render_dialog import FORMAT_CHOICES

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


_FIELD_PADDING = 16
"""The frame and text margins a placeholder sits inside. Qt has no public
number for this; sixteen is what the two default styles leave."""


def _wide_enough_for_its_placeholder(field: QLineEdit) -> QLineEdit:
    """Let a field be at least as wide as the hint written inside it.

    A ``QLineEdit`` asks for a width off its own metrics — about seventeen
    characters — and knows nothing about the placeholder, so a hint longer
    than that is elided to "same as the sourc…", which is not a hint. On
    macOS the field is then held at that width, because ``QFormLayout``
    defaults to ``FieldsStayAtSizeHint`` there and nowhere else.

    A minimum rather than a fixed width: a form that has room gives it more,
    and this only stops it being given less than its own text needs.
    """
    room = field.fontMetrics().horizontalAdvance(field.placeholderText())
    field.setMinimumWidth(room + _FIELD_PADDING)
    return field


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


UNRAR_NOTE = QCoreApplication.translate(
    "PreferencesDialog",
    "Only for opening a .cbr, and only when unrar is somewhere this "
    "application cannot see. unar, bsdtar and 7z do as well. CBZ and PDF "
    "need nothing.",
)
"""Why a field about somebody else's binary is in this dialog at all.

Two sentences where there were five. What the long version added was the
reason the PATH is short for an application opened from the Finder and the
reason unrar cannot ship here — both true, neither of them anything you can
act on while looking at this field, and together they were four lines of a
window that had stopped fitting on a screen."""

RAR_NOTE = QCoreApplication.translate(
    "PreferencesDialog",
    "Only for saving a .cbr. That needs rar itself, which comes with WinRAR "
    "and needs a licence; unrar cannot write. CBZ needs nothing.",
)
"""Why the field above it is not the same field. See ``Preferences``."""

MINIMUM_HEIGHT = 240
"""A floor under the cap, so a screen reporting something absurd leaves a
window you can still use rather than a title bar and a button."""

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

        self._source_language = LanguageBox()
        self._source_language.set_value(preferences.source_language)
        self._target_language = LanguageBox()
        self._target_language.set_value(preferences.target_language)
        self._ocr_languages = OcrLanguagesField(preferences.ocr_languages, preferences.ocr_engine)
        _wide_enough_for_its_placeholder(self._ocr_languages.line_edit())
        self._engine = QComboBox()
        for label, value in ENGINE_CHOICES:
            self._engine.addItem(label, value)
        self._engine.setCurrentIndex(self._engine.findData(preferences.ocr_engine))
        self._engine.currentIndexChanged.connect(
            lambda _index: self._ocr_languages.set_engine(str(self._engine.currentData()))
        )

        # allow_default, because "no font recorded here" is a real answer and
        # the one this shipped with. An unresolvable name is marked rather
        # than swapped, the same as everywhere else a font is named.
        self._font = FontBox(allow_default=True, default_text=FONT_DEFAULT)
        self._font.set_value(preferences.font or None)

        self._output = QLineEdit(preferences.output_directory)
        self._output.setPlaceholderText(self.tr("beside the pages"))
        _wide_enough_for_its_placeholder(self._output)
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
        for label, value, _description in erase_choices.STRATEGIES:
            self._erase.addItem(label, value)
        self._erase.setCurrentIndex(self._erase.findData(preferences.erase_strategy))

        self._format = QComboBox()
        for format_label, image_format in FORMAT_CHOICES:
            # "" rather than None for "match the source": a preference is a
            # string everywhere, and QSettings hands None back as an empty
            # one anyway. The render dialog turns it back into None.
            self._format.addItem(format_label, image_format or "")
        self._format.setCurrentIndex(self._format.findData(preferences.image_format))

        self._unrar_tool = QLineEdit(preferences.unrar_tool)
        self._unrar_tool.setPlaceholderText(self.tr("found on PATH"))
        _wide_enough_for_its_placeholder(self._unrar_tool)
        unrar_widget = self._tool_row(self._unrar_tool, self._on_choose_unrar_tool)
        self._unrar_note = Note(UNRAR_NOTE)

        self._rar_tool = QLineEdit(preferences.rar_tool)
        self._rar_tool.setPlaceholderText(self.tr("found on PATH"))
        _wide_enough_for_its_placeholder(self._rar_tool)
        rar_widget = self._tool_row(self._rar_tool, self._on_choose_rar_tool)
        self._rar_note = Note(RAR_NOTE)

        self._language = QComboBox()
        for label, code in self.language_choices():
            self._language.addItem(label, code)
        self._language.setCurrentIndex(self._language_index(preferences.language))
        self._language_note = Note(LANGUAGE_NOTE)

        self._experimental = QCheckBox(self.tr("experimental features"))
        self._experimental.setChecked(preferences.experimental_on)

        heading = QLabel(WHAT_IT_IS)
        heading.setWordWrap(True)

        # One form for both groups, not one each: two form layouts size their
        # label columns separately, so "write pages to" and "pages are
        # lettered in" would put their fields at different places down the
        # same dialog. The group headings are spanning rows inside it.
        #
        # So are the notes, and for a reason worth knowing: a widget in the
        # field column is given its *own* size hint's width under
        # ``FieldsStayAtSizeHint``, which is macOS's default, and a wrapped
        # label's hint width is a guess at a shape that depends on how much
        # text it holds. Two notes in that column therefore wrapped at two
        # different widths, one of them the whole window and the other half
        # of it. Spanning the form, they all get the same width and wrap
        # alike.
        form = QFormLayout()
        form.addRow(_section(self.tr("a new plan starts as")))
        form.addRow(self.tr("pages are lettered in"), self._source_language)
        form.addRow(self.tr("translating into"), self._target_language)
        form.addRow(self.tr("OCR languages"), self._ocr_languages)
        form.addRow(self.tr("recogniser"), self._engine)
        form.addRow(self.tr("font"), self._font)
        form.addRow(_spacer())
        form.addRow(_section(self.tr("chapter files")))
        form.addRow(self.tr("unrar is at"), unrar_widget)
        form.addRow(self._unrar_note)
        form.addRow(self.tr("rar is at"), rar_widget)
        form.addRow(self._rar_note)
        form.addRow(_spacer())
        form.addRow(_section(self.tr("rendering pages")))
        form.addRow(self.tr("write pages to"), output_widget)
        form.addRow(erase_choices.ERASE_FIELD, self._erase)
        form.addRow(self.tr("format"), self._format)
        form.addRow(_spacer())
        form.addRow(_section(self.tr("this window")))
        form.addRow(self.tr("language"), self._language)
        form.addRow(self._language_note)
        form.addRow("", self._experimental)

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

        # The form scrolls; the heading and Done do not. There are a dozen
        # settings here and each note is as tall as the width it is
        # given, so the honest height of this window depends on the system
        # font and the language it is in — neither of which this can know in
        # advance, and one short screen is all it takes for Done to end up
        # under the Dock. So the window is capped at the screen (see
        # :meth:`cap_height`) and the middle gives way rather than the ends.
        scrolled = QWidget()
        scrolled.setLayout(form)
        self._scroll = QScrollArea()
        self._scroll.setWidget(scrolled)
        self._scroll.setWidgetResizable(True)
        # No frame and no ground of its own: a sunken panel around two thirds
        # of a preferences window is Qt showing through, not a design.
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.viewport().setAutoFillBackground(False)
        scrolled.setAutoFillBackground(False)
        # Never sideways. The one thing in here with no width of its own is a
        # note, and a note is supposed to rewrap rather than run off the edge.
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addSpacing(10)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(buttons)
        self.setMinimumWidth(520)

        self._source_language.currentTextChanged.connect(self._commit)
        self._target_language.currentTextChanged.connect(self._commit)
        self._ocr_languages.changed.connect(self._commit)
        self._engine.currentIndexChanged.connect(self._commit)
        self._font.chosen.connect(self._commit)
        self._output.textChanged.connect(self._commit)
        self._unrar_tool.textChanged.connect(self._commit)
        self._rar_tool.textChanged.connect(self._commit)
        self._erase.currentIndexChanged.connect(self._commit)
        self._format.currentIndexChanged.connect(self._commit)
        self._language.currentIndexChanged.connect(self._commit)
        self._experimental.toggled.connect(self._commit)

    # -- fitting on the screen -------------------------------------------

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            self.cap_height(screen.availableGeometry().height())

    def cap_height(self, available: int) -> None:
        """Never be taller than ``available``, and give way from the middle.

        Takes the number rather than reading the screen so that the rule can
        be tested against a short one; :meth:`showEvent` supplies the real
        thing. ``availableGeometry`` already leaves out the menu bar and the
        Dock, so what is left to subtract is this window's own frame, which
        is outside the height a widget is asked to have.
        """
        overhead = max(0, self.frameGeometry().height() - self.height())
        room = max(MINIMUM_HEIGHT, available - overhead)
        self.setMaximumHeight(room)
        if self.height() > room:
            self.resize(self.width(), room)

    # -- the value -------------------------------------------------------

    def preferences(self) -> Preferences:
        """What the fields currently say, over what was passed in.

        ``last_directory`` is carried through untouched: it is remembered
        rather than chosen, and this dialog never shows it.
        """
        return replace(
            self._preferences,
            source_language=self._source_language.value(),
            target_language=self._target_language.value(),
            ocr_languages=self._ocr_languages.text().strip(),
            ocr_engine=str(self._engine.currentData()),
            font=self._font.value() or "",
            output_directory=self._output.text().strip(),
            unrar_tool=self._unrar_tool.text().strip(),
            rar_tool=self._rar_tool.text().strip(),
            erase_strategy=str(self._erase.currentData()),
            image_format=str(self._format.currentData()),
            language=str(self._language.currentData()),
            # A string, like every other field — see ``preferences``. The
            # checkbox is the only thing in this dialog that is a yes or a no,
            # and it is stored as one of two words rather than as a bool so
            # that a settings file holding "false" cannot read as true.
            experimental=ON if self._experimental.isChecked() else "",
        )

    @staticmethod
    def _tool_row(field: QLineEdit, on_choose: Callable[[], None]) -> QWidget:
        """A path field with a Choose… beside it. Built twice, so written once."""
        choose = QPushButton(QCoreApplication.translate("PreferencesDialog", "Choose…"))
        choose.setAutoDefault(False)
        choose.clicked.connect(on_choose)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(field, 1)
        row.addWidget(choose)
        holder = QWidget()
        holder.setLayout(row)
        return holder

    def _on_choose_unrar_tool(self) -> None:
        self._choose_tool(self._unrar_tool, self.tr("Where unrar Is"))

    def _on_choose_rar_tool(self) -> None:
        self._choose_tool(self._rar_tool, self.tr("Where rar Is"))

    def _choose_tool(self, field: QLineEdit, title: str) -> None:
        """Pick the binary itself, not a folder: it is one file somewhere.

        Starting where the field points, so somebody correcting a path does
        not start over from the top of the disk.
        """
        start = field.text().strip() or "/usr/local/bin"
        name, _filter = QFileDialog.getOpenFileName(self, title, start)
        if name:
            field.setText(name)

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
                self._unrar_tool,
                self._rar_tool,
                self._output,
                self._erase,
                self._format,
                self._language,
                self._experimental,
            ):
                blockers.enter_context(QSignalBlocker(widget))
            self._source_language.set_value(preferences.source_language)
            self._target_language.set_value(preferences.target_language)
            self._ocr_languages.set_text(preferences.ocr_languages)
            self._engine.setCurrentIndex(self._engine.findData(preferences.ocr_engine))
            self._ocr_languages.set_engine(preferences.ocr_engine)
            self._font.set_value(preferences.font or None)
            self._unrar_tool.setText(preferences.unrar_tool)
            self._rar_tool.setText(preferences.rar_tool)
            self._output.setText(preferences.output_directory)
            self._erase.setCurrentIndex(self._erase.findData(preferences.erase_strategy))
            self._format.setCurrentIndex(self._format.findData(preferences.image_format))
            self._language.setCurrentIndex(self._language_index(preferences.language))
            self._experimental.setChecked(preferences.experimental_on)


__all__ = [
    "DONE_TEXT",
    "FONT_DEFAULT",
    "LANGUAGE_NOTE",
    "SYSTEM_LANGUAGE",
    "WHAT_IT_IS",
    "PreferencesDialog",
    "language_name",
]
