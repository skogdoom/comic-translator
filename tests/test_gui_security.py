"""The audit's checks that need a window: plugins, the guide, and the reader.

Split from ``test_security.py`` for the reason ``CLAUDE.md`` gives — without
the ``gui`` extra, pytest skips every widget test — and a module-level
``importorskip`` skips the whole file it is in. The no-network check must
run on a machine that has never installed PySide6, so it lives over there
and these live here.

Both of these are about the same thing from two sides: **this window runs
code and shows documents that somebody else wrote**. A plugin is Python and
runs; the guide is HTML and is rendered. Each is fine on the terms it is
offered on, and each has exactly one switch keeping it there.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from comictrans import plugins

pytest.importorskip("PySide6")

from comictrans.gui import help_dialog
from comictrans.gui.main_window import MainWindow
from comictrans.gui.preferences import Preferences

# -- third-party code runs only when it is asked for --------------------------


RECORDS_ITS_OWN_IMPORT = """\
from pathlib import Path

Path(r"{marker}").write_text("imported", encoding="utf-8")

PLUGIN_NAME = "Shouts When Loaded"


def run(plan, settings):
    return plan
"""
"""A plugin whose top level leaves a trace, which is what loading one does.

``discover_plugins`` imports the folder, and an import runs the module —
there is no reading a Python file without running it, which is the whole
reason the menu is off until asked for. A marker file is the only way to
observe that from outside.
"""


def test_a_plugin_is_not_imported_until_experimental_features_are_on(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping a folder into the plugin directory must not be enough.

    The menu being hidden is not the property that matters — a hidden menu
    over a plugin that has already run its module-level code would be a
    window that executed somebody's Python because it was opened. What is
    checked here is that nothing ran at all.

    Turning the switch on is then the same test from the other side: the
    marker appears, which is both proof the plugin really would have run and
    proof the switch is what runs it.
    """
    directory = tmp_path / "plugins"
    directory.mkdir()
    monkeypatch.setenv(plugins.PLUGIN_PATH_ENV, str(directory))
    marker = tmp_path / "loaded"
    folder = directory / "shouts"
    folder.mkdir()
    (folder / "__init__.py").write_text(
        RECORDS_ITS_OWN_IMPORT.format(marker=marker), encoding="utf-8"
    )

    window = MainWindow()

    assert not marker.exists(), "opening the window ran a plugin"
    assert window._plugins_menu.menuAction().isVisible() is False

    window._on_preferences_changed(Preferences(experimental="yes"))

    assert marker.exists(), "the plugin never ran, so the check above proved nothing"


# -- the guide fetches nothing ------------------------------------------------

REMOTE_MARKERS = ("http://", "https://", "//", "src=", "@import", "<script", "<iframe", "<object")
"""Every way an HTML document asks for something that is not itself.

``QTextBrowser`` does not run scripts and this application turns its
navigation off outright, so most of these could not do anything today. They
are checked because the guide is a document meant to be *translated*: today
it is `en.html` alone, and the sibling that eventually joins it will be
written by somebody translating prose rather than auditing markup, where a
pasted `<img src="https://…">` arrives looking like an improvement.
"""


def test_the_guide_names_nothing_outside_itself() -> None:
    """Every language's guide, held to the same reading.

    ``//`` catches a protocol-relative URL, and is why the check is written
    against the raw text rather than against parsed attributes: it has no
    scheme to look for.
    """
    documents = sorted(help_dialog.HELP_DIR.glob(f"*{help_dialog.SUFFIX}"))
    assert documents, f"no guide found under {help_dialog.HELP_DIR}"

    guilty = [
        f"{path.name}: {marker}"
        for path in documents
        for marker in REMOTE_MARKERS
        if marker in path.read_text(encoding="utf-8")
    ]

    assert not guilty, "the guide refers to something remote:\n  " + "\n  ".join(guilty)


def test_the_guide_cannot_navigate_anywhere(qapp: object) -> None:
    """The two switches ``help_dialog`` turns off, read off the built widget.

    A docstring saying navigation is off is not the same as navigation being
    off, and these are one call each to undo.
    """
    dialog = help_dialog.HelpDialog()

    assert dialog._browser.openLinks() is False
    assert dialog._browser.openExternalLinks() is False


# -- what the reader window will hold -----------------------------------------


def test_the_reader_refuses_a_page_larger_than_qt_will_decode(qapp: object) -> None:
    """A page is held decoded, so how large one may decode to is a bound.

    Qt's default allocation limit, 256MB, is what sets it: nothing here
    raises the limit, and this is what would notice if something did. Qt
    judges a page before decoding it, at four bytes a pixel whatever it
    holds, so this one — 88KB on disk — counts as 275MB and is refused,
    measured, though the grey page it is would have decoded to 69MB. Small
    on disk and large decoded is the shape a bomb takes.
    """
    import io

    from PIL import Image

    from comictrans.errors import InputError
    from comictrans.gui.reader_window import decode_page

    buffer = io.BytesIO()
    Image.new("L", (9000, 8000), 255).save(buffer, "PNG")

    with pytest.raises(InputError, match=r"this 9000x8000 image"):
        decode_page(buffer.getvalue())


# -- what a chapter says about itself is text, never markup -------------------


def test_a_chapters_details_are_shown_as_the_text_they_are(qapp: object) -> None:
    """A ComicInfo.xml is somebody else's, and a label left to guess renders
    HTML — an image from anywhere on disk, a link. Every value is plain text.

    Measured first, so the test is known to be asking the right question: the
    same string in a label left on its default format is taken for rich text.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import Qt as GuiQt
    from PySide6.QtWidgets import QLabel

    from comictrans.gui.chapter_info import ChapterInfoDialog
    from comictrans.reading import ChapterInfo

    markup = '<img src="file:///etc/hosts"><a href="https://example.invalid">Tex</a>'
    assert GuiQt.mightBeRichText(markup), "the string a default label would render"

    dialog = ChapterInfoDialog(
        ChapterInfo(details=(("series", markup), ("summary", markup))), "chapter.cbz"
    )
    shown = [label for label in dialog.findChildren(QLabel) if label.text() == markup]

    assert shown, "the value is on screen, as typed"
    assert all(label.textFormat() == Qt.TextFormat.PlainText for label in shown)
    assert all(not label.openExternalLinks() for label in shown)
