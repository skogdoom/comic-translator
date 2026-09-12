from __future__ import annotations

import stat
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from comictrans.errors import InputError
from comictrans.imaging import collect_inputs, load_page, page_size
from comictrans.imaging import save_page as write_output_page

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


def test_page_size_reads_the_header_without_decoding_the_pixels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = save_page(_blank(width=40, height=30), tmp_path / "page.png")
    decoded: list[str] = []
    original = Image.Image.load
    monkeypatch.setattr(
        Image.Image, "load", lambda self: (decoded.append("decoded"), original(self))[1]
    )

    assert page_size(path) == (40, 30)
    assert decoded == []


def test_page_size_answers_none_for_what_it_cannot_open(tmp_path: Path) -> None:
    (tmp_path / "broken.png").write_bytes(b"not really a png")

    assert page_size(tmp_path / "broken.png") is None
    assert page_size(tmp_path / "not-there.png") is None


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


def test_save_page_writes_the_source_transparency_back(tmp_path: Path) -> None:
    rgba = np.zeros((12, 12, 4), dtype=np.uint8)
    rgba[:, :, :3] = 40
    rgba[:, :, 3] = 255
    rgba[0:4, 0:4, 3] = 0
    source = tmp_path / "src.png"
    Image.fromarray(rgba, mode="RGBA").save(source)

    page = load_page(source)
    destination = tmp_path / "out.png"
    write_output_page(Image.fromarray(page.rgb), destination, page.meta, alpha=page.alpha)

    with Image.open(destination) as written:
        assert written.mode == "RGBA"
        assert np.array_equal(np.asarray(written.getchannel("A")), page.alpha)


def test_save_page_keeps_opaque_sources_opaque(tmp_path: Path) -> None:
    page = load_page(save_page(_blank(), tmp_path / "opaque.png"))
    destination = tmp_path / "out.png"
    write_output_page(Image.fromarray(page.rgb), destination, page.meta, alpha=page.alpha)
    with Image.open(destination) as written:
        assert written.mode == "RGB"


def test_jpeg_output_cannot_carry_alpha_and_says_so(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    rgba = np.full((10, 10, 4), 200, dtype=np.uint8)
    rgba[0:3, 0:3, 3] = 0
    source = tmp_path / "src.png"
    Image.fromarray(rgba, mode="RGBA").save(source)
    page = load_page(source)

    with caplog.at_level(logging.WARNING):
        write_output_page(
            Image.fromarray(page.rgb), tmp_path / "out.jpg", page.meta, "jpeg", page.alpha
        )

    assert "cannot store transparency" in caplog.text
    with Image.open(tmp_path / "out.jpg") as written:
        assert written.mode == "RGB"
