"""What one render run is going to do, settled before it starts.

Five decisions, and one refusal that is not a decision at all.

The decisions the command line offers as flags are here as fields: where to
write, what each page is encoded as, and how to remove the original
lettering. Overwriting what is already there is ``--force`` turned back into
what it is in a window — a checkbox you tick, rather than a refusal you rerun
the command to get past.

**What holds the pages is the same decision as where they go**, shown twice.
``apply`` decides it from the output's name and nothing else, because an
output file does not exist to be inspected; a window that made you discover
that by typing ``.cbz`` would be hiding the feature behind a guess. So the
box and the path are two views of one fact and each sets the other: choosing
a ``.cbz`` renames the path, and typing one moves the box. There is no third
state for them to disagree in.

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

from PySide6.QtCore import QCoreApplication, QSignalBlocker, Qt
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

from ..apply import check_output_dir
from ..config import EraseConfig
from ..errors import ComictransError
from ..model import Plan, source_path
from ..pack import RAR_MISSING, archive_kind
from ..pack import check_writable as check_can_pack
from ..sources import RAR, ZIP
from .document import PlanDocument
from .note import Note
from .preferences import DEFAULTS, Preferences
from .preview import apply_config_for
from .run_job import RenderRequest

# ``QCoreApplication.translate`` rather than ``tr`` here and below: these
# tables are module-level and have no ``self`` to ask. They are evaluated at
# import, which is after the translator goes in — see ``translations``. Each
# call is written out in full rather than wrapped in a helper: ``lupdate``
# reads the source rather than running it, and extracts nothing from behind
# one. Measured, not assumed.
FORMAT_CHOICES: tuple[tuple[str, str | None], ...] = (
    (QCoreApplication.translate("RenderDialog", "same as the source"), None),
    ("PNG", "png"),
    ("JPEG", "jpeg"),
    ("TIFF", "tiff"),
)
"""``None`` is what ``apply`` does by default: keep the source's format,
except that JPEG becomes PNG rather than re-compressing lettering."""

STRATEGY_CHOICES: tuple[tuple[str, str, str], ...] = (
    (
        QCoreApplication.translate("RenderDialog", "the lettering"),
        "flat",
        QCoreApplication.translate(
            "RenderDialog", "repaint the original lettering in the fill colour"
        ),
    ),
    (
        QCoreApplication.translate("RenderDialog", "the whole region"),
        "polygon",
        QCoreApplication.translate("RenderDialog", "flood the whole outline with the fill colour"),
    ),
    (
        QCoreApplication.translate("RenderDialog", "reconstruct"),
        "inpaint",
        QCoreApplication.translate(
            "RenderDialog", "rebuild the lettering's pixels from the ones around them"
        ),
    ),
    (
        QCoreApplication.translate("RenderDialog", "nothing"),
        "none",
        QCoreApplication.translate(
            "RenderDialog", "paint nothing; letter straight onto the page as it is"
        ),
    ),
)
"""Label, strategy name, and what it does. The words are the inspector's own
words for the same four things, so the box that decides it for one region and
the box that decides it for the rest do not describe them differently.

A region's own ``erase`` still wins over whichever of these is chosen: this
is the fallback for the regions that do not name one, exactly as ``--erase``
is on the command line."""

CONTAINER_CHOICES: tuple[tuple[str, str | None], ...] = (
    (QCoreApplication.translate("RenderDialog", "a folder"), None),
    (QCoreApplication.translate("RenderDialog", "one .cbz file"), ".cbz"),
    (QCoreApplication.translate("RenderDialog", "one .cbr file"), ".cbr"),
)
"""What the pages end up in, and the suffix that says so.

The suffix is the value because it is what the decision *is*: ``apply``
reads the output's name and nothing else, so choosing a row here is choosing
an extension. ``.cbz`` and ``.cbr`` rather than ``.zip`` and ``.rar``
because those are what a chapter is called; a path already ending in the
plain ones is recognised and left as it is.
"""

NO_RAR_HERE = QCoreApplication.translate(
    "RenderDialog",
    "Saving as .cbr needs the rar compressor, which is not on this Mac. Say "
    "where it is in Preferences ▸ chapter files, or save as .cbz instead.",
)
"""The window's own answer to a missing compressor, in place of the
pipeline's.

