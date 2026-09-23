"""The reader window: a chapter paged through, and nothing written anywhere."""

from __future__ import annotations

import io
import time
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6")

from PIL import Image
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QGraphicsPixmapItem, QGraphicsSimpleTextItem

from comictrans.cli import EXIT_FATAL, main
from comictrans.gui import about
from comictrans.gui.reader_window import (
    ReaderWindow,
    Zoom,
    decode_page,
    measure_page,
)
from comictrans.reading import open_pages

PAGE = (65, 100)
WIDE = (130, 100)


def _image(size: tuple[int, int], shade: int, mode: str = "RGB") -> bytes:
    colour: Any = (shade, shade, shade) if mode == "RGB" else (shade, shade, shade, 0)
    buffer = io.BytesIO()
    Image.new(mode, size, colour).save(buffer, "PNG")
    return buffer.getvalue()


def _chapter(tmp_path: Path, sizes: list[tuple[int, int]], name: str = "chapter.cbz") -> Path:
    chapter = tmp_path / name
    with zipfile.ZipFile(chapter, "w") as archive:
        for number, size in enumerate(sizes, start=1):
            archive.writestr(f"{number:03d}.png", _image(size, 10 * number))
    return chapter


def _until(condition: Callable[[], bool], timeout: float = 10.0) -> None:
    """Let the loader thread's signals through until ``condition`` holds."""
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "the reader never got there"
        QApplication.processEvents()
        time.sleep(0.005)


@contextmanager
def _reader(target: Path) -> Iterator[ReaderWindow]:
    with open_pages(target) as pages:
        window = ReaderWindow(pages, target.name)
        window.resize(600, 500)
        window.show()
        try:
            yield window
        finally:
            window.shutdown()
            window.hide()


def _on_screen(window: ReaderWindow) -> bool:
    """Every page of the spread on screen is drawn, not a placeholder."""
    items = window._view.scene().items()
    pictures = [item for item in items if isinstance(item, QGraphicsPixmapItem)]
    return len(pictures) == len(window.spread)


def _press(window: ReaderWindow, key: Qt.Key) -> None:
    for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
        QApplication.sendEvent(window._view, QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier))


def _all_measured(window: ReaderWindow) -> bool:
    return all(size is not None for size in window._sizes)


# -- paging -------------------------------------------------------------------


def test_the_arrows_page_through_one_at_a_time(qapp: object, tmp_path: Path) -> None:
    with _reader(_chapter(tmp_path, [PAGE] * 4)) as window:
        _until(lambda: _on_screen(window))
        assert window.spread == (0,)

        _press(window, Qt.Key.Key_Right)
        _press(window, Qt.Key.Key_Right)
        assert window.spread == (2,)

        _press(window, Qt.Key.Key_Left)
        assert window.spread == (1,)

        _press(window, Qt.Key.Key_End)
        assert window.spread == (3,)
        _press(window, Qt.Key.Key_Right)
        assert window.spread == (3,), "nothing past the last page"

        _press(window, Qt.Key.Key_Home)
        assert window.spread == (0,)


def test_two_pages_opens_with_the_cover_and_shows_a_spread_alone(
    qapp: object, tmp_path: Path
) -> None:
    with _reader(_chapter(tmp_path, [PAGE, PAGE, PAGE, WIDE, PAGE, PAGE])) as window:
        _until(lambda: _all_measured(window))
        window._two_pages_action.trigger()

        seen = [window.spread]
        for _ in range(4):
            _press(window, Qt.Key.Key_PageDown)
            seen.append(window.spread)

        assert seen == [(0,), (1, 2), (3,), (4, 5), (4, 5)]


def test_a_spread_found_while_reading_two_up_moves_the_pairs_but_not_the_page(
    qapp: object, tmp_path: Path
) -> None:
    """Two pages chosen before anything is measured, then the sizes arrive.

    Chosen before the event loop has run at all, so no size has reached the
    window yet — they come through it — and the pairing is a guess that the
    spread on page 3 has to correct.
    """
    with _reader(_chapter(tmp_path, [PAGE, PAGE, WIDE, PAGE, PAGE, PAGE])) as window:
        window._two_pages_action.trigger()
        _press(window, Qt.Key.Key_Right)
        assert window.spread == (1, 2), "every page taken for one page, so far"

        _until(lambda: _all_measured(window))

        assert window._groups == ((0,), (1,), (2,), (3, 4), (5,))
        assert window.spread == (1,), "still reading page 2"


