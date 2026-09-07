# Fixture images

Drop real scanned pages here. Nothing in this directory is committed as image
data by the tool itself, and the synthetic pages the unit tests use are
generated in `tests/conftest.py` rather than stored.

`tests/test_fixtures.py` picks up any `.png`, `.jpg`, `.jpeg`, `.tif`, or
`.tiff` file in this directory and runs the real detection pipeline over it,
so a page that used to work stays working. Those tests skip when the
directory is empty.

Useful things to add, roughly in order of how much they will teach you:

- a plain white balloon on flat art (the easy case)
- a white-on-black caption box (colour round-trip)
- a screentoned or halftoned page (threshold noise)
- two balloons joined by a tail, and two that share an edge
- a borderless caption sitting directly on artwork
- a page with a balloon touching or broken by the panel border
- the same page scanned at two resolutions (ratios, not pixel constants)
- a multi-panel page where each panel is small relative to the page, which
  is what defeats guards expressed as a fraction of the page
