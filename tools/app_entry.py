"""What the bundled application runs when it is double-clicked.

One line of work, and it is deliberately not ``cli.main``. A bundle has no
command line: macOS launches it with arguments of its own (``-psn_…`` on
older systems, a document path through an Apple Event on newer ones), and an
argument parser handed those either refuses to start or opens the wrong
thing. The window is the only thing this build is for, so it opens the
window.

Kept out of the package because it is not part of it. Nothing imports this;
PyInstaller's spec names it, and the wheel never sees it.
"""

from __future__ import annotations

import sys

from comictrans.gui.app import run

if __name__ == "__main__":
    sys.exit(run())