def test_switching_layout_keeps_the_page_being_read(qapp: object, tmp_path: Path) -> None:
    with _reader(_chapter(tmp_path, [PAGE] * 6)) as window:
        for _ in range(3):
            _press(window, Qt.Key.Key_Right)
        assert window.spread == (3,)

        window._two_pages_action.trigger()
        assert window.spread == (3, 4)

        window._one_page_action.trigger()
        assert window.spread == (3,)


def test_right_to_left_turns_the_arrows_and_the_pages_round(qapp: object, tmp_path: Path) -> None:
    with _reader(_chapter(tmp_path, [PAGE] * 5)) as window:
        window._two_pages_action.trigger()
        window._right_to_left_action.setChecked(True)

        _press(window, Qt.Key.Key_Left)
        assert window.spread == (1, 2), "the left arrow is forward, right to left"
        _until(lambda: _on_screen(window))

        # Page 2, the first read, is the one on the right.
        pictures = sorted(
            (item.pos().x(), item.pixmap().toImage().pixelColor(0, 0).red())
            for item in window._view.scene().items()
            if isinstance(item, QGraphicsPixmapItem)
        )
        assert [shade for _x, shade in pictures] == [30, 20]
        assert window._bar.slider.invertedAppearance()

        _press(window, Qt.Key.Key_Right)
        assert window.spread == (0,)


def test_a_page_wider_than_the_window_is_begun_on_the_side_reading_starts(
    qapp: object, tmp_path: Path
) -> None:
    with _reader(_chapter(tmp_path, [(900, 1200)] * 3)) as window:
        across = window._view.horizontalScrollBar()
        window._actual_size_action.trigger()

        _press(window, Qt.Key.Key_Right)
        QApplication.processEvents()
        assert across.maximum() > 0, "the page has to be wider than the window"
        assert across.value() == across.minimum()

        window._right_to_left_action.setChecked(True)
        _press(window, Qt.Key.Key_Left)
        QApplication.processEvents()
        assert across.value() == across.maximum(), "right to left starts on the right"


def test_the_count_says_which_pages_are_on_screen(qapp: object, tmp_path: Path) -> None:
    with _reader(_chapter(tmp_path, [PAGE] * 5)) as window:
        assert window._bar.count.text() == "page 1 of 5"

        window._two_pages_action.trigger()
        _press(window, Qt.Key.Key_Right)

        assert window._bar.count.text() == "pages 2–3 of 5"  # noqa: RUF001


# -- the bar ------------------------------------------------------------------


