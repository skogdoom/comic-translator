"""What one render run is going to do, settled before it starts.

Four decisions, and one refusal that is not a decision at all.

The three decisions the command line offers as flags are here as fields:
where to write, what format, and how to remove the original lettering. The
fourth, overwriting files that are already there, is ``--force`` turned back
into what it is in a window — a checkbox you tick, rather than a refusal you
rerun the command to get past.

The refusal is the output directory. ``check_output_dir`` is where the
invariant that source images are never written to is actually enforced, and
it is asked here, as each character is typed, rather than after the dialog
closes: the button stays disabled and says why. There is deliberately no
override — no checkbox, no confirmation, nothing to hold down. It is the one
refusal in this tool that a user cannot argue with.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
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
    QVBoxLayout,
    QWidget,
)

from ..apply import check_output_dir, source_for
from ..config import EraseConfig
from ..errors import ComictransError
from ..model import Plan
from .document import PlanDocument
from .preferences import DEFAULTS, Preferences
from .preview import apply_config_for
from .run_job import RenderRequest

FORMAT_CHOICES: tuple[tuple[str, str | None], ...] = (
    ("same as the source", None),
    ("PNG", "png"),
    ("JPEG", "jpeg"),
    ("TIFF", "tiff"),
)
"""``None`` is what ``apply`` does by default: keep the source's format,
except that JPEG becomes PNG rather than re-compressing lettering."""

STRATEGY_CHOICES: tuple[tuple[str, str, str], ...] = (
    ("the lettering", "flat", "repaint the original lettering in the fill colour"),
    ("the whole region", "polygon", "flood the whole outline with the fill colour"),
    ("reconstruct", "inpaint", "rebuild the lettering's pixels from the ones around them"),
    ("nothing", "none", "paint nothing; letter straight onto the page as it is"),
)
"""Label, strategy name, and what it does. The words are the inspector's own
words for the same four things, so the box that decides it for one region and
the box that decides it for the rest do not describe them differently.

