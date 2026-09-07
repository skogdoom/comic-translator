from __future__ import annotations

import stat
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from comictrans.errors import InputError
from comictrans.imaging import collect_inputs, load_page

from .conftest import save_page


def _blank(width: int = 40, height: int = 30) -> np.ndarray:
    return np.full((height, width, 3), 255, dtype=np.uint8)


def test_collect_inputs_sorts_naturally_and_reports_skips(tmp_path: Path) -> None:
    for name in ("page10.png", "page2.png", "page1.jpg"):
        save_page(_blank(), tmp_path / name)
    (tmp_path / "notes.txt").write_text("not an image")
    (tmp_path / ".hidden.png").write_bytes(b"")
    (tmp_path / "subdir").mkdir()

    accepted, skipped = collect_inputs(tmp_path)

    assert [p.name for p in accepted] == ["page1.jpg", "page2.png", "page10.png"]
    reasons = {p.name: reason for p, reason in skipped}
    assert set(reasons) == {"notes.txt", ".hidden.png", "subdir"}
    assert "non-recursive" in reasons["subdir"]


def test_collect_inputs_accepts_a_single_file(tmp_path: Path) -> None:
    path = save_page(_blank(), tmp_path / "one.tif")
    assert collect_inputs(path) == ([path], [])


def test_collect_inputs_rejects_unsupported_file(tmp_path: Path) -> None:
    path = tmp_path / "scan.bmp"
    path.write_bytes(b"nope")
    with pytest.raises(InputError, match="unsupported input file type"):
        collect_inputs(path)


def test_collect_inputs_rejects_empty_directory(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="no supported images"):
        collect_inputs(tmp_path)


def test_load_page_captures_metadata_for_the_apply_pass(tmp_path: Path) -> None:
    path = save_page(_blank(64, 48), tmp_path / "page.png", dpi=(300, 300))
    page = load_page(path)
    assert (page.width, page.height) == (64, 48)
    assert page.meta.format == "PNG"
    # PNG stores resolution as integer pixels per metre, so 300 dpi is not exact.
    assert page.meta.dpi == pytest.approx((300.0, 300.0), abs=0.01)
    assert len(page.sha256) == 64


def test_load_page_does_not_modify_the_source(tmp_path: Path) -> None:
    path = save_page(_blank(), tmp_path / "page.png")
    before = path.stat()
    digest = load_page(path).sha256
    after = path.stat()
    assert (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size)
    assert digest == load_page(path).sha256


def test_load_page_works_on_a_read_only_file(tmp_path: Path) -> None:
    path = save_page(_blank(), tmp_path / "page.png")
    path.chmod(stat.S_IRUSR)
    try:
        assert load_page(path).width == 40
    finally:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_load_page_reports_a_corrupt_image(tmp_path: Path) -> None:
    path = tmp_path / "broken.png"
    path.write_bytes(b"not really a png")
    with pytest.raises(InputError, match="cannot read image"):
        load_page(path)


def test_transparency_is_flattened_onto_white_not_dropped(tmp_path: Path) -> None:
    # Transparent pixels whose colour channels are black: dropping alpha would
    # turn them into a solid black band and shift every threshold on the page.
    rgba = np.zeros((10, 10, 4), dtype=np.uint8)
    rgba[:, :, 3] = 0
    rgba[4:6, 4:6] = (10, 20, 30, 255)
    path = tmp_path / "alpha.png"
    Image.fromarray(rgba, mode="RGBA").save(path)

    page = load_page(path)

    assert page.meta.had_alpha
    assert tuple(page.rgb[0, 0]) == (255, 255, 255), "transparent area flattened to white"
    assert tuple(page.rgb[4, 4]) == (10, 20, 30), "opaque pixels untouched"


def test_opaque_images_report_no_alpha(tmp_path: Path) -> None:
    path = save_page(_blank(), tmp_path / "page.png")
    assert load_page(path).meta.had_alpha is False
