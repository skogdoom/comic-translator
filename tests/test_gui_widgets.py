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

from pathlib import Path

import pytest

from comictrans.model import Box, Color, Geometry, Plan, PlanHeader, Region, TextCase
from comictrans.planfile import load_plan, write_plan
from comictrans.util import sha256_file

from .conftest import ART_DARK, BALLOON_WHITE, INK_BLACK, make_page_array, save_page

pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QLineEdit, QMessageBox

from comictrans.gui.main_window import MainWindow

BALLOON_A = Box(60, 60, 260, 200)
TEXT_A = Box(90, 110, 230, 140)
BALLOON_B = Box(300, 60, 500, 200)
TEXT_B = Box(330, 110, 470, 140)


def _header(**overrides: object) -> PlanHeader:
    base: dict[str, object] = {
        "version": 1,
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


def _region(image: str, digest: str, **overrides: object) -> Region:
    base: dict[str, object] = {
        "id": "page-001",
        "image": image,
        "image_sha256": digest,
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
    digest1, digest2 = sha256_file(page1), sha256_file(page2)
    plan = Plan(
        header=_header(),
        regions=(
            _region(
                "page-001.png",
                digest1,
                id="page-001-001",
                order=1,
                polygon=BALLOON_A.as_polygon(),
            ),
            _region(
                "page-001.png",
                digest1,
                id="page-001-002",
                order=2,
                polygon=BALLOON_B.as_polygon(),
                translation="",  # held back
            ),
            _region(
                "page-002.png", digest2, id="page-002-001", order=1, polygon=BALLOON_A.as_polygon()
            ),
        ),
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

    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel)
    )
    assert window._confirm_discard_if_dirty() is False

    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Discard)
    )
    assert window._confirm_discard_if_dirty() is True


def test_close_event_is_actually_wired_to_the_dirty_guard(
    qapp: object, two_page_plan: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtGui import QCloseEvent

    window = MainWindow()
    window.open_plan(two_page_plan)
    window._inspector._translation.setPlainText("EDITED")

    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel)
    )
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted(), "cancelling the prompt must keep the window open"

    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Discard)
    )
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

    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        staticmethod(lambda *a: shown.append(a[2]) or QMessageBox.StandardButton.Ok),
    )
    window.open_plan(broken)

    assert shown, "a plan that fails to parse must be reported, not silently ignored"
    assert window.document is original_document


def test_render_preview_shows_a_different_image_and_can_return_to_the_overlay(
    qapp: object, two_page_plan: Path, font_dir: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)

    before_items = set(window._canvas._items)
    assert before_items  # the overlay is showing region outlines

    window._on_render_preview()

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
        Plan(
            header=_header(font="Definitely Not A Real Font XYZ"),
            regions=(_region("page-001.png", sha256_file(image)),),
        ),
        plan_path,
    )

    window = MainWindow()
    window.open_plan(plan_path)

    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        staticmethod(lambda *a: shown.append(a[2]) or QMessageBox.StandardButton.Ok),
    )
    window._on_render_preview()

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


