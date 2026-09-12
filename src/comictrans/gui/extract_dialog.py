"""What one extract run is going to read, and where it will write the plan.

Four fields, and everything else left on the command line. `extract` has
around fifteen detection-tuning flags — contour area, solidity, extent,
colour segmentation — and they exist for the page that came out wrong, which
is a thing you iterate on in a terminal against `--debug-dir`. What belongs
here is what you have to decide every time: which pages, where the plan
goes, what language they are in and what language you are writing, and which
recogniser reads them.

The font is not here either, and that is the same decision seen from the
other side: with no ``--font``, extract walks its fallback chain and records
what it found, and the header font is editable in this window the moment the
plan opens. Asking for it up front would be asking before there is anything
to look at.

**There is no merge.** Re-extracting over a plan you have worked on is the
merge case — carrying translations across by matching geometry, reporting
the hand work that has nowhere to go — and that is a second feature with its
own failure mode, still on the command line as ``--merge``. Now that regions
can be drawn, reshaped and merged by hand, re-detecting a page is destructive
against exactly that work. Extract to a new plan, and open it.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..config import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_SOURCE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
    DetectConfig,
    ExtractConfig,
    OcrConfig,
)
from ..errors import ComictransError
from ..extract import PLAN_NAME, default_plan_path
from ..imaging import IMAGE_SUFFIXES, collect_inputs
from ..sources import (
    CONTAINER_SUFFIXES,
    RAR,
    chapter_kind,
    check_readable,
    default_unpack_dir,
    is_container,
)
from .preferences import DEFAULTS, Preferences
from .run_job import ExtractRequest

ENGINE_CHOICES: tuple[tuple[str, str], ...] = (
    (
        QCoreApplication.translate("ExtractDialog", "automatic (Apple Vision, then Tesseract)"),
        "auto",
    ),
    ("Apple Vision", "vision"),
    ("Tesseract", "tesseract"),
)
"""The same three ``--ocr`` takes. Naming one makes its absence an error
rather than a quiet downgrade, which is why ``auto`` is spelled out."""

EXTRACT = QCoreApplication.translate("ExtractDialog", "Extract")

RAR_IN_PREFERENCES = QCoreApplication.translate(
    "ExtractDialog",
    "Preferences, under “reading a .cbr”, is where to say where the tool is.",
)
"""Where the window's answer to a missing RAR tool lives. The pipeline's own
message names an environment variable instead, which is the command line's
answer to the same question."""


def _patterns(suffixes: frozenset[str]) -> str:
    """``*.cbr *.cbz *.pdf`` — a file panel's filter, from the list itself.

    Written from the same constants the run reads, so a panel cannot come to
    offer something the pass then refuses, or hide something it would have
    taken.
    """
    return " ".join(f"*{suffix}" for suffix in sorted(suffixes))


class ExtractDialog(QDialog):
    """Settings for one run of the extract pass."""

    def __init__(
        self,
        start_in: Path | None = None,
        parent: QWidget | None = None,
        *,
        preferences: Preferences = DEFAULTS,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Extract Pages"))
        self._preferences = preferences
        self._start_in = start_in or Path.cwd()
        self._plan_edited = False
        """Whether the plan path has been typed in by hand.

        Until it has, it follows the input the way ``extract`` with no
        ``--plan`` does. Once it has, it stays where it was put — a path
        someone typed is not a default to overwrite.
        """

        # One question — which pages? — so one button. Which kind of thing it
        # will ask for is a pair of radio buttons beside it, rather than a
        # menu on the button: the mode is then visible without clicking
        # anything, and browsing stays one click. Qt has no file dialog that
        # accepts either kind (measured: `FileMode.Directory` refuses a file,
        # `ExistingFile` refuses a directory), and the one route to a panel
        # that does forces `DontUseNativeDialog` — a Qt-drawn Open panel on
        # macOS, and the only non-native one in an application whose every
        # other file dialog is the system's.
        #
        # A folder or a file, and not a third for chapter files: a chapter
        # file and a page are both one file to open, the panel offers both
        # at once, and what a file turns out to be is read out of it rather
        # than asked about here — see `sources.chapter_kind`. Asking twice
        # would be asking a question the answer is already in.
        self._count = QLabel()
        # On a line of its own below the radios rather than beside them: a
        # row holding both it and three radio buttons cannot be laid out for
        # every language, measured at 571pt of content in an English window
        # 406pt wide, which Qt spends by squeezing every widget in the row
        # equally — three radios clipped mid-word to pay for a label.
        #
        # One line, and it asks for no width at all. Wrapping this was the
        # obvious answer and the wrong one: a wrapped QLabel reports a size
        # hint from a guess at its own shape rather than from the width it is
        # given, so a form row is laid out a line shorter than the text turns
        # out to need and the second line is drawn under the row below. An
        # Ignored width policy is the other half — whatever this says, and in
        # whatever language, it can neither widen the dialog nor take space
        # from anything. The text is kept short enough to fit instead.
        self._count.setWordWrap(False)
        self._count.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._quieten(self._count)

        self._source = QLineEdit()
        self._folder_choice = QRadioButton(self.tr("a folder of pages"))
        self._file_choice = QRadioButton(self.tr("a file"))
        self._file_choice.setToolTip(
            self.tr("One page, or a whole chapter as a .cbz, .cbr or .pdf.")
        )
        self._folder_choice.setChecked(True)  # the usual case, by a long way
        self._source_kind = QButtonGroup(self)
        self._source_kind.addButton(self._folder_choice)
        self._source_kind.addButton(self._file_choice)
        choose_source = QPushButton(self.tr("Open…"))
        choose_source.setAutoDefault(False)
        choose_source.clicked.connect(self._on_choose_source)

        source_row = QHBoxLayout()
        source_row.setContentsMargins(0, 0, 0, 0)
        source_row.addWidget(self._source, 1)
        source_row.addWidget(choose_source)
        source_widget = QWidget()
        source_widget.setLayout(source_row)

        kind_row = QHBoxLayout()
        kind_row.setContentsMargins(0, 0, 0, 0)
        kind_row.addWidget(self._folder_choice)
        kind_row.addWidget(self._file_choice)
        kind_row.addStretch(1)
        kind_widget = QWidget()
        kind_widget.setLayout(kind_row)

        self._plan = QLineEdit()
        plan_button = QPushButton(self.tr("Choose…"))
        plan_button.setAutoDefault(False)
        plan_button.clicked.connect(self._on_choose_plan)
        plan_row = QHBoxLayout()
        plan_row.setContentsMargins(0, 0, 0, 0)
        plan_row.addWidget(self._plan, 1)
        plan_row.addWidget(plan_button)
        plan_widget = QWidget()
        plan_widget.setLayout(plan_row)

        # "Open…" and "Choose…" are different widths, so left alone the two
        # fields beside them end at different places. Matched off their own
        # size hints rather than a number, so it holds on any platform's font.
        button_width = max(choose_source.sizeHint().width(), plan_button.sizeHint().width())
        choose_source.setMinimumWidth(button_width)
        plan_button.setMinimumWidth(button_width)

        self._source_language = QLineEdit(preferences.source_language)
        self._target_language = QLineEdit(preferences.target_language)
        self._languages = QLineEdit(preferences.ocr_languages)
        self._languages.setPlaceholderText(self.tr("same as the source language"))
        self._languages.setToolTip(
            self.tr(
                "Languages to hand the recogniser, comma-separated. Left empty "
                "this is the source language; give a region-qualified tag here "
                "if the recogniser needs one, such as pt-BR."
            )
        )

        self._engine = QComboBox()
        for label, value in ENGINE_CHOICES:
            self._engine.addItem(label, value)
        self._engine.setCurrentIndex(self._engine.findData(preferences.ocr_engine))

        self._force = QCheckBox(self.tr("overwrite it, discarding everything in it"))
        self._force.hide()  # shown only when there is something to overwrite
        self._force.toggled.connect(self._validate)

        self._problem = QLabel()
        self._problem.setWordWrap(True)
        palette = self._problem.palette()
        palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.red)
        self._problem.setPalette(palette)

        form = QFormLayout()
        form.addRow(self.tr("read pages from"), source_widget)
        form.addRow("", kind_widget)
        # Always in the layout, empty or not: it is empty exactly when the
        # refusal below has something to say instead, and a dialog that
        # changes height as you type a path is worse than one line of space.
        form.addRow("", self._count)
        form.addRow(self.tr("write the plan to"), plan_widget)
        form.addRow("", self._force)
        form.addRow(self.tr("pages are lettered in"), self._source_language)
        form.addRow(self.tr("translating into"), self._target_language)
        form.addRow(self.tr("OCR languages"), self._languages)
        form.addRow(self.tr("recogniser"), self._engine)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok.setText(EXTRACT)
        self._ok.setDefault(True)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self._problem)
        layout.addStretch(1)
        layout.addWidget(self._buttons)
        self.setMinimumWidth(560)

        self._source.textChanged.connect(self._on_source_changed)
        self._plan.textEdited.connect(self._on_plan_edited)
        self._validate()

    # -- appearance ------------------------------------------------------

    @staticmethod
    def _quieten(label: QLabel) -> None:
        palette = label.palette()
        palette.setColor(
            QPalette.ColorRole.WindowText,
            palette.color(QPalette.ColorRole.PlaceholderText),
        )
        label.setPalette(palette)

    # -- choosing paths --------------------------------------------------

    def _on_choose_source(self) -> None:
        """Open the system panel the radio buttons asked for.

        The radios steer this button and nothing else. What the field will
        accept is decided by looking at the path, not by which of them is
        checked, so a folder typed in under "a single image" still works.
        """
        start = str(self._start_in)
        if self._folder_choice.isChecked():
            name = QFileDialog.getExistingDirectory(self, "Pages to Read", start)
        else:
            # One panel for both, because both are one file to open. "All
            # files" stays on the end for the chapter saved under a name
            # nobody uses, which the run reads anyway: what it is decides,
            # and a file panel can only filter on what it is called.
            everything = _patterns(IMAGE_SUFFIXES | CONTAINER_SUFFIXES)
            name, _filter = QFileDialog.getOpenFileName(
                self,
                "Page or Chapter to Read",
                start,
                f"Pages and chapters ({everything});;All files (*)",
            )
        if name:
            self._source.setText(name)

    def _on_choose_plan(self) -> None:
        start = self._plan.text().strip() or str(self._start_in)
        name, _filter = QFileDialog.getSaveFileName(
            self, "Write the Plan To", start, "Plan files (*.yaml *.yml)"
        )
        if name:
            self._plan_edited = True
            self._plan.setText(name)
            self._validate()

    def _on_plan_edited(self, _text: str) -> None:
        self._plan_edited = True
        self._validate()

    def _on_source_changed(self, text: str) -> None:
        if not self._plan_edited:
            self._plan.setText(str(self.suggested_plan()) if text.strip() else "")
        self._validate()

    def suggested_plan(self) -> Path:
        """Where the plan goes if nobody says otherwise.

        A chapter file's plan belongs with the pages it describes, which are
        not beside the chapter file but inside the folder it unpacks into —
        so this answers for the folder, which is decided before it exists.
        That is what ``comictrans extract chapter.cbz`` writes too: the two
        must not put the same plan in two places.

        Spelled out rather than handed to ``default_plan_path``, which
        decides between a directory and a file by looking at the path: the
        folder is not there yet, so it would be taken for a file and the plan
        would land beside it under a name nothing else uses.
        """
        source = self.source()
        if is_container(source):
            return default_unpack_dir(source) / PLAN_NAME
        return default_plan_path(source)

    # -- what it will read -----------------------------------------------

    def source(self) -> Path:
        return Path(self._source.text().strip()).expanduser()

    def plan_path(self) -> Path:
        return Path(self._plan.text().strip()).expanduser()

    def pages(self) -> tuple[Path, ...]:
        """The images this run would read, or empty if the input is unusable.

        A chapter file has none yet, and counting them would mean reading it:
        an archive's member list on every keystroke, or worse, a RAR tool
        started as a subprocess on each one. The pages are counted when they
        are unpacked, and the panel is told the total then — see
        :class:`ExtractJob`.
        """
        text = self._source.text().strip()
        if not text or is_container(self.source()):
            return ()
        try:
            accepted, _skipped = collect_inputs(Path(text).expanduser())
        except (ComictransError, OSError):
            return ()
        return tuple(accepted)

    def refusal(self) -> str:
        """Why this run cannot start, or an empty string.

        Asked on every keystroke rather than once the dialog has closed, so
        the button says what is wrong while there is still something to
        change. Reading the directory to count its pages is the same listing
        ``extract`` does first thing, so what the dialog reports and what the
        run will read cannot disagree.
        """
        if not self._source.text().strip():
            return self.tr("Choose a folder of pages, a chapter file, or one image.")
        source = self.source()
        if is_container(source):
            try:
                # Cheap on purpose: the kind of file it is, and whether the
                # tool a .cbr needs is here — which is the refusal worth
                # making now rather than after a wait. Nothing is read.
                check_readable(source, self._preferences.rar_tool)
            except ComictransError as exc:
                if chapter_kind(source) == RAR:
                    # The pipeline's own message names the environment
                    # variable, which is the command line's answer and no use
                    # to somebody reading this. The window's answer is a
                    # preference, and this is the moment to say where.
                    return f"{exc}\n{RAR_IN_PREFERENCES}"
                return f"{exc}"
            except OSError as exc:
                return self.tr("{0} cannot be read: {1}").format(source, exc)
        else:
            try:
                # Raises on a path that does not exist, one that is neither
                # file nor directory, an unsupported single file, and a
                # directory with no images in it — every refusal the command
                # line makes, in the words it makes them in.
                collect_inputs(source)
            except ComictransError as exc:
                return f"{exc}"
            except OSError as exc:
                return self.tr("{0} cannot be read: {1}").format(source, exc)

        plan = self._plan.text().strip()
        if not plan:
            return self.tr("Choose where to write the plan file.")
        target = self.plan_path()
        if target.is_dir():
            return self.tr("{0} is a directory, not a plan file.").format(target)
        if target.exists() and not self._force.isChecked():
            return self.tr("{0} already exists.").format(target.name)
        return ""

    def _validate(self) -> None:
        pages = self.pages()
        source = self.source()
        if is_container(source):
            # Said only once there is a chapter to say it about: a path
            # half-typed is a refusal, and two answers at once about the same
            # field is one too many.
            self._count.setText(self.tr("counted when unpacked") if source.is_file() else "")
        else:
            self._count.setText(self.tr("%n page(s)", None, len(pages)) if pages else "")
        plan = self._plan.text().strip()
        # The overwrite box appears only when there is a file under the
        # cursor to overwrite, so it cannot be ticked in advance and then
        # forgotten about by the time it matters.
        self._force.setVisible(bool(plan) and Path(plan).expanduser().is_file())
        problem = self.refusal()
        self._problem.setText(problem)
        self._problem.setVisible(bool(problem))
        self._ok.setEnabled(not problem)

    # -- the result ------------------------------------------------------

    def request(self) -> ExtractRequest:
        """What was chosen, ready to hand to an :class:`ExtractJob`.

        Everything this dialog does not ask about is left at the value
        ``extract`` uses with no flag, so a plan written from this window and
        one written by ``comictrans extract <dir>`` come out the same.
        """
        source_language = self._source_language.text().strip() or DEFAULT_SOURCE_LANGUAGE
        typed = [part.strip() for part in self._languages.text().split(",")]
        languages = tuple(part for part in typed if part) or (source_language,)
        return ExtractRequest(
            source=self.source(),
            plan_path=self.plan_path(),
            config=ExtractConfig(
                ocr=OcrConfig(
                    languages=languages,
                    confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD,
                    engine=str(self._engine.currentData()),
                ),
                detect=DetectConfig(),
            ),
            source_language=source_language,
            target_language=self._target_language.text().strip() or DEFAULT_TARGET_LANGUAGE,
            # Empty stays None, which is extract walking its own fallback
            # chain — the behaviour with no --font, and what this dialog did
            # before there was anywhere to record an answer.
            font=self._preferences.font or None,
            force=self._force.isChecked(),
            pages=self.pages(),
            rar_tool=self._preferences.rar_tool,
        )


__all__ = ["ENGINE_CHOICES", "EXTRACT", "ExtractDialog"]
