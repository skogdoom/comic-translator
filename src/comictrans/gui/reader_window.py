"""The reader window: a chapter paged through, and nothing else.

Its own window with its own entry point, ``comictrans read``, and not a mode
of the review window. It opens a chapter rather than a plan, which is what
keeps it small: no document, no undo, nothing dirty, nothing to save.

**It reads what extract reads, and writes nothing** — a chapter file is read
where it is, a page at a time; see :mod:`comictrans.reading`. What it holds
decoded is the spread on screen and one either side of it, so a chapter of
any length costs the same to have open.

**Decoding is off the window's thread.** A page is not free to decode —
measured on an eleven-megapixel page, 55ms for a JPEG and 71ms for a PNG
through Qt, and 143ms and 242ms through Pillow, which is why this is Qt's
decoder and not the pipeline's — and a reader is the one place that cost is
felt on a keypress rather than once per run. So pages are decoded on a
thread and the next spread is decoded before it is asked for: turning a page
at reading pace shows one that is already there.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Sequence
from enum import Enum

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QEvent,
    QIODevice,
    QObject,
    QRectF,
    QSignalBlocker,
    Qt,
    QThread,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QColor,
    QImage,
    QImageReader,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPixmap,
    QResizeEvent,
    QShortcut,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QSlider,
    QToolBar,
    QToolButton,
    QWidget,
)

from ..errors import ComictransError, InputError
from ..reading import Pages, Size, group_of, pages_to_keep, spreads
from . import about
from .canvas import ZOOM_MAX, ZOOM_MIN, ZOOM_STEP

log = logging.getLogger(__name__)

PLACEHOLDER_SIZE: Size = (650, 1000)
"""The shape of a page not yet read, when no page has been: a printed one's."""


def decode_page(data: bytes) -> QImage:
    """A page's bytes as an image to paint, transparency flattened onto white.

    White because pages are paper, and because it is what ``load_page`` does
    for every other pass: a page shown here looks like the page ``extract``
    reads. Raises :class:`InputError` for bytes Qt cannot make an image of.
    """
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    size = reader.size()
    image = reader.read()
    if image.isNull():
        # The size, when the header gave one, because the likeliest reason a
        # page with a readable header will not decode is that it is larger
        # than Qt will hold — 256MB, its default, which nothing here raises —
        # and Qt's own word for that is "Unable to read image data".
        what = f"this {size.width()}x{size.height()} image" if size.isValid() else "it"
        raise InputError(f"Qt could not decode {what}: {reader.errorString()}")
    if not image.hasAlphaChannel():
        return image
    flat = QImage(image.size(), QImage.Format.Format_RGB32)
    flat.fill(QColor("white"))
    painter = QPainter(flat)
    painter.drawImage(0, 0, image)
    painter.end()
    return flat


def measure_page(data: bytes) -> Size | None:
    """A page's size from its header, decoding nothing. ``None`` if unreadable."""
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    size = QImageReader(buffer).size()
    return (size.width(), size.height()) if size.isValid() else None


