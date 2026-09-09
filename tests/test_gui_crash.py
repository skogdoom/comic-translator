"""Fatal-signal traces: the one thing that survives the process dying.

Everything here runs against an explicit directory, and the end-to-end case
runs in a subprocess — because the only honest way to test that a segfault
leaves a trace is to cause one.
"""

from __future__ import annotations

import faulthandler
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from comictrans import __version__
from comictrans.gui import crash


@pytest.fixture(autouse=True)
def _leave_faulthandler_as_found() -> object:
    """The suite's own handlers are not this module's to keep."""
    was_enabled = faulthandler.is_enabled()
    yield
    crash.disable()
    if was_enabled:
        faulthandler.enable()


def test_macos_logs_go_where_macos_keeps_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not Application Support, which is what QStandardPaths would say."""
    monkeypatch.delenv(crash.LOG_DIR_ENV, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")

    assert crash.crash_directory() == Path.home() / "Library" / "Logs" / "comictrans"


def test_elsewhere_they_go_to_the_xdg_state_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(crash.LOG_DIR_ENV, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", "/var/state")

    assert crash.crash_directory() == Path("/var/state/comictrans")

    monkeypatch.delenv("XDG_STATE_HOME")
    assert crash.crash_directory() == Path.home() / ".local" / "state" / "comictrans"


def test_the_environment_can_put_them_somewhere_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(crash.LOG_DIR_ENV, str(tmp_path))

    assert crash.crash_directory() == tmp_path


def test_enabling_writes_a_banner_naming_the_version(tmp_path: Path) -> None:
    path = crash.enable(tmp_path)

    assert path == tmp_path / crash.FILENAME
    assert faulthandler.is_enabled()
    banner = path.read_text(encoding="utf-8")
    assert __version__ in banner
    assert "review started" in banner, "so a trace can be told from its session"


def test_a_second_session_appends_rather_than_losing_the_first(tmp_path: Path) -> None:
    crash.enable(tmp_path)
    crash.disable()
    crash.enable(tmp_path)

    text = (tmp_path / crash.FILENAME).read_text(encoding="utf-8")

    assert text.count("review started") == 2


def test_an_unwritable_directory_costs_the_traces_and_nothing_else(tmp_path: Path) -> None:
    """A diagnostic that stops the window opening is worse than no diagnostic."""
    blocked = tmp_path / "a-file"
    blocked.write_text("not a directory", encoding="utf-8")

    assert crash.enable(blocked / "under-a-file") is None


def test_the_file_is_rolled_once_it_gets_large(tmp_path: Path) -> None:
    path = tmp_path / crash.FILENAME
    path.write_text("x" * (crash.MAX_BYTES + 1), encoding="utf-8")

    crash.enable(tmp_path)

    assert (tmp_path / "review-crash.log.old").stat().st_size > crash.MAX_BYTES
    assert path.stat().st_size < crash.MAX_BYTES, "started again, keeping one generation"


_SEGFAULT = """
import ctypes, sys
from pathlib import Path
sys.path.insert(0, {src!r})
from comictrans.gui import crash
crash.enable(Path({directory!r}))

def the_line_that_died():
    ctypes.string_at(0)

the_line_that_died()
"""


def test_a_real_segfault_leaves_a_trace_naming_the_line(tmp_path: Path) -> None:
    """The whole point, and the only honest way to check it.

    In a subprocess, because the test is that the process dies.
    """
    source = Path(__file__).resolve().parents[1] / "src"
    script = tmp_path / "die.py"
    script.write_text(
        textwrap.dedent(_SEGFAULT).format(src=str(source), directory=str(tmp_path)),
        encoding="utf-8",
    )

    finished = subprocess.run(
        [sys.executable, str(script)], capture_output=True, timeout=120, check=False
    )

    assert finished.returncode != 0, "it was supposed to die"
    trace = (tmp_path / crash.FILENAME).read_text(encoding="utf-8")
    assert "Fatal Python error" in trace
    assert "the_line_that_died" in trace, "the trace names the line, not just the signal"
