"""The application's own icon: that it ships, renders, and stays in step.

Two files and a script rather than one drawing. ``dog-book-master-1024.svg``
is the source, ``dog-book-icon-512.svg`` is the master with its fine detail
hidden and a heavier line, and ``recreate-icons.py`` derives the second from
the first. The check that matters here is the one a person cannot be relied
on to do: that the icon in the repository is still what the master says it
should be. That test needs no display, so it is not behind ``qapp`` — a
master edited and not regenerated fails the suite on any machine.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from comictrans.gui import icons

APPICON_DIR = icons.APPICON.parent
SCRIPT = APPICON_DIR / "recreate-icons.py"
MASTER = APPICON_DIR / "dog-book-master-1024.svg"


def test_the_master_the_icon_and_the_script_all_ship() -> None:
    for path in (MASTER, icons.APPICON, SCRIPT):
        assert path.is_file(), f"{path.name} is missing from {APPICON_DIR}"


def test_the_icon_is_still_what_the_master_says_it_should_be() -> None:
    """``--check`` in the suite, so a regeneration cannot be forgotten.

    The script's own comparison, run its own way, rather than a second
    reimplementation of the derive here that could agree with a bug.
    """
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"{icons.APPICON.name} is out of date — run {SCRIPT.name} "
        f"({result.stdout.strip()}{result.stderr.strip()})"
    )


def test_the_script_refuses_rather_than_writing_a_wrong_icon(tmp_path: Path) -> None:
    """Every edit it makes has to land, and a miss is loud.

    ``re.sub`` and ``str.replace`` both answer "not found" by handing back
    what they were given, so a master re-saved with a stroke width spelled
    differently would otherwise produce a hairline icon, reported as written.
    """
    work = tmp_path / "appicon"
    work.mkdir()
    for path in (MASTER, icons.APPICON, SCRIPT):
        (work / path.name).write_bytes(path.read_bytes())
    master = work / MASTER.name
    master.write_text(master.read_text().replace('stroke-width="4.5"', 'stroke-width="4.50"'))

    result = subprocess.run(
        [sys.executable, str(work / SCRIPT.name)], capture_output=True, text=True, check=False
    )

    assert result.returncode == 1
    assert "refusing to write" in result.stderr
    assert 'stroke-width="4.5"' in result.stderr


def test_the_application_icon_renders_at_the_sizes_an_icon_is_asked_for(qapp: object) -> None:
    """Full colour and no tinting, so this is Qt's SVG engine on its own."""
    icon = icons.app_icon()
    assert not icon.isNull()

    for size in (16, 32, 128, 512):
        image = icon.pixmap(size, size).toImage()
        assert not image.isNull(), f"nothing at {size}px"
        assert image.width() == size
        drawn = {
            image.pixelColor(x, y).rgb()
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 0
        }
        assert len(drawn) > 1, f"{size}px came out a single flat colour"


def test_a_missing_application_icon_costs_the_picture_and_nothing_else(
    qapp: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """It is fetched while the application is starting up. Nothing may raise."""
    from comictrans.gui.about_dialog import AboutDialog

    monkeypatch.setattr(icons, "APPICON", tmp_path / "not-here.svg")

    from PySide6.QtWidgets import QLabel

    assert icons.app_icon().isNull()

    dialog = AboutDialog()  # builds without it, rather than leaving a hole
    pictures = [
        label
        for label in dialog.findChildren(QLabel)
        if label.pixmap() is not None and not label.pixmap().isNull()
    ]
    assert not pictures, "an empty label would hold the words away from the edge"


def test_the_about_dialog_shows_the_icon(qapp: object) -> None:
    from PySide6.QtWidgets import QLabel

    from comictrans.gui.about_dialog import AboutDialog

    dialog = AboutDialog()
    pictures = [
        label.pixmap()
        for label in dialog.findChildren(QLabel)
        if label.pixmap() is not None and not label.pixmap().isNull()
    ]

    assert len(pictures) == 1, "one drawing, not none and not two"
    assert pictures[0].width() > 0


def test_review_gives_every_window_the_icon(qapp: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set on the application, so dialogs inherit it without being told.

    On macOS the Dock reads the bundle instead, which is 4.10's business;
    this is what a window carries before there is one.
    """
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from comictrans.gui import app as gui_app

    was = QApplication.windowIcon()
    monkeypatch.setattr(QApplication, "exec", lambda self: QApplication.processEvents() or 0)
    try:
        assert gui_app.run() == 0
        assert not QApplication.windowIcon().isNull()
    finally:
        QApplication.setWindowIcon(was or QIcon())