``pack.RAR_MISSING`` explains the licence and names ``COMICTRANS_RAR``, which
is the command line's answer to this and no use to somebody reading a dialog:
there is a field for it, and Preferences is where the licence is explained.
What a refusal owes you is what to do next, in one sentence, and there are
two things you can do."""

RENDER = QCoreApplication.translate("RenderDialog", "Render")
SAVE_AND_RENDER = QCoreApplication.translate("RenderDialog", "Save and Render")
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
    return sorted({source_path(plan_path, name).parent for name in plan.image_names()})


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
        self._preferences = preferences
        self._sources = source_dirs(document.plan, document.path)
        self.setWindowTitle(self.tr("Render Pages"))

        self._output = QLineEdit(str(suggested_output(document.path, preferences)))
        self._choose_button = QPushButton(self.tr("Choose…"))
        self._choose_button.setAutoDefault(False)
        self._choose_button.clicked.connect(self._on_choose)
        where = QHBoxLayout()
        where.setContentsMargins(0, 0, 0, 0)
        where.addWidget(self._output, 1)
        where.addWidget(self._choose_button)
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
        self._erase_help = Note()

        self._container = QComboBox()
        for label, suffix in CONTAINER_CHOICES:
            self._container.addItem(label, suffix)
        self._container.setCurrentIndex(self._container.findData(self._suffix_of(self.output())))
        self._force = QCheckBox()

        # Red rather than the usual grey: this is the one message here that
        # is stopping something from happening. A ``Note`` all the same —
        # what that class is for is the height, and this is the longest and
        # most variable thing the dialog ever says.
        self._problem = Note()
        palette = self._problem.palette()
        palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.red)
        self._problem.setPalette(palette)

        form = QFormLayout()
        form.addRow(self.tr("write pages to"), where_widget)
        form.addRow(self.tr("as"), self._container)
        form.addRow(self.tr("format"), self._format)
        form.addRow(self.tr("erase"), self._erase)
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

        self._output.textChanged.connect(self._on_output_typed)
        self._container.currentIndexChanged.connect(self._on_container_chosen)
        self._erase.currentIndexChanged.connect(self._describe_erase)
        self._describe_erase()
        self._describe_container()
        self._validate()

    # -- appearance ------------------------------------------------------

    def _what_it_will_do(self) -> str:
        """What pressing the button will do, counted.

        Two counts in one sentence, and Qt's ``%n`` inflects one message for
        one of them — so the regions are their own complete phrase, dropped
        into the sentence at a placeholder. Each half is then a thing a
        translator can get right on its own, which gluing "3" to "region"
        would not be.
        """
        pages = len(self._document.plan.image_names())
        regions = self.tr("%n region(s)", None, len(self._document.plan.regions))
        if self._document.dirty:
            return self.tr("Saves the plan, then renders %n page(s) and {0}.", None, pages).format(
                regions
            )
        return self.tr("Renders %n page(s) and {0}.", None, pages).format(regions)

    def _describe_erase(self) -> None:
        chosen = self._erase.currentData()
        for _label, value, description in STRATEGY_CHOICES:
            if value == chosen:
                self._erase_help.setText(description)
                return
        self._erase_help.setText("")

    def _describe_container(self) -> None:
        """What overwriting would mean, which is not the same for the two.

        A directory is overwritten page by page and a chapter file all at
        once, so "pages already in that directory" over a ``.cbz`` would be
        describing something that is not going to happen.

        There is deliberately no second line here saying what a .cbr needs.
        That sentence is the refusal's, below, which says it in full and says
        it only when it is true — a grey note repeating a red one above it is
        the same text twice, and the two of them together grew this dialog
        past what it had room for.
        """
        self._force.setText(
            self.tr("overwrite the chapter file if it is already there")
            if self.output_is_archive()
            else self.tr("overwrite pages already in that directory")
        )

    # -- where the pages go ----------------------------------------------

    @staticmethod
    def _suffix_of(output: Path) -> str | None:
        """The row in the box that this path already is.

        ``archive_kind`` decides, so ``chapter.zip`` and ``chapter.cbz`` both
        land on the CBZ row — the path keeps the name it was given and only
        the box moves, because renaming what somebody typed is not this
        dialog's business.
        """
        kind = archive_kind(output)
        if kind is None:
            return None
        return ".cbz" if kind == ZIP else ".cbr"

    @staticmethod
    def _renamed(output: Path, suffix: str | None) -> Path:
        """The same output, held in the thing ``suffix`` names.

        ``with_suffix`` only where there is an archive suffix to replace: a
        folder called ``vol.2-translated`` has a suffix as far as
        :class:`Path` is concerned, and ``with_suffix`` would turn it into
        ``vol.cbz``.
        """
        if archive_kind(output) is not None:
            return output.with_suffix(suffix) if suffix else output.with_suffix("")
        if not suffix or not output.name:
            # ``with_name`` raises on a path with no name of its own — "/",
            # or the "." an empty field comes back as. There is nothing to
            # rename, so the field stays as it is and the refusal below asks
            # for a name instead.
            return output
        return output.with_name(output.name + suffix)

    def _on_container_chosen(self) -> None:
        """Choosing a container renames the output; see the module docstring.

        An empty field is the one case with nothing to rename, and it is left
        empty rather than filled with the "." a blank path parses as.
        """
        if self._output.text().strip():
            renamed = self._renamed(self.output(), self._container.currentData())
            with QSignalBlocker(self._output):
                self._output.setText(str(renamed))
        self._describe_container()
        self._validate()

    def _on_output_typed(self) -> None:
        """And naming one moves the box, which is the same fact from the
        other end. Blocked, or it would rename the path back mid-keystroke."""
        with QSignalBlocker(self._container):
            self._container.setCurrentIndex(
                self._container.findData(self._suffix_of(self.output()))
            )
        self._describe_container()
        self._validate()

    def _on_choose(self) -> None:
        """A folder panel or a save panel, whichever the output is.

        An archive is one file that does not exist yet, and the panel that
        asks for a folder cannot name one.
        """
        start = self._output.text().strip() or str(self._document.path.parent)
        if self.output_is_archive():
            suffix = self._container.currentData()
            name, _filter = QFileDialog.getSaveFileName(
                self,
                self.tr("Save Chapter As"),
                start,
                self.tr("Chapter file (*{0})").format(suffix),
            )
        else:
            name = QFileDialog.getExistingDirectory(self, self.tr("Render Into"), start)
        if name:
            self._output.setText(name)

    def output(self) -> Path:
        """Where the run will write: a directory, or one chapter file."""
        return Path(self._output.text().strip()).expanduser()

    def output_dir(self) -> Path:
        """Kept under its old name, which is still what it is most of the
        time. :meth:`output` is the honest one now that it can be a file."""
        return self.output()

    def output_is_archive(self) -> bool:
        """Whether this run writes one file, asked of the box rather than the
        path: the two agree except when the path is empty, and an empty path
        still has a kind chosen for it."""
        return self._container.currentData() is not None

    def refusal(self) -> str:
        """Why this output cannot be used, or an empty string.

        Public because it is the whole point of the dialog and the thing
        worth testing: the judgement is ``check_output_dir``'s, made here on
        every keystroke instead of once the dialog has closed. An archive is
        asked the same question about the folder it would go in, plus one of
        its own — whether there is anything on this machine to write it with,
        which is worth knowing before a chapter is rendered rather than
        after.
        """
        text = self._output.text().strip()
        if not text:
            return (
                self.tr("Name the chapter file to write.")
                if self._container.currentData()
                else self.tr("Choose a directory to write the pages into.")
            )
        path = Path(text).expanduser()
        try:
            if archive_kind(path) is None:
                if path.exists() and not path.is_dir():
                    return self.tr("{0} is a file, not a directory.").format(path)
                check_output_dir(path, self._sources)
            else:
                if path.is_dir():
                    return self.tr("{0} is a directory, not a file.").format(path)
                check_output_dir(path.parent, self._sources)
                check_can_pack(path, self._preferences.rar_tool)
        except ComictransError as exc:
            if archive_kind(path) == RAR and str(exc) == RAR_MISSING:
                # Only the one the window has a better answer to. A path that
                # was given and does not work says which path, which is the
                # useful half and is not this module's to reword.
                return NO_RAR_HERE
            return f"{exc}"
        except OSError as exc:
            return self.tr("{0} cannot be used: {1}").format(path, exc)
        return ""

    def _validate(self) -> None:
        problem = self.refusal()
        self._problem.setText(problem)
        self._problem.setVisible(bool(problem))
        self._ok.setEnabled(not problem)
        self._fit()

    def _fit(self) -> None:
        """Grow to hold what is now being said, and never shrink below it.

        A refusal is several lines of wrapped text that appears and goes as a
        path is typed, and a dialog that stays the height it opened at does
        not refuse to show it — it takes the height out of whatever else can
        give, which is the other wrapped labels, and they come out clipped.
        Measured: the form wanting 303px in a window holding 247 left the
        erase note 14px of the 28 it had.

        Grow only. ``adjustSize`` would also shrink, which would undo a size
        somebody had dragged out for themselves every time a message came and
        went; a window left taller than it needs has a stretch in it and
        looks like nothing at all.
        """
        self.resize(self.width(), max(self.height(), self.sizeHint().height()))

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
            apply_config_for(self._document.plan),
            erase=EraseConfig(strategy=str(self._erase.currentData())),
        )
        return RenderRequest(
            plan=self._document.plan,
            plan_path=self._document.path,
            output=self.output(),
            config=config,
            image_format=self._format.currentData(),
            rar_tool=self._preferences.rar_tool,
            force=self._force.isChecked(),
        )


__all__ = [
    "CONTAINER_CHOICES",
    "FORMAT_CHOICES",
    "NO_RAR_HERE",
    "RENDER",
    "SAVE_AND_RENDER",
    "STRATEGY_CHOICES",
    "RenderDialog",
]
