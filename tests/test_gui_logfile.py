"""The application log, and the hooks that fill it.

Everything here runs against an explicit directory and a logger the test
owns, so nothing lands in the log of whoever is running the suite.
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

import pytest

from comictrans.gui import logfile
from comictrans.model import Box, Color, Geometry, Region


@pytest.fixture(autouse=True)
def _leave_logging_as_found() -> object:
    """Neither the root logger nor the hooks are this module's to keep."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    excepthook, threadhook = sys.excepthook, threading.excepthook
    yield
    logfile.uninstall()
    logfile.set_notifier(None)
    root.handlers[:] = handlers
    root.setLevel(level)
    sys.excepthook = excepthook
    threading.excepthook = threadhook


# -- where it goes -----------------------------------------------------


def test_macos_logs_go_where_macos_keeps_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not Application Support, which is what QStandardPaths would say."""
    monkeypatch.delenv(logfile.LOG_DIR_ENV, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")

    assert logfile.log_directory() == Path.home() / "Library" / "Logs" / "comictrans"


def test_elsewhere_they_go_to_the_xdg_state_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(logfile.LOG_DIR_ENV, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", "/var/state")

    assert logfile.log_directory() == Path("/var/state/comictrans")

    monkeypatch.delenv("XDG_STATE_HOME")
    assert logfile.log_directory() == Path.home() / ".local" / "state" / "comictrans"


def test_the_environment_can_put_them_somewhere_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(logfile.LOG_DIR_ENV, str(tmp_path))

    assert logfile.log_directory() == tmp_path


def test_the_log_and_the_crash_traces_sit_side_by_side(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Whoever finds one finds the other."""
    from comictrans.gui import crash

    monkeypatch.setenv(logfile.LOG_DIR_ENV, str(tmp_path))

    assert logfile.install() == tmp_path / logfile.FILENAME
    assert crash.enable() == tmp_path / crash.FILENAME
    crash.disable()


# -- what reaches it ---------------------------------------------------


def test_installing_writes_records_to_the_file(tmp_path: Path) -> None:
    path = logfile.install(tmp_path)
    assert path is not None

    logging.getLogger("comictrans.test").warning("a page was skipped")

    assert "a page was skipped" in path.read_text(encoding="utf-8")


def test_the_file_takes_debug_even_when_stderr_does_not(tmp_path: Path) -> None:
    """ "Log the risky thing before doing it" is only worth doing if it lands."""
    root = logging.getLogger()
    stream = logging.StreamHandler()
    root.handlers[:] = [stream]
    root.setLevel(logging.WARNING)  # what -q leaves behind

    path = logfile.install(tmp_path)
    assert path is not None
    logging.getLogger("comictrans.test").debug("about to open %s", "page-004.png")

    assert "page-004.png" in path.read_text(encoding="utf-8")
    assert stream.level == logging.WARNING, "stderr stays where the CLI put it"


def test_an_unwritable_directory_costs_the_log_and_nothing_else(tmp_path: Path) -> None:
    blocked = tmp_path / "a-file"
    blocked.write_text("not a directory", encoding="utf-8")

    assert logfile.install(blocked / "under-a-file") is None


# -- what must never reach it ------------------------------------------


def _region() -> Region:
    return Region(
        id="page-001-001",
        image="page-001.png",
        order=1,
        geometry=Geometry.EXACT,
        polygon=Box(0, 0, 10, 10).as_polygon(),
        fill_color=Color(255, 255, 255),
        text_color=Color(0, 0, 0),
        confidence=0.9,
        source_text="IL SEGRETO",
        translation="THE SECRET",
        notes="ask the editor",
    )


def test_a_region_logged_whole_does_not_take_the_comic_with_it(tmp_path: Path) -> None:
    """The easy way to write a log line is to interpolate the whole object.

    So the three fields holding the comic are kept out of Region's repr
    rather than out of the log lines somebody remembers to be careful in.
    """
    path = logfile.install(tmp_path)
    assert path is not None

    logging.getLogger("comictrans.test").info("region %s", _region())
    written = path.read_text(encoding="utf-8")

    assert "page-001-001" in written, "the id is the point of logging it"
    assert "IL SEGRETO" not in written
    assert "THE SECRET" not in written
    assert "ask the editor" not in written


def test_a_traceback_carrying_a_region_does_not_either(tmp_path: Path) -> None:
    """A local variable in a frame is the other way text escapes."""
    path = logfile.install(tmp_path)
    assert path is not None

    try:
        region = _region()  # noqa: F841 - the point is that it is a local
        raise ValueError("something went wrong")
    except ValueError:
        logging.getLogger("comictrans.test").exception("while rendering")

    written = path.read_text(encoding="utf-8")
    assert "something went wrong" in written
    assert "THE SECRET" not in written


# -- the hooks ---------------------------------------------------------


def test_an_unhandled_exception_reaches_the_log_and_the_notifier(tmp_path: Path) -> None:
    path = logfile.install(tmp_path)
    assert path is not None
    said: list[str] = []
    logfile.set_notifier(said.append)
    logfile.install_hooks()

    try:
        raise RuntimeError("nobody caught this")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())  # type: ignore[arg-type]

    written = path.read_text(encoding="utf-8")
    assert "nobody caught this" in written
    assert "Traceback" in written
    assert said == ["RuntimeError in the window — see the log"]


def test_a_worker_thread_that_raises_is_logged_under_its_own_name(tmp_path: Path) -> None:
    path = logfile.install(tmp_path)
    assert path is not None
    logfile.install_hooks()

    def raises() -> None:
        raise ValueError("on the worker")

    worker = threading.Thread(target=raises, name="comictrans-renderjob")
    worker.start()
    worker.join(10)

    written = path.read_text(encoding="utf-8")
    assert "on the worker" in written
    assert "comictrans-renderjob" in written


def test_ctrl_c_is_left_to_python(tmp_path: Path) -> None:
    """An interrupt is not a bug and does not want a traceback in a file."""
    path = logfile.install(tmp_path)
    assert path is not None
    logfile.install_hooks()

    try:
        raise KeyboardInterrupt
    except KeyboardInterrupt:
        sys.excepthook(*sys.exc_info())  # type: ignore[arg-type]

    assert "KeyboardInterrupt" not in path.read_text(encoding="utf-8")


def test_a_notifier_that_itself_fails_does_not_take_the_hook_down(tmp_path: Path) -> None:
    logfile.install(tmp_path)

    def unhelpful(_message: str) -> None:
        raise RuntimeError("the status bar is gone")

    logfile.set_notifier(unhelpful)
    logfile.install_hooks()

    try:
        raise ValueError("the original problem")
    except ValueError:
        sys.excepthook(*sys.exc_info())  # type: ignore[arg-type]