def _move(window: ReaderWindow, y: int) -> None:
    viewport = window._view.viewport()
    point = QPointF(100, y)
    QApplication.sendEvent(
        viewport,
        QMouseEvent(
            QEvent.Type.MouseMove,
            point,
            viewport.mapToGlobal(point),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )


def test_the_slider_is_there_only_while_the_pointer_is(qapp: object, tmp_path: Path) -> None:
    with _reader(_chapter(tmp_path, [PAGE] * 5)) as window:
        bottom = window._view.viewport().height() - 2
        assert not window._bar.isVisible()

        _move(window, 10)
        assert not window._bar.isVisible(), "over the pages, not over the bar"

        _move(window, bottom)
        assert window._bar.isVisible()
        assert window._bar.geometry().bottom() == window._view.viewport().geometry().bottom()

        QApplication.sendEvent(window._bar, QEvent(QEvent.Type.Leave))
        assert not window._bar.isVisible()


def test_the_slider_moves_the_page_and_never_takes_the_arrows(qapp: object, tmp_path: Path) -> None:
    with _reader(_chapter(tmp_path, [PAGE] * 8)) as window:
        window._bar.slider.setValue(5)
        assert window.spread == (5,)

        _press(window, Qt.Key.Key_Right)
        assert window._bar.slider.value() == 6, "the slider follows the keys"
        assert window._bar.slider.focusPolicy() == Qt.FocusPolicy.NoFocus


# -- what is held -------------------------------------------------------------


def test_only_the_pages_near_the_one_on_screen_are_held(qapp: object, tmp_path: Path) -> None:
    """The spread on screen, the next and the previous: six pages at most."""
    with _reader(_chapter(tmp_path, [PAGE] * 20)) as window:
        window._two_pages_action.trigger()
        for _ in range(8):
            _press(window, Qt.Key.Key_Right)
            _until(lambda: _on_screen(window))
        _until(lambda: len(window._images) == 6)

        assert window.spread == (15, 16)
        assert sorted(window._images) == [13, 14, 15, 16, 17, 18]


def test_reading_a_chapter_writes_nothing(qapp: object, tmp_path: Path) -> None:
    chapter = _chapter(tmp_path, [PAGE] * 4)
    before = sorted(tmp_path.iterdir())

    with _reader(chapter) as window:
        for _ in range(3):
            _press(window, Qt.Key.Key_Right)
            _until(lambda: _on_screen(window))

    assert sorted(tmp_path.iterdir()) == before


def test_a_page_that_cannot_be_read_says_so_in_its_place(qapp: object, tmp_path: Path) -> None:
    chapter = tmp_path / "chapter.cbz"
    with zipfile.ZipFile(chapter, "w") as archive:
        archive.writestr("001.png", _image(PAGE, 10))
        archive.writestr("002.png", b"not an image at all")

    with _reader(chapter) as window:
        _press(window, Qt.Key.Key_Right)

        def said() -> str:
            texts = [
                item.text()
                for item in window._view.scene().items()
                if isinstance(item, QGraphicsSimpleTextItem)
            ]
            return texts[0] if texts else ""

        _until(lambda: "could not be read" in said())
        assert said().startswith("Page 2 could not be read: Qt could not decode it")


# -- zoom ---------------------------------------------------------------------


def test_the_page_fits_the_window_until_a_zoom_is_chosen(qapp: object, tmp_path: Path) -> None:
    with _reader(_chapter(tmp_path, [PAGE] * 3)) as window:
        view = window._view
        _until(lambda: _on_screen(window))
        assert view.zoom_mode is Zoom.FIT_PAGE
        assert window._zoom_button.text() == "Fit Page"

        window._fit_width_action.trigger()
        QApplication.processEvents()  # the scroll bar is laid out on the way
        assert round(view.sceneRect().width() * view.scale_factor) == view.viewport().width()
        assert view.horizontalScrollBar().maximum() == 0, "as wide as the window, not wider"
        assert view.mapFromScene(0, 0).x() == 0, "flush with the edge, not off by a bar"

        window._actual_size_action.trigger()
        window._zoom_in_action.trigger()
        assert view.zoom_mode is Zoom.CHOSEN
        assert window._zoom_button.text() == "125%"

        window.resize(700, 600)
        QApplication.processEvents()
        assert view.scale_factor == pytest.approx(1.25), "a chosen zoom survives a resize"

        _press(window, Qt.Key.Key_Right)
        assert view.scale_factor == pytest.approx(1.25), "and a turned page"


# -- decoding -----------------------------------------------------------------


def test_a_transparent_page_is_shown_on_white_paper(qapp: object) -> None:
    image = decode_page(_image((4, 4), 0, mode="RGBA"))

    assert not image.hasAlphaChannel()
    assert image.pixelColor(0, 0).name() == "#ffffff"


def test_a_page_is_measured_from_its_header(qapp: object) -> None:
    assert measure_page(_image((120, 80), 0)) == (120, 80)
    assert measure_page(b"not an image") is None


# -- closing and starting -----------------------------------------------------


def test_closing_the_window_stops_the_reading(qapp: object, tmp_path: Path) -> None:
    with open_pages(_chapter(tmp_path, [PAGE] * 3)) as pages:
        window = ReaderWindow(pages, "chapter.cbz")
        window.show()
        window.close()

        assert not window._loader.isRunning()


def test_read_opens_the_window_on_a_chapter(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from comictrans.gui import app as gui_app

    shown: list[str] = []

    def event_loop(self: object) -> int:
        shown.extend(
            widget.windowTitle()
            for widget in QApplication.topLevelWidgets()
            if isinstance(widget, ReaderWindow) and widget.isVisible()
        )
        return 0

    monkeypatch.setattr(QApplication, "exec", event_loop)

    assert gui_app.read(_chapter(tmp_path, [PAGE] * 2)) == 0
    assert shown == [f"chapter.cbz — {about.NAME}"]


def test_read_refuses_what_extract_refuses(
    qapp: object, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["read", str(tmp_path / "missing.cbz")]) == EXIT_FATAL
    assert "input path does not exist" in capsys.readouterr().err


def test_help_opens_the_guide_where_this_window_is_described(qapp: object, tmp_path: Path) -> None:
    from comictrans.gui.help_dialog import document
    from comictrans.gui.reader_window import GUIDE_SECTION

    assert f'<a name="{GUIDE_SECTION}"></a>' in document()

    with _reader(_chapter(tmp_path, [PAGE])) as window:
        window._help_action.trigger()

        assert window._help is not None and window._help.isVisible()
        window._help.hide()