class PageLoader(QThread):
    """Reads and decodes pages on one thread, most wanted first.

    **One thread, and it is the only thing that reads the chapter.** A zip
    handle, a RAR tool and a PDF reader are none of them made to be read
    from two threads at once, and one is enough: a page decodes in well
    under a tenth of a second, so the next spread is ready long before
    anybody has finished reading this one.

    **Two kinds of work.** Decoding is what the window wants on screen and
    next, in the order it asks, replaced every time it asks. Measuring is
    every other page's size, read from its header: two pages at a time has
    to know where the spreads are, and knowing only about pages already seen
    would pair everything after a jump as though there were no spread before
    it. Measuring happens only when there is nothing to decode.

    Subclasses ``QThread`` and overrides ``run()`` for the reason
    ``run_job`` gives: nothing is delivered to it through slots — it is
    told what to do through a condition variable — so it needs no event
    loop, and ``wait()`` returns when the work stops.
    """

    decoded = Signal(int, object, str)
    """``(page, image, error)``: a ``QImage``, or ``None`` and why not."""

    measured = Signal(int, int, int)
    """``(page, width, height)``, once per page, whichever work found it."""

    def __init__(self, pages: Pages, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("comictrans-pageloader")
        self._pages = pages
        self._condition = threading.Condition()
        self._wanted: list[int] = []
        self._unmeasured: deque[int] = deque(range(len(pages)))
        self._measured: set[int] = set()
        self._stopping = False

    def want(self, pages: Sequence[int]) -> None:
        """Decode these, in this order, instead of whatever was asked before."""
        with self._condition:
            self._wanted = list(pages)
            self._condition.notify()

    def stop(self) -> None:
        """Finish the page in hand, do no more, and return once stopped."""
        with self._condition:
            self._stopping = True
            self._condition.notify()
        self.wait()

    def run(self) -> None:
        while True:
            with self._condition:
                while not (self._stopping or self._wanted or self._unmeasured):
                    self._condition.wait()
                if self._stopping:
                    return
                if self._wanted:
                    page, decoding = self._wanted.pop(0), True
                else:
                    page, decoding = self._unmeasured.popleft(), False
                    if page in self._measured:
                        continue
            if decoding:
                self._decode(page)
            else:
                self._measure(page)

    def _read(self, page: int) -> tuple[bytes | None, str]:
        try:
            return self._pages.read(page), ""
        except ComictransError as exc:
            return None, str(exc)
        except Exception as exc:
            # An archive broken past its header fails here, in whatever the
            # library reading it raises. Said on the page rather than lost
            # on a thread nobody is watching.
            log.exception("reading page %d failed", page + 1)
            return None, f"{type(exc).__name__}: {exc}"

    def _decode(self, page: int) -> None:
        data, error = self._read(page)
        image: QImage | None = None
        if data is not None:
            try:
                image = decode_page(data)
            except InputError as exc:
                error = str(exc)
        if image is not None:
            self._found(page, (image.width(), image.height()))
        self.decoded.emit(page, image, error)

    def _measure(self, page: int) -> None:
        data, _error = self._read(page)
        size = measure_page(data) if data is not None else None
        if size is not None:
            self._found(page, size)

    def _found(self, page: int, size: Size) -> None:
        with self._condition:
            if page in self._measured:
                return
            self._measured.add(page)
        self.measured.emit(page, *size)


class Zoom(Enum):
    """How the view decides its scale."""

    FIT_PAGE = "fit-page"
    """The whole spread in the window, following it as it is resized."""

    FIT_WIDTH = "fit-width"
    """The spread as wide as the window, scrolled down through."""

    CHOSEN = "chosen"
    """A scale somebody picked, kept until they pick another."""


class ReaderView(QGraphicsView):
    """The pages on screen, and the keys and wheel that move through them."""

    turned = Signal(int)
    """``+1`` for the next spread in reading order, ``-1`` for the previous."""

    ended = Signal(bool)
    """``True`` for the last spread, ``False`` for the first."""

    zoom_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor(40, 40, 40))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.right_to_left = False
        self._zoom = Zoom.FIT_PAGE

    @property
    def zoom_mode(self) -> Zoom:
        return self._zoom

    @property
    def scale_factor(self) -> float:
        """1.0 is one screen pixel per page pixel."""
        return float(self.transform().m11())

    def show_pages(self, pages: Sequence[tuple[QImage | None, Size, str]]) -> None:
        """Lay out one spread, in display order, at one height.

        Each entry is ``(image, size, message)``: an image to show, or none
        and a message to show in its place — "reading", or why it could not
        be. Pages of a spread are scaled to the tallest of them, so two scans
        of slightly different sizes still meet along one edge.
        """
        scene = self.scene()
        scene.clear()
        height = max(size[1] for _image, size, _message in pages)
        left = 0.0
        for image, size, message in pages:
            scale = height / size[1]
            width = size[0] * scale
            item: QGraphicsItem
            if image is not None:
                picture = QGraphicsPixmapItem(QPixmap.fromImage(image))
                picture.setTransform(QTransform.fromScale(scale, scale))
                picture.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
                item = picture
            else:
                blank = QGraphicsRectItem(0, 0, width, height)
                blank.setBrush(QColor(60, 60, 60))
                blank.setPen(Qt.PenStyle.NoPen)
                item = blank
                text = QGraphicsSimpleTextItem(message, item)
                text.setBrush(QColor(200, 200, 200))
                # The same size on screen at any zoom, or a page shrunk to
                # fit the window would shrink its message out of sight.
                text.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
                text.setPos(width / 2, height / 2)
            item.setPos(left, 0)
            scene.addItem(item)
            left += width
        scene.setSceneRect(QRectF(0, 0, left, height))
        self._apply_zoom()
        self._to_start()

    # -- zoom ---------------------------------------------------------------

    def fit_page(self) -> None:
        self._zoom = Zoom.FIT_PAGE
        self._apply_zoom()

    def fit_width(self) -> None:
        self._zoom = Zoom.FIT_WIDTH
        self._apply_zoom()
        self._to_start()

    def set_scale(self, factor: float) -> None:
        """Zoom to an absolute factor, clamped, and stop following the window."""
        self._zoom = Zoom.CHOSEN
        clamped = max(ZOOM_MIN, min(ZOOM_MAX, factor))
        self.setTransform(QTransform.fromScale(clamped, clamped))
        self.zoom_changed.emit()

    def zoom_in(self) -> None:
        self.set_scale(self.scale_factor * ZOOM_STEP)

    def zoom_out(self) -> None:
        self.set_scale(self.scale_factor / ZOOM_STEP)

    def _apply_zoom(self) -> None:
        rect = self.sceneRect()
        if rect.isEmpty():
            return
        # The vertical bar is kept on while fitting the width and left to
        # come and go otherwise. Fitting the width is what brings it on —
        # the spread is nearly always taller than the window once it is that
        # wide — and Qt centres against the width the viewport had without
        # it: measured, a spread exactly as wide as the viewport drawn 6px to
        # the right, one 39px narrower at 26px where centred is 19.5. With
        # the bar on from the start there is no other width to centre
        # against, and both land where they should.
        fitting_width = self._zoom is Zoom.FIT_WIDTH
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
            if fitting_width
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        if self._zoom is Zoom.FIT_PAGE:
            self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        elif fitting_width:
            # Asked after the policy is set, so it is already the width left
            # beside a bar that is always there.
            scale = self.maximumViewportSize().width() / rect.width()
            self.setTransform(QTransform.fromScale(scale, scale))
        self.zoom_changed.emit()

    def _to_start(self) -> None:
        """Where a spread is begun: the top, on the side reading starts from."""
        self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())
        across = self.horizontalScrollBar()
        across.setValue(across.maximum() if self.right_to_left else across.minimum())

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        # Only while following the window: a chosen zoom stays chosen.
        if self._zoom is not Zoom.CHOSEN:
            self._apply_zoom()

    # -- keys and wheel -----------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        """The arrows turn pages in the direction the pages lie.

        Left to right, the right arrow is the next page; right to left it is
        the left arrow, because that is the side the next page is on. Page
        Up and Page Down, and Space, go by reading order whatever the
        direction. Up and Down are left to scroll, which is what they do
        when a page is wider than it is shown.
        """
        key = event.key()
        forward = Qt.Key.Key_Left if self.right_to_left else Qt.Key.Key_Right
        backward = Qt.Key.Key_Right if self.right_to_left else Qt.Key.Key_Left
        if key in (forward, Qt.Key.Key_PageDown, Qt.Key.Key_Space):
            self.turned.emit(1)
        elif key in (backward, Qt.Key.Key_PageUp):
            self.turned.emit(-1)
        elif key == Qt.Key.Key_Home:
            self.ended.emit(False)
        elif key == Qt.Key.Key_End:
            self.ended.emit(True)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt override
        """Ctrl (Command on macOS) and the wheel zooms, as in the review window."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)


