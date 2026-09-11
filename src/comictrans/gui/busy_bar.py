"""A small bar in the status bar, saying how far a preview has got.

It starts indeterminate and becomes determinate, because that is the shape of
what is actually known. A preview opens the page and plans its regions before
it can say how many there are to erase, so until the first count arrives the
only true statement is *something is happening* — a ``QProgressBar`` with an
empty range, which Qt animates from its own timer. From the first count on
there is a real fraction, and it shows it.

The fraction counts regions erased, which is not an arbitrary choice of unit:
on an eleven-megapixel page with ten regions, erasing is 80% of the whole
preview at 1.28s a region, against 0.099s to plan one and 0.002s to draw one.
A bar that follows the erases follows the wait.

**It exists because the work moved off this thread.** While the render
blocked, nothing could animate: the event loop was not turning, and the
status bar had to be repainted by hand just to get one message onto the
screen. A spinner then would have been a still picture of a spinner. With the
render on a worker thread the loop turns throughout, so this is the first
point at which an animation is a true statement rather than a decoration.

Hidden when idle, and fixed-width, so the status bar does not jump as it
comes and goes.
"""

from __future__ import annotations

from PySide6.QtWidgets import QProgressBar, QWidget

WIDTH = 90
"""Wide enough to read as a progress bar and narrow enough to leave the
status bar's message the room it needs. The bar says nothing by itself; the
message beside it is what says what is being waited for."""

HEIGHT = 12
"""A status bar's height is the font's, and a bar at its full natural height
makes the bar the loudest thing in the window for a second and a half."""


class BusyBar(QProgressBar):
    """Shown while something runs; indeterminate until it has a count.

    ``setRange(0, 0)`` is Qt's indeterminate mode, animated from Qt's own
    timer with nothing here driving it. :meth:`advance` swaps it for a real
    range the moment there is one, and :meth:`set_busy` puts it back on the
    way in, so the next run starts from "something is happening" rather than
    from the last run's last fraction.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # No percentage drawn on it. An indeterminate bar has none, and a
        # determinate one this size has no room for the text.
        self.setTextVisible(False)
        self.setFixedSize(WIDTH, HEIGHT)
        self._start_waiting()
        self.hide()

    def _start_waiting(self) -> None:
        self.setRange(0, 0)

    @property
    def waiting(self) -> bool:
        """True while it is saying *something* rather than *how much*."""
        return self.minimum() == self.maximum() == 0

    def set_busy(self, busy: bool) -> None:
        """Show or hide it, and reset it to waiting on the way in."""
        if busy:
            self._start_waiting()
        self.setVisible(busy)

    def advance(self, done: int, total: int) -> None:
        """Report a real fraction, switching out of the waiting state.

        A total of zero stays indeterminate: a page whose every region failed
        to plan has nothing to erase, and a bar of zero steps would be a full
        bar for a render that has not finished.
        """
        if total <= 0:
            return
        if self.maximum() != total:
            self.setRange(0, total)
        self.setValue(done)


__all__ = ["HEIGHT", "WIDTH", "BusyBar"]
