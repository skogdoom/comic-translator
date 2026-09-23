"""Widget-level tests for the review GUI.

Everything the widgets *decide* is already covered without Qt, in
test_gui_document.py and test_gui_preview.py. What is worth testing here is
the plumbing itself: that opening a plan actually populates these specific
widgets, that a click on the canvas actually reaches the region it landed on,
that an edit actually reaches the window title and the page list. Skips
entirely when PySide6 is not installed or no display can be opened — see the
``qapp`` fixture.
"""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from comictrans import fonts
from comictrans.config import ExtractConfig
from comictrans.errors import InputError, OcrUnavailableError
from comictrans.model import (
    Box,
    Color,
    Erase,
    Geometry,
    PlanHeader,
    PlanImage,
    Region,
    TextCase,
)
from comictrans.ocr.base import OcrLine
from comictrans.planfile import load_plan, write_plan
from comictrans.planfile.schema import PLAN_VERSION
from comictrans.util import sha256_file

from .conftest import (
    ART_DARK,
    BALLOON_WHITE,
    INK_BLACK,
    FakeRecognizer,
    lines_for,
    make_page_array,
    make_plan,
    save_page,
)

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QAction, QContextMenuEvent, QDesktopServices, QKeyEvent, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QWidget,
)

from comictrans.gui import (
    about,
    erase_choices,
    extract_dialog,
    logfile,
    main_window,
    render_dialog,
    run_job,
)
from comictrans.gui.canvas import (
    COLOR_MANUAL,
    NUDGE_ACCELERATES_AFTER,
    NUDGE_STEP,
    NUDGE_STRIDE,
    CanvasMode,
    mode_hint,
    move_modifier_name,
)
from comictrans.gui.extract_dialog import ExtractDialog
from comictrans.gui.inspector import ERASE_CHOICES
from comictrans.gui.main_window import (
    CLEAR_RECENT_TEXT,
    OVERLAY_TEXT,
    PREVIEW_TEXT,
    PREVIEW_WORKING,
    MainWindow,
)
from comictrans.gui.note import Note
from comictrans.gui.preferences import ON, Preferences
from comictrans.gui.preferences_dialog import (
    FONT_DEFAULT,
    MINIMUM_HEIGHT,
    PreferencesDialog,
)
from comictrans.gui.render_dialog import (
    NO_RAR_HERE,
    RENDER,
    SAVE_AND_RENDER,
    STRATEGY_CHOICES,
    RenderDialog,
    suggested_output,
)
from comictrans.gui.run_job import (
    ExtractJob,
    ExtractRequest,
    RegionTextRequest,
    RenderJob,
    RenderRequest,
)

BALLOON_A = Box(60, 60, 260, 200)
TEXT_A = Box(90, 110, 230, 140)
BALLOON_B = Box(300, 60, 500, 200)
TEXT_B = Box(330, 110, 470, 140)


def _catch_alerts(
    monkeypatch: pytest.MonkeyPatch,
    answer: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
) -> list[QMessageBox]:
    """Answer every alert with ``answer``, and collect the boxes it opened.

    The seam is ``exec``, not the static ``QMessageBox.question``/``critical``
    helpers, because ``gui.alerts`` does not use those: they take a window
    title macOS throws away. Patching the thing that would have blocked leaves
    the real box built, so a test can read back both strings it carries —
    which is the half a title would have taken with it.
    """
    shown: list[QMessageBox] = []

    def answered(box: QMessageBox) -> int:
        shown.append(box)
        return int(answer)

    monkeypatch.setattr(QMessageBox, "exec", answered)
    return shown


def _settle_preview(window: MainWindow) -> None:
    """Let a preview finish, the way the run tests let a run finish.

    Waited for rather than polled: the thread is joined, and then the event
    loop is turned over, because the result is a queued signal and arrives
    only once the main thread processes events. Calling `_on_render_preview`
    and asserting immediately would be asserting before the render started.

    Loops because a superseded request starts a second job from the first
    one's `finished`, which is a thread this has not waited for yet.
    """
    for _ in range(5):
        job = window._preview_job
        if job is None:
            break
        job.wait()
        QApplication.processEvents()


def _header(**overrides: object) -> PlanHeader:
    base: dict[str, object] = {
        "version": PLAN_VERSION,
        "generator": "comictrans test",
        "created": "2026-09-07T12:00:00Z",
        "source_language": "it",
        "target_language": "en",
        "ocr_engine": "fake",
        "font": "Comic Sans MS",
        "case": TextCase.UPPER,
        "font_size_min_ratio": 0.012,
        "condense_min": 0.9,
    }
    base.update(overrides)
    return PlanHeader(**base)  # type: ignore[arg-type]


def _region(image: str, **overrides: object) -> Region:
    base: dict[str, object] = {
        "id": "page-001",
        "image": image,
        "order": 1,
        "geometry": Geometry.EXACT,
        "polygon": BALLOON_A.as_polygon(),
        "fill_color": Color(250, 250, 250),
        "text_color": Color(20, 20, 20),
        "confidence": 0.9,
        "source_text": "CIAO",
        "translation": "HELLO",
    }
    base.update(overrides)
    return Region(**base)  # type: ignore[arg-type]


@pytest.fixture
def two_page_plan(tmp_path: Path) -> Path:
    """Two pages: the first has two balloons (one held back), the second one."""
    source = tmp_path / "pages"
    source.mkdir()
    page1 = save_page(
        make_page_array(
            (600, 260),
            ART_DARK,
            [
                ("ellipse", BALLOON_A, BALLOON_WHITE, INK_BLACK, [TEXT_A]),
                ("ellipse", BALLOON_B, BALLOON_WHITE, INK_BLACK, [TEXT_B]),
            ],
        ),
        source / "page-001.png",
    )
    page2 = save_page(
        make_page_array(
            (600, 260), ART_DARK, [("ellipse", BALLOON_A, BALLOON_WHITE, INK_BLACK, [TEXT_A])]
        ),
        source / "page-002.png",
    )
    digests = {"page-001.png": sha256_file(page1), "page-002.png": sha256_file(page2)}
    plan = make_plan(
        _header(),
        (
            _region(
                "page-001.png",
                id="page-001-001",
                order=1,
                polygon=BALLOON_A.as_polygon(),
            ),
            _region(
                "page-001.png",
                id="page-001-002",
                order=2,
                polygon=BALLOON_B.as_polygon(),
                translation="",  # held back
            ),
            _region("page-002.png", id="page-002-001", order=1, polygon=BALLOON_A.as_polygon()),
        ),
        digests,
    )
    plan_path = source / "comic-plan.yaml"
    write_plan(plan, plan_path)
    return plan_path


def test_opening_a_plan_populates_the_page_list_and_lands_on_the_first_region(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)

    assert window.document is not None
    assert window._pages.count() == 2
    assert "page-001.png" in window._pages.item(0).text()
    assert "2 region" in window._pages.item(0).text()
    assert "1 flagged" in window._pages.item(0).text()

    assert window._current_image == "page-001.png"
    assert window._inspector._id_label.text().startswith("page-001-001")
    assert window._inspector._translation.toPlainText() == "HELLO"


@pytest.fixture
def plan_with_a_blank_page(tmp_path: Path, two_page_plan: Path) -> Path:
    """The two-page plan with a third page nothing was found on."""
    source = two_page_plan.parent
    blank = save_page(make_page_array((600, 260), ART_DARK, []), source / "page-003.png")
    plan = load_plan(two_page_plan, check_images=False)
    write_plan(
        replace(plan, images=(*plan.images, PlanImage("page-003.png", sha256_file(blank)))),
        two_page_plan,
        force=True,
    )
    return two_page_plan


def test_a_page_with_no_regions_is_listed_and_can_be_opened(
    qapp: object, plan_with_a_blank_page: Path
) -> None:
    window = MainWindow()
    window.open_plan(plan_with_a_blank_page)

    assert window._pages.count() == 3
    assert "page-003.png" in window._pages.item(2).text()
    assert "0 region" in window._pages.item(2).text()

    window._pages.select_image("page-003.png")

    assert window._current_image == "page-003.png"
    assert window._canvas._items == {}
    assert window._inspector._id_label.text() == "\u2014", "nothing to inspect"


def test_selecting_a_page_switches_the_canvas_and_inspector(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)

    window._pages.select_image("page-002.png")

    assert window._current_image == "page-002.png"
    assert window._inspector._id_label.text().startswith("page-002-001")
    assert set(window._canvas._items) == {"page-002-001"}


def test_clicking_a_region_on_the_canvas_selects_it(qapp: object, two_page_plan: Path) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    window.resize(900, 500)
    window.show()
    QTest.qWaitForWindowExposed(window)

    canvas = window._canvas
    item_b = canvas._items["page-001-002"]
    # The item's centre in scene coordinates, mapped through the view's
    # current fit-to-window transform to where a real click would land.
    scene_point = item_b.polygon().boundingRect().center()
    view_point = canvas.mapFromScene(scene_point)

    assert canvas.region_at(QPointF(view_point)) == "page-001-002"

    QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=view_point)

    assert window._inspector._id_label.text().startswith("page-001-002")


def test_editing_the_translation_marks_the_document_dirty(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)

    assert not window.document.dirty  # type: ignore[union-attr]
    assert not window.isWindowModified()

    window._inspector._translation.setPlainText("HELLO THERE")

    assert window.document.dirty  # type: ignore[union-attr]
    # Qt substitutes the [*] placeholder in the title from this, so the
    # title string itself carries the placeholder either way.
    assert window.isWindowModified()
    assert "[*]" in window.windowTitle()
    assert window.document.region("page-001-001").translation == "HELLO THERE"  # type: ignore[union-attr]


def test_review_tells_the_platform_what_the_application_is_called(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two names Qt itself reads, which are not the ones macOS reads.

    ``applicationDisplayName`` titles windows that do not title themselves;
    ``applicationName`` is the fallback key for a ``QSettings`` built
    without explicit ones. The macOS application menu takes neither — that
    comes from ``argv[0]``, and has its own test.
    """
    from PySide6.QtWidgets import QApplication

    from comictrans.gui import about
    from comictrans.gui import app as gui_app

    was_display = QApplication.applicationDisplayName()
    was_name = QApplication.applicationName()
    monkeypatch.setattr(QApplication, "exec", lambda self: QApplication.processEvents() or 0)
    try:
        assert gui_app.run() == 0
        assert QApplication.applicationName() == about.NAME
        assert QApplication.applicationDisplayName() == about.NAME
    finally:
        QApplication.setApplicationDisplayName(was_display)
        QApplication.setApplicationName(was_name)


def _menu_items(window: MainWindow, title: str) -> list[QAction]:
    """Every action in the named top-level menu, separators left out."""
    for action in window.menuBar().actions():
        if action.text() == title:
            menu = action.menu()
            assert menu is not None, f"{title} is not a menu"
            return [item for item in menu.actions() if not item.isSeparator()]
    raise AssertionError(f"no {title} menu")


def test_nothing_takes_the_shortcut_macos_reserves_for_minimising(qapp: object) -> None:
    """Cmd+M minimises a window on every Mac, so it is not ours to spend.

    Qt adds no Window menu of its own, so a window that does not define
    Minimise has no Cmd+M at all — which is how Merge Region came to hold it.
    """
    window = MainWindow()
    minimise = QKeySequence("Ctrl+M")

    taken = {
        action.text(): action.shortcuts()
        for action in window.findChildren(QAction)
        if minimise in action.shortcuts()
    }
    assert list(taken) == ["&Minimise"], f"Cmd+M is Minimise and nothing else, got {taken}"


def test_the_window_menu_opens_the_way_a_mac_window_menu_opens(qapp: object) -> None:
    """Minimise and Zoom first, then this window's own panels."""
    window = MainWindow()
    items = [action.text() for action in _menu_items(window, "&Window")]

    assert items[:2] == ["&Minimise", "&Zoom"]
    assert items[2:] == ["Pages", "Region", "Run", "&Reset Layout"]


def test_zoom_is_a_toggle_and_not_a_one_way_trip(qapp: object) -> None:
    window = MainWindow()
    window._on_zoom_window()
    assert window.isMaximized()
    window._on_zoom_window()
    assert not window.isMaximized()


def test_only_the_commands_that_stop_to_ask_carry_an_ellipsis(qapp: object) -> None:
    """A checkable item is a mode you are in, not a command awaiting input.

    Merge Region had one because a second click follows, which is true of
    Edit Region Shape and Add Region too — and neither of those wore it.
    """
    window = MainWindow()
    wearing = [
        action.text()
        for action in window.findChildren(QAction)
        if action.isCheckable() and action.text().endswith("…")
    ]
    assert wearing == [], f"a mode does not open a dialog: {wearing}"


def test_every_menu_role_macos_moves_is_spelled_out(qapp: object) -> None:
    """Not left to Qt's heuristic, which reads the English label.

    ``detectMenuRole`` looks for "about", "quit", "exit", "preference" and
    friends in the item's own text — a match that goes away the first time
    one of these is translated, taking the item out of the application menu
    with it.
    """
    window = MainWindow()
    roles = {
        action.text(): action.menuRole()
        for action in window.findChildren(QAction)
        if action.menuRole() not in (QAction.MenuRole.TextHeuristicRole, QAction.MenuRole.NoRole)
    }
    assert roles == {
        f"&About {about.NAME}": QAction.MenuRole.AboutRole,
        "&Preferences…": QAction.MenuRole.PreferencesRole,
        "&Quit": QAction.MenuRole.QuitRole,
    }


def test_the_help_menu_opens_with_the_guide_named_for_the_application(qapp: object) -> None:
    """What macOS puts at the top of every Help menu."""
    from comictrans.gui import help_dialog

    window = MainWindow()
    items = _menu_items(window, "&Help")

    assert items[0].text() == help_dialog.TITLE == f"{about.NAME} Help"
    assert items[0].shortcut() == QKeySequence(QKeySequence.StandardKey.HelpContents)


def test_asking_for_the_guide_twice_raises_the_one_already_open(qapp: object) -> None:
    """Help is read beside the thing it describes, so it is not modal.

    Two windows would be two to close, and the second would have lost
    whatever place the first was scrolled to.
    """
    window = MainWindow()
    window._on_help()
    first = window._help
    assert first is not None
    assert first.isVisible()
    assert not first.isModal(), "a modal guide could not be read beside the page"

    window._on_help()
    assert window._help is first


def test_closing_the_guide_and_asking_again_opens_the_same_window(qapp: object) -> None:
    """Closed, not destroyed — which is what makes the handle safe to keep.

    A ``WA_DeleteOnClose`` on this dialog would take the C++ object with the
    Close button and leave ``_help`` pointing at nothing, so the second
    ``show()`` would raise rather than reopen.
    """
    window = MainWindow()
    window._on_help()
    first = window._help
    assert first is not None

    first.reject()
    QApplication.processEvents()
    assert not first.isVisible()

    window._on_help()
    assert window._help is first
    assert first.isVisible(), "and it comes back rather than raising"


def test_every_contents_link_in_the_guide_goes_somewhere_further_down(qapp: object) -> None:
    """Each one lands somewhere new, which is what proves it resolved.

    Checking that the anchors exist at all is a string comparison and lives
    in ``test_gui_help.py``; this is the other half — that the wiring from a
    clicked link to a scrolled document is connected.

    The window is made short on purpose. At its own size the last two
    sections both land against the bottom of the scroll range, and two
    equal answers cannot tell a resolved anchor from an unresolved one.
    """
    from PySide6.QtCore import QUrl

    from comictrans.gui.help_dialog import HelpDialog, document

    dialog = HelpDialog()
    dialog.resize(420, 200)
    dialog.show()
    QApplication.processEvents()

    scrollbar = dialog._browser.verticalScrollBar()
    landed = []
    for name in re.findall(r'href="#([^"]+)"', document()):
        dialog._on_anchor(QUrl(f"#{name}"))
        QApplication.processEvents()
        landed.append(scrollbar.value())

    assert len(landed) >= 5, "the guide has sections to link to"
    assert landed == sorted(set(landed)), f"each link lands further down: {landed}"
    assert landed[0] > 0, "and the first one moves at all"


def test_the_guide_follows_links_into_itself_and_no_others(qapp: object) -> None:
    """A ``QTextBrowser`` will fetch a URL, and nothing here may reach one."""
    from PySide6.QtCore import QUrl

    from comictrans.gui.help_dialog import HelpDialog

    dialog = HelpDialog()
    dialog.show()
    QApplication.processEvents()
    dialog._browser.verticalScrollBar().setValue(40)

    dialog._on_anchor(QUrl("https://example.invalid/guide#flags"))
    QApplication.processEvents()

    assert dialog._browser.source().isEmpty(), "nothing was loaded"
    assert dialog._browser.verticalScrollBar().value() == 40, "and nothing moved"


def test_the_name_macos_reads_is_the_one_handed_to_qt(tmp_path: Path) -> None:
    """macOS titles "About X", "Hide X" and "Quit X" from argv[0], not from us.

    ``qt_mac_applicationName`` reads ``CFBundleName`` out of a bundle's
    ``Info.plist`` and otherwise falls back to the basename of ``argv[0]``;
    ``QCoreApplicationPrivate::appName`` does the same. Neither consults
    ``setApplicationName``, which is why setting that moved nothing and why
    this asserts what the constructor is *given* rather than what Qt reports
    afterwards. Launched from ``.venv/bin/comictrans`` those three items read
    "comictrans", because that is the basename and there is nothing else.

    In a subprocess because the suite already holds a ``QApplication``, so
    in-process the constructor never runs.
    """
    source = Path(__file__).resolve().parents[1] / "src"
    script = tmp_path / "named.py"
    script.write_text(
        textwrap.dedent(f"""
            import os, sys
            sys.path.insert(0, {str(source)!r})
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
            os.environ["COMICTRANS_LOG_DIR"] = {str(tmp_path)!r}

            from PySide6.QtWidgets import QApplication

            QApplication.exec = lambda self: 0

            from comictrans.gui import about
            from comictrans.gui import app as gui_app

            assert gui_app.run() == 0
            print(QApplication.arguments()[0])
            print(about.NAME)
        """),
        encoding="utf-8",
    )

    finished = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=180, check=False
    )
    assert finished.returncode == 0, finished.stderr

    handed, name = finished.stdout.strip().splitlines()[-2:]
    assert handed == name, f"argv[0] was {handed!r}, so the macOS menu would say that"


