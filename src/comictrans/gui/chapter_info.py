"""What a chapter says about itself, shown by the reader window.

Read-only, and a window of its own rather than a panel: it is looked at once
or twice while reading, not kept beside the pages, and a panel would take
room the pages have to themselves. It stays open while the pages are turned.

**Every value is shown as plain text.** They are somebody else's bytes — a
ComicInfo.xml arrives inside a chapter file from wherever chapters arrive
from — and a ``QLabel`` left to decide for itself renders anything that looks
like HTML as HTML, images and links included. So nothing here is asked to
guess: each label is told it holds plain text, and the summary is a plain
text box.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..model import ReadingDirection
from ..reading import ChapterInfo
from .language_box import language_label


def _plain(text: str) -> QLabel:
    label = QLabel()
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setText(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class ChapterInfoDialog(QDialog):
    """The details a chapter's ComicInfo.xml gives, or why it gave none."""

    def __init__(self, info: ChapterInfo, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Chapter Info — {0}").format(title))
        layout = QVBoxLayout(self)
        self.form = QFormLayout()
        layout.addLayout(self.form)

        if info.problem:
            self.form.addRow(
                _plain(
                    self.tr("This chapter's ComicInfo.xml could not be read: {0}").format(
                        info.problem
                    )
                )
            )
        elif not info.details and info.reading_direction is None:
            self.form.addRow(
                _plain(self.tr("This chapter's ComicInfo.xml says nothing a reader shows."))
            )

        labels = self._labels()
        summary = ""
        for element, value in info.details:
            if element == "summary":
                summary = value
                continue
            shown = language_label(value) if element == "languageiso" else value
            self.form.addRow(labels[element], _plain(shown))
        if info.reading_direction is not None:
            self.form.addRow(
                self.tr("reading direction"),
                _plain(
                    self.tr("right to left")
                    if info.reading_direction is ReadingDirection.RIGHT_TO_LEFT
                    else self.tr("left to right")
                ),
            )
        if summary:
            # A box of its own, because a summary runs to paragraphs and the
            # rest are a line each; plain by construction.
            text = QPlainTextEdit(summary)
            text.setReadOnly(True)
            self.form.addRow(labels["summary"], text)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.resize(460, self.sizeHint().height())

    def _labels(self) -> dict[str, str]:
        """What each of ``reading.DETAILS`` is called here.

        Written out one ``tr`` call at a time, because ``lupdate`` reads the
        source and sees nothing behind a loop.
        """
        return {
            "series": self.tr("series"),
            "number": self.tr("number"),
            "title": self.tr("title"),
            "volume": self.tr("volume"),
            "year": self.tr("year"),
            "publisher": self.tr("publisher"),
            "writer": self.tr("writer"),
            "penciller": self.tr("penciller"),
            "inker": self.tr("inker"),
            "colorist": self.tr("colourist"),
            "letterer": self.tr("letterer"),
            "coverartist": self.tr("cover"),
            "editor": self.tr("editor"),
            "translator": self.tr("translator"),
            "genre": self.tr("genre"),
            "languageiso": self.tr("language"),
            "pagecount": self.tr("pages"),
            "summary": self.tr("summary"),
        }


__all__ = ["ChapterInfoDialog"]
