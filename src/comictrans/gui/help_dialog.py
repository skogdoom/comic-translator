"""The in-application guide to reviewing a plan.

**A bundled document, not strings in the source.** The text is one HTML file
per language under ``resources/help/``, which is what keeps translating it to
translating a file rather than hunting literals through a widget. It is also
why the colours in it are written out rather than substituted in: a
translator receives a document with no machinery in it, and the drift that
buys is caught by a test instead, which reads the swatches back out and holds
them to ``canvas``.

**A window, not a modal.** Help you cannot keep open beside the thing it
describes is help you have to memorise a paragraph at a time. It is
non-modal, and asking for it twice raises the one that is already up rather
than stacking a second. Closing it hides it and does not destroy it, which
is what makes the window safe to hold on to: ``WA_DeleteOnClose`` here would
leave the handle pointing at nothing and the next open would raise.

Nothing here reaches the network. ``QTextBrowser`` will happily fetch a URL
if the document names one, so ``setOpenLinks(False)`` turns navigation off
outright and the only links the document has are anchors into itself, which
this scrolls to by hand.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import about

HELP_DIR = Path(__file__).parent / "resources" / "help"
LANGUAGE_DEFAULT = "en"
SUFFIX = ".html"

TITLE = f"{about.NAME} Help"
"""What the menu item says, and what the window is called. macOS puts
"⟨Application⟩ Help" at the top of the Help menu; this is that item."""

SIZE = (620, 640)
"""Tall and narrow: a column of prose, at a width that does not ask the eye
to track across a paragraph."""


def available_languages() -> tuple[str, ...]:
    """Every language the guide ships in, sorted."""
    if not HELP_DIR.is_dir():
        return ()
    return tuple(sorted(path.stem for path in HELP_DIR.glob(f"*{SUFFIX}")))


def document_path(language: str = LANGUAGE_DEFAULT) -> Path:
    """The guide in ``language``, falling back to English.

    A language with no guide of its own is not an error worth a missing Help
    menu: the English one is still help, and a partial translation is the
    normal state of one of these.
    """
    wanted = HELP_DIR / f"{language}{SUFFIX}"
    return wanted if wanted.is_file() else HELP_DIR / f"{LANGUAGE_DEFAULT}{SUFFIX}"


def document(language: str = LANGUAGE_DEFAULT) -> str:
    """The guide's HTML, or a line saying it is not there.

    A missing file costs the text and nothing else. The alternative — a Help
    menu whose item opens an exception — is the worse failure, and this is
    the same bargain a missing toolbar icon strikes.
    """
    path = document_path(language)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"<p>The guide could not be read: {exc}</p>"


class HelpDialog(QDialog):
    """The guide, in a window you can leave open."""

    def __init__(self, parent: QWidget | None = None, language: str = LANGUAGE_DEFAULT) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)

        self._browser = QTextBrowser()
        # Navigation off: the browser must never be the thing that fetches a
        # URL, and every link in the document is an anchor into itself.
        self._browser.setOpenLinks(False)
        self._browser.setOpenExternalLinks(False)
        self._browser.anchorClicked.connect(self._on_anchor)
        # No colours are set on the document, so the text and the page behind
        # it come from the palette and follow a light window and a dark one.
        self._browser.setHtml(document(language))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._browser)
        layout.addWidget(buttons)
        self.resize(*SIZE)

    def _on_anchor(self, url: QUrl) -> None:
        """Follow a link into this document, and nothing else.

        With ``setOpenLinks`` off nothing happens on its own, so a fragment
        is scrolled to here and anything else — a URL a translated document
        picked up somewhere — is ignored rather than opened.

        Only a bare fragment. A name the document does not have scrolls to
        the top rather than doing nothing, so a link that has gone stale
        reads as a link back to the contents; a test holds every link in
        the guide to an anchor that exists.
        """
        if url.isRelative() and not url.path():
            self._browser.scrollToAnchor(url.fragment())


__all__ = [
    "HELP_DIR",
    "LANGUAGE_DEFAULT",
    "SIZE",
    "TITLE",
    "HelpDialog",
    "available_languages",
    "document",
    "document_path",
]
