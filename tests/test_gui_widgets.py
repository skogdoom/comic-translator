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
from PySide6.QtWidgets import QLabel, QMessageBox

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
    assert "*" not in window.windowTitle()

    window._inspector._translation.setPlainText("HELLO THERE")

    assert window.document.dirty  # type: ignore[union-attr]
    assert "*" in window.windowTitle()
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
    assert "*" not in window.windowTitle()
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

    window._inspector._font.setText("Chalkboard SE")

    assert window.document.region("page-001-001").font == "Chalkboard SE"  # type: ignore[union-attr]
    assert window.document.dirty  # type: ignore[union-attr]

    # Emptying it clears the override rather than pinning an empty name.
    window._inspector._font.setText("")
    assert window.document.region("page-001-001").font is None  # type: ignore[union-attr]


def test_selecting_another_region_does_not_carry_the_font_override_across(
    qapp: object, two_page_plan: Path
) -> None:
    window = MainWindow()
    window.open_plan(two_page_plan)
    window._inspector._font.setText("Chalkboard SE")

    window._on_region_selected("page-001-002")

    assert window._inspector._font.text() == ""
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