class PageBar(QWidget):
    """The slider and the page count, over the foot of the pages.

    Hidden until the pointer is over where it sits, and hidden again when it
    leaves: the pages are what the window is for, and a bar across the
    bottom of every one of them is in the way of the last panel on each.
    Laid over the pages rather than beside them, so showing it moves
    nothing.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAutoFillBackground(True)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        # Never the focus: the arrow keys belong to the pages, and a slider
        # holding the focus would take them for itself.
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.count = QLabel()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.count)
        self.hide()

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().leaveEvent(event)
        # Not while the slider is being dragged: the pointer strays off a
        # thin bar all the time mid-drag, and the drag is still going.
        if not self.slider.isSliderDown():
            self.hide()


class ReaderWindow(QMainWindow):
    """One chapter, a spread at a time.

    No menu bar and no Help: there is nothing here a menu would add. Every
    command is on the toolbar or a key, and the keys work without a menu to
    hold them — the zoom commands live in the zoom button's own menu, which
    is enough for Qt to answer their shortcuts.
    """

    def __init__(self, pages: Pages, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pages = pages
        self._sizes: list[Size | None] = [None] * len(pages)
        self._images: dict[int, QImage] = {}
        self._errors: dict[int, str] = {}
        self._two_up = False
        self._groups = spreads(self._sizes, two_up=False)
        self._current = 0
        self._stopped = False

        self.setWindowTitle(self.tr("{0} — {1}").format(title, about.NAME))
        self._view = ReaderView()
        self._view.turned.connect(self._turn)
        self._view.ended.connect(self._to_end)
        self._view.zoom_changed.connect(self._show_zoom)
        self.setCentralWidget(self._view)

        self._bar = PageBar(self._view)
        self._bar.slider.setRange(0, max(0, len(pages) - 1))
        self._bar.slider.valueChanged.connect(self._on_slider)
        self._view.viewport().setMouseTracking(True)
        self._view.viewport().installEventFilter(self)

        self._build_actions()
        self._build_toolbar()

        self._loader = PageLoader(pages, self)
        self._loader.decoded.connect(self._on_decoded)
        self._loader.measured.connect(self._on_measured)
        self._loader.start()

        self.resize(900, 1100)
        self._show_current()
        self._view.setFocus()

    # -- building -----------------------------------------------------------

    def _build_actions(self) -> None:
        self._fit_page_action = QAction(self.tr("Fit &Page"), self)
        self._fit_page_action.setShortcut(QKeySequence("Ctrl+0"))
        self._fit_page_action.triggered.connect(self._view.fit_page)

        self._fit_width_action = QAction(self.tr("Fit &Width"), self)
        self._fit_width_action.triggered.connect(self._view.fit_width)

        self._actual_size_action = QAction(self.tr("&Actual Size"), self)
        self._actual_size_action.setShortcut(QKeySequence("Ctrl+1"))
        self._actual_size_action.triggered.connect(lambda: self._view.set_scale(1.0))

        self._zoom_in_action = QAction(self.tr("Zoom &In"), self)
        self._zoom_in_action.setShortcut(QKeySequence.StandardKey.ZoomIn)
        self._zoom_in_action.triggered.connect(self._view.zoom_in)

        self._zoom_out_action = QAction(self.tr("Zoom &Out"), self)
        self._zoom_out_action.setShortcut(QKeySequence.StandardKey.ZoomOut)
        self._zoom_out_action.triggered.connect(self._view.zoom_out)

        # Unmodified keys, which the review window could not spare: there
        # its text fields hold the focus. Nothing here takes typing.
        self._one_page_action = QAction(self.tr("&One Page"), self)
        self._one_page_action.setShortcut(QKeySequence("1"))
        self._one_page_action.setCheckable(True)
        self._one_page_action.setChecked(True)
        self._one_page_action.triggered.connect(lambda: self._set_two_up(False))

        self._two_pages_action = QAction(self.tr("&Two Pages"), self)
        self._two_pages_action.setShortcut(QKeySequence("2"))
        self._two_pages_action.setCheckable(True)
        self._two_pages_action.triggered.connect(lambda: self._set_two_up(True))

        layout = QActionGroup(self)
        layout.addAction(self._one_page_action)
        layout.addAction(self._two_pages_action)

        self._right_to_left_action = QAction(self.tr("&Right to Left"), self)
        self._right_to_left_action.setCheckable(True)
        self._right_to_left_action.toggled.connect(self._set_right_to_left)

        QShortcut(QKeySequence.StandardKey.Close, self, self.close)

    def _build_toolbar(self) -> None:
        """Three controls, in words.

        Words rather than the review window's icons: there are three of
        them, which is room enough, and none of them has a picture in that
        set to borrow.
        """
        toolbar = QToolBar(self.tr("Reading"), self)
        toolbar.setObjectName("reader_toolbar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        self.setUnifiedTitleAndToolBarOnMac(True)
        self.addToolBar(toolbar)

        self._zoom_button = QToolButton()
        self._zoom_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        zoom_menu = QMenu(self._zoom_button)
        for action in (
            self._fit_page_action,
            self._fit_width_action,
            self._actual_size_action,
            self._zoom_in_action,
            self._zoom_out_action,
        ):
            zoom_menu.addAction(action)
        self._zoom_button.setMenu(zoom_menu)
        toolbar.addWidget(self._zoom_button)
        toolbar.addSeparator()
        toolbar.addAction(self._one_page_action)
        toolbar.addAction(self._two_pages_action)
        toolbar.addSeparator()
        toolbar.addAction(self._right_to_left_action)

    # -- what is on screen --------------------------------------------------

    @property
    def spread(self) -> tuple[int, ...]:
        """The pages on screen, in reading order, counting from zero."""
        return self._groups[self._current]

    def _show_current(self) -> None:
        """Put the current spread on screen and ask for what comes next."""
        keep = pages_to_keep(self._groups, self._current)
        for page in list(self._images):
            if page not in keep:
                del self._images[page]
        self._loader.want([page for page in keep if page not in self._images])

        order = reversed(self.spread) if self._view.right_to_left else self.spread
        self._view.show_pages([self._entry(page) for page in order])
        self._show_position()

    def _entry(self, page: int) -> tuple[QImage | None, Size, str]:
        image = self._images.get(page)
        size = self._sizes[page] or next((s for s in self._sizes if s), PLACEHOLDER_SIZE)
        if image is not None:
            return image, size, ""
        error = self._errors.get(page)
        if error is not None:
            return None, size, self.tr("Page {0} could not be read: {1}").format(page + 1, error)
        return None, size, self.tr("Reading page {0}…").format(page + 1)

    def _show_position(self) -> None:
        first, last = self.spread[0], self.spread[-1]
        total = len(self._pages)
        with QSignalBlocker(self._bar.slider):
            self._bar.slider.setValue(first)
        if first == last:
            text = self.tr("page {0} of {1}").format(first + 1, total)
        else:
            # An en dash, which is what a range of pages is written with.
            text = self.tr("pages {0}–{1} of {2}").format(  # noqa: RUF001
                first + 1, last + 1, total
            )
        self._bar.count.setText(text)

    def _show_zoom(self) -> None:
        mode = self._view.zoom_mode
        if mode is Zoom.FIT_PAGE:
            self._zoom_button.setText(self.tr("Fit Page"))
        elif mode is Zoom.FIT_WIDTH:
            self._zoom_button.setText(self.tr("Fit Width"))
        else:
            self._zoom_button.setText(self.tr("{0}%").format(round(self._view.scale_factor * 100)))

    def _regroup(self) -> None:
        """Group the pages again, keeping the page being read on screen."""
        anchor = self.spread[0]
        groups = spreads(self._sizes, self._two_up)
        if groups == self._groups:
            return
        self._groups = groups
        self._current = group_of(groups, anchor)
        self._show_current()

    # -- moving -------------------------------------------------------------

    def _turn(self, step: int) -> None:
        target = max(0, min(len(self._groups) - 1, self._current + step))
        if target != self._current:
            self._current = target
            self._show_current()

    def _to_end(self, last: bool) -> None:
        self._turn(len(self._groups) if last else -len(self._groups))

    def _on_slider(self, page: int) -> None:
        target = group_of(self._groups, page)
        if target != self._current:
            self._current = target
            self._show_current()

    def _set_two_up(self, two_up: bool) -> None:
        if two_up != self._two_up:
            self._two_up = two_up
            self._regroup()

    def _set_right_to_left(self, right_to_left: bool) -> None:
        self._view.right_to_left = right_to_left
        # The slider runs the way the pages do: dragged towards the side the
        # next page is on, it goes forward.
        self._bar.slider.setInvertedAppearance(right_to_left)
        self._show_current()

    # -- pages arriving -----------------------------------------------------

    def _on_decoded(self, page: int, image: QImage | None, error: str) -> None:
        if page not in pages_to_keep(self._groups, self._current):
            return  # asked for, then scrolled past before it came
        if image is None:
            self._errors[page] = error
        else:
            self._images[page] = image
        if page in self.spread:
            self._show_current()

    def _on_measured(self, page: int, width: int, height: int) -> None:
        was = self._sizes[page]
        self._sizes[page] = (width, height)
        if self._two_up:
            self._regroup()
        if was is None and page in self.spread and page not in self._images:
            self._show_current()  # the placeholder takes the page's own shape

    # -- the bar ------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if watched is self._view.viewport():
            if event.type() == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
                area = self._view.viewport().rect()
                if event.position().y() >= area.bottom() - self._bar.sizeHint().height():
                    self._bar.show()
                    self._bar.raise_()
            elif event.type() == QEvent.Type.Resize:
                # The viewport's own resize rather than the view's: the view
                # hears of its new size before it has laid the viewport out
                # in it, so the viewport's geometry would still be the old.
                self._place_bar()
        return super().eventFilter(watched, event)

    def _place_bar(self) -> None:
        area = self._view.viewport().geometry()
        height = self._bar.sizeHint().height()
        self._bar.setGeometry(area.left(), area.bottom() + 1 - height, area.width(), height)

    # -- closing ----------------------------------------------------------

    def shutdown(self) -> None:
        """Stop reading the chapter. Before it is closed, and safe twice."""
        if not self._stopped:
            self._stopped = True
            self._loader.stop()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        self.shutdown()
        super().closeEvent(event)


__all__ = [
    "PageBar",
    "PageLoader",
    "ReaderView",
    "ReaderWindow",
    "Zoom",
    "decode_page",
    "measure_page",
]