def test_naming_the_application_moves_neither_the_settings_nor_the_logs(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Why setting ``applicationName`` is safe here, measured rather than assumed.

    Qt falls back to it for a ``QSettings`` built without explicit keys, and
    for ``QStandardPaths``. Neither is how this project asks: the one real
    ``QSettings`` names its organisation and application outright, and the
    log directory comes from ``logfile.APPLICATION``, a literal. Worth a
    test rather than a comment, because getting it wrong would quietly
    orphan someone's saved layout and hide their crash logs somewhere new.
    """
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from comictrans.gui import about, logfile
    from comictrans.gui.app import _APPLICATION, _ORGANIZATION

    # The suite points the log directory at a temporary one; without that
    # override this computes the real path, which is the one under test.
    monkeypatch.delenv(logfile.LOG_DIR_ENV, raising=False)

    def where() -> tuple[str, str]:
        return QSettings(_ORGANIZATION, _APPLICATION).fileName(), str(logfile.log_directory())

    was = QApplication.applicationName()
    try:
        QApplication.setApplicationName("something else entirely")
        before = where()
        QApplication.setApplicationName(about.NAME)
        assert where() == before
    finally:
        QApplication.setApplicationName(was)


def test_the_window_uses_one_name_everywhere_it_names_itself(
    qapp: object, two_page_plan: Path
) -> None:
    """One name, from one place. ``comictrans review`` is the command.

    The assertions read the name from ``about.NAME`` rather than spelling
    it out, so renaming the application is that constant and nothing else —
    which is the point of the constant.

    That is what the CLI is invoked as and what opens this window; it is not
    what the window is called, and the title bar, the Help menu and the
    About box all have to agree on which is which.
    """
    from comictrans.gui import about
    from comictrans.gui.about_dialog import AboutDialog

    window = MainWindow()
    assert window.windowTitle() == about.NAME
    assert window._about_action.text() == f"&About {about.NAME}"

    window.open_plan(two_page_plan)
    assert window.windowTitle() == f"{two_page_plan.name}[*] — {about.NAME}"

    dialog = AboutDialog()
    assert dialog.windowTitle() == f"About {about.NAME}"

    named = (
        window.windowTitle(),
        window._about_action.text(),
        dialog.windowTitle(),
    )
    for shown in named:
        assert "review" not in shown.lower(), f"{shown!r} names the command, not the window"


def test_editing_an_empty_translation_clears_the_held_back_flag_in_the_page_list(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)

    window._on_region_selected("page-001-002")
    window._inspector._translation.setPlainText("NOW TRANSLATED")

    assert "1 flagged" not in window._pages.item(0).text()


def test_save_writes_the_edit_and_clears_the_dirty_marker(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._inspector._translation.setPlainText("HELLO THERE")
    assert window.document.dirty  # type: ignore[union-attr]

    window._on_save()

    assert not window.document.dirty  # type: ignore[union-attr]
    assert not window.isWindowModified()
    on_disk = load_plan(two_page_plan, check_images=False)
    assert on_disk.regions[0].translation == "HELLO THERE"


def test_closing_with_unsaved_changes_asks_and_can_be_cancelled(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._inspector._translation.setPlainText("EDITED")

    _catch_alerts(monkeypatch, QMessageBox.StandardButton.Cancel)
    assert window._confirm_discard_if_dirty() is False

    _catch_alerts(monkeypatch, QMessageBox.StandardButton.Discard)
    assert window._confirm_discard_if_dirty() is True


def test_the_unsaved_changes_alert_offers_to_save_and_saving_is_enough(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discard and Cancel alone make Cancel the only way to keep the work."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._inspector._translation.setPlainText("EDITED")

    shown = _catch_alerts(monkeypatch, QMessageBox.StandardButton.Save)
    assert window._confirm_discard_if_dirty() is True

    offered = shown[0].standardButtons()
    assert offered & QMessageBox.StandardButton.Save, "keeping the work is a button"
    assert offered & QMessageBox.StandardButton.Discard
    assert offered & QMessageBox.StandardButton.Cancel
    assert shown[0].defaultButton() == shown[0].button(QMessageBox.StandardButton.Save)

    assert not window.document.dirty, "and pressing it saved"  # type: ignore[union-attr]
    on_disk = load_plan(two_page_plan, check_images=False)
    assert on_disk.regions[0].translation == "EDITED"


def test_a_save_that_fails_is_not_permission_to_close_over_it(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole reason the save path reports rather than raises."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._inspector._translation.setPlainText("EDITED")

    def refuse(*args: object, **kwargs: object) -> None:
        raise InputError("the directory has gone")

    monkeypatch.setattr(type(window.document), "save", refuse)
    shown = _catch_alerts(monkeypatch, QMessageBox.StandardButton.Save)

    assert window._confirm_discard_if_dirty() is False, "a failed save is not a saved plan"
    assert len(shown) == 2, "the question, then the failure"
    assert "the directory has gone" in shown[1].informativeText()


def test_an_alert_says_what_happened_without_using_a_window_title(
    qapp: object, two_page_plan: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """macOS drops a message box's title, so nothing may live only there.

    ``QMessageBox`` overrides ``setWindowTitle`` for exactly that reason. An
    alert whose framing sentence went in as the title would reach a Mac as
    the bare detail — here, a parse error with nothing saying it came from
    opening a plan.
    """
    window = MainWindow()
    window.open_plan(two_page_plan)
    broken = tmp_path / "broken.yaml"
    broken.write_text("not a plan file at all: [", encoding="utf-8")

    shown = _catch_alerts(monkeypatch)
    window.open_plan(broken)

    assert shown, "the failure is reported"
    assert "plan" in shown[0].text().lower(), "the message says what was being done"
    assert shown[0].informativeText(), "and the detail is under it, not in a title"
    assert shown[0].windowTitle() == "", "nothing is put where macOS will not show it"


def test_close_event_is_actually_wired_to_the_dirty_guard(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtGui import QCloseEvent

    window = MainWindow()
    window.open_plan(two_page_plan)
    window._inspector._translation.setPlainText("EDITED")

    _catch_alerts(monkeypatch, QMessageBox.StandardButton.Cancel)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted(), "cancelling the prompt must keep the window open"

    _catch_alerts(monkeypatch, QMessageBox.StandardButton.Discard)
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()


def test_opening_a_broken_plan_shows_an_error_and_keeps_the_old_document(
    qapp: object, two_page_plan: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    original_document = window.document

    broken = tmp_path / "broken.yaml"
    broken.write_text("not a plan file at all: [", encoding="utf-8")

    shown = _catch_alerts(monkeypatch)
    window.open_plan(broken)

    assert shown, "a plan that fails to parse must be reported, not silently ignored"
    assert window.document is original_document


def test_the_window_keeps_working_while_a_preview_renders(
    qapp: object, two_page_plan: Path, font_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole of this milestone, and the one thing the old version could not do.

    The render is made slow on purpose so there is a middle to look at. What
    is checked from that middle is not that the window *could* respond but
    that it does: an edit made while the thread is running reaches the
    document, and the status bar is saying a render is going.
    """
    import time

    from comictrans.gui import run_job as rj

    window = MainWindow()
    window.open_plan(two_page_plan)
    region_id = window.document.ordered_ids()[0]  # type: ignore[union-attr]

    real = rj.render_preview

    def slow(*args: object, **kwargs: object) -> object:
        time.sleep(0.2)
        return real(*args, **kwargs)

    monkeypatch.setattr(rj, "render_preview", slow)
    window._on_render_preview()

    assert window._preview_job is not None
    assert window._preview_job.isRunning(), "the render is on its own thread"
    assert window.statusBar().currentMessage() == PREVIEW_WORKING

    # The point of all of it: this thread is free while that one works.
    window.document.set_translation(region_id, "TYPED WHILE IT RENDERED")  # type: ignore[union-attr]
    QApplication.processEvents()
    assert window.document.region(region_id).translation == "TYPED WHILE IT RENDERED"  # type: ignore[union-attr]

    _settle_preview(window)
    assert window._showing_preview
    assert window.statusBar().currentMessage() != PREVIEW_WORKING, (
        "the working message is replaced by what the preview found"
    )


def _slow_render(monkeypatch: pytest.MonkeyPatch, seconds: float = 0.2) -> None:
    """Give a preview a middle, so a test can do something during it."""
    import time

    from comictrans.gui import run_job as rj

    real = rj.render_preview

    def slow(*args: object, **kwargs: object) -> object:
        time.sleep(seconds)
        return real(*args, **kwargs)

    monkeypatch.setattr(rj, "render_preview", slow)


def test_a_preview_that_lands_after_you_have_moved_on_is_dropped(
    qapp: object, two_page_plan: Path, font_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect a thread makes possible, and the reason for _preview_wanted.

    Rendering used to block, so the page could not change underneath it.
    Now it can — and a result arriving for the page you have left would be
    painted over the page you are on.
    """
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._on_image_selected("page-001.png")

    _slow_render(monkeypatch)
    window._on_render_preview()
    assert window._preview_job is not None and window._preview_job.isRunning()

    job = window._preview_job
    window._on_image_selected("page-002.png")

    assert job is not None and job.cancelling, "the render was told to stop, not just ignored"

    _settle_preview(window)

    assert window._current_image == "page-002.png"
    assert not window._showing_preview, "the render of the page we left was dropped"
    assert window._canvas._items, "and the outlines of the page we are on are up"


def test_asking_again_while_one_renders_renders_again_rather_than_twice_over(
    qapp: object, two_page_plan: Path, font_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job cannot be stopped, so a superseded one is left to finish into nothing.

    What must not happen is two jobs at once, or the first one's result being
    shown and then the second one's painted over it.
    """
    window = MainWindow()
    window.open_plan(two_page_plan)
    region_id = window.document.ordered_ids()[0]  # type: ignore[union-attr]

    _slow_render(monkeypatch)
    window._on_render_preview()
    first = window._preview_job
    assert first is not None

    window.document.set_translation(region_id, "CHANGED MID-RENDER")  # type: ignore[union-attr]
    window._on_render_preview()

    assert window._preview_job is first, "still one thread, not two"
    assert window._preview_wanted is not None, "and the newer request is remembered"
    assert window._preview_wanted.plan.regions[0].translation == "CHANGED MID-RENDER"

    _settle_preview(window)

    assert window._showing_preview
    assert window._preview_job is None
    assert window._preview_wanted is None, "nothing left outstanding"


def test_asking_for_the_same_preview_twice_renders_it_once(
    qapp: object, two_page_plan: Path, font_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falls out of matching on the request rather than on a counter.

    Two presses with nothing changed in between are the same question, so
    the answer already being computed is the answer.
    """
    from comictrans.gui import run_job as rj

    window = MainWindow()
    window.open_plan(two_page_plan)

    started = []
    real = rj.render_preview

    def counted(*args: object, **kwargs: object) -> object:
        started.append(args)
        import time

        time.sleep(0.2)
        return real(*args, **kwargs)

    monkeypatch.setattr(rj, "render_preview", counted)

    window._on_render_preview()
    window._on_render_preview()
    _settle_preview(window)

    assert window._showing_preview
    assert len(started) == 1, f"rendered {len(started)} times for one unchanged page"


def test_switching_page_stops_the_render_rather_than_waiting_it_out(
    qapp: object, two_page_plan: Path, font_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asked to stop, and it does — between regions, inside the page.

    Counted rather than timed: with both regions slow to erase and the page
    switched while the first is in flight, the second one never starting is
    the evidence. The switch is made from this thread, not from inside the
    patched erase — a test that reached into the window from the worker
    would be doing the thing the whole design exists to avoid, and Qt says
    so out loud when it happens.
    """
    import time

    from comictrans import render as rm

    window = MainWindow()
    window.open_plan(two_page_plan)
    # Both of this page's regions have to be renderable, or only one is ever
    # erased and the count below proves nothing: the fixture holds the second
    # back with an empty translation.
    window.document.set_translation("page-001-002", "ALSO RENDERED")  # type: ignore[union-attr]
    window._on_image_selected("page-001.png")

    erased: list[str] = []
    real = rm.erase

    def slow_and_counted(rgb: object, region: object, *args: object, **kwargs: object) -> object:
        erased.append(region.id)  # type: ignore[attr-defined]
        time.sleep(0.3)
        return real(rgb, region, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(rm, "erase", slow_and_counted)
    window._on_render_preview()

    window._on_image_selected("page-002.png")
    job = window._preview_job
    assert job is not None and job.cancelling, "it was told to stop"

    _settle_preview(window)

    assert len(erased) < 2, f"it went on erasing after being told to stop: {erased}"
    assert not window._showing_preview


def test_the_busy_bar_counts_regions_once_it_knows_how_many(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    """Indeterminate until there is a count, determinate after.

    Which is the shape of what is known: a preview has to open the page and
    plan its regions before it can say how many there are to erase.
    """
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._on_image_selected("page-001.png")

    seen: list[tuple[bool, int, int]] = []
    window._busy.valueChanged.connect(
        lambda _v: seen.append((window._busy.waiting, window._busy.value(), window._busy.maximum()))
    )

    assert window._busy.waiting, "nothing known yet"
    window._on_render_preview()
    _settle_preview(window)

    assert seen, "the bar was given real numbers"
    assert seen[-1][1] == seen[-1][2], f"and finished full: {seen[-1]}"
    assert not any(waiting for waiting, _v, _m in seen), "each report is a real fraction"


def test_the_busy_bar_goes_back_to_waiting_for_the_next_preview(
    qapp: object, two_page_plan: Path, font_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Or the next one would open on the last one's finished bar."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    region_id = window.document.ordered_ids()[0]  # type: ignore[union-attr]
    window._on_render_preview()
    _settle_preview(window)
    assert not window._busy.waiting, "the first one left it full"

    # Something has to change, or the second preview is a cache hit and never
    # starts a thread for the bar to report from.
    window.document.set_translation(region_id, "SOMETHING ELSE ENTIRELY")  # type: ignore[union-attr]
    _slow_render(monkeypatch)
    window._on_back_to_overlay()
    window._on_render_preview()

    assert window._busy.waiting, "the second starts from nothing known"
    _settle_preview(window)


def _count_renders(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every page actually rendered, so a cache hit is visible as an absence."""
    from comictrans.gui import run_job as rj

    rendered: list[str] = []
    real = rj.render_preview

    def counted(plan: object, plan_path: object, image: str, **kwargs: object) -> object:
        rendered.append(image)
        return real(plan, plan_path, image, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(rj, "render_preview", counted)
    return rendered


def test_looking_at_the_same_page_twice_renders_it_once(
    qapp: object, two_page_plan: Path, font_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gesture this exists for. Measured before it did: three toggles of
    an eleven-megapixel page with nothing edited cost three renders and 34s.
    """
    window = MainWindow()
    window.open_plan(two_page_plan)
    rendered = _count_renders(monkeypatch)

    for _ in range(3):
        window._on_render_preview()
        _settle_preview(window)
        assert window._showing_preview
        window._on_back_to_overlay()

    assert rendered == ["page-001.png"], f"rendered {len(rendered)} times for one page"


def test_a_cached_preview_says_what_it_found_like_a_fresh_one(
    qapp: object, two_page_plan: Path, font_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hit that went quiet about regions that do not fit would be worse than
    the render it saved. Both routes go through the same display path.
    """
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._on_render_preview()
    _settle_preview(window)
    fresh = window.statusBar().currentMessage()
    window._on_back_to_overlay()

    rendered = _count_renders(monkeypatch)
    window._on_render_preview()

    assert rendered == [], "it came from the cache"
    assert window._showing_preview, "and it is on screen without waiting for a thread"
    assert window.statusBar().currentMessage() == fresh
    assert window._busy.isHidden(), "nothing is running, so nothing says it is"


def test_an_edit_makes_the_next_preview_render_again(
    qapp: object, two_page_plan: Path, font_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure that matters is the quiet one: a stale page shown as current."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    region_id = window.document.ordered_ids()[0]  # type: ignore[union-attr]
    window._on_render_preview()
    _settle_preview(window)
    window._on_back_to_overlay()

    window.document.set_translation(region_id, "A DIFFERENT LINE ENTIRELY")  # type: ignore[union-attr]
    rendered = _count_renders(monkeypatch)
    window._on_render_preview()
    _settle_preview(window)

    assert rendered == ["page-001.png"], "the edit was not skipped over"


def test_rescanning_fonts_throws_the_cache_away(
    qapp: object, two_page_plan: Path, font_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one input the key cannot see.

    ``resolve_styles`` goes to the filesystem for a face, so installing a font
    changes what a plan renders as without changing the plan: a region that
    would not resolve before now draws.
    """
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._on_render_preview()
    _settle_preview(window)
    window._on_back_to_overlay()
    assert window._preview_cache.holding

    window._on_rescan_fonts()

    assert not window._preview_cache.holding
    rendered = _count_renders(monkeypatch)
    window._on_render_preview()
    _settle_preview(window)
    assert rendered == ["page-001.png"], "it rendered again rather than trusting the old one"


def test_opening_another_plan_does_not_keep_a_page_of_the_last_one(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    """33MB for an eleven-megapixel page, held for a plan nobody has open."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._on_render_preview()
    _settle_preview(window)
    assert window._preview_cache.holding

    window._on_back_to_overlay()
    window.open_plan(two_page_plan)

    assert not window._preview_cache.holding


def test_the_busy_bar_reports_no_number_because_a_preview_has_none(qapp: object) -> None:
    """An empty range is Qt's indeterminate mode, and the rule behind the rest.

    It is what makes Qt animate the bar from its own timer, and it is why
    there is no value to update from anywhere: a preview is one page, so a
    percentage would have to be invented.
    """
    from comictrans.gui.busy_bar import BusyBar

    bar = BusyBar()
    assert bar.minimum() == bar.maximum() == 0
    assert not bar.isTextVisible(), "there is no percentage to draw"
    assert bar.isHidden(), "and nothing to say while nothing is running"


def test_the_busy_bar_actually_moves(qapp: object) -> None:
    """The animation itself, not just the mode that should produce one.

    Grabbing the bar rather than the window: a grab of the whole window came
    back identical across half a second, which reads as a still bar and is
    not — the bar repaints on its own timer and the window's cached frame did
    not follow. Polled rather than slept: it passes on the first frame that
    differs, which is usually the first one asked for.
    """
    import hashlib
    import time

    from comictrans.gui.busy_bar import BusyBar

    bar = BusyBar()
    bar.set_busy(True)
    bar.show()
    QApplication.processEvents()

    first = hashlib.md5(bytes(bar.grab().toImage().constBits())).hexdigest()
    deadline = time.time() + 2.0
    while time.time() < deadline:
        QApplication.processEvents()
        if hashlib.md5(bytes(bar.grab().toImage().constBits())).hexdigest() != first:
            bar.hide()
            return
    bar.hide()
    raise AssertionError("the indeterminate bar never repainted differently")


def test_the_busy_bar_shows_while_a_preview_renders(
    qapp: object, two_page_plan: Path, font_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Possible only because the render moved off this thread.

    While it blocked, the event loop was not turning and an animation would
    have been a still picture of one.
    """
    window = MainWindow()
    window.open_plan(two_page_plan)
    assert window._busy.isHidden()

    _slow_render(monkeypatch)
    window._on_render_preview()

    # isHidden, not isVisible: a child of a window nobody showed is never
    # "visible", so isVisible would read False throughout and pass for the
    # wrong reason. What is being asserted is the widget's own state.
    assert not window._busy.isHidden(), "something is happening and it says so"

    _settle_preview(window)
    assert window._busy.isHidden(), "and it stops saying so when it stops"


def test_the_busy_bar_stays_up_across_a_superseded_preview(
    qapp: object, two_page_plan: Path, font_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One handover between two threads, and one wait from where anyone sits."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    region_id = window.document.ordered_ids()[0]  # type: ignore[union-attr]

    _slow_render(monkeypatch)
    window._on_render_preview()
    window.document.set_translation(region_id, "SUPERSEDED")  # type: ignore[union-attr]
    window._on_render_preview()

    first = window._preview_job
    assert first is not None
    first.wait()
    QApplication.processEvents()

    assert not window._busy.isHidden(), "the second render is still going"

    _settle_preview(window)
    assert window._busy.isHidden()


def test_the_busy_bar_goes_down_before_a_failure_alert_blocks(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The alert is modal and sits there; a bar behind it must not still spin.

    Taking it down on the thread's ``finished`` would be too late — that is
    delivered only once the box has been dismissed.
    """
    from comictrans.gui import run_job as rj

    window = MainWindow()
    window.open_plan(two_page_plan)

    def refuse(*args: object, **kwargs: object) -> object:
        raise InputError("no font for that")

    monkeypatch.setattr(rj, "render_preview", refuse)

    seen: list[bool] = []

    def catch(box: QMessageBox) -> int:
        seen.append(not window._busy.isHidden())
        return int(QMessageBox.StandardButton.Ok)

    monkeypatch.setattr(QMessageBox, "exec", catch)

    window._on_render_preview()
    _settle_preview(window)

    assert seen == [False], "the bar was down by the time the alert opened"


def test_a_preview_that_fails_stops_saying_it_is_working(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise the box saying it failed sits over a status bar saying it has not."""
    from comictrans.gui import run_job as rj

    window = MainWindow()
    window.open_plan(two_page_plan)

    def refuse(*args: object, **kwargs: object) -> object:
        raise InputError("no font for that")

    monkeypatch.setattr(rj, "render_preview", refuse)
    said = _catch_alerts(monkeypatch)

    window._on_render_preview()
    _settle_preview(window)

    assert said, "the failure is reported"
    assert window.statusBar().currentMessage() == ""
    assert not window._showing_preview, "a failed preview leaves the overlay up"


def test_render_preview_shows_a_different_image_and_can_return_to_the_overlay(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)

    before_items = set(window._canvas._items)
    assert before_items  # the overlay is showing region outlines

    window._on_render_preview()
    _settle_preview(window)

    assert not window._canvas._items, "preview mode has no clickable region outlines"
    assert window._showing_preview

    window._on_back_to_overlay()

    assert set(window._canvas._items) == before_items
    assert not window._showing_preview


def test_render_preview_reports_a_font_error_instead_of_crashing(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A font name that can never resolve on any machine, so this fails the
    # same way regardless of what is actually installed on whatever runs the
    # suite — unlike relying on a real font being merely absent here, which
    # would stop being true on a Mac that ships it.
    monkeypatch.delenv("COMICTRANS_FONT_PATH", raising=False)
    source = tmp_path / "pages"
    source.mkdir()
    image = save_page(
        make_page_array(
            (600, 260), ART_DARK, [("ellipse", BALLOON_A, BALLOON_WHITE, INK_BLACK, [TEXT_A])]
        ),
        source / "page-001.png",
    )
    plan_path = source / "comic-plan.yaml"
    write_plan(
        make_plan(
            _header(font="Definitely Not A Real Font XYZ"),
            (_region("page-001.png"),),
            {"page-001.png": sha256_file(image)},
        ),
        plan_path,
    )

    window = MainWindow()
    window.open_plan(plan_path)

    shown = _catch_alerts(monkeypatch)
    window._on_render_preview()
    _settle_preview(window)

    assert shown
    assert not window._showing_preview


def test_the_font_size_box_has_room_for_the_word_auto(qapp: object) -> None:
    """The special value text has to fit where the numbers fit.

    Nothing shows here, where the form stretches its fields to the panel
    width. On macOS the style asks for ``FieldsStayAtSizeHint`` instead, and
    a size hint measured for three digits is what "auto" then has to sit in.
    """
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QSpinBox

    from comictrans.gui.inspector import RegionInspector

    inspector = RegionInspector()  # held, or its children go with it
    box = inspector._font_size
    assert box.specialValueText() == "auto"

    plain = QSpinBox()
    plain.setRange(box.minimum(), box.maximum())
    metrics = QFontMetrics(box.font())
    wider_by = metrics.horizontalAdvance("auto") - metrics.horizontalAdvance(str(box.maximum()))

    assert box.minimumWidth() >= plain.sizeHint().width() + wider_by


def test_notes_is_a_text_box_and_writes_through_as_it_is_typed(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)

    # Multi-line: a note spanning lines survives as typed, which a QLineEdit
    # could not hold in the first place.
    window._inspector._notes.setPlainText("check the tail\nsecond line")

    assert window.document.region("page-001-001").notes == "check the tail\nsecond line"  # type: ignore[union-attr]
    assert window.document.dirty  # type: ignore[union-attr]


def test_selecting_another_region_does_not_carry_notes_across(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._inspector._notes.setPlainText("only about the first one")

    window._on_region_selected("page-001-002")

    assert window._inspector._notes.toPlainText() == ""
    assert window.document.region("page-001-002").notes == ""  # type: ignore[union-attr]
    assert window.document.region("page-001-001").notes == "only about the first one"  # type: ignore[union-attr]


def test_open_plan_dialog_opens_what_it_is_given_and_ignores_a_cancel(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QFileDialog

    window = MainWindow()

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
    window.open_plan_dialog()
    assert window.document is None, "cancelling the dialog must not open anything"

    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(two_page_plan), ""))
    )
    window.open_plan_dialog()
    assert window.document is not None
    assert window.document.path == two_page_plan


def test_review_without_a_plan_opens_empty_and_asks_nothing(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty window is the front door to two things, not a dead end.

    It used to put the Open dialog up straight away, on the grounds that
    there was nothing else to do in an empty window. Extract moved into the
    window, so there is.
    """
    from PySide6.QtWidgets import QApplication

    from comictrans.gui import app as gui_app

    asked: list[bool] = []
    monkeypatch.setattr(MainWindow, "open_plan_dialog", lambda self: asked.append(True))
    # Stand in for the event loop, and run anything queued on it.
    monkeypatch.setattr(QApplication, "exec", lambda self: QApplication.processEvents() or 0)

    assert gui_app.run() == 0
    assert not asked, "nothing modal in front of a window you might want to extract from"


def test_review_with_a_plan_does_not_ask(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QApplication

    from comictrans.gui import app as gui_app

    asked: list[bool] = []
    monkeypatch.setattr(MainWindow, "open_plan_dialog", lambda self: asked.append(True))
    monkeypatch.setattr(QApplication, "exec", lambda self: QApplication.processEvents() or 0)

    assert gui_app.run(two_page_plan) == 0
    assert not asked


def test_an_empty_window_says_where_both_doors_are(qapp: object) -> None:
    window = MainWindow()
    message = window.statusBar().currentMessage()

    assert "Open Plan" in message
    assert "Extract Pages" in message


def test_a_font_override_is_recorded_once_it_names_a_font(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    """Not as it is typed: half a font name is not a font.

    Every other field here writes through keystroke by keystroke, and this
    one did too, which is how a plan came to be saved naming "Sans". A
    fragment now waits for Tab, which takes the best match.
    """
    window = MainWindow()
    window.open_plan(two_page_plan)
    box = window._inspector._font
    region = lambda: window.document.region("page-001-001")  # noqa: E731  # type: ignore[union-attr]

    popup = _type_into(window, box, "Comic")
    try:
        assert region().font is None, "a fragment reaches nothing"
        assert not window.document.dirty  # type: ignore[union-attr]

        _press_key(Qt.Key.Key_Tab)
        assert region().font == "Comic Sans MS"

        # Emptying it clears the override rather than pinning an empty name.
        popup = _type_into(window, box, "")
        _press_key(Qt.Key.Key_Tab)
        assert region().font is None
    finally:
        popup.hide()
        window.hide()


def test_selecting_another_region_does_not_carry_the_font_override_across(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    """A name typed in full is recorded as it is typed; a fragment is dropped.

    Moving to another region need not take focus from the field — Next
    Region is a shortcut — so the field is never told it was left. What was
    typed in full is already in the plan by then, and what was not is
    nobody's font.
    """
    window = MainWindow()
    window.open_plan(two_page_plan)
    popup = _type_into(window, window._inspector._font, "comic sans ms")
    try:
        window._on_region_selected("page-001-002")
        popup = _type_into(window, window._inspector._font, "Comic")
        window._on_region_selected("page-001-001")
    finally:
        popup.hide()
        window.hide()

    assert window._inspector._font.value() == "Comic Sans MS", "spelled as the family is"
    assert window.document.region("page-001-001").font == "Comic Sans MS"  # type: ignore[union-attr]
    assert window.document.region("page-001-002").font is None  # type: ignore[union-attr]


def test_the_window_menu_can_close_and_reopen_a_dock(qapp: object) -> None:
    from PySide6.QtWidgets import QMenu

    window = MainWindow()
    # Shown for real: a dock's toggle action only picks up its checked state
    # from a show event, so on a window that never opened it starts out of
    # step with the dock it controls.
    window.show()
    QTest.qWaitForWindowExposed(window)

    menu = next(m for m in window.menuBar().findChildren(QMenu) if m.title() == "&Window")

    toggle = window._pages_dock.toggleViewAction()
    assert toggle in menu.actions(), "a closed dock needs a way back"
    assert "&Reset Layout" in [a.text() for a in menu.actions()]

    toggle.trigger()
    assert window._pages_dock.isHidden()
    toggle.trigger()
    assert not window._pages_dock.isHidden()


def test_show_page_lets_go_of_every_item_before_the_scene_deletes_them(
    qapp: object,
) -> None:
    """A wrapper that outlives ``QGraphicsScene.clear()`` is a dangling pointer.

    ``clear()`` destroys the C++ objects without telling shiboken, so a
    Python wrapper still holding one points into freed memory. Nothing has
    to read it: merely *dropping* it — rebinding the attribute — is what
    frees it a second time, because shiboken releases what it wrapped as the
    last reference goes. On a Mac that landed as a SIGSEGV inside
    ``SbkDeallocWrapperCommon``, reached from the line that installs the
    next page, which is the line that happened to drop the old one.

    So the rule is purely about order, and this is the test for it: when the
    scene is told to clear, nothing here may still be holding an item.
    """
    from PySide6.QtGui import QColor, QPixmap

    from comictrans.gui.canvas import PageCanvas, RegionAppearance

    canvas = PageCanvas()
    appearance = RegionAppearance(
        region_id="r1",
        polygon=((10, 10), (90, 10), (90, 90), (10, 90)),
        color=QColor(40, 170, 70),
        flagged=False,
    )
    canvas.show_page(QPixmap(200, 200), [appearance])
    canvas.set_mode(CanvasMode.RESHAPE)
    canvas.set_selected("r1")
    assert canvas._pixmap_item is not None
    assert canvas._handles, "the test needs handles in the scene to be worth running"

    held: dict[str, object] = {}
    scene_clear = canvas._scene.clear

    def spy() -> None:
        held["pixmap_item"] = canvas._pixmap_item
        held["draft_item"] = canvas._draft_item
        held["items"] = dict(canvas._items)
        held["handles"] = list(canvas._handles)
        held["draft_handles"] = list(canvas._draft_handles)
        scene_clear()

    canvas._scene.clear = spy  # type: ignore[method-assign]
    canvas.show_page(QPixmap(200, 200), [appearance])

    assert held["pixmap_item"] is None, "the page item outlived the clear"
    assert held["draft_item"] is None, "the draft outline outlived the clear"
    assert held["items"] == {}, "region outlines outlived the clear"
    assert held["handles"] == [], "vertex handles outlived the clear"
    assert held["draft_handles"] == [], "draft handles outlived the clear"


def test_a_page_swap_in_reshape_mode_rebuilds_the_scene(qapp: object) -> None:
    """The crash path, exercised: swap pages with handles on screen.

    Cheap next to the test above and worth having anyway, because it is the
    sequence a reader actually performs — reshaping a region and then moving
    to another page — rather than an assertion about ordering.
    """
    from PySide6.QtGui import QColor, QPixmap

    from comictrans.gui.canvas import PageCanvas, RegionAppearance

    canvas = PageCanvas()
    appearance = RegionAppearance(
        region_id="r1",
        polygon=((10, 10), (90, 10), (90, 90), (10, 90)),
        color=QColor(40, 170, 70),
        flagged=False,
    )
    for _ in range(25):
        canvas.show_page(QPixmap(200, 200), [appearance])
        canvas.set_mode(CanvasMode.RESHAPE)
        canvas.set_selected("r1")

    assert canvas._pixmap_item is not None
    assert set(canvas._items) == {"r1"}
    assert len(canvas._handles) == 4


def test_reset_layout_reopens_whatever_was_closed(qapp: object) -> None:
    window = MainWindow()
    window._pages_dock.setVisible(False)
    window._inspector_dock.setVisible(False)

    window._on_reset_layout()

    assert not window._pages_dock.isHidden()
    assert not window._inspector_dock.isHidden()


def test_the_toolbar_reuses_the_menu_actions(qapp: object) -> None:
    """One action per command, not one for the menu and another for the bar."""
    window = MainWindow()
    on_toolbar = window._toolbar.actions()

    for action in (
        window._open_action,
        window._save_action,
        window._previous_region_action,
        window._next_region_action,
        window._next_flagged_action,
        window._preview_action,
    ):
        assert action in on_toolbar


def test_the_toolbar_is_fixed_where_it_is(qapp: object) -> None:
    """A Mac toolbar does not come away from the top of the window.

    Qt draws a grip for a movable one and indents the first button behind it,
    which is nine pixels of the left edge the row is meant to start at —
    measured, and visible as a column of dots in a screenshot.
    """
    window = MainWindow()

    assert not window._toolbar.isMovable()
    assert not window._toolbar.isFloatable()

    first = window._toolbar.widgetForAction(window._open_action)
    assert first is not None
    assert first.x() <= 6, f"the first button starts at {first.x()}, behind something"


def test_the_layout_is_remembered_for_the_next_window(qapp: object, tmp_path: Path) -> None:
    from PySide6.QtCore import QSettings

    settings = QSettings(str(tmp_path / "layout.ini"), QSettings.Format.IniFormat)

    first = MainWindow(settings=settings)
    first._pages_dock.setVisible(False)
    first._save_layout()

    assert MainWindow(settings=settings)._pages_dock.isHidden()
    # A window opened without settings is unaffected by any of that.
    assert not MainWindow()._pages_dock.isHidden()


def _recent_labels(window: MainWindow) -> list[str]:
    return [action.text() for action in window._recent_menu.actions() if not action.isSeparator()]


def _settings_in(tmp_path: Path) -> object:
    from PySide6.QtCore import QSettings

    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


def test_open_recent_is_empty_and_greyed_out_until_something_is_opened(
    qapp: object, tmp_path: Path
) -> None:
    window = MainWindow(settings=_settings_in(tmp_path))

    assert _recent_labels(window) == []
    assert not window._recent_menu.isEnabled(), "an empty submenu that opens is a dead end"


def test_opening_a_plan_puts_it_on_the_recent_menu(
    qapp: object, tmp_path: Path, two_page_plan: Path
) -> None:
    """The label carries the directory because the filenames do not differ.

    Plans written by ``extract`` are all called ``comic-plan.yaml``, so a
    menu of bare filenames would be a column of identical rows. The parent
    is the chapter, and the whole path is on the tooltip for the case where
    two chapters are named alike too.
    """
    window = MainWindow(settings=_settings_in(tmp_path))
    window.open_plan(two_page_plan)

    labels = _recent_labels(window)
    assert labels[0] == f"{two_page_plan.parent.name}/{two_page_plan.name}"
    assert CLEAR_RECENT_TEXT in labels
    assert window._recent_menu.isEnabled()
    assert window._recent_menu.actions()[0].toolTip() == str(two_page_plan)


def test_clicking_a_recent_entry_opens_that_plan_and_not_another(
    qapp: object, tmp_path: Path, two_page_plan: Path
) -> None:
    """Triggered, not called — the path now travels on the action.

    It used to travel in a ``functools.partial`` bound to the window, which
    is the object a crash trace from a built application ended in: freeing
    the partial during the window's own destruction dropped the last
    reference to the window's wrapper while its C++ object was part-way
    through being destroyed. ``QAction.data`` holds a plain string instead
    and nothing holds a reference to anything — but the wiring from a click
    to the right plan is only exercised by actually clicking.
    """
    other = tmp_path / "other-chapter"
    other.mkdir()
    page = save_page(
        make_page_array(
            (600, 260), ART_DARK, [("ellipse", BALLOON_A, BALLOON_WHITE, INK_BLACK, [TEXT_A])]
        ),
        other / "page-001.png",
    )
    second = other / "comic-plan.yaml"
    write_plan(
        make_plan(
            _header(),
            (_region("page-001.png", id="page-001-001", order=1),),
            {"page-001.png": sha256_file(page)},
        ),
        second,
    )

    window = MainWindow(settings=_settings_in(tmp_path))
    window.open_plan(two_page_plan)
    window.open_plan(second)
    window.document = None

    entries = [action for action in window._recent_menu.actions() if action.data()]
    assert [action.data() for action in entries] == [str(second), str(two_page_plan)], (
        "most recent first, and each carrying its own path"
    )

    entries[1].trigger()

    assert window.document is not None
    assert window.document.path == two_page_plan, "the entry clicked, not the first one"


def test_a_window_without_settings_remembers_nothing(qapp: object, two_page_plan: Path) -> None:
    """The rule the layout already follows: no settings, no trace anywhere.

    Every window the suite builds is one of these, which is what keeps one
    test's history out of the next one and out of the config of whoever is
    running it.
    """
    window = MainWindow()
    window.open_plan(two_page_plan)

    assert _recent_labels(window) == []


def test_a_plan_that_has_gone_is_dropped_when_it_is_chosen(
    qapp: object, tmp_path: Path, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checked on the click, not while the menu is being built.

    A stat per entry every time File opens would put the cost on every
    glance at the menu, and an entry on a network volume that is not
    answering would hang the menu rather than the click.
    """
    window = MainWindow(settings=_settings_in(tmp_path))
    window.open_plan(two_page_plan)
    assert _recent_labels(window)[0].endswith(two_page_plan.name)

    said = _catch_alerts(monkeypatch)
    two_page_plan.unlink()
    window._on_open_recent(two_page_plan)

    assert _recent_labels(window) == [], "the entry goes when the file is not there"
    assert said, "and it says so"
    assert two_page_plan.name in said[0].text(), "naming which one"
    assert str(two_page_plan.parent) in said[0].informativeText(), "and where it was"


def test_clear_menu_empties_the_list(qapp: object, tmp_path: Path, two_page_plan: Path) -> None:
    """It is a privacy control, so it has to leave nothing listed."""
    window = MainWindow(settings=_settings_in(tmp_path))
    window.open_plan(two_page_plan)
    assert _recent_labels(window)

    window._on_clear_recent()

    assert _recent_labels(window) == []
    assert not window._recent_menu.isEnabled()


def test_the_recent_list_outlives_the_window_that_made_it(
    qapp: object, tmp_path: Path, two_page_plan: Path
) -> None:
    settings = _settings_in(tmp_path)
    MainWindow(settings=settings).open_plan(two_page_plan)

    assert _recent_labels(MainWindow(settings=settings))[0].endswith(two_page_plan.name)


def test_the_page_list_lets_rows_be_dragged_within_itself_only(
    qapp: object, two_page_plan: Path
) -> None:
    """A page cannot be dragged in from elsewhere: the pages are what extract
    found, and this is about their order rather than their membership."""
    from PySide6.QtWidgets import QListWidget

    window = MainWindow()
    window.open_plan(two_page_plan)

    assert window._pages.dragDropMode() == QListWidget.DragDropMode.InternalMove
    assert window._pages.current_order() == window.document.images()  # type: ignore[union-attr]


def test_a_drop_reports_the_order_the_rows_are_in_afterwards(
    qapp: object, two_page_plan: Path
) -> None:
    """The override emits after ``super()``, which is the whole of its job.

    It cannot check more than that here: moving a row needs a real drag
    session, and a synthesised ``QDropEvent`` does not give ``QListWidget``
    one — so the rows stay put and the reported order is the one they are
    already in. What this does prove is that a drop is reported at all, and
    reported from after the base class has had it rather than before.
    """
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QDropEvent

    window = MainWindow()
    window.open_plan(two_page_plan)
    pages = window._pages

    reported: list[list[str]] = []
    pages.order_changed.connect(reported.append)

    pages.setCurrentRow(1)
    event = QDropEvent(
        QPointF(5, 1),
        Qt.DropAction.MoveAction,
        pages.mimeData([pages.item(1)]),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    pages.dropEvent(event)

    assert reported == [list(pages.current_order())]


def test_dragging_a_page_reorders_the_plan(qapp: object, two_page_plan: Path) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    reversed_order = list(reversed(window.document.images()))  # type: ignore[union-attr]

    window._on_pages_reordered(reversed_order)

    assert list(window.document.images()) == reversed_order  # type: ignore[union-attr]
    assert window.document.dirty  # type: ignore[union-attr]
    assert "[*]" in window.windowTitle(), "the title says the plan has moved on"


def test_undoing_a_reorder_puts_the_rows_back_too(qapp: object, two_page_plan: Path) -> None:
    """The rows are what changed, and one row's label is all a refresh repaints.

    Without rebuilding the list here, undo would leave the window showing an
    order the plan no longer has — the two disagreeing with nothing on
    screen to say which is right.
    """
    window = MainWindow()
    window.open_plan(two_page_plan)
    before = window._pages.current_order()
    showing = window._current_image

    window._on_pages_reordered(list(reversed(before)))
    assert window._pages.current_order() != before or len(before) < 2

    window._on_undo()

    assert window._pages.current_order() == before
    assert window._pages.current_order() == window.document.images()  # type: ignore[union-attr]
    assert window._current_image == showing, "undoing an order does not change the page shown"


def test_the_run_dock_stays_closed_however_the_last_session_left_it(
    qapp: object, tmp_path: Path
) -> None:
    """It opens when there is a report, and a new session has none.

    ``restoreState`` brings a dock back exactly as it was left, so before
    this every session that rendered a chapter reopened holding the bottom
    of the window for a panel reading "Nothing has been run yet."
    """
    from PySide6.QtCore import QSettings

    settings = QSettings(str(tmp_path / "layout.ini"), QSettings.Format.IniFormat)

    first = MainWindow(settings=settings)
    first._run_dock.setVisible(True)
    first._save_layout()

    assert MainWindow(settings=settings)._run_dock.isHidden()


def test_the_run_dock_reopens_where_it_was_dragged_to(qapp: object, tmp_path: Path) -> None:
    """Closed is not the same as forgotten: only the visibility is overruled."""
    from PySide6.QtCore import QSettings, Qt

    settings = QSettings(str(tmp_path / "layout.ini"), QSettings.Format.IniFormat)

    first = MainWindow(settings=settings)
    first._run_dock.setVisible(True)
    first.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, first._run_dock)
    first._save_layout()

    second = MainWindow(settings=settings)
    assert second._run_dock.isHidden()

    second._run_dock.setVisible(True)
    assert second.dockWidgetArea(second._run_dock) == Qt.DockWidgetArea.LeftDockWidgetArea


def test_next_region_carries_on_to_the_following_page(qapp: object, two_page_plan: Path) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    assert window._current_region == "page-001-001"

    window._on_next_region()
    assert window._current_region == "page-001-002"

    window._on_next_region()
    assert window._current_region == "page-002-001"
    assert window._current_image == "page-002.png", "the page follows the region"
    assert set(window._canvas._items) == {"page-002-001"}
    assert window._inspector._id_label.text().startswith("page-002-001")

    window._on_previous_region()
    assert window._current_region == "page-001-002"
    assert window._current_image == "page-001.png"


def test_the_navigation_actions_switch_off_at_the_ends_of_the_plan(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)

    assert not window._previous_region_action.isEnabled(), "nothing before the first region"
    assert window._next_region_action.isEnabled()

    window._on_next_region()
    window._on_next_region()

    assert window._current_region == "page-002-001"
    assert not window._next_region_action.isEnabled(), "nothing after the last region"
    assert window._previous_region_action.isEnabled()


def test_next_flagged_skips_the_regions_with_nothing_to_check(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)

    # page-001-002 is held back; the other two have nothing wrong with them.
    window._on_next_flagged_region()
    assert window._current_region == "page-001-002"
    assert not window._next_flagged_action.isEnabled()


def test_translating_the_last_flagged_region_switches_next_flagged_off(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    assert window._next_flagged_action.isEnabled()

    window._on_next_flagged_region()
    window._inspector._translation.setPlainText("NOW TRANSLATED")
    window._on_region_selected("page-001-001")

    assert not window._next_flagged_action.isEnabled(), "nothing left to check"


def test_the_help_menu_offers_about(qapp: object) -> None:
    from PySide6.QtWidgets import QMenu

    window = MainWindow()
    menu = next(m for m in window.menuBar().findChildren(QMenu) if m.title() == "&Help")
    assert window._about_action in menu.actions()


def test_about_reports_the_version_author_licence_and_libraries(qapp: object) -> None:
    from comictrans.gui import about
    from comictrans.gui.about_dialog import AboutDialog

    dialog = AboutDialog()
    shown = " ".join(label.text() for label in dialog.findChildren(QLabel))

    assert about.package_version() in shown
    assert about.author() in shown
    assert "MIT" in shown
    assert "PySide6" in shown, "the binding and the Qt it wraps are versioned apart"

    listed = dialog._libraries.toPlainText()
    for library in about.libraries():
        assert library.name in listed
        assert library.version in listed


def test_the_about_action_actually_opens_the_dialog(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from comictrans.gui.about_dialog import AboutDialog

    opened: list[bool] = []
    # exec() would block on a real modal loop; everything up to it is real.
    monkeypatch.setattr(AboutDialog, "exec", lambda self: opened.append(True) or 0)

    window = MainWindow()
    window._about_action.trigger()

    assert opened


def test_undo_puts_the_text_back_in_the_inspector_too(qapp: object, two_page_plan: Path) -> None:
    """The change did not come from the fields, so they have to be repopulated."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._inspector._translation.setPlainText("HELLO THERE")

    window._on_undo()

    assert window.document.region("page-001-001").translation == "HELLO"  # type: ignore[union-attr]
    assert window._inspector._translation.toPlainText() == "HELLO"
    assert not window.isWindowModified()

    window._on_redo()

    assert window._inspector._translation.toPlainText() == "HELLO THERE"
    assert window.isWindowModified()


def test_undoing_does_not_write_itself_straight_back_out(qapp: object, two_page_plan: Path) -> None:
    """Repopulating the fields must not read as a fresh edit.

    Without blocked signals the restored text would be written back through
    textChanged, leaving the document dirty and the undo stack one deeper
    than the user's actions.
    """
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._inspector._translation.setPlainText("HELLO THERE")

    window._on_undo()

    assert not window.document.dirty  # type: ignore[union-attr]
    assert not window.document.can_undo  # type: ignore[union-attr]
    assert window.document.can_redo  # type: ignore[union-attr]


def test_the_undo_actions_switch_off_when_there_is_nothing_to_undo(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    assert not window._undo_action.isEnabled(), "no document at all"

    window.open_plan(two_page_plan)
    assert not window._undo_action.isEnabled()
    assert not window._redo_action.isEnabled()

    window._inspector._translation.setPlainText("HELLO THERE")
    assert window._undo_action.isEnabled()
    assert not window._redo_action.isEnabled()

    window._on_undo()
    assert not window._undo_action.isEnabled()
    assert window._redo_action.isEnabled()


def test_undo_refreshes_the_page_list_and_the_canvas(qapp: object, two_page_plan: Path) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)

    window._on_region_selected("page-001-002")  # held back, so flagged
    window._inspector._translation.setPlainText("NOW TRANSLATED")
    assert "1 flagged" not in window._pages.item(0).text()

    window._on_undo()

    assert "1 flagged" in window._pages.item(0).text(), "the flag came back"


def test_the_edit_menu_and_toolbar_carry_undo(qapp: object) -> None:
    from PySide6.QtWidgets import QMenu

    window = MainWindow()
    menu = next(m for m in window.menuBar().findChildren(QMenu) if m.title() == "&Edit")

    assert window._undo_action in menu.actions()
    assert window._redo_action in menu.actions()
    assert window._undo_action in window._toolbar.actions()
    assert window._redo_action in window._toolbar.actions()


def test_the_prose_fields_keep_no_undo_history_of_their_own(qapp: object) -> None:
    """One stack, so Ctrl+Z means the same thing wherever the focus is."""
    from comictrans.gui.inspector import RegionInspector

    inspector = RegionInspector()
    assert not inspector._translation.isUndoRedoEnabled()
    assert not inspector._notes.isUndoRedoEnabled()


def test_selecting_another_region_ends_the_current_undo_run(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._inspector._translation.setPlainText("FIRST")

    window._on_region_selected("page-001-002")
    window._on_region_selected("page-001-001")
    window._inspector._translation.setPlainText("FIRST AND SECOND")

    window._on_undo()

    assert window.document.region("page-001-001").translation == "FIRST"  # type: ignore[union-attr]


def _shown_window(plan: Path) -> MainWindow:
    """A window laid out for real, so the view has a viewport to scale into."""
    window = MainWindow()
    window.resize(900, 500)
    window.show()
    QTest.qWaitForWindowExposed(window)
    window.open_plan(plan)
    return window


def test_zooming_in_and_out_moves_the_scale_and_leaves_fit_mode(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    canvas = window._canvas
    assert canvas.fitting, "a page opens fitted to the window"

    fitted = canvas.zoom
    canvas.zoom_in()

    assert canvas.zoom > fitted
    assert not canvas.fitting, "a chosen zoom stops following the window"

    canvas.zoom_out()
    assert canvas.zoom == pytest.approx(fitted)


def test_actual_size_is_one_screen_pixel_per_page_pixel(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    window._canvas.zoom_actual()
    assert window._canvas.zoom == pytest.approx(1.0)


def test_fit_to_window_goes_back_to_following_the_window(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    canvas = window._canvas
    canvas.zoom_actual()
    assert not canvas.fitting

    canvas.fit()

    assert canvas.fitting
    assert canvas.zoom != pytest.approx(1.0), "the page is wider than the viewport"


def test_zoom_is_clamped_at_both_ends(qapp: object, two_page_plan: Path) -> None:
    from comictrans.gui.canvas import ZOOM_MAX, ZOOM_MIN

    window = _shown_window(two_page_plan)  # held, or its canvas goes with it
    canvas = window._canvas

    canvas.set_zoom(1000.0)
    assert canvas.zoom == pytest.approx(ZOOM_MAX)

    canvas.set_zoom(0.0001)
    assert canvas.zoom == pytest.approx(ZOOM_MIN)


def test_resizing_the_window_keeps_a_chosen_zoom(qapp: object, two_page_plan: Path) -> None:
    """The bug this milestone exists to avoid: a resize used to refit always."""
    window = _shown_window(two_page_plan)
    canvas = window._canvas
    canvas.set_zoom(2.0)

    window.resize(700, 420)
    QTest.qWait(10)

    assert canvas.zoom == pytest.approx(2.0)
    assert not canvas.fitting


def test_resizing_the_window_still_refits_while_fitting(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    canvas = window._canvas
    before = canvas.zoom

    window.resize(500, 300)
    QTest.qWait(10)

    assert canvas.fitting
    assert canvas.zoom != pytest.approx(before), "a fitted page follows the window"


def test_a_page_not_opened_before_starts_fitted(qapp: object, two_page_plan: Path) -> None:
    """Zoom is per page: a page has no level until it has been read at one."""
    window = _shown_window(two_page_plan)
    window._canvas.set_zoom(2.0)

    window._pages.select_image("page-002.png")

    assert window._current_image == "page-002.png"
    assert window._canvas.fitting
    assert window._canvas.zoom != pytest.approx(2.0)


def test_each_page_keeps_its_own_zoom(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    canvas = window._canvas

    canvas.set_zoom(2.0)  # page 1 read close up
    window._pages.select_image("page-002.png")
    canvas.set_zoom(0.5)  # page 2 read at a distance

    window._pages.select_image("page-001.png")
    assert canvas.zoom == pytest.approx(2.0)
    assert not canvas.fitting

    window._pages.select_image("page-002.png")
    assert canvas.zoom == pytest.approx(0.5)


def test_a_page_left_fitted_comes_back_fitted_to_the_window_as_it_is_now(
    qapp: object, two_page_plan: Path
) -> None:
    """Restoring "it was fitted" refits, rather than pinning the old factor."""
    window = _shown_window(two_page_plan)
    canvas = window._canvas
    assert canvas.fitting
    fitted_wide = canvas.zoom

    window._pages.select_image("page-002.png")
    canvas.set_zoom(2.0)
    window.resize(600, 380)
    QTest.qWait(10)

    window._pages.select_image("page-001.png")

    assert canvas.fitting
    assert canvas.zoom < fitted_wide, "refitted to the smaller window it came back to"


def test_opening_another_plan_forgets_the_zoom_levels(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    window._canvas.set_zoom(2.0)
    window._pages.select_image("page-002.png")
    assert window._views, "page one's level was captured on the way out"

    window.open_plan(two_page_plan)

    assert window._views == {}, "no zoom levels carried over from the old session"
    assert window._canvas.fitting


def test_a_chosen_zoom_survives_the_preview_round_trip(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    """Comparing overlay against rendered output is the point of holding it."""
    window = _shown_window(two_page_plan)
    window._canvas.set_zoom(2.0)

    window._on_render_preview()
    _settle_preview(window)
    assert window._showing_preview
    assert window._canvas.zoom == pytest.approx(2.0)

    window._on_back_to_overlay()
    assert window._canvas.zoom == pytest.approx(2.0)


def test_ctrl_and_the_wheel_zooms_while_the_wheel_alone_scrolls(
    qapp: object, two_page_plan: Path
) -> None:
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent

    window = _shown_window(two_page_plan)
    canvas = window._canvas
    canvas.set_zoom(1.0)

    def wheel(modifier: Qt.KeyboardModifier) -> QWheelEvent:
        centre = QPointF(canvas.viewport().rect().center())
        return QWheelEvent(
            centre,
            canvas.viewport().mapToGlobal(centre),
            QPoint(0, 0),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            modifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )

    canvas.wheelEvent(wheel(Qt.KeyboardModifier.ControlModifier))
    assert canvas.zoom > 1.0, "Ctrl and the wheel zooms in"

    zoomed = canvas.zoom
    canvas.wheelEvent(wheel(Qt.KeyboardModifier.NoModifier))
    assert canvas.zoom == pytest.approx(zoomed), "the wheel alone scrolls, it does not zoom"


def test_the_status_bar_reports_the_zoom(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    assert "(fit)" in window._zoom_label.text()

    window._canvas.zoom_actual()

    assert window._zoom_label.text() == "100%"


def test_the_zoom_actions_need_a_page(qapp: object, two_page_plan: Path) -> None:
    window = MainWindow()
    assert not window._zoom_in_action.isEnabled()

    window.open_plan(two_page_plan)

    for action in (
        window._zoom_in_action,
        window._zoom_out_action,
        window._zoom_fit_action,
        window._zoom_actual_action,
    ):
        assert action.isEnabled()


def test_the_header_dialog_writes_through_as_it_is_edited(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    from comictrans.gui.header_dialog import HeaderDialog
    from comictrans.model import TextCase

    _install_font(font_dir, "Chalkboard SE")
    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = HeaderDialog(window.document, window)  # type: ignore[arg-type]

    popup = _type_into(dialog, dialog._font, "Chalkboard SE")
    popup.hide()
    dialog._case.setCurrentIndex(dialog._case.findData(TextCase.PRESERVE))
    dialog._condense.setValue(0.8)
    dialog._target_language.setCurrentText("sv")
    dialog.hide()

    header = window.document.plan.header  # type: ignore[union-attr]
    assert header.font == "Chalkboard SE"
    assert header.case is TextCase.PRESERVE
    assert header.condense_min == pytest.approx(0.8)
    assert header.target_language == "sv"
    assert window.isWindowModified() or window.document.dirty  # type: ignore[union-attr]


def test_clearing_the_header_font_writes_nothing(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    """An empty font is not a legal header value, so it must not be recorded.

    The header has no default to fall back to, so empty matches nothing, and
    leaving the field puts back what it held.
    """
    from comictrans.gui.header_dialog import HeaderDialog

    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = HeaderDialog(window.document, window)  # type: ignore[arg-type]

    _type_into(dialog, dialog._font, "")
    _press_key(Qt.Key.Key_Tab)
    dialog.hide()

    assert window.document.plan.header.font == "Comic Sans MS"  # type: ignore[union-attr]
    assert dialog._font.currentText() == "Comic Sans MS"
    assert not window.document.dirty  # type: ignore[union-attr]


def test_the_header_dialog_offers_only_the_range_the_reader_accepts(
    qapp: object, two_page_plan: Path
) -> None:
    from comictrans.gui.header_dialog import HeaderDialog
    from comictrans.planfile.schema import CONDENSE_MIN_RANGE, FONT_SIZE_MIN_RATIO_RANGE

    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = HeaderDialog(window.document, window)  # type: ignore[arg-type]

    assert (dialog._min_ratio.minimum(), dialog._min_ratio.maximum()) == pytest.approx(
        FONT_SIZE_MIN_RATIO_RANGE
    )
    assert (dialog._condense.minimum(), dialog._condense.maximum()) == pytest.approx(
        CONDENSE_MIN_RANGE
    )


def test_the_header_dialog_says_how_far_the_font_reaches(qapp: object, two_page_plan: Path) -> None:
    from comictrans.gui.header_dialog import HeaderDialog

    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = HeaderDialog(window.document, window)  # type: ignore[arg-type]
    assert dialog.font_reach() == "used by all 3 regions"

    window.document.set_font("page-001-001", "Marker Felt")  # type: ignore[union-attr]
    dialog = HeaderDialog(window.document, window)  # type: ignore[arg-type]

    assert dialog._font_reach.text() == "used by 2 of 3 regions; the rest override it"


def test_the_recorded_fields_are_shown_but_not_editable(qapp: object, two_page_plan: Path) -> None:
    """You are not the authority on which OCR engine ran."""
    from comictrans.gui.header_dialog import HeaderDialog

    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = HeaderDialog(window.document, window)  # type: ignore[arg-type]

    shown = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "fake" in shown, "the OCR engine is reported"
    assert "comictrans test" in shown, "so is what wrote the plan"

    # Reported in labels and nowhere else: no field holds either of them, so
    # there is nothing to type over and no way to claim a different one.
    typed = [field.text() for field in dialog.findChildren(QLineEdit)]
    assert "fake" not in typed
    assert "comictrans test" not in typed


def test_undoing_a_header_edit_puts_the_dialog_fields_back(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    from comictrans.gui.header_dialog import HeaderDialog

    _install_font(font_dir, "Chalkboard SE")
    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = HeaderDialog(window.document, window)  # type: ignore[arg-type]
    popup = _type_into(dialog, dialog._font, "Chalkboard SE")
    popup.hide()
    assert window.document.plan.header.font == "Chalkboard SE"  # type: ignore[union-attr]

    window._on_undo()
    dialog.repopulate()
    dialog.hide()

    assert dialog._font.currentText() == "Comic Sans MS"
    assert not window.document.dirty, "repopulating must not write itself back out"  # type: ignore[union-attr]


def test_the_header_action_needs_a_document_and_opens_the_dialog(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from comictrans.gui.header_dialog import HeaderDialog

    window = MainWindow()
    assert not window._header_action.isEnabled()

    window.open_plan(two_page_plan)
    assert window._header_action.isEnabled()

    opened: list[bool] = []
    monkeypatch.setattr(HeaderDialog, "exec", lambda self: opened.append(True) or 0)
    window._header_action.trigger()

    assert opened


def _flags(**on: bool) -> object:
    from comictrans.gui.document import RegionFlags

    fields = dict.fromkeys(
        ("approximate", "low_confidence", "held_back", "unedited", "overlapping", "skipped"),
        False,
    )
    fields.update(on)
    return RegionFlags(**fields)  # type: ignore[arg-type]


def test_flag_labels_gives_one_line_per_reason() -> None:
    from comictrans.gui.inspector import flag_labels

    assert flag_labels(None) == ("—",)
    assert flag_labels(_flags()) == ("nothing flagged",)  # type: ignore[arg-type]
    assert flag_labels(_flags(approximate=True, unedited=True)) == (  # type: ignore[arg-type]
        "approximate geometry",
        "same as source",
    )
    assert (
        len(
            flag_labels(
                _flags(
                    **dict.fromkeys(  # type: ignore[arg-type]
                        (
                            "approximate",
                            "low_confidence",
                            "held_back",
                            "unedited",
                            "overlapping",
                            "skipped",
                        ),
                        True,
                    )
                )
            )
        )
        == 6
    )


def test_the_flag_list_is_always_tall_enough_for_its_rows(qapp: object) -> None:
    """The bug: several flags on one wrapped line were clipped by the form.

    Checked under the macOS field-growth policy, which is where it showed:
    fields stay at their size hint there, and a wrapped label's hint is
    computed for a width it does not end up with.
    """
    from PySide6.QtWidgets import QFormLayout

    from comictrans.gui.inspector import RegionInspector

    inspector = RegionInspector()
    form = inspector.layout().itemAt(0).layout()
    assert isinstance(form, QFormLayout)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
    inspector.resize(320, 700)
    inspector.show()
    QTest.qWaitForWindowExposed(inspector)

    every = dict.fromkeys(
        ("approximate", "low_confidence", "held_back", "unedited", "overlapping", "skipped"),
        True,
    )
    for flags, expected_rows in (
        (_flags(), 1),
        (_flags(approximate=True, unedited=True), 2),
        (_flags(**every), 6),
    ):
        inspector._flags.set_flags(flags)  # type: ignore[arg-type]
        QTest.qWait(1)
        widget = inspector._flags
        needed = sum(widget.sizeHintForRow(i) for i in range(widget.count()))
        needed += 2 * widget.frameWidth()

        assert widget.count() == expected_rows
        assert widget.height() >= needed, f"{expected_rows} flags do not fit"


def test_the_flag_list_stays_tall_enough_when_the_panel_narrows(qapp: object) -> None:
    from comictrans.gui.inspector import RegionInspector

    inspector = RegionInspector()
    inspector.resize(320, 700)
    inspector.show()
    QTest.qWaitForWindowExposed(inspector)
    inspector._flags.set_flags(_flags(held_back=True, overlapping=True))  # type: ignore[arg-type]

    inspector.resize(150, 700)
    QTest.qWait(10)

    widget = inspector._flags
    needed = sum(widget.sizeHintForRow(i) for i in range(widget.count()))
    needed += 2 * widget.frameWidth()
    assert widget.height() >= needed, "a narrower panel wraps rows and needs more height"


def test_the_flag_list_is_a_readout_not_a_control(qapp: object) -> None:
    from PySide6.QtWidgets import QAbstractItemView

    from comictrans.gui.inspector import RegionInspector

    inspector = RegionInspector()
    widget = inspector._flags
    assert widget.selectionMode() == QAbstractItemView.SelectionMode.NoSelection
    assert widget.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_the_flag_list_follows_the_selected_region_and_its_edits(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)

    rows = lambda: [  # noqa: E731
        window._inspector._flags.item(i).text() for i in range(window._inspector._flags.count())
    ]

    assert rows() == ["nothing flagged"]

    window._on_region_selected("page-001-002")  # held back
    assert rows() == ["held back (no translation)"]

    window._inspector._translation.setPlainText("NOW TRANSLATED")
    assert rows() == ["nothing flagged"], "the flag clears as the reason for it goes"


def test_the_font_box_offers_only_fonts_that_will_render(qapp: object, font_dir: Path) -> None:
    """Populated from fonts.py, not QFontDatabase, which knows a wider set."""
    from comictrans import fonts
    from comictrans.gui.font_box import PLAN_DEFAULT, FontBox

    fonts.forget_available_families()
    box = FontBox(allow_default=True)
    box.showPopup()

    offered = [box.itemText(i) for i in range(box.count())]
    assert offered == [PLAN_DEFAULT, "Comic Sans MS"]
    assert "Marker Felt" not in offered, "no bold face, so apply would refuse it"


def test_the_font_box_reports_no_override_whatever_it_shows(qapp: object, font_dir: Path) -> None:
    from comictrans.gui.font_box import PLAN_DEFAULT, FontBox

    box = FontBox(allow_default=True)
    box.set_value(None)
    assert box.currentText() == PLAN_DEFAULT
    assert box.value() is None

    box.set_value("Comic Sans MS")
    assert box.value() == "Comic Sans MS"


def test_the_header_font_box_has_no_no_override_entry(qapp: object, font_dir: Path) -> None:
    """Every region without an override falls back to it; it cannot itself."""
    from comictrans.gui.font_box import PLAN_DEFAULT, FontBox

    box = FontBox(allow_default=False)
    box.showPopup()
    assert PLAN_DEFAULT not in [box.itemText(i) for i in range(box.count())]


def test_a_font_the_machine_does_not_have_is_kept_and_marked(qapp: object, font_dir: Path) -> None:
    """Silently swapping it is the one thing the spec says never happens."""
    from comictrans.gui.font_box import FontBox

    box = FontBox(allow_default=True)
    box.set_value("A Font From Another Mac")

    assert box.value() == "A Font From Another Mac", "kept verbatim"
    assert not box.resolvable()
    assert "not installed" in box.toolTip()

    box.set_value("Comic Sans MS")
    assert box.resolvable()
    assert box.toolTip() == ""


def _relative_luminance(color: object) -> float:
    """WCAG 2.1 relative luminance, so the contrast below is the real ratio."""
    channels = []
    for value in (color.redF(), color.greenF(), color.blueF()):  # type: ignore[attr-defined]
        channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(one: object, other: object) -> float:
    darker, lighter = sorted((_relative_luminance(one), _relative_luminance(other)))
    return (lighter + 0.05) / (darker + 0.05)


@pytest.mark.parametrize("base", [(255, 255, 255), (30, 30, 30)])
def test_the_unresolvable_mark_reads_on_a_dark_window_too(
    qapp: object, font_dir: Path, base: tuple[int, int, int]
) -> None:
    """A warning nobody can see is not a warning.

    Qt's ``darkRed``, which this used to be, measures 1.5:1 against a dark
    base — invisible on a Mac in dark mode, which is where this tool is
    meant to run. Both ways round the mark has to clear the 4.5:1 that
    ordinary text is held to, and still be a red rather than just a colour.
    """
    from PySide6.QtGui import QColor, QPalette

    from comictrans.gui.font_box import FontBox

    box = FontBox(allow_default=True)
    line_edit = box.lineEdit()
    assert line_edit is not None
    palette = line_edit.palette()
    palette.setColor(QPalette.ColorRole.Base, QColor(*base))
    line_edit.setPalette(palette)

    box.set_value("A Font From Another Mac")
    assert not box.resolvable(), "the test needs the mark to be showing"

    mark = line_edit.palette().color(QPalette.ColorRole.Text)
    ratio = _contrast(mark, QColor(*base))
    assert ratio >= 4.5, f"{ratio:.2f}:1 against {base} is not readable"
    assert mark.red() > mark.green() and mark.red() > mark.blue(), "a warning is red"


def test_typing_part_of_a_font_name_writes_nothing(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    """A fragment from the middle of a name is offered the name and nothing more.

    Qt's default completion for an editable combo is inline, which finishes
    what was typed as though the match began with it. With the contains
    filter this field uses, typing "Sans" wrote eight fonts into the plan
    header, half of them nonsense: each keystroke, then the same keystroke
    with the rest of Comic Sans MS glued on after it — "Somic Sans MS",
    "Samic Sans MS", "Sanic Sans MS", "Sansc Sans MS". Measured. The popup
    that replaced it wrote the keystrokes themselves, "S" to "Sans", which is
    no better a font; now nothing is written until a font is settled on.
    Typed as keys, because the completer's model filters the same way in
    either mode and it is the mode that decides what typing does.
    """
    from comictrans.gui.header_dialog import HeaderDialog

    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = HeaderDialog(window.document, window)  # type: ignore[arg-type]
    written: list[str] = []
    dialog.edited.connect(
        lambda: written.append(window.document.plan.header.font)  # type: ignore[union-attr]
    )
    popup = _type_into(dialog, dialog._font, "Sans")
    try:
        assert written == [], "nothing reaches the plan"
        assert popup.isVisible(), "the name containing it is offered"
        completer = dialog._font.completer()
        assert completer is not None
        model = completer.completionModel()
        offered = [model.index(row, 0).data() for row in range(model.rowCount())]
        assert offered == ["Comic Sans MS"]
    finally:
        popup.hide()
        dialog.hide()


def _install_font(font_dir: Path, family: str) -> None:
    """A second family that will render, from the fixture's own pair."""
    import shutil

    from comictrans import fonts

    shutil.copy(font_dir / "Comic Sans MS.ttf", font_dir / f"{family}.ttf")
    shutil.copy(font_dir / "Comic Sans MS Bold.ttf", font_dir / f"{family} Bold.ttf")
    fonts.forget_available_families()


def _key_target() -> QWidget:
    """Where Qt delivers a real key: the open list, or else the focused widget.

    Not the line edit, which is where a test reaches for first. The combo
    has the focus and hands keys on to its line edit by a direct call, which
    no event filter on the line edit sees — so a test typing into the line
    edit would pass for code a keyboard never reaches.
    """
    target = QApplication.activePopupWidget() or QApplication.focusWidget()
    assert target is not None
    return target


def _press_key(key: Qt.Key) -> None:
    QTest.keyClick(_key_target(), key)
    QApplication.processEvents()


def _type_into(holder: QWidget, box: object, text: str) -> QWidget:
    """Select what a font field holds and type over it; hand back its list.

    Empty means the selection is deleted, which is how a field is cleared.
    """
    from comictrans.gui.font_box import FontBox

    assert isinstance(box, FontBox)
    holder.show()
    holder.activateWindow()
    box.setFocus()
    line_edit = box.lineEdit()
    assert line_edit is not None
    line_edit.selectAll()
    QApplication.processEvents()
    if text:
        for character in text:
            QTest.keyClicks(_key_target(), character)
            QApplication.processEvents()
    else:
        _press_key(Qt.Key.Key_Backspace)
    completer = box.completer()
    assert completer is not None
    popup = completer.popup()
    assert popup is not None
    return popup


def _font_field(*families: str, holding: str | None = None) -> tuple[QDialog, object, QLineEdit]:
    """A dialog with a font field listing ``families``, and a field after it."""
    from comictrans.gui.font_box import FontBox

    dialog = QDialog()
    layout = QFormLayout(dialog)
    box = FontBox(allow_default=False)
    box._loaded = True
    box._fill(families)
    box.set_value(holding)
    after = QLineEdit()
    layout.addRow("font", box)
    layout.addRow("next", after)
    return dialog, box, after


def test_a_fragment_entered_in_the_header_becomes_the_font_it_found(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    """Typed "Sans", pressed Enter, saved the plan naming "Sans". Reported.

    Inline completion used to finish the name, garbling the plan on the way;
    the popup that replaced it wrote only what was typed, and then kept it.
    Enter takes the suggestion the list marks, as finishing the name did, and
    that is the only edit the plan sees.
    """
    from comictrans.gui.header_dialog import HeaderDialog

    _install_font(font_dir, "Chalkboard SE")
    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = HeaderDialog(window.document, window)  # type: ignore[arg-type]
    written: list[str] = []
    dialog.edited.connect(
        lambda: written.append(window.document.plan.header.font)  # type: ignore[union-attr]
    )
    popup = _type_into(dialog, dialog._font, "board")
    try:
        assert popup.currentIndex().data() == "Chalkboard SE", "marked, for Enter to take"
        assert dialog._font.currentText() == "board", "and only marked: nothing is replaced yet"

        _press_key(Qt.Key.Key_Return)

        assert window.document.plan.header.font == "Chalkboard SE"  # type: ignore[union-attr]
        assert written == ["Chalkboard SE"], "one edit, and only once it is a font"
    finally:
        popup.hide()
        dialog.hide()


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab])
def test_enter_and_tab_take_the_best_match(qapp: object, key: Qt.Key) -> None:
    dialog, box, after = _font_field("Comic Sans MS", "Marker Felt", holding="Marker Felt")
    popup = _type_into(dialog, box, "Comic")
    try:
        _press_key(key)

        assert box.value() == "Comic Sans MS"  # type: ignore[attr-defined]
        assert box.currentText() == "Comic Sans MS", "shown as the family is spelled"  # type: ignore[attr-defined]
        assert not popup.isVisible()
        if key == Qt.Key.Key_Tab:
            assert after.hasFocus(), "Tab still moves on to the next field"
    finally:
        dialog.hide()


def test_clicking_another_field_takes_the_best_match(qapp: object) -> None:
    """Leaving is leaving, whichever way: the same as Tab."""
    dialog, box, after = _font_field("Comic Sans MS", "Marker Felt", holding="Marker Felt")
    popup = _type_into(dialog, box, "Comic")
    try:
        popup.hide()  # the first click outside a list only closes it
        QTest.mouseClick(after, Qt.MouseButton.LeftButton)
        QApplication.processEvents()

        assert after.hasFocus()
        assert box.value() == "Comic Sans MS"  # type: ignore[attr-defined]
    finally:
        dialog.hide()


def test_closing_the_dialog_takes_the_best_match(qapp: object) -> None:
    """Close need not take focus from the field — on macOS a button does not."""
    from PySide6.QtWidgets import QPushButton

    dialog, box, _after = _font_field("Comic Sans MS", "Marker Felt", holding="Marker Felt")
    close = QPushButton("Close")
    close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    close.clicked.connect(dialog.reject)
    dialog.layout().addWidget(close)  # type: ignore[union-attr]
    popup = _type_into(dialog, box, "Comic")
    popup.hide()

    QTest.mouseClick(close, Qt.MouseButton.LeftButton)
    QApplication.processEvents()

    assert not dialog.isVisible()
    assert box.value() == "Comic Sans MS"  # type: ignore[attr-defined]


def test_a_font_picked_from_the_list_is_recorded(qapp: object) -> None:
    """Picked without typing anything first, which is not the end of an edit.

    It was taken for one, found no edit under way, and did nothing: the
    field showed the font picked while the value stayed the one before.
    """
    dialog, box, _after = _font_field("Comic Sans MS", "Marker Felt", holding="Comic Sans MS")
    chosen: list[object] = []
    box.chosen.connect(lambda: chosen.append(box.value()))  # type: ignore[attr-defined]
    try:
        dialog.show()
        dialog.activateWindow()
        box.setFocus()  # type: ignore[attr-defined]
        box.showPopup()  # type: ignore[attr-defined]
        QApplication.processEvents()
        view = box.view()  # type: ignore[attr-defined]
        row = view.visualRect(box.model().index(1, 0))  # type: ignore[attr-defined]
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=row.center())
        QApplication.processEvents()

        assert (box.currentText(), box.value()) == ("Marker Felt", "Marker Felt")  # type: ignore[attr-defined]
        assert chosen == ["Marker Felt"]
    finally:
        dialog.hide()


def test_a_suggestion_clicked_is_recorded(qapp: object) -> None:
    """The suggestions are the combo's to pass on, and it does, as a pick."""
    dialog, box, _after = _font_field("Comic Sans MS", "Marker Felt", holding="Marker Felt")
    popup = _type_into(dialog, box, "Sans")
    try:
        model = box.completer().completionModel()  # type: ignore[attr-defined]
        row = popup.visualRect(model.index(0, 0))  # type: ignore[attr-defined]
        QTest.mouseClick(popup.viewport(), Qt.MouseButton.LeftButton, pos=row.center())  # type: ignore[attr-defined]
        QApplication.processEvents()

        assert (box.currentText(), box.value()) == ("Comic Sans MS", "Comic Sans MS")  # type: ignore[attr-defined]
    finally:
        dialog.hide()


def test_escape_puts_back_what_was_there(qapp: object) -> None:
    """Escape is the one way out that takes nothing.

    Even a name typed in full, and so already recorded, is taken back. The
    first Escape does that and nothing else; the dialog stays open for a
    second one to close.
    """
    dialog, box, _after = _font_field("Comic Sans MS", "Marker Felt", holding="Marker Felt")
    chosen: list[object] = []
    box.chosen.connect(lambda: chosen.append(box.value()))  # type: ignore[attr-defined]
    try:
        _type_into(dialog, box, "Comic")
        _press_key(Qt.Key.Key_Escape)
        assert (box.value(), box.currentText()) == ("Marker Felt", "Marker Felt")  # type: ignore[attr-defined]
        assert chosen == []

        popup = _type_into(dialog, box, "comic sans ms")
        popup.hide()
        assert box.value() == "Comic Sans MS"  # type: ignore[attr-defined]
        _press_key(Qt.Key.Key_Escape)
        assert (box.value(), box.currentText()) == ("Marker Felt", "Marker Felt")  # type: ignore[attr-defined]
        assert chosen == ["Comic Sans MS", "Marker Felt"], "recorded, then taken back"
        assert dialog.isVisible(), "the first Escape cancels the edit, not the dialog"

        _press_key(Qt.Key.Key_Escape)
        assert not dialog.isVisible(), "with nothing left to cancel, Escape closes it"
    finally:
        dialog.hide()


def test_escape_goes_back_only_as_far_as_the_last_font_settled_on(qapp: object) -> None:
    """Each edit is its own: Escape undoes this one, not the one before."""
    dialog, box, _after = _font_field("Comic Sans MS", "Marker Felt", holding="Marker Felt")
    try:
        _type_into(dialog, box, "Comic")
        _press_key(Qt.Key.Key_Return)
        assert box.value() == "Comic Sans MS"  # type: ignore[attr-defined]

        _type_into(dialog, box, "Mark")
        _press_key(Qt.Key.Key_Escape)

        assert (box.value(), box.currentText()) == ("Comic Sans MS", "Comic Sans MS")  # type: ignore[attr-defined]
    finally:
        dialog.hide()


def test_a_name_nothing_here_contains_is_never_recorded(qapp: object) -> None:
    """Nothing is offered, so nothing is taken.

    Enter leaves it in the field, marked as not installed, to be corrected.
    Leaving the field puts back what was there — after Enter too, which Qt's
    own "editing finished" does not report a second time.
    """
    dialog, box, after = _font_field("Comic Sans MS", "Marker Felt", holding="Marker Felt")
    popup = _type_into(dialog, box, "Helvetica")
    try:
        assert not popup.isVisible()
        _press_key(Qt.Key.Key_Return)
        assert box.value() == "Marker Felt"  # type: ignore[attr-defined]
        assert box.currentText() == "Helvetica" and not box.resolvable()  # type: ignore[attr-defined]

        _press_key(Qt.Key.Key_Tab)
        assert after.hasFocus()
        assert (box.value(), box.currentText()) == ("Marker Felt", "Marker Felt")  # type: ignore[attr-defined]
    finally:
        dialog.hide()


def test_switching_to_another_application_leaves_the_edit_under_way(qapp: object) -> None:
    """Looking something up elsewhere is not leaving the field."""
    dialog, box, _after = _font_field("Comic Sans MS", "Marker Felt", holding="Marker Felt")
    elsewhere = QWidget()
    try:
        popup = _type_into(dialog, box, "Comic")
        popup.hide()
        elsewhere.show()
        elsewhere.activateWindow()
        QTest.qWait(10)

        assert box.value() == "Marker Felt"  # type: ignore[attr-defined]
        assert box.currentText() == "Comic", "still being typed"
    finally:
        elsewhere.hide()
        dialog.hide()


def test_a_font_from_another_mac_passes_through_the_field_untouched(qapp: object) -> None:
    """Given, not typed: focus in and out again and it is exactly as it came.

    It contains nothing installed here, but it would not matter if it did —
    nothing was typed, so there is nothing to take a match for.
    """
    dialog, box, after = _font_field("Comic Sans MS", holding="Comic")
    chosen: list[object] = []
    box.chosen.connect(lambda: chosen.append(box.value()))  # type: ignore[attr-defined]
    try:
        dialog.show()
        dialog.activateWindow()
        box.setFocus()  # type: ignore[attr-defined]
        QApplication.processEvents()
        _press_key(Qt.Key.Key_Tab)

        assert after.hasFocus()
        assert (box.value(), chosen) == ("Comic", [])  # type: ignore[attr-defined]
    finally:
        dialog.hide()


def test_a_name_typed_in_full_is_not_traded_for_a_longer_one(qapp: object) -> None:
    """ "Sans" is marked over "Comic Sans MS", which only contains it."""
    dialog, box, _after = _font_field("Comic Sans MS", "Sans")
    popup = _type_into(dialog, box, "sans")
    try:
        assert popup.currentIndex().data() == "Sans"
        _press_key(Qt.Key.Key_Return)

        assert box.value() == "Sans"  # type: ignore[attr-defined]
    finally:
        dialog.hide()


def test_enter_straight_after_typing_still_takes_a_suggestion(qapp: object) -> None:
    """The mark is made once the list has settled; a key can get there first.

    It takes the same font regardless: the mark only shows the best match,
    which is worked out again when the edit ends.
    """
    dialog, box, _after = _font_field("Comic Sans MS", "Marker Felt")
    dialog.show()
    dialog.activateWindow()
    box.setFocus()  # type: ignore[attr-defined]
    QApplication.processEvents()
    try:
        for character in "Mark":
            QTest.keyClicks(_key_target(), character)
        popup = box.completer().popup()  # type: ignore[attr-defined]
        assert popup.isVisible() and not popup.currentIndex().isValid(), "nothing marked yet"
        QTest.keyClick(popup, Qt.Key.Key_Return)
        QApplication.processEvents()

        assert box.value() == "Marker Felt"  # type: ignore[attr-defined]
    finally:
        dialog.hide()


def test_the_cleanup_leaves_a_completer_popup_to_the_combo_that_owns_it(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleted on its own, a completer's list is deleted twice, and that crashes.

    The test above was the first in the suite to open a completer popup, and
    it took the suite down with a segmentation fault three runs in four — in
    a later test, whichever first ran an event loop. The popup has no parent, so
    the window cleanup after each test saw a window of its own and deleted
    it; the completer deletes it again when its combo goes. Measured
    directly: popup first, the process dies; combo first, it does not.

    What the cleanup schedules is recorded rather than carried out, so that
    getting this wrong fails here, as an assertion, instead of as that crash
    somewhere later. A completer makes its popup when first asked for it,
    shown or not, and from then on it is listed as a window.
    """
    from PySide6.QtWidgets import QComboBox

    from .conftest import close_windows_opened_since

    box = QComboBox()
    box.setEditable(True)
    completer = box.completer()
    assert completer is not None
    completer.setCompletionMode(completer.CompletionMode.PopupCompletion)
    before = set(QApplication.topLevelWidgets())

    popup = completer.popup()
    window = QWidget()
    window.show()
    popup.show()
    assert {popup, window} <= set(QApplication.topLevelWidgets()) - before

    deleted: list[QWidget] = []
    monkeypatch.setattr(QWidget, "deleteLater", lambda widget: deleted.append(widget))
    close_windows_opened_since(before)
    monkeypatch.undo()

    assert deleted == [window], "a window the test opened goes; the popup is its owner's"
    assert not popup.isVisible(), "but it is hidden like everything else"
    window.deleteLater()


def test_a_name_typed_that_nothing_matches_is_not_recorded(qapp: object, font_dir: Path) -> None:
    """Typeable, but what it records is a font from the list.

    This used to say the opposite — "typing must not be forced onto a listed
    item" — for a font from another Mac. That is still kept when it is
    *given* (see the test above); it is typing one that stopped, with a plan
    saved naming "Sans".
    """
    from comictrans.gui.font_box import FontBox

    box = FontBox(allow_default=True)
    box.set_value(None)
    assert box.isEditable()

    holder = QWidget()
    QFormLayout(holder).addRow("font", box)
    popup = _type_into(holder, box, "Typed By Hand")
    try:
        _press_key(Qt.Key.Key_Tab)
    finally:
        popup.hide()
        holder.hide()

    assert box.value() is None


def test_rescanning_picks_up_a_font_installed_since(qapp: object, font_dir: Path) -> None:
    from comictrans import fonts
    from comictrans.gui.font_box import FontBox

    fonts.forget_available_families()
    box = FontBox(allow_default=False)
    box.showPopup()
    assert [box.itemText(i) for i in range(box.count())] == ["Comic Sans MS"]

    (font_dir / "Comic Sans MS.ttf").unlink()
    box.rescan()

    assert [box.itemText(i) for i in range(box.count())] == []


def test_the_inspector_writes_the_font_the_box_reports(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    box = window._inspector._font

    popup = _type_into(window, box, "Comic Sans MS")
    try:
        assert window.document.region("page-001-001").font == "Comic Sans MS"  # type: ignore[union-attr]

        popup = _type_into(window, box, "plan def")
        _press_key(Qt.Key.Key_Return)
        assert window.document.region("page-001-001").font is None  # type: ignore[union-attr]
    finally:
        popup.hide()
        window.hide()


def test_rescan_fonts_is_on_the_edit_menu(qapp: object) -> None:
    from PySide6.QtWidgets import QMenu

    window = MainWindow()
    menu = next(m for m in window.menuBar().findChildren(QMenu) if m.title() == "&Edit")
    assert window._rescan_fonts_action in menu.actions()


def _mac_form(inspector: object) -> None:
    """Put a form under the policy macOS uses: fields stay at their size hint."""
    from PySide6.QtWidgets import QFormLayout

    form = inspector.layout().itemAt(0).layout()  # type: ignore[attr-defined]
    assert isinstance(form, QFormLayout)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)


def test_the_font_box_is_wide_enough_for_the_name_it_holds(qapp: object, font_dir: Path) -> None:
    """A combo sizes itself from its items, not from the text typed into it.

    Under the macOS field-growth policy that left six pixels for a
    thirteen-character font name.
    """
    from PySide6.QtGui import QFontMetrics

    from comictrans.gui.font_box import PLAN_DEFAULT
    from comictrans.gui.inspector import RegionInspector

    inspector = RegionInspector()
    _mac_form(inspector)
    inspector.resize(420, 700)
    inspector.show()
    QTest.qWaitForWindowExposed(inspector)

    box = inspector._font
    metrics = QFontMetrics(box.font())
    for value, shown in ((None, PLAN_DEFAULT), ("Comic Sans MS", "Comic Sans MS")):
        box.set_value(value)
        QTest.qWait(1)
        assert box.lineEdit() is not None
        assert box.lineEdit().width() >= metrics.horizontalAdvance(shown), shown


def test_the_header_font_box_is_wide_enough_too(qapp: object, two_page_plan: Path) -> None:
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QFormLayout

    from comictrans.gui.header_dialog import HeaderDialog

    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = HeaderDialog(window.document, window)  # type: ignore[arg-type]
    form = dialog.layout().itemAt(0).layout()
    assert isinstance(form, QFormLayout)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
    dialog.adjustSize()
    dialog.show()
    QTest.qWaitForWindowExposed(dialog)

    box = dialog._font
    needed = QFontMetrics(box.font()).horizontalAdvance("Comic Sans MS")
    assert box.lineEdit() is not None
    assert box.lineEdit().width() >= needed


def test_a_very_long_font_name_is_capped_but_kept_in_the_tooltip(
    qapp: object, font_dir: Path
) -> None:
    """The cap stops one font name pushing the whole panel wide."""
    from comictrans.gui.font_box import FontBox

    box = FontBox(allow_default=True)
    box.set_value("Comic Sans MS")
    modest = box.minimumWidth()

    enormous = "A Ridiculously Long Font Name Indeed And Then Some More"
    box.set_value(enormous)

    assert box.minimumWidth() > modest, "it does widen for a longer name"
    assert box.minimumWidth() < modest * 3, "but not without limit"
    assert box.value() == enormous, "the name itself is untouched"
    assert enormous in box.toolTip(), "and readable somewhere"


def test_the_font_size_box_has_room_to_spare_around_auto(qapp: object) -> None:
    """It fit before, with eight pixels of slack, which reads as a mistake."""
    from PySide6.QtGui import QFontMetrics

    from comictrans.gui.inspector import RegionInspector

    inspector = RegionInspector()
    _mac_form(inspector)
    inspector.resize(420, 700)
    inspector.show()
    QTest.qWaitForWindowExposed(inspector)

    from PySide6.QtWidgets import QSpinBox

    box = inspector._font_size
    box.setValue(0)
    QTest.qWait(1)
    metrics = QFontMetrics(box.font())

    # What the old rule gave: exactly the slack a three-digit number gets,
    # measured at eight pixels, which fits the word without room to read it.
    reference = QSpinBox()
    reference.setRange(box.minimum(), box.maximum())
    bare = (
        reference.sizeHint().width()
        - metrics.horizontalAdvance(str(box.maximum()))
        + metrics.horizontalAdvance("auto")
    )
    assert box.minimumWidth() > bare, "no more air around 'auto' than before"
    assert box.lineEdit().width() > metrics.horizontalAdvance("auto")


# -- reshaping ---------------------------------------------------------------


def _drag(canvas: object, start: QPoint, end: QPoint) -> None:
    """Press, move and release on the canvas viewport, in view coordinates."""
    viewport = canvas.viewport()  # type: ignore[attr-defined]
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(viewport, end)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)


def test_edit_mode_puts_a_handle_on_every_corner(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    canvas = window._canvas
    corners = len(window.document.region("page-001-001").polygon)  # type: ignore[union-attr]

    assert canvas._handles == [], "no handles until the mode is on"

    window._edit_shape_action.setChecked(True)

    assert canvas.mode is CanvasMode.RESHAPE
    assert len(canvas._handles) == corners

    window._edit_shape_action.setChecked(False)

    assert canvas._handles == []


def test_dragging_a_corner_reshapes_the_region(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    canvas = window._canvas
    window._edit_shape_action.setChecked(True)
    before = window.document.region("page-001-001").polygon  # type: ignore[union-attr]

    start = canvas.mapFromScene(canvas._handles[0].pos())
    _drag(canvas, start, start + QPoint(20, 20))

    after = window.document.region("page-001-001").polygon  # type: ignore[union-attr]
    assert after != before
    assert after[1:] == before[1:], "only the corner that was dragged moved"
    assert window.document.region("page-001-001").geometry is Geometry.MANUAL  # type: ignore[union-attr]
    assert window.isWindowModified()
    assert canvas.dragMode() == canvas.DragMode.ScrollHandDrag, "the page pans again"


def test_dragging_inside_the_region_moves_the_whole_shape(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    canvas = window._canvas
    window._edit_shape_action.setChecked(True)
    before = window.document.region("page-001-001").polygon  # type: ignore[union-attr]

    centre = canvas.mapFromScene(canvas._items["page-001-001"].polygon().boundingRect().center())
    _drag(canvas, centre, centre + QPoint(15, 10))

    after = window.document.region("page-001-001").polygon  # type: ignore[union-attr]
    offsets = {(x2 - x1, y2 - y1) for (x1, y1), (x2, y2) in zip(before, after, strict=True)}
    assert len(offsets) == 1, f"the shape was deformed, not moved: {offsets}"
    assert offsets != {(0, 0)}, "nothing moved at all"


def test_escape_abandons_a_drag_and_leaves_the_region_alone(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    canvas = window._canvas
    window._edit_shape_action.setChecked(True)
    before = window.document.region("page-001-001").polygon  # type: ignore[union-attr]

    start = canvas.mapFromScene(canvas._handles[0].pos())
    QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(canvas.viewport(), start + QPoint(30, 30))
    assert canvas.polygon_of("page-001-001") != before, "the drag never started"

    QTest.keyClick(canvas, Qt.Key.Key_Escape)
    QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start + QPoint(30, 30))

    assert canvas.polygon_of("page-001-001") == before, "the outline went back"
    assert window.document.region("page-001-001").polygon == before  # type: ignore[union-attr]
    assert not window.isWindowModified()


def test_enter_finishes_reshaping_the_same_way_add_region_finishes_drawing(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    canvas = window._canvas
    window._edit_shape_action.setChecked(True)
    assert canvas.mode is CanvasMode.RESHAPE

    QTest.keyClick(canvas, Qt.Key.Key_Return)

    assert canvas.mode is CanvasMode.SELECT
    assert not window._edit_shape_action.isChecked()


@pytest.mark.parametrize(
    "action_name,expected_mode",
    [
        ("_edit_shape_action", CanvasMode.RESHAPE),
        ("_add_region_action", CanvasMode.DRAW),
        ("_merge_action", CanvasMode.MERGE),
    ],
    ids=["reshape", "draw", "merge"],
)
def test_entering_a_tool_mode_takes_focus_off_a_text_field(
    qapp: object, two_page_plan: Path, action_name: str, expected_mode: CanvasMode
) -> None:
    """Selecting a region focuses the translation; turning on a tool mode
    should not leave it there — Esc and Enter are how each mode is left, and
    both are the canvas's own keys, not the field's."""
    window = _shown_window(two_page_plan)
    window._canvas.region_selected.emit("page-001-001")
    QApplication.processEvents()
    assert QApplication.focusWidget() is window._inspector._translation, "sanity"

    getattr(window, action_name).setChecked(True)

    assert window._canvas.mode is expected_mode, "sanity"
    assert QApplication.focusWidget() is window._canvas


def test_enter_finishes_reshaping_even_when_the_translation_field_had_focus(
    qapp: object, two_page_plan: Path
) -> None:
    """The bug as reported: selecting a region focuses the translation, so
    turning on Edit Region Shape right after — without clicking the page
    first — left Enter landing in the field as a newline instead of
    reaching the canvas."""
    window = _shown_window(two_page_plan)
    window._canvas.region_selected.emit("page-001-001")
    QApplication.processEvents()
    assert QApplication.focusWidget() is window._inspector._translation, "sanity"

    window._edit_shape_action.setChecked(True)
    QApplication.processEvents()

    QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Return)

    assert window._canvas.mode is CanvasMode.SELECT
    assert not window._edit_shape_action.isChecked()
    assert "\n" not in window._inspector._translation.toPlainText()


def test_enter_mid_drag_does_not_interrupt_the_drag(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    canvas = window._canvas
    window._edit_shape_action.setChecked(True)

    start = canvas.mapFromScene(canvas._handles[0].pos())
    QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(canvas.viewport(), start + QPoint(30, 30))
    dragged = canvas.polygon_of("page-001-001")
    assert dragged != window.document.region("page-001-001").polygon  # type: ignore[union-attr]

    QTest.keyClick(canvas, Qt.Key.Key_Return)
    assert canvas.mode is CanvasMode.RESHAPE, "still reshaping — the drag was not cut short"
    assert canvas.polygon_of("page-001-001") == dragged, (
        "and the shape it had mid-drag is unchanged"
    )

    QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start + QPoint(30, 30))
    assert window.document.region("page-001-001").polygon == dragged  # type: ignore[union-attr]


def test_a_shape_the_reader_would_refuse_is_put_back(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    before = window.document.region("page-001-001").polygon  # type: ignore[union-attr]

    # Straight through the signal's handler: this is the case the canvas
    # cannot judge for itself, and the drag that produces it is a bow tie.
    window._on_polygon_edited("page-001-001", ((10, 10), (110, 110), (110, 10), (10, 110)))

    assert window.document.region("page-001-001").polygon == before  # type: ignore[union-attr]
    assert window._canvas.polygon_of("page-001-001") == before, "the canvas was put back too"
    assert "crosses or folds" in window.statusBar().currentMessage()


def test_undo_puts_a_reshaped_outline_back_on_the_canvas(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    before = window.document.region("page-001-001").polygon  # type: ignore[union-attr]

    window._on_polygon_edited("page-001-001", ((10, 10), (200, 10), (200, 150), (10, 150)))
    assert window._canvas.polygon_of("page-001-001") != before

    window._on_undo()

    assert window._canvas.polygon_of("page-001-001") == before
    assert not window.isWindowModified()


def test_double_clicking_an_edge_adds_a_corner_and_a_corner_removes_it(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    canvas = window._canvas
    window._edit_shape_action.setChecked(True)
    corners = len(window.document.region("page-001-001").polygon)  # type: ignore[union-attr]

    points = canvas._items["page-001-001"].points()
    first, second = QPointF(*points[0]), QPointF(*points[1])
    middle = canvas.mapFromScene(QPointF((first + second) / 2))
    QTest.mouseDClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=middle)

    assert len(window.document.region("page-001-001").polygon) == corners + 1  # type: ignore[union-attr]

    QTest.mouseDClick(
        canvas.viewport(),
        Qt.MouseButton.LeftButton,
        pos=canvas.mapFromScene(canvas._handles[1].pos()),
    )

    assert len(window.document.region("page-001-001").polygon) == corners  # type: ignore[union-attr]


def test_a_triangle_keeps_its_last_three_corners(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    canvas = window._canvas
    window._on_polygon_edited("page-001-001", ((60, 60), (260, 60), (160, 200)))
    window._edit_shape_action.setChecked(True)

    for _ in range(2):
        corner = canvas.mapFromScene(canvas._handles[0].pos())
        assert canvas.handle_at(QPointF(corner)) == 0, "the double-click missed the corner"
        QTest.mouseDClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=corner)

    assert len(window.document.region("page-001-001").polygon) == 3  # type: ignore[union-attr]


def test_reshaping_stays_on_the_region_it_was_chosen_for(qapp: object, two_page_plan: Path) -> None:
    """A stray click near another balloon must not move the handles to it."""
    window = _shown_window(two_page_plan)
    canvas = window._canvas
    window._go_to_region("page-001-001")
    window._edit_shape_action.setChecked(True)
    corners = [handle.pos() for handle in canvas._handles]

    other = canvas._items["page-001-002"]
    elsewhere = canvas.mapFromScene(other.polygon().boundingRect().center())
    QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=elsewhere)

    assert window._current_region == "page-001-001"
    assert [handle.pos() for handle in canvas._handles] == corners, "the handles stayed put"
    assert "page-001-002" in window.statusBar().currentMessage(), "and it said why"

    window._edit_shape_action.setChecked(False)
    QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=elsewhere)

    assert window._current_region == "page-001-002", "the mode was the only thing in the way"


def test_a_rendered_preview_leaves_edit_mode(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    # There are no outlines on a rendered page, so there is nothing to drag;
    # leaving the mode checked would say otherwise.
    window = _shown_window(two_page_plan)
    window._edit_shape_action.setChecked(True)

    window._on_render_preview()
    _settle_preview(window)

    assert window._canvas.mode is CanvasMode.SELECT
    assert not window._edit_shape_action.isChecked()
    assert not window._edit_shape_action.isEnabled()

    window._on_back_to_overlay()

    assert window._edit_shape_action.isEnabled(), "and it can be turned back on"


def test_a_corner_is_grabbable_at_any_zoom(qapp: object, two_page_plan: Path) -> None:
    # Handles ignore the view transform, so the grab distance is measured on
    # screen: a corner is the same target at 25% as at 400%.
    window = _shown_window(two_page_plan)
    canvas = window._canvas
    window._edit_shape_action.setChecked(True)

    for zoom in (0.25, 4.0):
        canvas.set_zoom(zoom)
        corner = canvas.mapFromScene(canvas._handles[2].pos())
        assert canvas.handle_at(QPointF(corner)) == 2, f"missed the corner at {zoom}x"
        assert canvas.handle_at(QPointF(corner + QPoint(40, 40))) is None


def test_a_reshaped_region_is_redrawn_as_hand_drawn_geometry(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    window._pages.select_image("page-002.png")
    region_id = "page-002-001"

    window._on_polygon_edited(region_id, ((10, 10), (200, 10), (200, 150), (10, 150)))

    assert "manual" in window._inspector._id_label.text()
    assert window._canvas._appearances[region_id].color == COLOR_MANUAL
    assert window._canvas.polygon_of(region_id) == ((10, 10), (200, 10), (200, 150), (10, 150))


def test_edit_mode_survives_a_page_change(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    window._edit_shape_action.setChecked(True)

    window._pages.select_image("page-002.png")

    assert window._edit_shape_action.isChecked()
    assert window._canvas.mode is CanvasMode.RESHAPE
    assert len(window._canvas._handles) == len(
        window.document.region("page-002-001").polygon  # type: ignore[union-attr]
    )


# -- the region context menu -------------------------------------------------


def _canvas_with_two_regions() -> object:
    """A shown, bare canvas — no window, no document — with two named regions.

    Bare because what these tests check is the canvas's own hit-testing and
    mode-gating, the same thing the ``PageCanvas``-only tests above check
    without a window either. Shown, so ``mapFromScene`` reflects a real
    fit-to-window transform rather than a 0x0 one.
    """
    from PySide6.QtGui import QPixmap

    from comictrans.gui.canvas import COLOR_EXACT, PageCanvas, RegionAppearance

    canvas = PageCanvas()
    canvas.resize(300, 200)
    canvas.show()
    QTest.qWaitForWindowExposed(canvas)
    canvas.show_page(
        QPixmap(300, 200),
        [
            RegionAppearance(
                region_id="a",
                polygon=((10, 10), (90, 10), (90, 90), (10, 90)),
                color=COLOR_EXACT,
                flagged=False,
            ),
            RegionAppearance(
                region_id="b",
                polygon=((150, 10), (280, 10), (280, 90), (150, 90)),
                color=COLOR_EXACT,
                flagged=False,
            ),
        ],
    )
    return canvas


def _right_click_scene(canvas: object, x: float, y: float) -> None:
    """Right-click a page coordinate, the same way ``_click_scene`` left-clicks one."""
    view_point = canvas.mapFromScene(QPointF(x, y))  # type: ignore[attr-defined]
    global_point = canvas.mapToGlobal(view_point)  # type: ignore[attr-defined]
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, view_point, global_point)
    canvas.contextMenuEvent(event)  # type: ignore[attr-defined]


def test_right_clicking_a_region_reports_the_same_hit_test_a_click_uses(qapp: object) -> None:
    canvas = _canvas_with_two_regions()
    requested: list[tuple[str, QPoint]] = []
    canvas.region_context_menu_requested.connect(
        lambda region_id, pos: requested.append((region_id, pos))
    )

    _right_click_scene(canvas, 215, 50)  # the centre of region "b"

    assert [region_id for region_id, _pos in requested] == ["b"]


def test_right_clicking_empty_page_requests_no_menu(qapp: object) -> None:
    canvas = _canvas_with_two_regions()
    requested: list[str] = []
    canvas.region_context_menu_requested.connect(
        lambda region_id, _pos: requested.append(region_id)
    )

    _right_click_scene(canvas, 120, 50)  # between the two regions, on neither

    assert requested == []


def test_the_context_menu_does_not_appear_outside_select_mode(qapp: object) -> None:
    canvas = _canvas_with_two_regions()
    canvas.set_mode(CanvasMode.RESHAPE)
    requested: list[str] = []
    canvas.region_context_menu_requested.connect(
        lambda region_id, _pos: requested.append(region_id)
    )

    _right_click_scene(canvas, 215, 50)  # the centre of region "b"

    assert requested == [], "reshaping is in progress; a menu of other commands would contradict it"


def test_the_region_menu_selects_its_region_and_offers_its_commands(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-001")  # a different region than the one asked for below

    menu = window._region_menu("page-001-002")

    assert window._current_region == "page-001-002", "chosen the same way a left-click would"
    actions = menu.actions()
    assert window._extract_text_action in actions
    assert window._edit_shape_action in actions


def test_right_clicking_a_region_on_the_window_opens_its_menu(
    qapp: object, monkeypatch: pytest.MonkeyPatch, two_page_plan: Path
) -> None:
    from comictrans.gui import main_window as main_window_module

    shown: list[object] = []

    class _NonBlockingMenu(main_window_module.QMenu):  # type: ignore[misc]
        def exec(self, *args: object, **kwargs: object) -> None:
            shown.append(self)

    monkeypatch.setattr(main_window_module, "QMenu", _NonBlockingMenu)
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-001")

    # The centre of BALLOON_B, which page-001-002 is drawn over.
    _right_click_scene(window._canvas, 400, 130)

    assert window._current_region == "page-001-002"
    assert len(shown) == 1
    actions = shown[0].actions()  # type: ignore[attr-defined]
    assert window._extract_text_action in actions
    assert window._edit_shape_action in actions


# -- adding and deleting ------------------------------------------------------


AROUND_BALLOON_B = ((310, 70), (490, 70), (490, 190), (310, 190))
"""A hand-drawn outline that hugs the second balloon rather than the art."""


def _near(polygon: object, expected: tuple[tuple[int, int], ...], slack: int = 2) -> bool:
    """Whether a polygon is where the clicks that drew it meant to put it."""
    points = tuple(polygon)  # type: ignore[call-overload]
    return len(points) == len(expected) and all(
        abs(ax - bx) <= slack and abs(ay - by) <= slack
        for (ax, ay), (bx, by) in zip(points, expected, strict=True)
    )


def _click_scene(canvas: object, x: float, y: float) -> None:
    """Click a page coordinate, wherever the view currently puts it."""
    QTest.mouseClick(
        canvas.viewport(),  # type: ignore[attr-defined]
        Qt.MouseButton.LeftButton,
        pos=canvas.mapFromScene(QPointF(x, y)),  # type: ignore[attr-defined]
    )


def test_drawing_an_outline_adds_a_region_with_colours_off_the_page(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    window._add_region_action.setChecked(True)
    assert window._canvas.mode is CanvasMode.DRAW

    # Around the second balloon, which the fixture draws white with black
    # lettering on dark art — so the colours have somewhere to come from.
    for point in AROUND_BALLOON_B:
        _click_scene(window._canvas, *point)
    QTest.keyClick(window._canvas, Qt.Key.Key_Return)

    added = window.document.regions_for("page-001.png")[-1]  # type: ignore[union-attr]
    assert added.geometry is Geometry.MANUAL
    # Within a pixel of where the clicks landed: a click is a view coordinate
    # and the page is fitted to the window, so the round trip through the
    # view transform is not exact at every zoom.
    assert _near(added.polygon, AROUND_BALLOON_B)
    assert added.source_text == "", "review never reads a page for text"
    assert added.fill_color.as_tuple() == BALLOON_WHITE, "measured, not assumed"
    assert added.text_color.as_tuple() == INK_BLACK
    assert window._current_region == added.id, "and it is what you are now editing"
    assert window._canvas.mode is CanvasMode.SELECT, "drawing is over"
    assert not window._add_region_action.isChecked()
    assert window.isWindowModified()


def test_an_outline_closes_by_clicking_its_first_corner(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    window._add_region_action.setChecked(True)

    for point in ((320, 60), (560, 60), (560, 200)):
        _click_scene(window._canvas, *point)
    assert _near(window._canvas.draft, ((320, 60), (560, 60), (560, 200)))

    _click_scene(window._canvas, 320, 60)

    assert window._canvas.draft == (), "the outline closed rather than gaining a corner"
    assert len(window.document.regions_for("page-001.png")) == 3  # type: ignore[union-attr]


def test_a_half_drawn_outline_can_be_taken_back_a_corner_at_a_time(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    before = len(window.document.plan.regions)  # type: ignore[union-attr]
    window._add_region_action.setChecked(True)

    for point in ((320, 60), (560, 60), (560, 200)):
        _click_scene(window._canvas, *point)
    QTest.keyClick(window._canvas, Qt.Key.Key_Backspace)

    assert _near(window._canvas.draft, ((320, 60), (560, 60)))

    QTest.keyClick(window._canvas, Qt.Key.Key_Escape)

    assert window._canvas.draft == ()
    assert len(window.document.plan.regions) == before, "nothing reached the plan"  # type: ignore[union-attr]
    assert not window.isWindowModified()


def test_two_corners_are_not_a_region(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    before = len(window.document.plan.regions)  # type: ignore[union-attr]
    window._add_region_action.setChecked(True)

    for point in ((320, 60), (560, 60)):
        _click_scene(window._canvas, *point)
    QTest.keyClick(window._canvas, Qt.Key.Key_Return)

    assert len(window.document.plan.regions) == before  # type: ignore[union-attr]
    assert len(window._canvas.draft) == 2, "still drawing"


def test_the_two_shape_modes_are_not_both_on_at_once(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    window._edit_shape_action.setChecked(True)

    window._add_region_action.setChecked(True)

    assert not window._edit_shape_action.isChecked(), "the toolbar follows the canvas"
    assert window._canvas.mode is CanvasMode.DRAW
    assert window._canvas._handles == [], "and the corner handles went with it"


def test_deleting_a_region_takes_it_off_the_page_and_out_of_the_plan(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-002")

    window._on_delete_region()

    assert [r.id for r in window.document.regions_for("page-001.png")] == ["page-001-001"]  # type: ignore[union-attr]
    assert "page-001-002" not in window._canvas._items
    assert window._current_region == "page-001-001", "somewhere to stand afterwards"
    assert "1 region" in window._pages.item(0).text()
    assert "Ctrl+Z" in window.statusBar().currentMessage()

    window._on_undo()

    assert len(window.document.regions_for("page-001.png")) == 2  # type: ignore[union-attr]
    assert "page-001-002" in window._canvas._items, "back on the page, not just in the plan"


def test_a_colour_can_be_taken_off_the_page_by_clicking_it(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-001")

    window._inspector._fill_color.sample_requested.emit()

    assert window._canvas.mode is CanvasMode.PICK

    _click_scene(window._canvas, 20, 20)  # artwork, well outside any balloon

    assert window.document.region("page-001-001").fill_color.as_tuple() == ART_DARK  # type: ignore[union-attr]
    assert window._inspector._fill_color.value().as_tuple() == ART_DARK
    assert window._canvas.mode is CanvasMode.SELECT


def test_a_standard_colour_can_be_chosen_from_the_field(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-001")

    action = next(a for a in window._inspector._text_color.menu().actions() if a.text() == "Red")
    action.trigger()

    assert window.document.region("page-001-001").text_color == Color(208, 32, 32)  # type: ignore[union-attr]


def test_the_source_text_of_a_drawn_region_is_typed_in(qapp: object, two_page_plan: Path) -> None:
    # A hand-drawn region has no OCR reading and no way to get one: nothing
    # in review reads a page for text.
    window = _shown_window(two_page_plan)
    window._add_region_action.setChecked(True)
    for point in ((320, 60), (560, 60), (560, 200)):
        _click_scene(window._canvas, *point)
    QTest.keyClick(window._canvas, Qt.Key.Key_Return)
    drawn = window._current_region
    assert drawn is not None

    assert window._inspector._source_text.toPlainText() == ""
    assert not window._inspector._source_text.isReadOnly()

    window._inspector._source_text.setPlainText("CIAO")
    window._inspector._translation.setPlainText("HELLO")

    assert window.document.region(drawn).source_text == "CIAO"  # type: ignore[union-attr]
    assert window.document.region(drawn).translation == "HELLO"  # type: ignore[union-attr]
    assert not window.document.flags(drawn).held_back  # type: ignore[union-attr]


def test_undoing_a_drawn_region_takes_its_outline_off_the_page(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    window._add_region_action.setChecked(True)
    for point in AROUND_BALLOON_B:
        _click_scene(window._canvas, *point)
    QTest.keyClick(window._canvas, Qt.Key.Key_Return)
    drawn = window._current_region
    assert drawn in window._canvas.region_ids()

    window._on_undo()

    assert drawn not in window._canvas.region_ids()
    assert not window.isWindowModified()
    assert "2 regions" in window._pages.item(0).text()


def test_a_region_can_be_drawn_on_a_page_with_nothing_on_it(
    qapp: object, plan_with_a_blank_page: Path
) -> None:
    # What the plan's images list is for: the page detection found nothing on
    # is in the file, so it can be opened and drawn on.
    window = _shown_window(plan_with_a_blank_page)
    window._pages.select_image("page-003.png")
    assert window._current_region is None

    window._add_region_action.setChecked(True)
    for point in ((60, 60), (260, 60), (260, 200)):
        _click_scene(window._canvas, *point)
    QTest.keyClick(window._canvas, Qt.Key.Key_Return)

    drawn = window.document.regions_for("page-003.png")  # type: ignore[union-attr]
    assert [region.id for region in drawn] == ["page-003-001"]
    assert window._current_region == "page-003-001"
    assert "1 region" in window._pages.item(2).text()


def test_the_outline_being_drawn_follows_the_pointer(qapp: object, two_page_plan: Path) -> None:
    # The rubber band needs move events with no button held, which the view's
    # viewport tracks by default — this is the test that says so.
    window = _shown_window(two_page_plan)
    canvas = window._canvas
    window._add_region_action.setChecked(True)
    _click_scene(canvas, 310, 70)
    reach = canvas._draft_item.path().boundingRect().right()  # type: ignore[union-attr]

    QTest.mouseMove(canvas.viewport(), canvas.mapFromScene(QPointF(490, 70)))

    assert canvas._draft_item is not None
    assert canvas._draft_item.path().boundingRect().right() > reach, "no rubber band"
    assert len(canvas.draft) == 1, "and moving is not placing"


def test_the_erase_field_writes_through_and_gates_the_fill_colour(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-001")
    erase = window._inspector._erase

    assert erase.currentIndex() == 0, "a detected region follows the run's flag"
    assert window._inspector._fill_color.isEnabled()

    erase.setCurrentIndex([mode for _, mode, _ in ERASE_CHOICES].index(Erase.NONE))

    assert window.document.region("page-001-001").erase is Erase.NONE  # type: ignore[union-attr]
    assert not window._inspector._fill_color.isEnabled(), "nothing is painted with it"

    erase.setCurrentIndex([mode for _, mode, _ in ERASE_CHOICES].index(Erase.POLYGON))

    assert window.document.region("page-001-001").erase is Erase.POLYGON  # type: ignore[union-attr]
    assert window._inspector._fill_color.isEnabled()


def test_a_drawn_region_arrives_set_to_fill_itself(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    window._add_region_action.setChecked(True)
    for point in AROUND_BALLOON_B:
        _click_scene(window._canvas, *point)
    QTest.keyClick(window._canvas, Qt.Key.Key_Return)

    assert window.document.region(window._current_region).erase is Erase.POLYGON  # type: ignore[union-attr, arg-type]
    assert window._inspector._erase.currentText() == "the whole region"


# -- the words the three erase boxes share ------------------------------------


def test_every_erase_mode_has_a_name_in_the_boxes_that_offer_them() -> None:
    """A new ``Erase`` member must not be able to arrive unnamed.

    Three boxes offer this — the inspector's, the render dialog's and the
    preferences' — and all three read ``erase_choices``. A mode added to the
    model with no row here would simply not be offered, which is the quiet
    kind of missing.
    """
    named = {mode for _label, mode, _hint in erase_choices.CHOICES}

    assert named == set(Erase), "every Erase member needs a row, and no row may invent one"


def test_the_three_boxes_describe_the_four_modes_in_the_same_words() -> None:
    """The contract the module exists for, pinned so a re-split fails.

    These were written out at each of the three, which let a reword in one
    leave the other two saying something else — and handed a translator the
    same sentences twice, with no way to notice they were the same.
    """
    inspector_rows = {(label, str(mode), hint) for label, mode, hint in ERASE_CHOICES if mode}
    render_rows = set(STRATEGY_CHOICES)

    assert inspector_rows == render_rows
    assert render_rows == set(erase_choices.STRATEGIES)


def test_the_inspector_alone_offers_following_the_run() -> None:
    """A region may decline to decide; a run has nothing to decline to."""
    assert ERASE_CHOICES[0][1] is None
    assert all(mode is not None for _label, mode, _hint in ERASE_CHOICES[1:])
    assert all(value for _label, value, _hint in STRATEGY_CHOICES)


# -- the preview toggle -------------------------------------------------------


def test_the_selected_region_survives_the_preview_round_trip(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    # Checking how one balloon came out and coming back to the top of the
    # page is a place lost every time.
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-002")

    window._on_render_preview()
    _settle_preview(window)
    window._on_back_to_overlay()

    assert window._current_region == "page-001-002"
    assert window._canvas._selected_id == "page-001-002"
    assert window._inspector._id_label.text().startswith("page-001-002")


def test_one_action_swaps_between_the_overlay_and_the_rendered_page(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    window = _shown_window(two_page_plan)

    assert window._preview_action.text() == PREVIEW_TEXT
    assert [a.text() for a in window._toolbar.actions()].count(OVERLAY_TEXT) == 0

    window._preview_action.trigger()
    _settle_preview(window)

    assert window._showing_preview
    assert window._preview_action.text() == OVERLAY_TEXT, "it now says what it will do"
    assert window._preview_action.isEnabled()

    window._preview_action.trigger()

    assert not window._showing_preview
    assert window._preview_action.text() == PREVIEW_TEXT


# -- merging ------------------------------------------------------------------

OVER_BALLOON_A = ((200, 60), (400, 60), (400, 200), (200, 200))
"""A second outline dragged across the first, as a double-traced balloon is."""


def test_merging_two_regions_from_the_canvas(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    window._on_polygon_edited("page-001-002", OVER_BALLOON_A)
    window._go_to_region("page-001-001")

    window._merge_action.setChecked(True)
    assert window._canvas.mode is CanvasMode.MERGE

    _click_scene(window._canvas, 350, 130)  # inside the second outline only

    assert [r.id for r in window.document.regions_for("page-001.png")] == ["page-001-001"]  # type: ignore[union-attr]
    assert window._current_region == "page-001-001"
    assert window._canvas.region_ids() == {"page-001-001"}
    assert window._canvas.mode is CanvasMode.SELECT
    assert not window._merge_action.isChecked()
    assert "merged" in window.statusBar().currentMessage()

    merged = window.document.region("page-001-001")  # type: ignore[union-attr]
    assert merged.geometry is Geometry.MANUAL
    assert (400, 60) in merged.polygon, "the hull reaches the far side"


def test_merging_refuses_outlines_that_do_not_overlap(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-001")
    window._merge_action.setChecked(True)

    _click_scene(window._canvas, 400, 130)  # the balloon across the page

    assert len(window.document.regions_for("page-001.png")) == 2  # type: ignore[union-attr]
    assert not window.isWindowModified()
    assert "do not overlap" in window.statusBar().currentMessage()


def test_a_merged_region_takes_its_colours_from_the_merged_shape(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    window._on_polygon_edited("page-001-002", OVER_BALLOON_A)
    window._go_to_region("page-001-001")
    # A colour the page cannot produce, so inheriting it would show.
    window.document.set_fill_color("page-001-001", Color(1, 2, 3))  # type: ignore[union-attr]
    window._merge_action.setChecked(True)

    _click_scene(window._canvas, 350, 130)

    merged = window.document.region("page-001-001")  # type: ignore[union-attr]
    assert merged.fill_color.as_tuple() == BALLOON_WHITE, "read off the page, not inherited"


def test_escape_leaves_merge_mode(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-001")
    window._merge_action.setChecked(True)

    QTest.keyClick(window._canvas, Qt.Key.Key_Escape)

    assert window._canvas.mode is CanvasMode.SELECT
    assert not window._merge_action.isChecked()


def test_merging_needs_something_to_merge_with(qapp: object, plan_with_a_blank_page: Path) -> None:
    window = _shown_window(plan_with_a_blank_page)
    window._go_to_region("page-001-001")
    assert window._merge_action.isEnabled(), "two regions on this page"

    window._pages.select_image("page-002.png")

    assert not window._merge_action.isEnabled(), "the only region on its page"


# -- the hint line ------------------------------------------------------------


def test_the_hint_line_says_what_a_click_does_in_each_mode(
    qapp: object, two_page_plan: Path
) -> None:
    # The gestures used to be announced once, in a status bar message the next
    # message replaced. This line stays put for as long as the mode does.
    window = _shown_window(two_page_plan)
    assert window._hint.hint() == mode_hint(CanvasMode.SELECT)

    window._edit_shape_action.setChecked(True)
    assert window._hint.hint() == mode_hint(CanvasMode.RESHAPE)
    assert "double-click an edge" in window._hint.hint()

    window._add_region_action.setChecked(True)
    assert window._hint.hint() == mode_hint(CanvasMode.DRAW)

    window._add_region_action.setChecked(False)
    assert window._hint.hint() == mode_hint(CanvasMode.SELECT), "and it comes back"


def test_the_hint_line_names_the_colour_being_taken(qapp: object, two_page_plan: Path) -> None:
    # Which of the two colours is being picked is not visible anywhere else.
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-001")

    window._inspector._text_color.sample_requested.emit()

    assert "text colour" in window._hint.hint()

    _click_scene(window._canvas, 20, 20)

    assert window._hint.hint() == mode_hint(CanvasMode.SELECT)


def test_the_hint_line_sits_under_the_canvas_and_cannot_widen_the_window(
    qapp: object, two_page_plan: Path
) -> None:
    # The shape of the defect recorded as 4 in known-bugs.md, one layer out: a
    # label wide enough to read must not set a floor under the whole window.
    window = _shown_window(two_page_plan)
    layout = window.centralWidget().layout()

    assert layout.itemAt(0).widget() is window._canvas
    assert layout.itemAt(1).widget() is window._hint

    assert window.centralWidget().minimumSizeHint().width() < 100, "it costs no width"

    # What will not fit is elided rather than cut off mid-word, and the whole
    # line stays readable in the tooltip.
    window._hint.set_hint(mode_hint(CanvasMode.RESHAPE))
    window._hint.resize(200, window._hint.height())

    assert window._hint.text() != window._hint.hint(), "a long line in a narrow one"
    assert window._hint.text().endswith("…")
    assert window._hint.toolTip() == mode_hint(CanvasMode.RESHAPE)


# -- moving a region ----------------------------------------------------------


def _press(canvas: object, pos: object, modifiers: object = Qt.KeyboardModifier.NoModifier) -> None:
    QTest.mousePress(
        canvas.viewport(),  # type: ignore[attr-defined]
        Qt.MouseButton.LeftButton,
        modifiers,  # type: ignore[arg-type]
        pos,  # type: ignore[arg-type]
    )


def test_ctrl_dragging_a_region_moves_it_without_entering_a_mode(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    canvas = window._canvas
    window._go_to_region("page-001-001")
    before = window.document.region("page-001-001").polygon  # type: ignore[union-attr]
    assert canvas.mode is CanvasMode.SELECT, "no mode was entered"

    inside = canvas.mapFromScene(QPointF(*BALLOON_A.center))
    _press(canvas, inside, Qt.KeyboardModifier.ControlModifier)
    QTest.mouseMove(canvas.viewport(), inside + QPoint(20, 12))
    QTest.mouseRelease(
        canvas.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier,
        inside + QPoint(20, 12),
    )

    after = window.document.region("page-001-001").polygon  # type: ignore[union-attr]
    offsets = {(x2 - x1, y2 - y1) for (x1, y1), (x2, y2) in zip(before, after, strict=True)}
    assert len(offsets) == 1, f"the shape was deformed, not moved: {offsets}"
    assert offsets != {(0, 0)}, "nothing moved"
    assert window.isWindowModified()


def test_a_plain_drag_in_select_mode_still_pans_rather_than_moving(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    canvas = window._canvas
    window._go_to_region("page-001-001")

    inside = canvas.mapFromScene(QPointF(*BALLOON_A.center))
    _press(canvas, inside)

    assert canvas._drag is None, "without the modifier the press is the view's"

    QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=inside)
    assert not window.isWindowModified()


def test_the_arrow_keys_nudge_the_selected_region(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-001")
    before = window.document.region("page-001-001").polygon  # type: ignore[union-attr]

    QTest.keyClick(window._canvas, Qt.Key.Key_Right)
    QTest.keyClick(window._canvas, Qt.Key.Key_Down)

    moved = window.document.region("page-001-001").polygon  # type: ignore[union-attr]
    assert moved == tuple((x + NUDGE_STEP, y + NUDGE_STEP) for x, y in before)

    QTest.keyClick(window._canvas, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)

    strode = window.document.region("page-001-001").polygon  # type: ignore[union-attr]
    assert strode == tuple((x - NUDGE_STRIDE, y) for x, y in moved), "Shift takes ten"


def test_a_run_of_nudges_is_one_undo_step(qapp: object, two_page_plan: Path) -> None:
    # Forty taps of an arrow key are one thing done, the way typing is.
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-001")
    before = window.document.region("page-001-001").polygon  # type: ignore[union-attr]

    for _ in range(5):
        QTest.keyClick(window._canvas, Qt.Key.Key_Right)

    window._on_undo()

    assert window.document.region("page-001-001").polygon == before  # type: ignore[union-attr]
    assert not window.isWindowModified()


def test_a_new_selection_starts_a_new_run_of_nudges(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-001")
    QTest.keyClick(window._canvas, Qt.Key.Key_Right)
    first = window.document.region("page-001-001").polygon  # type: ignore[union-attr]

    window._go_to_region("page-001-002")
    QTest.keyClick(window._canvas, Qt.Key.Key_Right)

    window._on_undo()

    assert window.document.region("page-001-001").polygon == first, "the first run stands"
    assert window.document.region("page-001-002").polygon == BALLOON_B.as_polygon()  # type: ignore[union-attr]


def test_a_nudge_stops_at_the_edge_of_the_page(qapp: object, two_page_plan: Path) -> None:
    # A corner off the top or left is a plan file the reader would refuse.
    window = _shown_window(two_page_plan)
    window._on_polygon_edited("page-001-001", ((0, 0), (100, 0), (100, 80), (0, 80)))
    window._go_to_region("page-001-001")

    for _ in range(3):
        QTest.keyClick(window._canvas, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)

    assert window.document.region("page-001-001").polygon[0] == (0, 0)  # type: ignore[union-attr]


def test_a_moved_region_is_recorded_as_hand_placed(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-001")
    assert window.document.region("page-001-001").geometry is Geometry.EXACT  # type: ignore[union-attr]

    QTest.keyClick(window._canvas, Qt.Key.Key_Right)

    assert window.document.region("page-001-001").geometry is Geometry.MANUAL  # type: ignore[union-attr]


def test_the_cursor_says_where_a_modifier_drag_would_move_something(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    canvas = window._canvas
    window._go_to_region("page-001-001")
    inside = QPointF(canvas.mapFromScene(QPointF(*BALLOON_A.center)))
    elsewhere = QPointF(canvas.mapFromScene(QPointF(20, 20)))

    assert canvas.wants_move_cursor(inside, Qt.KeyboardModifier.ControlModifier)
    assert not canvas.wants_move_cursor(inside, Qt.KeyboardModifier.NoModifier)
    assert not canvas.wants_move_cursor(elsewhere, Qt.KeyboardModifier.ControlModifier)

    window._add_region_action.setChecked(True)

    assert not canvas.wants_move_cursor(inside, Qt.KeyboardModifier.ControlModifier), (
        "not while drawing"
    )


def test_the_hint_calls_the_move_modifier_what_this_platform_calls_it(
    qapp: object, two_page_plan: Path
) -> None:
    # Qt maps ControlModifier to Command on macOS, so a hint hard-coded to
    # "Ctrl" would be wrong on the machine this tool is written for.
    window = _shown_window(two_page_plan)

    assert f"{move_modifier_name()}-drag" in window._hint.hint()
    assert "{move}" not in window._hint.hint(), "the placeholder was filled in"


def test_a_held_arrow_key_speeds_up_and_a_fresh_press_does_not(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    canvas = window._canvas
    window._go_to_region("page-001-001")

    def press(*, repeating: bool) -> int:
        before = window.document.region("page-001-001").polygon[0][0]  # type: ignore[union-attr]
        canvas.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Right,
                Qt.KeyboardModifier.NoModifier,
                "",
                repeating,
            )
        )
        return window.document.region("page-001-001").polygon[0][0] - before  # type: ignore[union-attr]

    assert press(repeating=False) == NUDGE_STEP, "a tap is a pixel"
    for _ in range(NUDGE_ACCELERATES_AFTER - 1):
        assert press(repeating=True) == NUDGE_STEP, "and so are the first repeats"

    assert press(repeating=True) > NUDGE_STEP, "then it picks up"

    assert press(repeating=False) == NUDGE_STEP, "and a fresh press is a pixel again"


# -- running a pass from the window --------------------------------------
#
# Each pipeline loop, its progress reports and its cancelling are tested
# where the loop lives — test_apply.py and test_extract.py — and what a
# finished run is worth showing in test_gui_run.py. What is left for here is
# the plumbing: that the dialogs refuse what they must, that a run reaches
# disk and comes back into the panel, and that a row in the panel goes where
# it says.


def _await_run(window: MainWindow) -> None:
    """Wait for the pass in flight to report back into the window.

    ``wait`` returns when the worker thread has stopped; the report is a
    queued signal and still needs an event-loop turn to be delivered.
    """
    job = window._job
    assert job is not None
    assert job.wait(60_000), "the worker thread did not finish"
    for _ in range(20):
        QApplication.processEvents()
        if window._job is None:
            return
    raise AssertionError("the report never reached the window")


def _run_render(window: MainWindow, request: RenderRequest) -> None:
    window._start_render(request)
    _await_run(window)


def test_the_render_dialog_suggests_a_directory_beside_the_pages(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = RenderDialog(window.document, window)  # type: ignore[arg-type]

    assert dialog.output_dir() == suggested_output(two_page_plan)
    assert dialog.refusal() == "", "the suggestion is one the dialog will accept"


def test_the_render_dialog_refuses_to_write_into_the_source_tree(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = RenderDialog(window.document, window)  # type: ignore[arg-type]
    ok = dialog._buttons.button(QDialogButtonBox.StandardButton.Ok)

    for target in (two_page_plan.parent, two_page_plan.parent / "rendered"):
        dialog._output.setText(str(target))
        assert "inside the source directory" in dialog.refusal()
        assert not ok.isEnabled(), "and there is no way to insist"

    dialog._output.setText(str(two_page_plan.parent.parent / "elsewhere"))
    assert dialog.refusal() == ""
    assert ok.isEnabled()


def test_the_render_dialog_refuses_an_empty_directory_and_a_file(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = RenderDialog(window.document, window)  # type: ignore[arg-type]

    dialog._output.setText("   ")
    assert "Choose a directory" in dialog.refusal()

    dialog._output.setText(str(two_page_plan))
    assert "is a file, not a directory" in dialog.refusal()


def test_the_render_button_says_when_it_will_save_first(qapp: object, two_page_plan: Path) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    assert RenderDialog(window.document, window)._ok.text() == RENDER  # type: ignore[arg-type]

    window.document.set_translation("page-001-001", "CHANGED")  # type: ignore[union-attr]
    assert RenderDialog(window.document, window)._ok.text() == SAVE_AND_RENDER  # type: ignore[arg-type]

    window.document.save()  # type: ignore[union-attr]


def test_the_dialog_asks_for_the_three_things_the_command_line_flags_ask_for(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = RenderDialog(window.document, window)  # type: ignore[arg-type]
    dialog._format.setCurrentIndex(dialog._format.findData("tiff"))
    dialog._erase.setCurrentIndex(dialog._erase.findData("inpaint"))
    dialog._force.setChecked(True)

    request = dialog.request()

    assert request.image_format == "tiff"
    assert request.config.erase.strategy == "inpaint"
    assert request.force
    assert request.config.typeset.condense_min == window.document.plan.header.condense_min  # type: ignore[union-attr]


def test_rendering_writes_the_pages_and_leaves_the_sources_alone(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    source = two_page_plan.parent
    before = {path.name: sha256_file(path) for path in source.glob("*.png")}
    request = RenderDialog(window.document, window).request()  # type: ignore[arg-type]

    _run_render(window, request)

    assert sorted(p.name for p in request.output.iterdir()) == ["page-001.png", "page-002.png"]
    assert {path.name: sha256_file(path) for path in source.glob("*.png")} == before


def test_a_finished_run_reports_into_the_panel(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    request = RenderDialog(window.document, window).request()  # type: ignore[arg-type]

    _run_render(window, request)

    panel = window._run_panel
    assert str(request.output) in panel._headline.text()
    assert not window._run_dock.isHidden(), "the panel opens itself when there is a report"
    # page-001-002 is the region the fixture holds back with no translation.
    assert panel.row_count() == 1
    assert window._render_action.isEnabled(), "the run is over"


def test_a_row_in_the_report_selects_the_region_it_names(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    request = RenderDialog(window.document, window).request()  # type: ignore[arg-type]
    _run_render(window, request)
    window._pages.select_image("page-002.png")
    assert window._current_region == "page-002-001"

    window._run_panel.select_row(0)

    assert window._current_image == "page-001.png", "it changed page to get there"
    assert window._current_region == "page-001-002"


def test_a_row_naming_a_region_that_has_since_gone_says_so(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    _run_render(window, RenderDialog(window.document, window).request())  # type: ignore[arg-type]
    window.document.delete_region("page-001-002")  # type: ignore[union-attr]
    window.document.save()  # type: ignore[union-attr]

    window._run_panel.select_row(0)

    assert "no longer in this plan" in window.statusBar().currentMessage()


def test_the_render_action_is_held_back_while_a_run_is_going(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    assert window._render_action.isEnabled()

    # A job that is never started: what is being tested is the rule, not how
    # long a render happens to take.
    window._job = RenderJob(RenderDialog(window.document, window).request(), window)  # type: ignore[arg-type]
    window._update_actions_enabled()

    assert not window._render_action.isEnabled()


def test_cancelling_from_the_panel_reaches_the_run(qapp: object, two_page_plan: Path) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    job = RenderJob(RenderDialog(window.document, window).request(), window)  # type: ignore[arg-type]
    window._job = job

    window._run_panel._cancel.click()

    assert job.cancelling
    assert not window._run_panel._cancel.isEnabled(), "one press is all there is to give"


def test_a_plan_with_no_pages_has_nothing_to_render(qapp: object, tmp_path: Path) -> None:
    empty = tmp_path / "pages" / "comic-plan.yaml"
    empty.parent.mkdir()
    write_plan(make_plan(_header(), ()), empty)
    window = MainWindow()
    window.open_plan(empty)

    assert not window._render_action.isEnabled()


def test_a_run_that_cannot_start_says_so_in_the_panel(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    """An unresolvable font fails before any page is written, and reports."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    window.document.set_header_font("No Such Font Anywhere")  # type: ignore[union-attr]
    window.document.save()  # type: ignore[union-attr]
    request = RenderDialog(window.document, window).request()  # type: ignore[arg-type]

    _run_render(window, request)

    assert "Could not run" in window._run_panel._headline.text()
    assert "No Such Font Anywhere" in window._run_panel._headline.text()
    assert not request.output.exists(), "nothing was written"
    assert window._render_action.isEnabled(), "and the window is usable again"


GROWTH_POLICIES = [
    QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint,
    QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow,
]
"""macOS uses the first and every other platform the second, so a form tested
under one of them is a form tested in half the arrangements it ships in."""


def _set_growth_policy(dialog: QDialog, policy: QFormLayout.FieldGrowthPolicy) -> None:
    """Stand this dialog's form up the way another platform's style would."""
    for form in dialog.findChildren(QFormLayout):
        form.setFieldGrowthPolicy(policy)


def _assert_nothing_is_clipped(labels: dict[str, Note], where: str) -> None:
    for name, label in labels.items():
        if not label.isVisible() or not label.text():
            continue
        assert label.height() >= label.needed_height(label.width()), f"{name} is cut off ({where})"


# -- a chapter written as one file ---------------------------------------


def test_choosing_a_chapter_file_renames_the_output_and_typing_one_moves_the_box(
    qapp: object, two_page_plan: Path
) -> None:
    """Two views of one fact; neither can be left saying the other thing."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = RenderDialog(window.document, window)  # type: ignore[arg-type]
    folder = dialog.output()

    dialog._container.setCurrentIndex(dialog._container.findData(".cbz"))
    assert dialog.output() == folder.with_name(folder.name + ".cbz")

    dialog._container.setCurrentIndex(dialog._container.findData(".cbr"))
    assert dialog.output() == folder.with_name(folder.name + ".cbr")

    dialog._container.setCurrentIndex(dialog._container.findData(None))
    assert dialog.output() == folder, "and back, with the name it started with"

    dialog._output.setText(str(folder.parent / "chapter-01.cbz"))
    assert dialog._container.currentData() == ".cbz"
    assert dialog.output() == folder.parent / "chapter-01.cbz", "typing it does not rename it"

    dialog._output.setText(str(folder.parent / "chapter-01.zip"))
    assert dialog._container.currentData() == ".cbz", "a chapter saved under the plain name"
    assert dialog.output().suffix == ".zip", "which is left exactly as it was typed"


def test_a_folder_whose_name_has_a_dot_in_it_keeps_all_of_it(
    qapp: object, two_page_plan: Path
) -> None:
    """``with_suffix`` would make ``vol.2-translated`` into ``vol.cbz``."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = RenderDialog(window.document, window)  # type: ignore[arg-type]
    dialog._output.setText(str(two_page_plan.parent.parent / "vol.2-translated"))

    dialog._container.setCurrentIndex(dialog._container.findData(".cbz"))
    assert dialog.output().name == "vol.2-translated.cbz"

    dialog._container.setCurrentIndex(dialog._container.findData(None))
    assert dialog.output().name == "vol.2-translated"


def test_a_chapter_file_is_asked_the_same_question_about_the_folder_it_goes_in(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = RenderDialog(window.document, window)  # type: ignore[arg-type]
    ok = dialog._buttons.button(QDialogButtonBox.StandardButton.Ok)

    dialog._output.setText(str(two_page_plan.parent / "chapter.cbz"))
    assert "inside the source directory" in dialog.refusal()
    assert not ok.isEnabled(), "the one refusal with no way past it, file or folder"

    dialog._output.setText(str(two_page_plan.parent.parent / "chapter.cbz"))
    assert dialog.refusal() == ""
    assert ok.isEnabled()


def test_a_chapter_file_that_is_really_a_directory_is_refused(
    qapp: object, two_page_plan: Path, tmp_path: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = RenderDialog(window.document, window)  # type: ignore[arg-type]
    (tmp_path / "chapter.cbz").mkdir()

    dialog._output.setText(str(tmp_path / "chapter.cbz"))

    assert "is a directory, not a file" in dialog.refusal()


def test_the_dialog_says_a_cbr_cannot_be_written_before_a_page_is_rendered(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chapter is minutes of work; this answer costs a keystroke."""
    monkeypatch.delenv("COMICTRANS_RAR", raising=False)
    monkeypatch.setattr("comictrans.pack.shutil.which", lambda name: None)
    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = RenderDialog(window.document, window)  # type: ignore[arg-type]
    ok = dialog._buttons.button(QDialogButtonBox.StandardButton.Ok)

    dialog._container.setCurrentIndex(dialog._container.findData(".cbr"))

    assert dialog.refusal() == NO_RAR_HERE, (
        "the window's own sentence, not the pipeline's: a dialog has a field "
        "for this and no business naming an environment variable"
    )
    assert "COMICTRANS_RAR" not in dialog.refusal()
    assert not ok.isEnabled()

    dialog._container.setCurrentIndex(dialog._container.findData(".cbz"))
    assert dialog.refusal() == "", "the format that needs nothing"


@pytest.mark.parametrize("policy", GROWTH_POLICIES)
def test_the_dialog_grows_to_hold_what_it_has_to_say(
    qapp: object,
    two_page_plan: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy: QFormLayout.FieldGrowthPolicy,
) -> None:
    """Every wrapped message gets the height it needs, in every state.

    A dialog that stays the height it opened at does not refuse to show a
    refusal — it takes the height out of the other wrapped labels, and they
    come out clipped with their last lines missing.

    Both growth policies, and that is the point rather than thoroughness:
    ``FieldsStayAtSizeHint`` is what ``QMacStyle`` uses and nothing else
    does, so the first version of this test passed on the one arrangement
    where the bug was not.
    """
    monkeypatch.delenv("COMICTRANS_RAR", raising=False)
    monkeypatch.setattr("comictrans.pack.shutil.which", lambda name: None)
    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = RenderDialog(window.document, window)  # type: ignore[arg-type]
    _set_growth_policy(dialog, policy)
    dialog.show()

    for suffix in (None, ".cbz", ".cbr", None):
        dialog._container.setCurrentIndex(dialog._container.findData(suffix))
        for strategy in ("flat", "inpaint"):
            dialog._erase.setCurrentIndex(dialog._erase.findData(strategy))
            for path in (two_page_plan.parent, two_page_plan.parent.parent / "out"):
                dialog._output.setText(str(path))
                for width in (420, 520, 760):
                    dialog.resize(width, dialog.height())
                    QApplication.processEvents()
                    _assert_nothing_is_clipped(
                        {"erase": dialog._erase_help, "refusal": dialog._problem},
                        f"{policy.name}, {suffix}, {strategy}, {path.name}, {width}px",
                    )


@pytest.mark.parametrize("policy", GROWTH_POLICIES)
def test_the_preferences_dialog_holds_its_notes_too(
    qapp: object, policy: QFormLayout.FieldGrowthPolicy
) -> None:
    """Three wrapped notes down one form, and the same trap under each."""
    dialog = PreferencesDialog(Preferences())
    _set_growth_policy(dialog, policy)
    dialog.show()

    notes = {
        "unrar": dialog._unrar_note,
        "rar": dialog._rar_note,
        "language": dialog._language_note,
    }
    for width in (520, 640, 900):
        dialog.resize(width, dialog.height())
        QApplication.processEvents()
        _assert_nothing_is_clipped(notes, f"{policy.name} at {width}px")
        widths = {name: note.width() for name, note in notes.items()}
        assert len(set(widths.values())) == 1, (
            f"the notes wrap at different widths ({widths}) — in the field "
            f"column each is given its own size hint's width, and a wrapped "
            f"label's hint width depends on how much text it holds"
        )


@pytest.mark.parametrize("policy", GROWTH_POLICIES)
def test_a_field_is_at_least_as_wide_as_the_hint_written_in_it(
    qapp: object, policy: QFormLayout.FieldGrowthPolicy
) -> None:
    """ "same as the sourc…" is not a hint, and it is what a default width gives.

    Under both policies and at the narrowest the window goes: a field that
    grows to fill a wide dialog fits its placeholder whether or not anything
    asked it to, which is exactly the arrangement macOS does not use.
    """
    dialog = PreferencesDialog(Preferences())
    _set_growth_policy(dialog, policy)
    dialog.show()
    dialog.resize(dialog.minimumWidth(), dialog.height())
    QApplication.processEvents()

    for name, field in (
        ("OCR languages", dialog._ocr_languages.line_edit()),
        ("output", dialog._output),
        ("unrar", dialog._unrar_tool),
        ("rar", dialog._rar_tool),
    ):
        placeholder = field.placeholderText()
        assert placeholder, name
        needed = field.fontMetrics().horizontalAdvance(placeholder)
        assert field.width() >= needed, f"{name} elides its own placeholder"


def test_a_dialog_somebody_has_made_taller_stays_that_way(
    qapp: object, two_page_plan: Path
) -> None:
    """Growing to fit must not turn into resizing on every keystroke."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = RenderDialog(window.document, window)  # type: ignore[arg-type]
    dialog.show()
    QApplication.processEvents()
    dialog.resize(dialog.width(), dialog.height() + 120)
    QApplication.processEvents()
    taller = dialog.height()

    dialog._output.setText(str(two_page_plan.parent))  # refused: the message appears
    QApplication.processEvents()
    dialog._output.setText(str(two_page_plan.parent.parent / "out"))  # and goes again
    QApplication.processEvents()

    assert dialog.height() == taller, "a message coming and going did not undo the drag"


def test_a_rar_path_that_does_not_work_says_which_path(
    qapp: object, two_page_plan: Path, tmp_path: Path
) -> None:
    """The window's own sentence replaces one message, not every message.

    "there is no rar here" it can say better, because it knows about the
    field. "the path you gave is a folder" it cannot: the useful half of that
    is the path, and this is not the place to reword it.
    """
    window = MainWindow()
    window.open_plan(two_page_plan)
    (tmp_path / "not-a-program").mkdir()
    dialog = RenderDialog(
        window.document,  # type: ignore[arg-type]
        window,
        preferences=Preferences(rar_tool=str(tmp_path / "not-a-program")),
    )

    dialog._container.setCurrentIndex(dialog._container.findData(".cbr"))

    assert "is not a program this can run" in dialog.refusal()
    assert str(tmp_path / "not-a-program") in dialog.refusal(), "which path it was"
    assert dialog.refusal() != NO_RAR_HERE, "a different problem, a different answer"
    assert "COMICTRANS_RAR" not in dialog.refusal()


def test_the_preferences_rar_tool_is_what_the_dialog_asks_about_and_hands_on(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []
    monkeypatch.setattr("comictrans.pack.rar_compressor", lambda named="": asked.append(named))
    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = RenderDialog(
        window.document,  # type: ignore[arg-type]
        window,
        preferences=Preferences(rar_tool="/opt/bin/rar"),
    )

    dialog._container.setCurrentIndex(dialog._container.findData(".cbr"))

    assert asked == ["/opt/bin/rar"], "the same tool it is about to render with"
    assert dialog.request().rar_tool == "/opt/bin/rar"


def test_the_overwrite_checkbox_says_what_it_would_overwrite(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = RenderDialog(window.document, window)  # type: ignore[arg-type]
    assert "directory" in dialog._force.text()

    dialog._container.setCurrentIndex(dialog._container.findData(".cbz"))

    assert "chapter file" in dialog._force.text()
    assert "directory" not in dialog._force.text()


def test_an_empty_field_with_a_chapter_file_chosen_asks_for_a_name(
    qapp: object, two_page_plan: Path
) -> None:
    """The one case where there is nothing to rename.

    ``Path("")`` is ``Path(".")``, whose ``name`` is empty, and ``with_name``
    raises on it — so choosing a container with the field cleared has to leave
    it cleared rather than filling it with a dot or falling over.
    """
    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = RenderDialog(window.document, window)  # type: ignore[arg-type]
    dialog._output.setText("   ")
    assert "Choose a directory" in dialog.refusal(), "cleared, it is a folder again"

    dialog._container.setCurrentIndex(dialog._container.findData(".cbz"))

    assert dialog._output.text().strip() == "", "nothing was invented to rename"
    assert "Name the chapter file" in dialog.refusal()
    assert not dialog._buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()

    dialog._output.setText(str(two_page_plan.parent.parent / "chapter.cbz"))
    assert dialog.refusal() == ""


@pytest.mark.parametrize("nameless", ["", ".", "/"])
def test_a_path_with_no_name_of_its_own_is_handed_back_unchanged(nameless: str) -> None:
    """``with_name`` raises on all three, and every one is typeable.

    The renaming helper directly, not through the box: an exception inside a
    Qt slot is printed and swallowed, so the widget would look the same
    either way and the test would prove nothing.
    """
    assert RenderDialog._renamed(Path(nameless), ".cbz") == Path(nameless)
    assert RenderDialog._renamed(Path(nameless), None) == Path(nameless)


def test_the_choose_button_opens_a_save_panel_for_a_chapter_file(
    qapp: object, two_page_plan: Path
) -> None:
    """A folder panel cannot name a file that does not exist yet."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = RenderDialog(window.document, window)  # type: ignore[arg-type]
    opened: list[str] = []

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            render_dialog.QFileDialog,
            "getExistingDirectory",
            lambda *args, **kwargs: (opened.append("directory"), "")[1],
        )
        patch.setattr(
            render_dialog.QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: (opened.append("save"), ("", ""))[1],
        )
        dialog._choose_button.click()
        dialog._container.setCurrentIndex(dialog._container.findData(".cbz"))
        dialog._choose_button.click()

    assert opened == ["directory", "save"]


def test_rendering_into_a_cbz_from_the_window_writes_one_file(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = RenderDialog(window.document, window)  # type: ignore[arg-type]
    dialog._container.setCurrentIndex(dialog._container.findData(".cbz"))
    request = dialog.request()

    _run_render(window, request)

    assert request.output.is_file()
    with zipfile.ZipFile(request.output) as packed:
        assert packed.namelist() == ["001-page-001.png", "002-page-002.png"]
    assert "packed" in window.statusBar().currentMessage()
    assert str(request.output) in window._run_panel._headline.text()


def test_the_compressor_the_dialog_was_given_is_the_one_the_run_uses(
    qapp: object, two_page_plan: Path, font_dir: Path, tmp_path: Path
) -> None:
    """All the way through: preference, request, job, ``pack``.

    Nothing redistributable writes RAR, so a shell script stands in for the
    compressor and records that it was the one asked. What is being tested is
    the path the setting travels, not the archive that comes out.
    """
    stub = tmp_path / "rar"
    # It reads what it is handed rather than touching the archive and
    # claiming success. A stub that never opens its inputs is how CBR output
    # shipped broken for a whole milestone — see tests/test_pack.py.
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys, zipfile\n"
        "archive, names = sys.argv[4], sys.argv[5:]\n"
        "missing = [n for n in names if not os.path.exists(n)]\n"
        "if missing:\n"
        "    print('cannot find: ' + ', '.join(missing), file=sys.stderr)\n"
        "    sys.exit(10)\n"
        "with zipfile.ZipFile(archive, 'a') as z:\n"
        "    for n in names:\n"
        "        z.write(n, n)\n"
    )
    stub.chmod(0o755)
    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = RenderDialog(
        window.document,  # type: ignore[arg-type]
        window,
        preferences=Preferences(rar_tool=str(stub)),
    )
    dialog._container.setCurrentIndex(dialog._container.findData(".cbr"))
    request = dialog.request()

    _run_render(window, request)

    assert request.output.is_file(), "the preference reached the compressor"
    with zipfile.ZipFile(request.output) as packed:
        assert packed.namelist() == ["001-page-001.png", "002-page-002.png"]


def test_a_cancelled_chapter_run_says_nothing_was_written_rather_than_none(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    """Zero pages is true and unhelpful: the promise is what needs saying."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = RenderDialog(window.document, window)  # type: ignore[arg-type]
    dialog._container.setCurrentIndex(dialog._container.findData(".cbz"))
    request = dialog.request()

    window._start_render(request)
    assert window._job is not None
    window._job.cancel()
    _await_run(window)

    assert not request.output.exists()
    assert "nothing written" in window._run_panel._headline.text()
    assert "nothing written" in window.statusBar().currentMessage()


# -- extracting pages from the window ------------------------------------


@contextmanager
def monkeypatched_panels(opened: list[str]) -> Iterator[None]:
    """Record which system panel a click asked for, without opening one.

    A real ``QFileDialog`` static call opens a native modal loop that nothing
    in an offscreen test will ever dismiss.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            extract_dialog.QFileDialog,
            "getExistingDirectory",
            lambda *args, **kwargs: opened.append("directory") or "",
        )
        patch.setattr(
            extract_dialog.QFileDialog,
            "getOpenFileName",
            lambda *args, **kwargs: (opened.append("file"), "", "")[1:],
        )
        yield


@pytest.fixture
def loose_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Three images and one file that is not one, with OCR stubbed out.

    ``get_recognizer`` is replaced where the job looks it up, so the test
    neither needs Apple Vision nor depends on whether the machine running it
    has Tesseract. What is being tested here is the window, not detection.
    """
    source = tmp_path / "scans"
    source.mkdir()
    boxes = [Box(160, 140, 360, 164), Box(160, 180, 340, 204)]
    for name in ("page-001.png", "page-002.png", "page-003.png"):
        save_page(
            make_page_array(
                (600, 800),
                ART_DARK,
                [("ellipse", Box(120, 100, 420, 260), BALLOON_WHITE, INK_BLACK, boxes)],
            ),
            source / name,
        )
    (source / "notes.txt").write_text("not a page", encoding="utf-8")

    lines = {
        name: lines_for(boxes, ["NON CI POSSO", "CREDERE!"])
        for name in ("page-001.png", "page-002.png")
    }
    lines["page-003.png"] = []  # a page with nothing on it
    monkeypatch.setattr(run_job, "get_recognizer", lambda config: FakeRecognizer(lines))
    return source


def test_a_button_each_for_the_two_panels_there_have_to_be(qapp: object) -> None:
    """Qt has no file dialog that accepts either — see the note on the buttons.

    Two buttons rather than one and a mode beside it: the mode never decided
    anything, since what the field accepts is decided by looking at the path.
    """
    dialog = ExtractDialog(None, None)
    opened: list[str] = []

    with monkeypatched_panels(opened):
        dialog._folder_button.click()
        dialog._file_button.click()

    assert opened == ["directory", "file"]


def test_either_kind_of_input_is_accepted_however_it_got_into_the_field(
    qapp: object, loose_pages: Path
) -> None:
    """The buttons only browse; the path itself decides what is accepted."""
    dialog = ExtractDialog(None, None)

    dialog._source.setText(str(loose_pages))
    assert dialog.refusal() == ""
    assert len(dialog.pages()) == 3

    dialog._source.setText(str(loose_pages / "page-001.png"))
    assert dialog.refusal() == ""
    assert dialog.pages() == (loose_pages / "page-001.png",)
    assert dialog.plan_path() == loose_pages / "page-001-plan.yaml"


def test_the_extract_dialog_puts_the_plan_where_the_command_line_would(
    qapp: object, loose_pages: Path
) -> None:
    dialog = ExtractDialog(None, None)
    dialog._source.setText(str(loose_pages))

    assert dialog.plan_path() == loose_pages / "comic-plan.yaml"
    assert dialog.refusal() == ""
    assert len(dialog.pages()) == 3, "the .txt is not one of them"


def test_a_plan_path_typed_by_hand_stops_following_the_input(
    qapp: object, loose_pages: Path, tmp_path: Path
) -> None:
    dialog = ExtractDialog(None, None)
    dialog._source.setText(str(loose_pages))
    chosen = tmp_path / "mine.yaml"
    dialog._plan.setText(str(chosen))
    dialog._plan.textEdited.emit(str(chosen))  # what typing into it does

    dialog._source.setText(str(loose_pages / "page-001.png"))

    assert dialog.plan_path() == chosen, "a path someone typed is not a default to overwrite"


def test_the_extract_dialog_refuses_an_input_with_nothing_to_read(
    qapp: object, tmp_path: Path
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    dialog = ExtractDialog(None, None)

    assert "Choose a folder" in dialog.refusal()

    dialog._source.setText(str(tmp_path / "nowhere"))
    assert "does not exist" in dialog.refusal()

    dialog._source.setText(str(empty))
    assert "no supported images found" in dialog.refusal()


def test_an_existing_plan_is_refused_until_overwriting_is_ticked(
    qapp: object, loose_pages: Path
) -> None:
    (loose_pages / "comic-plan.yaml").write_text("# already here\n", encoding="utf-8")
    dialog = ExtractDialog(None, None)
    dialog._source.setText(str(loose_pages))

    assert "already exists" in dialog.refusal()
    assert not dialog._ok.isEnabled()
    assert not dialog._force.isHidden(), "the box appears when there is something to overwrite"

    dialog._force.setChecked(True)

    assert dialog.refusal() == ""
    assert dialog.request().force


def test_the_dialog_asks_for_the_four_things_it_is_scoped_to(
    qapp: object, loose_pages: Path
) -> None:
    dialog = ExtractDialog(None, None)
    dialog._source.setText(str(loose_pages))
    dialog._source_language.setCurrentText("fr")
    dialog._target_language.setCurrentText("sv")
    dialog._languages.line_edit().setText("fr, pt-BR")
    dialog._engine.setCurrentIndex(dialog._engine.findData("tesseract"))

    request = dialog.request()

    assert request.source_language == "fr"
    assert request.target_language == "sv"
    assert request.config.ocr.languages == ("fr", "pt-BR")
    assert request.config.ocr.engine == "tesseract"
    assert request.font is None, "the header font is left to extract's own fallback chain"


def test_the_ocr_languages_default_to_the_source_language(qapp: object, loose_pages: Path) -> None:
    dialog = ExtractDialog(None, None)
    dialog._source.setText(str(loose_pages))
    dialog._source_language.setCurrentText("de")

    assert dialog.request().config.ocr.languages == ("de",)


def _extract(window: MainWindow, request: ExtractRequest) -> None:
    job = ExtractJob(request, window)
    job.completed.connect(window._on_extract_finished)
    window._run_panel.start_extract(request.total, request.plan_path)
    window._start(job, f"reading {request.total} page(s)")
    _await_run(window)


def test_extracting_writes_a_plan_and_opens_it(
    qapp: object, loose_pages: Path, font_dir: Path
) -> None:
    window = MainWindow()
    dialog = ExtractDialog(None, window)
    dialog._source.setText(str(loose_pages))
    request = dialog.request()
    before = {path.name: sha256_file(path) for path in loose_pages.glob("*.png")}

    _extract(window, request)

    assert request.plan_path.is_file()
    assert window.document is not None
    assert window.document.path == request.plan_path, "the window is now on what it just wrote"
    assert window._pages.count() == 3
    assert {path.name: sha256_file(path) for path in loose_pages.glob("*.png")} == before


def test_a_finished_extract_reports_the_pages_the_plan_cannot_speak_for(
    qapp: object, loose_pages: Path, font_dir: Path
) -> None:
    window = MainWindow()
    dialog = ExtractDialog(None, window)
    dialog._source.setText(str(loose_pages))

    _extract(window, dialog.request())

    panel = window._run_panel
    assert "3 pages read into" in panel._headline.text()
    assert not window._run_dock.isHidden()
    # page-003 came back empty; notes.txt was never a page.
    assert panel.row_count() == 2
    assert window._extract_action.isEnabled(), "the run is over"


def test_a_row_naming_a_page_selects_that_page(
    qapp: object, loose_pages: Path, font_dir: Path
) -> None:
    window = MainWindow()
    dialog = ExtractDialog(None, window)
    dialog._source.setText(str(loose_pages))
    _extract(window, dialog.request())
    assert window._current_image == "page-001.png"

    window._run_panel.select_row(0)

    assert window._current_image == "page-003.png", "the page that came back empty"


def test_a_row_for_something_that_never_became_a_page_goes_nowhere(
    qapp: object, loose_pages: Path, font_dir: Path
) -> None:
    """notes.txt is not in the plan, so its row is inert rather than lying."""
    window = MainWindow()
    dialog = ExtractDialog(None, window)
    dialog._source.setText(str(loose_pages))
    _extract(window, dialog.request())
    assert window._current_image == "page-001.png"

    window._run_panel.select_row(1)  # notes.txt

    assert window._current_image == "page-001.png", "nothing to go to, so nothing moved"


def test_a_cancelled_extract_writes_no_plan_and_opens_nothing(
    qapp: object, loose_pages: Path, font_dir: Path
) -> None:
    """Half a chapter is not a plan file — see ExtractJob."""
    window = MainWindow()
    dialog = ExtractDialog(None, window)
    dialog._source.setText(str(loose_pages))
    request = dialog.request()

    job = ExtractJob(request, window)
    job.completed.connect(window._on_extract_finished)
    job.cancel()  # before it starts, so no page is read at all
    window._run_panel.start_extract(request.total, request.plan_path)
    window._start(job, f"reading {request.total} page(s)")
    _await_run(window)

    assert not request.plan_path.exists()
    assert window.document is None, "there is nothing to open"
    assert "was not written" in window._run_panel._headline.text()


def test_a_run_with_no_recogniser_says_so_and_writes_nothing(
    qapp: object, loose_pages: Path, font_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(config: object) -> object:
        raise OcrUnavailableError("no OCR backend available")

    monkeypatch.setattr(run_job, "get_recognizer", refuse)
    window = MainWindow()
    dialog = ExtractDialog(None, window)
    dialog._source.setText(str(loose_pages))
    request = dialog.request()

    _extract(window, request)

    assert "no OCR backend available" in window._run_panel._headline.text()
    assert not request.plan_path.exists()
    assert window._extract_action.isEnabled()


def test_extract_needs_no_open_plan_and_render_does(qapp: object) -> None:
    window = MainWindow()

    assert window._extract_action.isEnabled(), "it is how you get a plan in the first place"
    assert not window._render_action.isEnabled()


# -- preferences ---------------------------------------------------------
#
# What a preference *is* — load, save, fall back — is tested without Qt in
# test_gui_preferences.py. What is left for here is the part that only shows
# up in a window: that the two run dialogs actually open holding these
# values, and that nothing here reaches a plan.


def _preferences(**overrides: str) -> Preferences:
    base: dict[str, str] = {
        "source_language": "ja",
        "target_language": "sv",
        "ocr_languages": "ja, en",
        "ocr_engine": "tesseract",
        "font": "Marker Felt",
        "erase_strategy": "inpaint",
        "image_format": "tiff",
    }
    base.update(overrides)
    return Preferences(**base)


def test_the_preferences_dialog_says_done_rather_than_close(qapp: object) -> None:
    """Nothing is being closed away: each field wrote through as it was edited.

    "Close" reads like a button that might be discarding something, and
    Save and Cancel would read even more wrongly — there is no pending edit
    for either of them to act on. The behaviour was never the problem here,
    only the word.
    """
    from PySide6.QtWidgets import QDialogButtonBox

    from comictrans.gui.preferences_dialog import DONE_TEXT

    dialog = PreferencesDialog(Preferences(), None)
    box = dialog.findChild(QDialogButtonBox)
    assert box is not None

    buttons = box.buttons()
    assert [button.text() for button in buttons] == [DONE_TEXT], "one button, and it says Done"
    assert box.buttonRole(buttons[0]) == QDialogButtonBox.ButtonRole.AcceptRole, (
        "finishing with the form is the affirmative answer, and puts Return on the button"
    )


def test_the_preferences_dialog_writes_through_as_it_is_edited(qapp: object) -> None:
    dialog = PreferencesDialog(Preferences(), None)
    seen: list[Preferences] = []
    dialog.changed.connect(lambda: seen.append(dialog.preferences()))

    dialog._source_language.setCurrentText("de")
    dialog._erase.setCurrentIndex(dialog._erase.findData("polygon"))

    assert len(seen) >= 2, "each field is its own edit; there is nothing to apply"
    assert seen[-1].source_language == "de"
    assert seen[-1].erase_strategy == "polygon"


def test_the_preferences_dialog_shows_what_it_was_given(qapp: object, font_dir: Path) -> None:
    dialog = PreferencesDialog(_preferences(), None)

    assert dialog._source_language.value() == "ja"
    assert dialog._engine.currentData() == "tesseract"
    assert dialog._font.value() == "Marker Felt"
    assert dialog._format.currentData() == "tiff"
    assert dialog.preferences() == _preferences()


def test_where_unrar_is_can_be_said_here_and_is_said_nowhere_else(qapp: object) -> None:
    """The one field about this machine rather than about comics.

    It is in this dialog because an application opened from the Finder does
    not inherit the shell's PATH: a Homebrew unrar works on the command line
    and is invisible to the window, which is not something a per-run dialog
    should be asking about.
    """
    from comictrans.gui.preferences_dialog import RAR_NOTE

    dialog = PreferencesDialog(Preferences(), None)
    assert dialog._unrar_tool.text() == ""
    assert "PATH" in dialog._unrar_tool.placeholderText() or dialog._unrar_tool.placeholderText()
    assert "licence" in RAR_NOTE, "why nothing ships is the part worth saying"

    dialog._unrar_tool.setText("  /opt/homebrew/bin/unrar  ")

    assert dialog.preferences().unrar_tool == "/opt/homebrew/bin/unrar"


def test_experimental_features_is_off_and_writes_through(qapp: object) -> None:
    """A flag with nothing behind it yet — see ``Preferences.experimental``."""
    dialog = PreferencesDialog(Preferences(), None)
    seen: list[Preferences] = []
    dialog.changed.connect(lambda: seen.append(dialog.preferences()))

    assert not dialog._experimental.isChecked(), "off until somebody says otherwise"
    assert dialog.preferences().experimental == ""

    dialog._experimental.setChecked(True)

    assert seen and seen[-1].experimental_on
    assert seen[-1].experimental == ON, "one of two words, not a bool"

    dialog._experimental.setChecked(False)

    assert not seen[-1].experimental_on
    assert seen[-1].experimental == ""


def test_the_dialog_shows_a_switch_that_was_already_on(qapp: object) -> None:
    dialog = PreferencesDialog(Preferences(experimental=ON), None)

    assert dialog._experimental.isChecked()

    dialog.repopulate(Preferences())

    assert not dialog._experimental.isChecked()
    assert dialog.preferences().experimental == "", "and showing it is not an edit"


def test_an_unset_font_is_not_called_the_plan_default_here(qapp: object) -> None:
    """There is no plan in this dialog; what happens instead is extract's chain."""
    dialog = PreferencesDialog(Preferences(), None)

    assert dialog._font.currentText() == FONT_DEFAULT
    assert dialog.preferences().font == ""


def test_choosing_a_language_says_it_waits_for_a_restart(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The setting is remembered now and read at the next start.

    Said in an alert as well as in the standing note under the field: a
    setting that visibly does nothing is a setting somebody presses twice.

    The environment variable is taken away first, because the suite sets it
    and it beats this setting by design — with it in place, choosing Swedish
    here would change nothing and correctly say nothing.
    """
    from comictrans.gui import translations

    monkeypatch.delenv(translations.LANGUAGE_ENV, raising=False)
    shown = _catch_alerts(monkeypatch)
    window = MainWindow()

    window._on_preferences_changed(replace(window._preferences, language="sv"))

    assert len(shown) == 1
    assert "Svenska" in shown[0].text()
    assert window._preferences.language == "sv", "remembered whatever the alert said"
    assert translations.current() == "en", "and the running window has not moved"


def test_a_language_change_that_changes_no_language_says_nothing(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two cases that are not a change: the same value, and the same result.

    The second is the one worth the code. The suite runs with English forced,
    so choosing English explicitly is a different setting with an identical
    effect — and an alert announcing a restart to reach the language already
    on screen would be worse than silence.
    """
    from comictrans.gui import translations

    shown = _catch_alerts(monkeypatch)
    window = MainWindow()

    window._on_preferences_changed(replace(window._preferences, ocr_engine="tesseract"))
    window._on_preferences_changed(replace(window._preferences, language=translations.current()))

    assert shown == []


def test_the_dialog_carries_the_remembered_directory_through_untouched(qapp: object) -> None:
    """It is remembered, not chosen, so the dialog never shows or clears it."""
    dialog = PreferencesDialog(_preferences(last_directory="/tmp/somewhere"), None)
    dialog._target_language.setCurrentText("fr")

    assert dialog.preferences().last_directory == "/tmp/somewhere"


def test_the_render_dialog_opens_holding_the_preferences(
    qapp: object, two_page_plan: Path, tmp_path: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    elsewhere = tmp_path / "somewhere-else"

    dialog = RenderDialog(
        window.document,  # type: ignore[arg-type]
        window,
        preferences=_preferences(output_directory=str(elsewhere)),
    )

    assert dialog.output_dir() == elsewhere
    request = dialog.request()
    assert request.config.erase.strategy == "inpaint"
    assert request.image_format == "tiff"


def test_a_preferred_output_directory_is_still_refused_inside_the_source_tree(
    qapp: object, two_page_plan: Path
) -> None:
    """A stored preference gets no more trust than a path typed by hand."""
    window = MainWindow()
    window.open_plan(two_page_plan)

    dialog = RenderDialog(
        window.document,  # type: ignore[arg-type]
        window,
        preferences=_preferences(output_directory=str(two_page_plan.parent)),
    )

    assert "inside the source directory" in dialog.refusal()
    assert not dialog._ok.isEnabled()


def test_the_extract_dialog_opens_holding_the_preferences(
    qapp: object, loose_pages: Path, font_dir: Path
) -> None:
    dialog = ExtractDialog(None, None, preferences=_preferences())
    dialog._source.setText(str(loose_pages))

    request = dialog.request()

    assert request.source_language == "ja"
    assert request.target_language == "sv"
    assert request.config.ocr.languages == ("ja", "en")
    assert request.config.ocr.engine == "tesseract"
    assert request.font == "Marker Felt", "recorded in the header of the plan it writes"


def test_no_preferred_font_leaves_extract_to_its_own_fallback_chain(
    qapp: object, loose_pages: Path
) -> None:
    dialog = ExtractDialog(None, None, preferences=Preferences())
    dialog._source.setText(str(loose_pages))

    assert dialog.request().font is None


def test_a_preference_never_reaches_a_plan_already_open(qapp: object, two_page_plan: Path) -> None:
    """The one thing this milestone must not do. 4.12 edits this plan; 4.13
    decides what a new one starts from."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    header_before = window.document.plan.header  # type: ignore[union-attr]
    file_before = two_page_plan.read_text(encoding="utf-8")

    window._on_preferences_changed(_preferences(font="Comic Sans MS"))

    assert window.document.plan.header == header_before  # type: ignore[union-attr]
    assert not window.document.dirty  # type: ignore[union-attr]
    assert two_page_plan.read_text(encoding="utf-8") == file_before


def test_the_window_remembers_where_the_last_plan_came_from(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    assert window._preferences.last_directory == ""

    window.open_plan(two_page_plan)

    assert window._preferences.last_directory == str(two_page_plan.parent)
    assert window._start_directory() == two_page_plan.parent


def test_a_window_with_no_plan_starts_where_it_was_last(qapp: object, tmp_path: Path) -> None:
    window = MainWindow()
    window._preferences = Preferences(last_directory=str(tmp_path))

    assert window._start_directory() == tmp_path


def test_a_window_without_settings_stores_nothing(qapp: object) -> None:
    """Every widget test builds one, and none of them may leak into the next."""
    window = MainWindow()
    assert window._settings is None

    window._on_preferences_changed(_preferences())

    assert window._preferences.source_language == "ja", "held, but only in this window"


# -- when something goes wrong -------------------------------------------
#
# The log file, the hooks and what must never reach them are tested without
# Qt in test_gui_logfile.py. What is left for here is what the window itself
# does: say so rather than silently doing nothing.


def test_an_expected_failure_says_what_to_do_about_it(qapp: object) -> None:
    window = MainWindow()

    window._report_failure("saving", InputError("the disk is full"))

    assert "the disk is full" in window.statusBar().currentMessage()


def test_a_bug_says_its_name_and_points_at_the_log(qapp: object) -> None:
    """A traceback belongs in a file; a status bar gets the type and a pointer."""
    window = MainWindow()

    window._report_failure("rendering", ValueError("index out of range"))

    message = window.statusBar().currentMessage()
    assert "ValueError" in message
    assert "see the log" in message
    assert "index out of range" not in message, "that is what the log is for"


def test_the_window_hears_about_an_exception_nobody_caught(qapp: object, tmp_path: Path) -> None:
    """PySide6 prints these and carries on, so without this nothing is said."""
    window = MainWindow()
    logfile.install(tmp_path)
    logfile.install_hooks()
    try:
        try:
            raise RuntimeError("in a slot")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())  # type: ignore[arg-type]
        assert "RuntimeError" in window.statusBar().currentMessage()
        assert "see the log" in window.statusBar().currentMessage()
    finally:
        logfile.uninstall()
        logfile.set_notifier(None)


def test_closing_the_window_stops_it_being_notified(qapp: object) -> None:
    """The next window registers itself; a closed one must not still be listening."""
    window = MainWindow()
    window.close()

    assert logfile._notify is None


def test_rescanning_fonts_reports_a_filesystem_failure_rather_than_vanishing(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MainWindow()

    def unreadable() -> tuple[str, ...]:
        raise OSError("permission denied")

    monkeypatch.setattr(fonts, "available_families", unreadable)
    monkeypatch.setattr(fonts, "forget_available_families", lambda: None)

    window._on_rescan_fonts()

    assert "rescanning fonts" in window.statusBar().currentMessage()


def test_the_help_menu_offers_the_log_folder(qapp: object, tmp_path: Path) -> None:
    window = MainWindow()

    assert window._open_logs_action.text() == "Open &Log Folder"


def test_opening_the_log_folder_makes_it_first(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder nothing has written to yet still has to open."""
    target = tmp_path / "not-there-yet"
    monkeypatch.setenv(logfile.LOG_DIR_ENV, str(target))
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda _url: True)
    window = MainWindow()

    window._on_open_logs()

    assert target.is_dir()


def test_a_desktop_that_will_not_open_it_still_says_where_it_is(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(logfile.LOG_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda _url: False)
    window = MainWindow()

    window._on_open_logs()

    assert str(tmp_path) in window.statusBar().currentMessage()


# -- a chapter that arrives as one file ---------------------------------


@pytest.fixture
def chapter_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Two pages and a stray note in a .cbz, with OCR stubbed out.

    The recogniser is keyed by the names the pages take once unpacked, which
    is the point: what the window reads is the folder the chapter becomes.
    """
    import zipfile

    boxes = [Box(160, 140, 360, 164), Box(160, 180, 340, 204)]
    raw = save_page(
        make_page_array(
            (600, 800),
            ART_DARK,
            [("ellipse", Box(120, 100, 420, 260), BALLOON_WHITE, INK_BLACK, boxes)],
        ),
        tmp_path / "raw.png",
    )
    chapter = tmp_path / "chapter.cbz"
    with zipfile.ZipFile(chapter, "w") as handle:
        handle.writestr("Ch/page-001.png", raw.read_bytes())
        handle.writestr("Ch/page-002.png", raw.read_bytes()[:-1] + b"\x00")
        handle.writestr("Ch/notes.txt", "not a page")
    raw.unlink()

    lines = {
        name: lines_for(boxes, ["NON CI POSSO", "CREDERE!"])
        for name in ("001-page-001.png", "002-page-002.png")
    }
    monkeypatch.setattr(run_job, "get_recognizer", lambda config: FakeRecognizer(lines))
    return chapter


def test_the_file_panel_offers_pages_and_chapters_alike(qapp: object) -> None:
    """One panel for both, because both are one file to open.

    And "All files" on the end of it, for the chapter saved under a name
    nobody uses: the run reads what a file is rather than what it is called,
    and a file panel can only filter on the name.
    """
    dialog = ExtractDialog(None, None)
    filters: list[str] = []

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            extract_dialog.QFileDialog,
            "getOpenFileName",
            lambda *args, **kwargs: (filters.append(args[3]), "", "")[1:],
        )
        dialog._file_button.click()

    offered = filters[0]
    for suffix in (".cbz", ".cbr", ".pdf", ".png", ".jpg", ".tiff"):
        assert f"*{suffix}" in offered, suffix
    assert "All files (*)" in offered


def _right_edge(dialog: ExtractDialog, widget: QWidget) -> int:
    """Where a field ends, in the dialog's own coordinates."""
    return widget.mapTo(dialog, widget.rect().topRight()).x()


def test_the_hint_under_the_field_is_read_in_every_language_and_on_a_mac(
    qapp: object, tmp_path: Path
) -> None:
    """One line of it, on screen, whatever the style does with a form's fields.

    Both ways this has been wrong were the layout being clever. Wrapping it
    makes a QLabel report a height from a guess at its own shape rather than
    from the width it is given, so the row is laid out a line short and the
    rest is drawn under the row below. An Ignored width policy makes it
    report a width of nought — and a form on macOS leaves a field at its size
    hint instead of growing it to the column, so nought is what it got, and
    the hint said nothing at all. This runs the layout both ways round,
    because the platform this ships on is the one it was invisible on.
    """
    from PySide6.QtCore import QTranslator
    from PySide6.QtWidgets import QFormLayout

    from comictrans.gui import translations

    archive = tmp_path / "chapter.cbr"
    archive.write_bytes(b"Rar!\x1a\x07\x00 pretend")
    policies = (
        QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow,
        QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint,  # what macOS does
    )

    for code in ("", *translations.available()):
        translator = QTranslator()
        if code:
            assert translator.load(
                f"{translations.PREFIX}_{code}", str(translations.TRANSLATION_DIR)
            )
            qapp.installTranslator(translator)  # type: ignore[attr-defined]
        try:
            for policy in policies:
                dialog = ExtractDialog(None, None)
                form = dialog.findChild(QFormLayout)
                assert form is not None
                form.setFieldGrowthPolicy(policy)
                dialog.show()
                dialog._source.setText(str(archive))
                QApplication.processEvents()
                where = f"{code or 'en'}/{policy.name}"

                hint = dialog._count
                assert hint.text(), f"{where}: the state this is about — a chapter, and a hint"
                assert hint.width() >= hint.sizeHint().width(), f"{where}: the hint is cut off"
                assert hint.height() == hint.fontMetrics().height(), f"{where}: not one line"
                for button in (dialog._folder_button, dialog._file_button):
                    assert button.width() >= button.sizeHint().width(), (
                        f"{where}: {button.text()!r} is clipped"
                    )

                # The two fields end together, which is what the buttons are
                # matched in width for and what the plan row is padded for.
                assert _right_edge(dialog, dialog._source) == _right_edge(dialog, dialog._plan), (
                    f"{where}: the two fields do not end at the same place"
                )

                # And a longer hint than any language has costs nothing but
                # its own tail: wrapped, it would cost the row a second line
                # that the row has not been given.
                dialog._count.setText("x " * 80)
                QApplication.processEvents()
                assert dialog._count.height() == dialog._count.fontMetrics().height(), (
                    f"{where}: the hint grew a second line"
                )
                dialog.close()
        finally:
            if code:
                qapp.removeTranslator(translator)  # type: ignore[attr-defined]


def test_a_chapter_file_is_accepted_and_its_plan_goes_with_its_pages(
    qapp: object, chapter_file: Path, tmp_path: Path
) -> None:
    dialog = ExtractDialog(None, None)

    dialog._source.setText(str(chapter_file))

    assert dialog.refusal() == ""
    assert dialog.pages() == ()
    assert "counted" in dialog._count.text()
    assert dialog.plan_path() == tmp_path / "chapter-pages" / "comic-plan.yaml", (
        "the plan belongs with the pages, which are not beside the chapter file"
    )
    assert dialog.request().source == chapter_file


def test_a_chapter_is_never_read_to_answer_a_keystroke(
    qapp: object, chapter_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Counting the pages inside one would mean opening it on every keystroke.

    An archive's member list is cheap and a PDF's page tree is not, and a
    .cbr would start a subprocess. The pages are counted when they are
    unpacked instead, and the panel is told the total then.
    """

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the chapter was opened to answer a keystroke")

    monkeypatch.setattr(extract_dialog, "collect_inputs", refuse)
    dialog = ExtractDialog(None, None)

    dialog._source.setText(str(chapter_file))

    assert dialog.pages() == ()
    assert dialog.refusal() == ""


def test_a_cbr_with_nothing_to_open_it_is_refused_before_the_run(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_tool(named: str = "") -> None:
        raise InputError("CBR needs a RAR tool and none was found")

    monkeypatch.setattr("comictrans.sources._unrar_tool", no_tool)
    archive = tmp_path / "chapter.cbr"
    archive.write_bytes(b"Rar!\x1a\x07\x00 pretend")
    dialog = ExtractDialog(None, None)

    dialog._source.setText(str(archive))

    refusal = dialog.refusal()
    assert "CBR needs a RAR tool" in refusal
    assert "Preferences" in refusal, (
        "the pipeline's message names an environment variable, which is the "
        "command line's answer and no use to somebody reading a window"
    )


def test_the_preferences_unrar_tool_reaches_the_run(qapp: object, tmp_path: Path) -> None:
    archive = tmp_path / "chapter.cbz"
    archive.write_bytes(b"not read by this test")
    dialog = ExtractDialog(None, None, preferences=Preferences(unrar_tool="/opt/bin/unrar"))

    dialog._source.setText(str(archive))

    assert dialog.request().unrar_tool == "/opt/bin/unrar"


def test_the_panel_says_it_is_unpacking_until_the_pages_are_counted(qapp: object) -> None:
    window = MainWindow()
    panel = window._run_panel

    panel.start_unpack(Path("/comics/chapter.cbz"))

    assert "chapter.cbz" in panel._headline.text()
    assert "0" not in panel._headline.text(), (
        "it says what it is doing rather than claiming a page count nobody has"
    )
    assert panel._progress.maximum() == 0, "a bar with no total, which Qt draws as busy"

    panel.start_extract(7, Path("/comics/chapter-pages/comic-plan.yaml"))

    assert "7" in panel._headline.text()


def test_where_unrar_is_reaches_the_unpacking(
    qapp: object, chapter_file: Path, font_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preference is no use unless it arrives where the tool is looked for."""
    from comictrans.sources import UnpackReport, unpack

    seen: list[str] = []

    def recorded(source: Path, into: Path | None = None, **kwargs: object) -> UnpackReport:
        seen.append(str(kwargs["unrar_tool"]))
        return unpack(source, into, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(run_job, "unpack", recorded)
    dialog = ExtractDialog(None, None, preferences=Preferences(unrar_tool="/opt/bin/unrar"))
    dialog._source.setText(str(chapter_file))
    job = ExtractJob(dialog.request(), None)

    job.work(lambda progress: None, lambda: False)

    assert seen == ["/opt/bin/unrar"]


def test_the_dialog_asks_about_the_unrar_tool_it_would_hand_to_the_run(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asked about before the run, with the same answer the run would use."""
    asked: list[str] = []
    monkeypatch.setattr("comictrans.sources._unrar_tool", lambda named="": asked.append(named))
    archive = tmp_path / "chapter.cbr"
    archive.write_bytes(b"Rar!\x1a\x07\x00 pretend")
    dialog = ExtractDialog(None, None, preferences=Preferences(unrar_tool="/opt/bin/unrar"))

    dialog._source.setText(str(archive))

    assert dialog.refusal() == ""
    assert set(asked) == {"/opt/bin/unrar"}
    assert asked, "asked once for the keystroke and once for this call, which is the cost of it"


def test_stopping_while_a_chapter_unpacks_reads_none_of_it(
    qapp: object, chapter_file: Path, font_dir: Path
) -> None:
    """A cancelled run writes no plan, and does not go on to read what it has."""
    dialog = ExtractDialog(None, None)
    dialog._source.setText(str(chapter_file))
    request = dialog.request()
    job = ExtractJob(request, None)

    report = job.work(lambda progress: None, lambda: True)

    assert report.cancelled
    assert report.pages_read == 0
    assert not request.plan_path.exists()


def test_the_panel_is_told_the_total_once_the_chapter_has_been_counted(
    qapp: object, chapter_file: Path
) -> None:
    window = MainWindow()
    dialog = ExtractDialog(None, window)
    dialog._source.setText(str(chapter_file))
    window._job = ExtractJob(dialog.request(), window)
    window._run_panel.start_unpack(chapter_file)

    window._on_unpacked(7)

    assert "7" in window._run_panel._headline.text()
    assert "7" in window.statusBar().currentMessage()
    assert "chapter-pages" in window._run_panel._headline.text(), "and where the plan is going"
    window._job = None


def test_the_window_reads_a_chapter_file_from_end_to_end(
    qapp: object,
    chapter_file: Path,
    font_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole path: pick a chapter, unpack it, read it, open the plan."""
    window = MainWindow()

    def prepared(start: Path | None, parent: object, **kwargs: object) -> ExtractDialog:
        dialog = ExtractDialog(start, window, **kwargs)  # type: ignore[arg-type]
        dialog._source.setText(str(chapter_file))
        return dialog

    monkeypatch.setattr(main_window.ExtractDialog, "exec", lambda self: 1)
    monkeypatch.setattr(main_window, "ExtractDialog", prepared)
    counted: list[int] = []
    monkeypatch.setattr(MainWindow, "_on_unpacked", lambda self, total: counted.append(total))

    window._on_extract()
    assert "unpacking chapter.cbz" in window.statusBar().currentMessage()
    assert "chapter.cbz" in window._run_panel._headline.text()
    assert "0" not in window._run_panel._headline.text(), "no page count has been taken yet"
    _await_run(window)

    pages = tmp_path / "chapter-pages"
    assert sorted(path.name for path in pages.glob("*.png")) == [
        "001-page-001.png",
        "002-page-002.png",
    ]
    assert counted == [2], "the job said how many pages there were once it knew"
    assert window.document is not None
    assert window.document.path == pages / "comic-plan.yaml"
    assert window._pages.count() == 2
    assert chapter_file.is_file(), "the chapter file is a source and is never written to"


# -- reading one region off the page -------------------------------------


class _Reads:
    """A recogniser that answers with one line across the middle of whatever
    it is handed — which, for a crop, puts it inside the region."""

    name = "reads"

    def __init__(self, text: str) -> None:
        self.text = text
        self.given: list[tuple[int, int]] = []

    def recognize(self, page: object, config: object) -> list[OcrLine]:
        height, width = page.rgb.shape[:2]  # type: ignore[attr-defined]
        self.given.append((width, height))
        if not self.text:
            return []
        box = Box(width // 4, height // 4, 3 * width // 4, 3 * height // 4)
        return [OcrLine(text=self.text, box=box, confidence=0.9)]


def _read_region(
    window: MainWindow, reader: _Reads | Callable[[object], object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run the read the way the menu item does, and wait for it to land.

    Every caller answers alerts first, including the ones that expect none:
    an unanswered modal blocks the offscreen event loop for as long as the
    suite is willing to wait, which turns a failing assertion into a hung
    run.
    """
    monkeypatch.setattr(
        run_job,
        "get_recognizer",
        reader if callable(reader) and not isinstance(reader, _Reads) else lambda config: reader,
    )
    window._on_extract_text()
    job = window._read_job
    assert job is not None, "the command did not start a reading"
    assert job.wait(60_000), "the worker thread did not finish"
    for _ in range(20):
        QApplication.processEvents()
        if window._read_job is None:
            return
    raise AssertionError("the reading never reached the window")


def test_reading_a_region_puts_what_the_page_says_into_the_source_text(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._go_to_region("page-001-002")
    window.document.set_source_text("page-001-002", "")  # type: ignore[union-attr]
    shown = _catch_alerts(monkeypatch)

    _read_region(window, _Reads("NON CI POSSO CREDERE"), monkeypatch)

    assert shown == [], "nothing to lose, so nothing to ask"
    assert window.document.region("page-001-002").source_text == "NON CI POSSO CREDERE"  # type: ignore[union-attr]
    assert window._inspector._source_text.toPlainText() == "NON CI POSSO CREDERE"


def test_a_region_with_nothing_in_it_yet_gets_the_translation_too(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What extract does for every region it reads, for the one just drawn.

    The text is then edited into the target language in place rather than
    retyped, and the region stops being flagged as held back.
    """
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._go_to_region("page-001-002")
    window.document.set_source_text("page-001-002", "")  # type: ignore[union-attr]
    assert window.document.region("page-001-002").translation == ""  # type: ignore[union-attr]
    _catch_alerts(monkeypatch)

    _read_region(window, _Reads("NON CI POSSO CREDERE"), monkeypatch)

    region = window.document.region("page-001-002")  # type: ignore[union-attr]
    assert region.source_text == "NON CI POSSO CREDERE"
    assert region.translation == "NON CI POSSO CREDERE"
    assert window._inspector._translation.toPlainText() == "NON CI POSSO CREDERE"
    assert window.document.undo()  # type: ignore[union-attr]
    assert window.document.region("page-001-002").translation == "", "one edit, one undo"  # type: ignore[union-attr]


def test_a_translation_somebody_cleared_is_not_seeded_over(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blanking a translation is how you say leave this balloon alone."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._go_to_region("page-001-001")
    window.document.set_translation("page-001-001", "")  # type: ignore[union-attr]
    _catch_alerts(monkeypatch, QMessageBox.StandardButton.Ok)

    _read_region(window, _Reads("WHAT THE PAGE SAYS"), monkeypatch)

    region = window.document.region("page-001-001")  # type: ignore[union-attr]
    assert region.source_text == "WHAT THE PAGE SAYS"
    assert region.translation == "", "the decision stands"


def test_a_reading_that_is_not_language_does_not_seed_a_translation(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Extract's own rule: a region whose reading is an artefact is kept and
    not seeded, because seeding makes it something apply will letter."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._go_to_region("page-001-002")
    window.document.set_source_text("page-001-002", "")  # type: ignore[union-attr]
    _catch_alerts(monkeypatch)

    _read_region(window, _Reads("(6 ~"), monkeypatch)

    region = window.document.region("page-001-002")  # type: ignore[union-attr]
    assert region.source_text == "(6 ~"
    assert region.translation == ""


def test_the_recogniser_is_given_the_region_and_not_the_page(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cheap way round is to read the page and keep the lines inside the
    outline; it costs a full-page recognition for one balloon."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._go_to_region("page-001-001")
    reader = _Reads("CIAO")
    _catch_alerts(monkeypatch)

    _read_region(window, reader, monkeypatch)

    assert window._page is not None
    given = reader.given[0]
    assert given < (window._page.width, window._page.height)


def test_reading_asks_before_writing_over_text_that_is_already_there(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._go_to_region("page-001-001")
    assert window.document.region("page-001-001").source_text == "CIAO"  # type: ignore[union-attr]
    shown = _catch_alerts(monkeypatch, QMessageBox.StandardButton.Cancel)

    _read_region(window, _Reads("SOMETHING ELSE"), monkeypatch)

    assert len(shown) == 1
    assert "page-001-001" in shown[0].text()
    detail = shown[0].informativeText()
    assert "Ctrl+Z" in detail
    assert "CIAO" not in detail and "SOMETHING ELSE" not in detail, (
        "a balloon's worth of lettering is a paragraph, and two of them are two"
    )
    assert window.document.region("page-001-001").source_text == "CIAO", "cancelled"  # type: ignore[union-attr]


def test_reading_over_text_that_is_there_replaces_it_when_asked_to(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._go_to_region("page-001-001")
    _catch_alerts(monkeypatch, QMessageBox.StandardButton.Ok)

    _read_region(window, _Reads("SOMETHING ELSE"), monkeypatch)

    assert window.document.region("page-001-001").source_text == "SOMETHING ELSE"  # type: ignore[union-attr]


def test_a_reading_is_one_undo_step_like_any_other_edit(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And a step of its own: not swallowed by the typing either side of it.

    Consecutive edits to one field collapse into a single undo step, because
    that is how a run of keystrokes should behave. A command is not a run of
    keystrokes, and one Ctrl+Z after it should put back what was typed rather
    than throwing the typing away as well.
    """
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._go_to_region("page-001-001")
    window.document.set_source_text("page-001-001", "TYPED BY HAND")  # type: ignore[union-attr]
    _catch_alerts(monkeypatch, QMessageBox.StandardButton.Ok)

    _read_region(window, _Reads("READ OFF THE PAGE"), monkeypatch)
    window.document.set_source_text("page-001-001", "TYPED AFTERWARDS")  # type: ignore[union-attr]

    assert window.document.undo()  # type: ignore[union-attr]
    assert window.document.region("page-001-001").source_text == "READ OFF THE PAGE"  # type: ignore[union-attr]
    assert window.document.undo()  # type: ignore[union-attr]
    assert window.document.region("page-001-001").source_text == "TYPED BY HAND"  # type: ignore[union-attr]
    assert window.document.undo()  # type: ignore[union-attr]
    assert window.document.region("page-001-001").source_text == "CIAO"  # type: ignore[union-attr]


def test_a_region_that_says_nothing_says_so_and_changes_nothing(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._go_to_region("page-001-001")
    shown = _catch_alerts(monkeypatch)

    _read_region(window, _Reads(""), monkeypatch)

    assert len(shown) == 1
    assert "No text was found" in shown[0].text()
    assert window.document.region("page-001-001").source_text == "CIAO"  # type: ignore[union-attr]
    assert not window.document.can_undo  # type: ignore[union-attr]


def test_a_reading_that_says_what_is_already_there_is_not_an_edit(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No question to ask, no undo step to take, and it says so."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._go_to_region("page-001-001")
    shown = _catch_alerts(monkeypatch)

    _read_region(window, _Reads("CIAO"), monkeypatch)

    assert shown == []
    assert not window.document.can_undo  # type: ignore[union-attr]
    assert "nothing changed" in window.statusBar().currentMessage(), (
        "a command that did nothing has to say so somewhere"
    )


def test_an_answer_about_a_region_that_has_gone_is_dropped(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recogniser takes seconds, and a plan can be edited while it reads."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._go_to_region("page-001-002")
    window._reading = RegionTextRequest(
        page=window._page,  # type: ignore[arg-type]
        polygon=window.document.region("page-001-002").polygon,  # type: ignore[union-attr]
        config=ExtractConfig(),
        region_id="gone-since",
        image="page-001.png",
    )
    shown = _catch_alerts(monkeypatch)

    window._on_region_text_ready("WHATEVER IT SAID")

    assert shown == [], "nothing to ask about a region that is not there"
    assert not window.document.can_undo  # type: ignore[union-attr]


def test_an_answer_about_a_plan_that_has_been_closed_is_dropped(
    qapp: object, two_page_plan: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two plans number their regions the same way, so an answer left over
    from the last one would land in this one under the same name."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._go_to_region("page-001-001")
    window._reading = RegionTextRequest(
        page=window._page,  # type: ignore[arg-type]
        polygon=window.document.region("page-001-001").polygon,  # type: ignore[union-attr]
        config=ExtractConfig(),
        region_id="page-001-001",
        image="page-001.png",
    )

    window.open_plan(two_page_plan)  # the same file, opened again: a new document
    _catch_alerts(monkeypatch)
    window._on_region_text_ready("FROM THE PLAN BEFORE")

    assert window.document.region("page-001-001").source_text == "CIAO"  # type: ignore[union-attr]
    assert not window.document.can_undo  # type: ignore[union-attr]


def test_a_reading_with_no_recogniser_to_do_it_says_so(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason this is a job rather than a call: it can fail, off-thread."""

    def refuse(config: object) -> object:
        raise OcrUnavailableError("no recogniser on this machine")

    window = MainWindow()
    window.open_plan(two_page_plan)
    window._go_to_region("page-001-001")
    shown = _catch_alerts(monkeypatch)

    _read_region(window, refuse, monkeypatch)  # type: ignore[arg-type]

    assert len(shown) == 1
    assert "could not be read" in shown[0].text()
    assert "no recogniser on this machine" in shown[0].informativeText()
    assert window.document.region("page-001-001").source_text == "CIAO"  # type: ignore[union-attr]
    assert window._extract_text_action.isEnabled(), "and the command is offered again"


def test_reading_waits_for_a_region_to_be_chosen(qapp: object, two_page_plan: Path) -> None:
    window = MainWindow()
    assert not window._extract_text_action.isEnabled(), "nothing open"

    window.open_plan(two_page_plan)
    window._pages.select_image("page-001.png")
    window._current_region = None
    window._update_actions_enabled()
    assert not window._extract_text_action.isEnabled(), "a page, but no region on it chosen"

    window._go_to_region("page-001-001")
    assert window._extract_text_action.isEnabled()


def test_the_preferences_window_never_outgrows_the_screen(qapp: object) -> None:
    """Eleven settings and a note under three of them; a short screen wins.

    How tall this window honestly wants to be depends on the system font and
    the language it is running in, so it cannot be settled by counting rows
    here. It is capped instead, and the form gives way rather than Done.
    """
    dialog = PreferencesDialog(Preferences())
    dialog.show()
    QApplication.processEvents()
    assert dialog.height() > 400, "it really is a tall window"

    dialog.cap_height(400)
    QApplication.processEvents()

    assert dialog.height() <= 400
    assert dialog.maximumHeight() <= 400, "and it cannot be dragged past it either"
    assert dialog._scroll.verticalScrollBar().maximum() > 0, "the form scrolls instead"


def test_a_screen_that_reports_nonsense_still_leaves_a_usable_window(qapp: object) -> None:
    dialog = PreferencesDialog(Preferences())
    dialog.show()

    dialog.cap_height(1)

    assert dialog.height() >= MINIMUM_HEIGHT
    assert dialog.maximumHeight() >= MINIMUM_HEIGHT


def test_the_ends_of_the_window_do_not_scroll_away(qapp: object) -> None:
    """The heading and Done stay put; the middle is what gives."""
    dialog = PreferencesDialog(Preferences())
    dialog.show()
    dialog.cap_height(400)
    QApplication.processEvents()

    inside = dialog._scroll.widget().findChildren(QWidget)
    assert dialog._ocr_languages in inside, "the form is what scrolls"
    buttons = dialog.findChildren(QDialogButtonBox)
    assert buttons and buttons[0] not in inside, "Done is not in there with it"


# -- nothing of ours is left for the interpreter to tear down ------------


def _flush_deferred_deletes() -> None:
    """Deliver what ``deleteLater`` posted.

    ``processEvents`` does not: a deferred delete is delivered when control
    returns to the event loop that was running when it was asked for, and
    there is no event loop in a test. Measured — three rounds of dialogs and
    three still alive until this is called instead.
    """
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_a_dialog_does_not_outlive_the_command_that_opened_it(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Qt owns a parented dialog, not the name it was built under.

    So a dialog opened and forgotten stays a child of the window for as long
    as the window lives, and opening Preferences three times leaves three of
    them — each holding its widgets, and a render dialog holding a whole
    plan. They are also what PySide's shutdown walk has to destroy, which is
    the crash this milestone is.
    """
    window = MainWindow()
    window.open_plan(two_page_plan)
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)

    for _ in range(3):
        window._on_preferences()
        window._on_about()
        window._on_edit_header()
        window._on_render()
        window._on_extract()
        _flush_deferred_deletes()

    assert window.findChildren(QDialog) == [], "each was finished with when its command was"


def test_the_guide_is_kept_while_the_window_lives_and_goes_with_it(
    qapp: object, two_page_plan: Path
) -> None:
    """The one dialog held on purpose — it is read beside the thing it
    describes, and asking twice should not open a second one."""
    window = MainWindow()
    window.open_plan(two_page_plan)

    window._on_help()
    first = window._help
    window._on_help()

    assert window._help is first, "the same guide, not a second one"
    assert first is not None

    window.close()
    _flush_deferred_deletes()

    assert window._help is None
    assert window.findChildren(type(first)) == [], "it went with the window"


def test_closing_down_destroys_the_window_and_its_children(qapp: object) -> None:
    """What the crash report asked for: nothing left for ``Py_FinalizeEx``.

    PySide's atexit handler walks every Python-wrapped QObject still alive
    and destroys it, in an order that is not ours to choose. What is ours is
    whether it finds anything, so the window is destroyed here instead —
    while the interpreter is running and Qt can order its own children.
    """
    import shiboken6

    from comictrans.gui.app import close_down

    window = MainWindow()
    kept = QDialog(window)
    assert shiboken6.isValid(window) and shiboken6.isValid(kept)

    close_down(window)

    assert not shiboken6.isValid(window)
    assert not shiboken6.isValid(kept), "children go with it"


def test_closing_down_twice_is_not_a_crash(qapp: object) -> None:
    """It runs on the way out of a process that may have got there oddly."""
    from comictrans.gui.app import close_down

    window = MainWindow()
    close_down(window)
    close_down(window)


# -- typing into a region, and getting back out --------------------------


def _undo_key() -> tuple[Qt.Key, Qt.KeyboardModifier]:
    sequence = QKeySequence(QKeySequence.StandardKey.Undo)
    return Qt.Key(sequence[0].key()), sequence[0].keyboardModifiers()


def test_undo_reaches_the_document_from_inside_a_translation(
    qapp: object, two_page_plan: Path
) -> None:
    """It did not. The field claimed Cmd+Z and, having no undo of its own,
    did nothing with it — so the window's action never fired and typing
    could not be taken back without first clicking somewhere else."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    window.show()
    window._go_to_region("page-001-001")
    field = window._inspector._translation
    field.setFocus()
    QApplication.processEvents()
    field.setPlainText("ONE TWO")
    QApplication.processEvents()
    assert window.document.region("page-001-001").translation == "ONE TWO"  # type: ignore[union-attr]

    QTest.keyClick(field, *_undo_key())
    QApplication.processEvents()

    assert window.document.region("page-001-001").translation != "ONE TWO"  # type: ignore[union-attr]
    assert field.toPlainText() == window.document.region("page-001-001").translation  # type: ignore[union-attr]


def test_clicking_a_region_puts_the_caret_in_its_translation(
    qapp: object, two_page_plan: Path
) -> None:
    """Clicking a balloon means "I am about to write", when nothing else
    was already focused to carry over instead."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    window.show()
    QApplication.processEvents()

    window._canvas.region_selected.emit("page-001-002")
    QApplication.processEvents()

    assert window._current_region == "page-001-002"
    assert QApplication.focusWidget() is window._inspector._translation


def test_clicking_a_region_carries_over_a_field_already_focused(
    qapp: object, two_page_plan: Path
) -> None:
    """A click is only the *default* to the translation, not a demand for
    it — typing in notes and then clicking another balloon is "carry on
    taking notes", the same as stepping there with Next Region would be."""
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-001")
    window._inspector._notes.setFocus()
    QApplication.processEvents()
    assert window._inspector._notes.hasFocus(), "sanity: focus is there before clicking"

    window._canvas.region_selected.emit("page-001-002")
    QApplication.processEvents()

    assert window._current_region == "page-001-002"
    field = window._inspector._notes
    assert field.hasFocus()
    assert field.textCursor().position() == len(field.toPlainText())


def test_walking_to_a_region_leaves_the_arrow_keys_where_they_were(
    qapp: object, two_page_plan: Path
) -> None:
    """The conflict this design exists for.

    Arrow keys nudge the selected region a pixel, twenty with Shift. Focusing
    a text field takes all four away, and walking the flagged regions from
    the keyboard is exactly when somebody is nudging polygons — so only a
    click moves the caret.
    """
    window = MainWindow()
    window.open_plan(two_page_plan)
    window.show()
    window._canvas.setFocus()
    QApplication.processEvents()

    for walk in (
        lambda: window._go_to_region("page-001-002"),
        lambda: window._step_region(forward=True),
        lambda: window._pages.select_image("page-002.png"),
    ):
        walk()
        QApplication.processEvents()
        assert QApplication.focusWidget() is not window._inspector._translation, walk


def test_escape_hands_the_page_back(qapp: object, two_page_plan: Path) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    window.show()
    window._canvas.region_selected.emit("page-001-001")
    QApplication.processEvents()
    assert QApplication.focusWidget() is window._inspector._translation

    QTest.keyClick(window._inspector._translation, Qt.Key.Key_Escape)
    QApplication.processEvents()

    assert QApplication.focusWidget() is window._canvas


def test_the_caret_lands_at_the_end_rather_than_over_the_text(
    qapp: object, two_page_plan: Path
) -> None:
    """Nothing is selected: the same gesture on a translation somebody has
    written would otherwise put one keystroke between them and losing it."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    window.show()

    window._canvas.region_selected.emit("page-001-001")
    QApplication.processEvents()

    field = window._inspector._translation
    assert not field.textCursor().hasSelection()
    assert field.textCursor().position() == len(field.toPlainText())


def test_walking_the_regions_works_with_the_caret_in_a_translation(
    qapp: object, two_page_plan: Path
) -> None:
    """Previous and Next Region are Cmd+Up and Cmd+Down, which on macOS are
    also a text field's "go to the start and the end of the document" — so
    the field claimed them and walking stopped working as soon as somebody
    had clicked a balloon."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    window.show()
    window._canvas.region_selected.emit("page-001-001")
    QApplication.processEvents()
    field = window._inspector._translation
    assert QApplication.focusWidget() is field

    for action in (window._previous_region_action, window._next_region_action):
        claim = QKeyEvent(
            QEvent.Type.ShortcutOverride,
            Qt.Key(action.shortcut()[0].key()),
            action.shortcut()[0].keyboardModifiers(),
        )
        QApplication.sendEvent(field, claim)
        assert not claim.isAccepted(), f"{action.text()} never reaches the window"


def test_walking_to_a_region_leaves_the_caret_where_typing_continues(
    qapp: object, two_page_plan: Path
) -> None:
    """The panel repopulates under a caret that never moved, and
    ``setPlainText`` puts it at the start — the wrong end of a translation
    somebody is about to add to."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    window.show()
    window._canvas.region_selected.emit("page-001-001")
    QApplication.processEvents()

    window._step_region(forward=True)
    QApplication.processEvents()

    for name, field in (
        ("translation", window._inspector._translation),
        ("source text", window._inspector._source_text),
        ("notes", window._inspector._notes),
    ):
        assert field.textCursor().position() == len(field.toPlainText()), name


@pytest.mark.parametrize(
    "field_name", ["_source_text", "_translation", "_notes"], ids=["source", "translation", "notes"]
)
@pytest.mark.parametrize(
    "start,step",
    [
        ("page-001-002", lambda window: window._on_next_region()),
        ("page-001-002", lambda window: window._on_previous_region()),
        ("page-001-001", lambda window: window._on_next_flagged_region()),
    ],
    ids=["next", "previous", "next-flagged"],
)
def test_stepping_regions_keeps_whichever_field_was_focused(
    qapp: object, two_page_plan: Path, field_name: str, start: str, step: object
) -> None:
    """Previous, Next and Next Flagged Region are keyboard shortcuts and
    toolbar buttons reached without leaving a field somebody is typing in —
    stepping should not knock the caret out of it."""
    window = _shown_window(two_page_plan)
    window._go_to_region(start)
    field = getattr(window._inspector, field_name)
    field.setFocus()
    QApplication.processEvents()
    assert field.hasFocus(), "sanity: focus is there before stepping"

    step(window)  # type: ignore[operator]
    QApplication.processEvents()

    field = getattr(window._inspector, field_name)  # same widget, repopulated
    assert field.hasFocus()
    assert field.textCursor().position() == len(field.toPlainText())


def test_stepping_onto_another_page_keeps_the_field_focused(
    qapp: object, two_page_plan: Path
) -> None:
    """Crossing onto another page reloads it, which disables the inspector's
    fields for a moment while it does — long enough, before this was fixed,
    for Qt to push focus onto the canvas instead of leaving it where
    somebody was typing."""
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-002")  # the last region on the first page
    window._inspector._translation.setFocus()
    QApplication.processEvents()

    window._on_next_region()
    QApplication.processEvents()

    assert window._current_region == "page-002-001"
    assert window._current_image == "page-002.png"
    assert window._inspector._translation.hasFocus()
    field = window._inspector._translation
    assert field.textCursor().position() == len(field.toPlainText())


def test_tab_walks_out_of_the_prose_fields(qapp: object, two_page_plan: Path) -> None:
    """It used to be typed into them, which stopped Tab walking the panel."""
    window = MainWindow()
    window.open_plan(two_page_plan)
    window.show()
    QApplication.processEvents()

    for field in (
        window._inspector._source_text,
        window._inspector._translation,
        window._inspector._notes,
    ):
        before = field.toPlainText()
        field.setFocus()
        QTest.keyClick(field, Qt.Key.Key_Tab)
        QApplication.processEvents()
        assert field.toPlainText() == before, "Tab was not typed into it"
        assert QApplication.focusWidget() is not field, "and it moved on"


def test_undoing_another_region_s_edit_brings_that_region_into_view(
    qapp: object, two_page_plan: Path
) -> None:
    """The hazard, solved by showing rather than refusing.

    The panel shows one region while undo walks back through a history that
    covers the whole plan, so an edit made somewhere else was taken back
    where nobody could see it happen. Now the step still happens and the
    region it happened to is selected first.
    """
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-001")
    window._inspector._translation.setPlainText("FIRST")
    window._go_to_region("page-001-002")
    window._inspector._translation.setPlainText("SECOND")
    QApplication.processEvents()

    window._on_undo()
    assert window._current_region == "page-001-002", "its own edit, where it already was"

    window._on_undo()

    assert window._current_region == "page-001-001", "and now the region that changed"
    assert window.document.region("page-001-001").translation != "FIRST"  # type: ignore[union-attr]
    assert window._inspector._translation.toPlainText() == (
        window.document.region("page-001-001").translation  # type: ignore[union-attr]
    )


def test_following_a_change_onto_another_page(qapp: object, two_page_plan: Path) -> None:
    """Selecting a region on another page has to bring the page with it."""
    window = _shown_window(two_page_plan)
    window._go_to_region("page-002-001")
    window._inspector._translation.setPlainText("ON THE SECOND PAGE")
    window._go_to_region("page-001-001")
    window._inspector._translation.setPlainText("ON THE FIRST")
    QApplication.processEvents()

    window._on_undo()
    window._on_undo()

    assert window._current_image == "page-002.png"
    assert window._current_region == "page-002-001"


def test_redo_follows_the_change_as_well(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-001")
    window._inspector._translation.setPlainText("FIRST")
    window._go_to_region("page-001-002")
    window._inspector._translation.setPlainText("SECOND")
    window._on_undo()
    window._on_undo()
    assert window._current_region == "page-001-001"

    window._on_redo()

    assert window._current_region == "page-001-001", "its own step, put back"

    window._on_redo()

    assert window._current_region == "page-001-002"
    assert window.document.region("page-001-002").translation == "SECOND"  # type: ignore[union-attr]


def test_the_change_does_not_hop_off_a_region_already_shown(
    qapp: object, two_page_plan: Path
) -> None:
    """Several regions touched, one of them already on screen: stay there.

    A step that touches more than one region — a merge — shows the first
    in the plan's own order when none of them is selected. That is a
    different case from this one: the region on screen already is among
    the touched, just not the first of them, and it should be left alone
    rather than swapped for the first merely because the first is touched
    too.
    """
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-002")
    before = window.document.plan  # type: ignore[union-attr]
    window.document.set_translation("page-001-001", "A CHANGED")  # type: ignore[union-attr]
    window.document.set_translation("page-001-002", "B CHANGED")  # type: ignore[union-attr]

    window._follow_the_change(before)

    assert window._current_region == "page-001-002"


def test_a_step_about_no_region_leaves_the_selection_alone(
    qapp: object, two_page_plan: Path
) -> None:
    """There is nothing a header edit could usefully select."""
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-002")
    window.document.set_header_font("Marker Felt")  # type: ignore[union-attr]

    window._on_undo()

    assert window._current_region == "page-001-002"


def test_with_the_caret_on_the_page_undo_is_the_whole_plan(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-001")
    window._inspector._translation.setPlainText("FIRST")
    window._go_to_region("page-001-002")
    window._inspector._translation.setPlainText("SECOND")
    window._canvas.setFocus()
    QApplication.processEvents()

    window._on_undo()
    window._on_undo()

    assert window.document.region("page-001-001").translation != "FIRST"  # type: ignore[union-attr]


# -- locking a region ---------------------------------------------------


def test_locking_a_region_takes_the_inspector_out_of_service(
    qapp: object, two_page_plan: Path
) -> None:
    """Every field dead except the lock, which is the way back out of one."""
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-001")
    inspector = window._inspector
    assert inspector._translation.isEnabled()

    window._lock_action.setChecked(True)

    assert not inspector._translation.isEnabled()
    assert not inspector._skip.isEnabled()
    assert not inspector._erase.isEnabled()
    assert not inspector._font.isEnabled()
    assert inspector._locked.isEnabled(), "the way back out stays live"
    assert inspector._locked.isChecked()

    window._lock_action.setChecked(False)

    assert inspector._translation.isEnabled(), "and everything comes back"


def test_the_lock_checkbox_and_the_menu_command_are_the_same_switch(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-001")

    window._inspector._locked.setChecked(True)

    assert window.document.region("page-001-001").locked  # type: ignore[union-attr]
    assert window._lock_action.isChecked(), "the menu follows the panel"


def test_the_lock_command_follows_the_selection(qapp: object, two_page_plan: Path) -> None:
    """Set rather than toggled, and silently.

    The tick tracking the selection is the easy half. The half worth a test
    is that following it says nothing: the command is set to the value the
    newly selected region already holds, so letting that reach the handler
    changes no region — it announces an edit nobody made. Measured before it
    was asserted; without the signal blocker the bar reads
    "page-001-002 is unlocked" after nothing but a click.
    """
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-001")
    window._lock_action.setChecked(True)
    window.statusBar().clearMessage()

    window._go_to_region("page-001-002")

    assert not window._lock_action.isChecked()
    assert not window.document.region("page-001-002").locked  # type: ignore[union-attr]
    assert window.statusBar().currentMessage() == "", "selecting is not an edit"

    window._go_to_region("page-001-001")

    assert window._lock_action.isChecked()
    assert window.statusBar().currentMessage() == ""


def test_a_locked_region_is_not_offered_the_commands_that_would_edit_it(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-001")
    assert window._delete_region_action.isEnabled()
    assert window._merge_action.isEnabled()
    assert window._edit_shape_action.isEnabled()

    window._lock_action.setChecked(True)

    assert not window._delete_region_action.isEnabled()
    assert not window._merge_action.isEnabled()
    assert not window._edit_shape_action.isEnabled()
    assert window._add_region_action.isEnabled(), "a lock is about its region, not the page"
    assert window._lock_action.isEnabled(), "and the lock itself stays reachable"


def test_the_region_menu_offers_the_lock(qapp: object, two_page_plan: Path) -> None:
    window = _shown_window(two_page_plan)

    menu = window._region_menu("page-001-001")

    assert window._lock_action in menu.actions()


def test_the_arrow_keys_do_not_move_a_locked_region(qapp: object, two_page_plan: Path) -> None:
    """The canvas refuses the gesture, rather than the document refusing it.

    The polygon is unchanged either way, which is why that assertion alone
    was not enough — measured: with the canvas guard removed the keys still
    move nothing, because ``set_polygon`` raises. But raising out of a key
    press is reported to whoever pressed it, so a lock would answer an arrow
    key with an error about an edit they did not know they had asked for.
    Watching the signal is what tells the two apart.
    """
    window = _shown_window(two_page_plan)
    window._go_to_region("page-001-001")
    window._lock_action.setChecked(True)
    before = window.document.region("page-001-001").polygon  # type: ignore[union-attr]
    nudges: list[str] = []
    window._canvas.polygon_nudged.connect(lambda region_id, _polygon: nudges.append(region_id))

    QTest.keyClick(window._canvas, Qt.Key.Key_Right)
    QTest.keyClick(window._canvas, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)

    assert nudges == [], "the canvas never asked for the move"
    assert window.document.region("page-001-001").polygon == before  # type: ignore[union-attr]


def test_a_modifier_drag_is_not_offered_over_a_locked_region(
    qapp: object, two_page_plan: Path
) -> None:
    window = _shown_window(two_page_plan)
    canvas = window._canvas
    window._go_to_region("page-001-001")
    inside = QPointF(canvas.mapFromScene(QPointF(*BALLOON_A.center)))
    assert canvas.wants_move_cursor(inside, Qt.KeyboardModifier.ControlModifier)

    window._lock_action.setChecked(True)

    assert not canvas.wants_move_cursor(inside, Qt.KeyboardModifier.ControlModifier)


# -- the region line does not push the panel about -----------------------

LONG_REGION_ID = "7-1-combined-box-balloon-and-bare-caption-001"


@pytest.fixture
def plan_with_a_long_region_id(tmp_path: Path, two_page_plan: Path) -> Path:
    """The two-page plan with the first region renamed to a long id."""
    plan = load_plan(two_page_plan, check_images=False)
    renamed = (replace(plan.regions[0], id=LONG_REGION_ID), *plan.regions[1:])
    write_plan(replace(plan, regions=renamed), two_page_plan, force=True)
    return two_page_plan


def test_the_region_dock_does_not_resize_when_the_id_gets_longer(
    qapp: object, plan_with_a_long_region_id: Path
) -> None:
    """The defect this fixes: the dock used to take its width from the id.

    Measured before the fix, on these same two regions: 415px of dock against
    538px, and 123px taken off the canvas, on nothing but a click.
    """
    window = _shown_window(plan_with_a_long_region_id)
    window._go_to_region("page-001-002")
    dock, canvas = window._inspector_dock.width(), window._canvas.width()

    window._go_to_region(LONG_REGION_ID)

    assert window._inspector_dock.width() == dock
    assert window._canvas.width() == canvas


def test_the_panel_minimum_no_longer_tracks_the_id(
    qapp: object, plan_with_a_long_region_id: Path
) -> None:
    window = _shown_window(plan_with_a_long_region_id)
    window._go_to_region("page-001-002")
    narrowest = window._inspector.minimumSizeHint().width()

    window._go_to_region(LONG_REGION_ID)

    assert window._inspector.minimumSizeHint().width() == narrowest


def test_a_long_region_id_is_elided_in_the_middle_keeping_its_tail(qapp: object) -> None:
    """The tail is the part that tells a region from its neighbours, and the
    geometry and order after it are a suffix that must survive whole."""
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from comictrans.gui.inspector import ElidedLabel

    host = QWidget()
    layout = QVBoxLayout(host)
    label = ElidedLabel()
    layout.addWidget(label)
    host.resize(300, 60)
    host.show()
    QTest.qWaitForWindowExposed(host)
    suffix = "  (approximate, order 12)"
    label.set_parts(LONG_REGION_ID, suffix)

    shown = label.text()

    assert shown != LONG_REGION_ID + suffix, "it had to be shortened to fit"
    assert "…" in shown
    assert shown.startswith("7-1-"), "the head stays"
    assert shown.endswith("-001" + suffix), "and so do the tail and the suffix"
    assert label.toolTip() == LONG_REGION_ID + suffix, "nothing is lost, it is one hover away"


def test_the_region_line_is_visible_where_macos_sizes_fields_to_their_hint(
    qapp: object, plan_with_a_long_region_id: Path
) -> None:
    """The defect this test exists for shipped, and looked fine on Linux.

    ``QFormLayout`` is asked how to size its fields and the answer is not the
    same everywhere: macOS asks for ``FieldsStayAtSizeHint`` where every other
    platform this suite runs on stretches fields to the panel. A widget whose
    hint is ignored is given the full width under one and *nothing* under the
    other — measured at zero pixels, a region line nobody could see. So this
    asks the form for the macOS policy and checks there is something there.
    """
    from PySide6.QtWidgets import QFormLayout

    window = _shown_window(plan_with_a_long_region_id)
    form = window._inspector.layout().itemAt(0).layout()
    assert isinstance(form, QFormLayout)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
    QTest.qWait(1)

    for region_id in ("page-001-002", LONG_REGION_ID):
        window._go_to_region(region_id)
        QTest.qWait(1)
        label = window._inspector._id_label
        assert label.width() > 0, f"{region_id}: the region line has no width at all"
        assert label.text().strip(), f"{region_id}: the region line is empty"
        # Not merely non-empty: the id itself has to be in there, not just
        # the geometry and order that follow it.
        assert label.text().startswith(region_id[:4]), label.text()


def test_the_region_line_asks_for_the_same_width_whatever_the_id(
    qapp: object, plan_with_a_long_region_id: Path
) -> None:
    """The hint is a constant, which is what stops it pushing the panel.

    Asserted on the hint rather than on the dock, because the dock only moves
    under a policy that honours the hint — testing the dock on Linux misses
    exactly the case that shipped broken.
    """
    window = _shown_window(plan_with_a_long_region_id)
    label = window._inspector._id_label
    window._go_to_region("page-001-002")
    short = (label.sizeHint().width(), label.minimumSizeHint().width())

    window._go_to_region(LONG_REGION_ID)

    assert (label.sizeHint().width(), label.minimumSizeHint().width()) == short


def test_the_region_line_is_re_elided_when_the_panel_is_resized(qapp: object) -> None:
    """Eliding once is not enough — the dock is draggable.

    Without this the id would keep whatever width it was first laid out at,
    and dragging the dock narrower would put the text under the edge of the
    panel rather than cutting it.
    """
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from comictrans.gui.inspector import ElidedLabel

    host = QWidget()
    layout = QVBoxLayout(host)
    label = ElidedLabel()
    layout.addWidget(label)
    host.resize(900, 60)
    host.show()
    QTest.qWaitForWindowExposed(host)
    suffix = "  (approximate, order 12)"
    label.set_parts(LONG_REGION_ID, suffix)
    assert "…" not in label.text(), "room enough at this width"

    host.resize(300, 60)
    QTest.qWait(1)

    assert "…" in label.text(), "and cut when there is not"


def test_the_region_line_never_draws_wider_than_it_was_given(qapp: object) -> None:
    """What it shows has to fit, suffix included.

    The suffix is appended whole, so the room left for the id is the width
    less the suffix — get that subtraction wrong and the line is not elided
    at all, it is clipped by the widget's own edge.
    """
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from comictrans.gui.inspector import ElidedLabel

    host = QWidget()
    layout = QVBoxLayout(host)
    label = ElidedLabel()
    layout.addWidget(label)
    host.show()
    QTest.qWaitForWindowExposed(host)

    for width in (900, 500, 340, 260):
        host.resize(width, 60)
        QTest.qWait(1)
        label.set_parts(LONG_REGION_ID, "  (approximate, order 12)")
        drawn = QFontMetrics(label.font()).horizontalAdvance(label.text())
        assert drawn <= label.contentsRect().width(), (
            f"at {width}px the line is {drawn}px in a {label.contentsRect().width()}px label"
        )


def test_a_region_id_that_fits_is_left_alone(qapp: object) -> None:
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from comictrans.gui.inspector import ElidedLabel

    host = QWidget()
    layout = QVBoxLayout(host)
    label = ElidedLabel()
    layout.addWidget(label)
    host.resize(900, 60)
    host.show()
    QTest.qWaitForWindowExposed(host)
    label.set_parts("p-001", "  (exact, order 1)")

    assert label.text() == "p-001  (exact, order 1)"
    assert "…" not in label.text()
