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

from PySide6.QtCore import Qt
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
from ..extract import default_plan_path
from ..imaging import IMAGE_SUFFIXES, collect_inputs
from .run_job import ExtractRequest

ENGINE_CHOICES: tuple[tuple[str, str], ...] = (
    ("automatic (Apple Vision, then Tesseract)", "auto"),
    ("Apple Vision", "vision"),
    ("Tesseract", "tesseract"),
)
"""The same three ``--ocr`` takes. Naming one makes its absence an error
rather than a quiet downgrade, which is why ``auto`` is spelled out."""

EXTRACT = "Extract"


class ExtractDialog(QDialog):
    """Settings for one run of the extract pass."""

    def __init__(self, start_in: Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Extract Pages")
        self._start_in = start_in or Path.cwd()
        self._plan_edited = False
        """Whether the plan path has been typed in by hand.

        Until it has, it follows the input the way ``extract`` with no
        ``--plan`` does. Once it has, it stays where it was put — a path
        someone typed is not a default to overwrite.
        """

        # One button, because it answers one question — which pages? — and
        # the two kinds of answer are the same decision made at two
        # granularities. It is a menu rather than a single panel because Qt
        # has no file dialog that accepts either: `Directory` mode refuses a
        # file and `ExistingFile` refuses a directory, both measured. The one
        # way to get a panel that takes both is to override `accept()`, which
        # forces `DontUseNativeDialog` — a Qt-drawn Open panel on macOS, and
        # the only non-native one in an application whose every other file
        # dialog is the system's. The extra click is the cheaper cost.
        # One question — which pages? — so one button. Which of the two kinds
        # of answer it will ask for is a pair of radio buttons beside it,
        # rather than a menu on the button: the mode is then visible without
        # clicking anything, and browsing stays one click. Qt has no file
        # dialog that accepts either kind (measured: `FileMode.Directory`
        # refuses a file, `ExistingFile` refuses a directory), and the one
        # route to a panel that does forces `DontUseNativeDialog` — a
        # Qt-drawn Open panel on macOS, and the only non-native one in an
        # application whose every other file dialog is the system's.
        self._count = QLabel()
        self._quieten(self._count)

        self._source = QLineEdit()
        self._folder_choice = QRadioButton("a folder of pages")
        self._image_choice = QRadioButton("a single image")
        self._folder_choice.setChecked(True)  # the usual case, by a long way
        self._source_kind = QButtonGroup(self)
        self._source_kind.addButton(self._folder_choice)
        self._source_kind.addButton(self._image_choice)
        choose_source = QPushButton("Open…")
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
        kind_row.addWidget(self._image_choice)
        kind_row.addSpacing(12)
        kind_row.addWidget(self._count)
        kind_row.addStretch(1)
        kind_widget = QWidget()
        kind_widget.setLayout(kind_row)

        self._plan = QLineEdit()
        plan_button = QPushButton("Choose…")
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

        self._source_language = QLineEdit(DEFAULT_SOURCE_LANGUAGE)
        self._target_language = QLineEdit(DEFAULT_TARGET_LANGUAGE)
        self._languages = QLineEdit()
        self._languages.setPlaceholderText("same as the source language")
        self._languages.setToolTip(
            "Languages to hand the recogniser, comma-separated. Left empty "
            "this is the source language; give a region-qualified tag here "
            "if the recogniser needs one, such as pt-BR."
        )

        self._engine = QComboBox()
        for label, value in ENGINE_CHOICES:
            self._engine.addItem(label, value)

        self._force = QCheckBox("Overwrite it, discarding everything in it")
        self._force.hide()  # shown only when there is something to overwrite
        self._force.toggled.connect(self._validate)

        self._problem = QLabel()
        self._problem.setWordWrap(True)
        palette = self._problem.palette()
        palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.red)
        self._problem.setPalette(palette)

        form = QFormLayout()
        form.addRow("read pages from", source_widget)
        form.addRow("", kind_widget)
        form.addRow("write the plan to", plan_widget)
        form.addRow("", self._force)
        form.addRow("pages are lettered in", self._source_language)
        form.addRow("translating into", self._target_language)
        form.addRow("OCR languages", self._languages)
        form.addRow("recogniser", self._engine)

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
            suffixes = " ".join(f"*{suffix}" for suffix in sorted(IMAGE_SUFFIXES))
            name, _filter = QFileDialog.getOpenFileName(
                self, "Page to Read", start, f"Images ({suffixes});;All files (*)"
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
            source = Path(text.strip()).expanduser()
            self._plan.setText(str(default_plan_path(source)) if text.strip() else "")
        self._validate()

    # -- what it will read -----------------------------------------------

    def source(self) -> Path:
        return Path(self._source.text().strip()).expanduser()

    def plan_path(self) -> Path:
        return Path(self._plan.text().strip()).expanduser()

    def pages(self) -> tuple[Path, ...]:
        """The images this run would read, or empty if the input is unusable."""
        text = self._source.text().strip()
        if not text:
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
            return "Choose a folder of pages, or one image."
        source = self.source()
        try:
            # Raises on a path that does not exist, one that is neither file
            # nor directory, an unsupported single file, and a directory with
            # no images in it — every refusal the command line makes, in the
            # words it makes them in.
            collect_inputs(source)
        except ComictransError as exc:
            return f"{exc}"
        except OSError as exc:
            return f"{source} cannot be read: {exc}"

        plan = self._plan.text().strip()
        if not plan:
            return "Choose where to write the plan file."
        target = self.plan_path()
        if target.is_dir():
            return f"{target} is a directory, not a plan file."
        if target.exists() and not self._force.isChecked():
            return f"{target.name} already exists."
        return ""

    def _validate(self) -> None:
        pages = self.pages()
        self._count.setText(f"{len(pages)} page{'' if len(pages) == 1 else 's'}" if pages else "")
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
            force=self._force.isChecked(),
            pages=self.pages(),
        )


__all__ = ["ENGINE_CHOICES", "EXTRACT", "ExtractDialog"]
