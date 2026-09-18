"""The four things an erase can do, in the words every box uses for them.

Three boxes decide this, and they are the same question asked at three
scopes: the inspector's, for one region; the render dialog's, for every
region in a run that does not name one of its own; and the preferences', for
what a new run starts from. They offer the same four things and have to call
them the same, or the window would describe one setting three ways.

They were written out at each of them, which also handed a translator the
same nine sentences twice over — free, and with no way to notice, to render
them differently in each. So the rows live here under one catalogue context,
and each box adds only what is its own: the inspector a "(plan default)" row
at the top, since a region may decline to decide, and the other two nothing,
since a run must.

``QCoreApplication.translate`` rather than ``tr``: a module-level table has
no ``self`` to ask, and it is evaluated at import, which is after the
translator goes in — see ``translations``. Each call is written out in full
because ``lupdate`` reads the source rather than running it, and extracts
nothing from behind a helper. Measured, not assumed.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication

from ..model import Erase

CHOICES: tuple[tuple[str, Erase, str], ...] = (
    (
        QCoreApplication.translate("EraseChoices", "the lettering"),
        Erase.FLAT,
        QCoreApplication.translate(
            "EraseChoices", "repaint the original lettering in the fill colour"
        ),
    ),
    (
        QCoreApplication.translate("EraseChoices", "the whole region"),
        Erase.POLYGON,
        QCoreApplication.translate("EraseChoices", "flood the whole outline with the fill colour"),
    ),
    (
        QCoreApplication.translate("EraseChoices", "reconstruct"),
        Erase.INPAINT,
        QCoreApplication.translate(
            "EraseChoices", "rebuild the lettering's pixels from the ones around them"
        ),
    ),
    (
        QCoreApplication.translate("EraseChoices", "nothing"),
        Erase.NONE,
        QCoreApplication.translate(
            "EraseChoices", "paint nothing; letter straight onto the page as it is"
        ),
    ),
)
"""Label, what it does, and what it does in a sentence.

In the order the inspector and the two dialogs show them, which is roughly
how much of the page each one touches. A region's own ``erase`` always wins
over a run-wide choice, exactly as it does over ``--erase``.
"""

STRATEGIES: tuple[tuple[str, str, str], ...] = tuple(
    (label, str(mode), hint) for label, mode, hint in CHOICES
)
"""The same rows with the strategy under its plain name.

What the two run-wide boxes want: a ``RenderRequest`` carries the name,
``erase.STRATEGIES`` is keyed by it, and a preference is a string like every
other. Converted once here rather than at each box, so nothing hands Qt an
enum for a combo box's data and then asks ``findData`` to match a string
against it.
"""

ERASE_FIELD = QCoreApplication.translate("EraseChoices", "erase")
"""What the field is called, in all three places that ask the question."""

__all__ = ["CHOICES", "ERASE_FIELD", "STRATEGIES"]
