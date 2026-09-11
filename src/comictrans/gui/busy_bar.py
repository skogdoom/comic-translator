"""A small indeterminate bar in the status bar, for work with no page count.

The run panel's progress bar counts pages, because a chapter has a known
number of them. A preview has one page and no useful fraction to report — the
honest thing to show is not "40%" but *that something is happening*, which is
what an indeterminate bar is for: a ``QProgressBar`` with an empty range
animates itself, from Qt's own timer, in whatever the platform's idiom is.

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
    """Shown while something runs, animating on its own, reporting no number.

    ``setRange(0, 0)`` is Qt's indeterminate mode. Nothing drives it: no
    timer here, no repaint here, and no value to update — which is the point,
    because a value would have to come from somewhere and a preview has none
    to give.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRange(0, 0)
        # No percentage over an indeterminate bar: there is no percentage,
        # and Qt would draw the last value it happened to hold.
        self.setTextVisible(False)
        self.setFixedSize(WIDTH, HEIGHT)
        self.hide()

    def set_busy(self, busy: bool) -> None:
        """Show or hide it. The only thing this widget is asked."""
        self.setVisible(busy)


__all__ = ["HEIGHT", "WIDTH", "BusyBar"]
