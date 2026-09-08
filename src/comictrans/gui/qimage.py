"""The one place a Pillow image becomes something Qt can paint.

Kept to a single function so nothing else in the widget layer needs to know
how that conversion works, or that ``PIL.ImageQt`` is what does it.
"""

from __future__ import annotations

from PIL import Image
from PIL.ImageQt import ImageQt
from PySide6.QtGui import QPixmap


def to_pixmap(image: Image.Image) -> QPixmap:
    return QPixmap.fromImage(ImageQt(image))