def test_review_without_a_plan_asks_for_one(qapp: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of 'review' with no argument: don't sit there empty."""
    from PySide6.QtWidgets import QApplication

    from comictrans.gui import app as gui_app

    asked: list[bool] = []
    monkeypatch.setattr(MainWindow, "open_plan_dialog", lambda self: asked.append(True))
    # Stand in for the event loop: run the queued zero-timer, then return.
    monkeypatch.setattr(QApplication, "exec", lambda self: QApplication.processEvents() or 0)

    assert gui_app.run() == 0
    assert asked, "no plan file means the file dialog, not an empty window"


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


def test_the_font_override_writes_through_as_it_is_typed(qapp: object, two_page_plan: Path) -> None:
    """Not on focus loss: a name typed and then abandoned is still an edit."""
    window = MainWindow()
    window.open_plan(two_page_plan)

    window._inspector._font.setCurrentText("Chalkboard SE")

    assert window.document.region("page-001-001").font == "Chalkboard SE"  # type: ignore[union-attr]
    assert window.document.dirty  # type: ignore[union-attr]

    # Emptying it clears the override rather than pinning an empty name.
    window._inspector._font.setCurrentText("")
    assert window.document.region("page-001-001").font is None  # type: ignore[union-attr]


def test_selecting_another_region_does_not_carry_the_font_override_across(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._inspector._font.setCurrentText("Chalkboard SE")

    window._on_region_selected("page-001-002")

    assert window._inspector._font.value() is None, "no override, however it is spelled"
    assert window.document.region("page-001-002").font is None  # type: ignore[union-attr]
    assert window.document.region("page-001-001").font == "Chalkboard SE"  # type: ignore[union-attr]


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
        window._overlay_action,
    ):
        assert action in on_toolbar


def test_the_layout_is_remembered_for_the_next_window(qapp: object, tmp_path: Path) -> None:
    from PySide6.QtCore import QSettings

    settings = QSettings(str(tmp_path / "layout.ini"), QSettings.Format.IniFormat)

    first = MainWindow(settings=settings)
    first._pages_dock.setVisible(False)
    first._save_layout()

    assert MainWindow(settings=settings)._pages_dock.isHidden()
    # A window opened without settings is unaffected by any of that.
    assert not MainWindow()._pages_dock.isHidden()


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
    qapp: object, two_page_plan: Path
) -> None:
    from comictrans.gui.header_dialog import HeaderDialog
    from comictrans.model import TextCase

    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = HeaderDialog(window.document, window)  # type: ignore[arg-type]

    dialog._font.setCurrentText("Chalkboard SE")
    dialog._case.setCurrentIndex(dialog._case.findData(TextCase.PRESERVE))
    dialog._condense.setValue(0.8)
    dialog._target_language.setText("sv")

    header = window.document.plan.header  # type: ignore[union-attr]
    assert header.font == "Chalkboard SE"
    assert header.case is TextCase.PRESERVE
    assert header.condense_min == pytest.approx(0.8)
    assert header.target_language == "sv"
    assert window.isWindowModified() or window.document.dirty  # type: ignore[union-attr]


def test_clearing_the_header_font_on_the_way_to_a_new_one_writes_nothing(
    qapp: object, two_page_plan: Path
) -> None:
    """An empty font is not a legal header value, so it must not be recorded."""
    from comictrans.gui.header_dialog import HeaderDialog

    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = HeaderDialog(window.document, window)  # type: ignore[arg-type]

    dialog._font.setCurrentText("")

    assert window.document.plan.header.font == "Comic Sans MS"  # type: ignore[union-attr]
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
    from comictrans.gui.header_dialog import HeaderDialog, font_reach

    window = MainWindow()
    window.open_plan(two_page_plan)
    assert font_reach(window.document) == "used by all 3 regions"  # type: ignore[arg-type]

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
    qapp: object, two_page_plan: Path
) -> None:
    from comictrans.gui.header_dialog import HeaderDialog

    window = MainWindow()
    window.open_plan(two_page_plan)
    dialog = HeaderDialog(window.document, window)  # type: ignore[arg-type]
    dialog._font.setCurrentText("Chalkboard SE")

    window._on_undo()
    dialog.repopulate()

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


def test_the_font_box_stays_editable_so_a_name_can_be_typed(qapp: object, font_dir: Path) -> None:
    from comictrans.gui.font_box import FontBox

    box = FontBox(allow_default=True)
    assert box.isEditable()
    box.setCurrentText("Typed By Hand")
    assert box.value() == "Typed By Hand", "typing must not be forced onto a listed item"


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

    window._inspector._font.setCurrentText("Comic Sans MS")
    assert window.document.region("page-001-001").font == "Comic Sans MS"  # type: ignore[union-attr]

    from comictrans.gui.font_box import PLAN_DEFAULT

    window._inspector._font.setCurrentText(PLAN_DEFAULT)
    assert window.document.region("page-001-001").font is None  # type: ignore[union-attr]


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