A region's own ``erase`` still wins over whichever of these is chosen: this
is the fallback for the regions that do not name one, exactly as ``--erase``
is on the command line."""

RENDER = "Render"
SAVE_AND_RENDER = "Save and Render"
"""The button names what pressing it will do, and an unsaved plan is saved
first — see the class docstring."""


def suggested_output(plan_path: Path, preferences: Preferences = DEFAULTS) -> Path:
    """Where to write, before anyone has said: a preference, or beside the pages.

    A suggestion either way, not a decision — it is prefilled so the common
    case is one keystroke, and it is validated like anything typed by hand,
    a stored preference included. The fallback is *beside* the pages rather
    than under them, because under is the one place it can never go.
    """
    if preferences.output_directory:
        return Path(preferences.output_directory).expanduser()
    pages = plan_path.parent
    return pages.parent / f"{pages.name}-translated"


def source_dirs(plan: Plan, plan_path: Path) -> list[Path]:
    """Every directory the plan reads pages from."""
    return sorted({source_for(plan_path, name).parent for name in plan.image_names()})


class RenderDialog(QDialog):
    """Settings for one run of the apply pass."""

    def __init__(
        self,
        document: PlanDocument,
        parent: QWidget | None = None,
        *,
        preferences: Preferences = DEFAULTS,
    ) -> None:
        super().__init__(parent)
        self._document = document
        self._sources = source_dirs(document.plan, document.path)
        self.setWindowTitle("Render Pages")

        self._output = QLineEdit(str(suggested_output(document.path, preferences)))
        choose = QPushButton("Choose…")
        choose.setAutoDefault(False)
        choose.clicked.connect(self._on_choose)
        where = QHBoxLayout()
        where.setContentsMargins(0, 0, 0, 0)
        where.addWidget(self._output, 1)
        where.addWidget(choose)
        where_widget = QWidget()
        where_widget.setLayout(where)

        self._format = QComboBox()
        for label, value in FORMAT_CHOICES:
            self._format.addItem(label, value)
        # A preference stores "" for "match the source"; the request wants
        # None. One conversion, here, at the edge.
        self._format.setCurrentIndex(self._format.findData(preferences.image_format or None))

        self._erase = QComboBox()
        for label, value, _description in STRATEGY_CHOICES:
            self._erase.addItem(label, value)
        self._erase.setCurrentIndex(self._erase.findData(preferences.erase_strategy))
        self._erase_help = QLabel()
        self._erase_help.setWordWrap(True)
        self._quieten(self._erase_help)

        self._force = QCheckBox("Overwrite pages already in that directory")

        # Red rather than the usual grey: this is the one message here that
        # is stopping something from happening.
        self._problem = QLabel()
        self._problem.setWordWrap(True)
        palette = self._problem.palette()
        palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.red)
        self._problem.setPalette(palette)

        form = QFormLayout()
        form.addRow("write pages to", where_widget)
        form.addRow("format", self._format)
        form.addRow("erase", self._erase)
        form.addRow("", self._erase_help)
        form.addRow("", self._force)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok.setText(SAVE_AND_RENDER if document.dirty else RENDER)
        self._ok.setDefault(True)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self._what_it_will_do()))
        layout.addSpacing(6)
        layout.addLayout(form)
        layout.addWidget(self._problem)
        layout.addStretch(1)
        layout.addWidget(self._buttons)
        self.setMinimumWidth(520)

        self._output.textChanged.connect(self._validate)
        self._erase.currentIndexChanged.connect(self._describe_erase)
        self._describe_erase()
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

    def _what_it_will_do(self) -> str:
        pages = len(self._document.plan.image_names())
        regions = len(self._document.plan.regions)
        saving = "Saves the plan, then renders" if self._document.dirty else "Renders"
        return (
            f"{saving} {pages} page{'' if pages == 1 else 's'} "
            f"and {regions} region{'' if regions == 1 else 's'}."
        )

    def _describe_erase(self) -> None:
        chosen = self._erase.currentData()
        for _label, value, description in STRATEGY_CHOICES:
            if value == chosen:
                self._erase_help.setText(description)
                return
        self._erase_help.setText("")

    # -- the output directory --------------------------------------------

    def _on_choose(self) -> None:
        start = self._output.text().strip() or str(self._document.path.parent)
        name = QFileDialog.getExistingDirectory(self, "Render Into", start)
        if name:
            self._output.setText(name)

    def output_dir(self) -> Path:
        return Path(self._output.text().strip()).expanduser()

    def refusal(self) -> str:
        """Why this output directory cannot be used, or an empty string.

        Public because it is the whole point of the dialog and the thing
        worth testing: the judgement is ``check_output_dir``'s, made here on
        every keystroke instead of once the dialog has closed.
        """
        text = self._output.text().strip()
        if not text:
            return "Choose a directory to write the pages into."
        path = Path(text).expanduser()
        try:
            if path.exists() and not path.is_dir():
                return f"{path} is a file, not a directory."
            check_output_dir(path, self._sources)
        except ComictransError as exc:
            return f"{exc}"
        except OSError as exc:
            return f"{path} cannot be used: {exc}"
        return ""

    def _validate(self) -> None:
        problem = self.refusal()
        self._problem.setText(problem)
        self._problem.setVisible(bool(problem))
        self._ok.setEnabled(not problem)

    # -- the result ------------------------------------------------------

    def request(self) -> RenderRequest:
        """What was chosen, ready to hand to a :class:`RenderJob`.

        The typesetting settings come from the plan's own header, through the
        same function the live preview uses, so a page rendered from this
        window and the same page rendered by ``comictrans apply`` are made
        the same way. Only the three things this dialog asks about differ
        from a plain command-line run, and each of them is a flag there too.
        """
        config = replace(
            apply_config_for(self._document),
            erase=EraseConfig(strategy=str(self._erase.currentData())),
        )
        return RenderRequest(
            plan=self._document.plan,
            plan_path=self._document.path,
            output=self.output_dir(),
            config=config,
            image_format=self._format.currentData(),
            force=self._force.isChecked(),
        )


__all__ = [
    "FORMAT_CHOICES",
    "RENDER",
    "SAVE_AND_RENDER",
    "STRATEGY_CHOICES",
    "RenderDialog",
]
