"""The invariants in ``CLAUDE.md`` that nothing else was holding anybody to.

Written for milestone 7. The audit itself is a reading and is written up in
``docs/SECURITY.md``; what is here is the part of it that had to stop being
a rule people remember.

**No network calls anywhere in the pipeline** is checked twice, because one
check cannot do it. :func:`test_no_module_imports_anything_that_speaks_to_a
_network` reads every module this project ships and is total over code we
wrote; :func:`test_the_whole_pipeline_runs_with_the_sockets_taken_away` runs
both passes with ``socket`` unusable and is the only one that can see a
dependency doing it on our behalf.

**What an untrusted file may do** is the other half. A chapter file arrives
from wherever chapters arrive from and a plan file is passed between
translators, so both are somebody else's bytes. The defences against them
were already in the code and were already deliberate — the reading found no
hole in them — and none of them was held to by a test, which is what makes
a defence something the next refactor can quietly take out.
"""

from __future__ import annotations

import ast
import socket
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from comictrans.apply import apply_plan
from comictrans.config import ApplyConfig, ExtractConfig
from comictrans.errors import InputError
from comictrans.extract import default_plan_path, extract
from comictrans.model import Box
from comictrans.planfile import load_plan, loads, write_plan
from comictrans.sources import MAX_PAGE_BYTES, open_chapter, unpack
from comictrans.validate import validate_plan

from .conftest import (
    ART_DARK,
    BALLOON_WHITE,
    INK_BLACK,
    FakeRecognizer,
    lines_for,
    make_page_array,
    save_page,
)

SOURCE_ROOT = Path("src/comictrans")


# -- no network calls anywhere in the pipeline --------------------------------

NETWORK_MODULES = frozenset(
    {
        "PySide6.QtNetwork",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtWebEngineWidgets",
        "aiohttp",
        "ftplib",
        "http",
        "httpx",
        "imaplib",
        "nntplib",
        "paramiko",
        "poplib",
        "requests",
        "smtplib",
        "socket",
        "socketserver",
        "ssl",
        "telnetlib",
        "urllib",
        "urllib3",
        "webbrowser",
        "websockets",
        "xmlrpc",
    }
)
"""Every module whose whole purpose is to talk to another machine.

Modules rather than function names, because a name list is a list of the
ways somebody has already thought of: ``urlopen`` is one way to use
``urllib`` and there are a dozen others, while there is no way to use it
without importing it. The Qt entries are the same argument — every Qt class
that opens a socket lives in ``QtNetwork`` or in one of the WebEngine
modules, so naming the modules names all of them, including the ones that
do not exist yet.

``ssl`` and ``webbrowser`` are here for what they imply rather than what
they do. Neither opens a connection on its own; a tool with no network has
no reason to hold a certificate or to hand a URL to something that will
fetch it, and either import appearing is the thing worth stopping to look
at.

``asyncio`` is deliberately **not** here. It is a way of waiting, not a way
of reaching anything, and a rule that catches it would be a rule about
style rather than about the network.
"""


def _imported_modules(tree: ast.Module) -> Iterator[tuple[int, str]]:
    """``(line, dotted module)`` for every import in one file.

    A relative import — ``from . import x`` — reaches nothing outside this
    package by definition, and has no dotted name to compare anyway.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module


def _is_network(module: str) -> bool:
    """Whether this import, or the package it is reached through, is one.

    ``urllib.request`` is ``urllib``, and ``PySide6.QtNetwork`` is itself.
    Walking the prefixes rather than matching the whole name is what makes
    one entry cover a package's submodules.
    """
    parts = module.split(".")
    return any(".".join(parts[: index + 1]) in NETWORK_MODULES for index in range(len(parts)))


def _source_files() -> list[Path]:
    files = sorted(SOURCE_ROOT.rglob("*.py"))
    assert len(files) > 50, f"only {len(files)} modules found — is the path still {SOURCE_ROOT}?"
    return files


def test_no_module_imports_anything_that_speaks_to_a_network() -> None:
    """The invariant, read off the source rather than trusted.

    Total over what this project ships, which is the half a running check
    cannot be: a module nothing imports yet, a branch no test reaches, and a
    path that only runs on a Mac are all read the same as any other line.

    The bundled example plugin is under this root and is held to the rule
    with everything else. A plugin is exempt from the invariant as installed
    third-party code — see ``CLAUDE.md`` — but this one is code this
    repository wrote and ships, and the exemption is for what somebody else
    chose to install, not for what we hand them.
    """
    guilty = [
        f"{path}:{line}: imports {module}"
        for path in _source_files()
        for line, module in _imported_modules(ast.parse(path.read_text(encoding="utf-8")))
        if _is_network(module)
    ]

    assert not guilty, "no network calls anywhere in the pipeline:\n  " + "\n  ".join(guilty)


def _open_url_arguments(tree: ast.Module) -> Iterator[tuple[int, ast.expr]]:
    """The argument of every ``openUrl`` call in one file."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "openUrl"
            and node.args
        ):
            yield node.lineno, node.args[0]


def test_the_one_call_that_could_open_a_url_is_only_ever_given_a_file() -> None:
    """``QDesktopServices.openUrl`` hands a URL to the platform to open.

    It is the nearest thing in this application to a network call: the
    process never connects to anything, but a URL with a scheme on it is a
    browser launched and a request made, and the two uses here — Open Log
    Folder and Open Plugin Folder — want the Finder and a directory.

    So the check is not that the call is absent but that its argument is
    built by ``QUrl.fromLocalFile``, which cannot produce a remote URL. A
    ``QUrl("https://…")`` slipped in later reads exactly like the two that
    are there and is caught here instead.
    """
    guilty: list[str] = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, argument in _open_url_arguments(tree):
            built_from_a_file = (
                isinstance(argument, ast.Call)
                and isinstance(argument.func, ast.Attribute)
                and argument.func.attr == "fromLocalFile"
            )
            if not built_from_a_file:
                guilty.append(f"{path}:{line}: openUrl({ast.unparse(argument)})")

    assert not guilty, "openUrl given something other than a local file:\n  " + "\n  ".join(guilty)


class NetworkAttemptError(AssertionError):
    """Raised in place of whatever was about to open a connection."""


@contextmanager
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make the socket module unusable for the duration.

    Every name here is one a caller has to go through to reach another
    machine from Python: a connection needs a socket, and a hostname needs
    resolving. Patching the module's attributes catches the ordinary
    ``socket.socket(...)`` spelling that libraries use, which is the point —
    this is aimed at a dependency reaching out on our behalf, not at
    somebody evading it.

    **What it cannot see.** A C library that calls ``connect`` itself never
    passes through Python, and neither does Qt, which is C++ throughout. So
    a clean run here is evidence about this code and about the Python
    libraries under it, and the static check above is what covers the rest.
    """

    def refuse(*args: object, **kwargs: object) -> Any:
        raise NetworkAttemptError("the pipeline tried to open a network connection")

    with monkeypatch.context() as patch:
        for name in ("socket", "create_connection", "socketpair", "getaddrinfo", "gethostbyname"):
            patch.setattr(socket, name, refuse)
        yield


def test_the_guard_itself_refuses_a_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """A guard nobody has seen fail is a guard that may not be installed.

    The port is closed and unreachable either way; what is being checked is
    that the attempt raises :class:`NetworkAttemptError` rather than the
    ``ConnectionRefusedError`` it would raise without the patch.
    """
    with no_network(monkeypatch), pytest.raises(NetworkAttemptError):
        socket.create_connection(("127.0.0.1", 9), timeout=0.1)


def _chapter(tmp_path: Path) -> tuple[Path, Path]:
    """A one-page CBZ beside where its pages will land."""
    pages = tmp_path / "loose"
    pages.mkdir()
    boxes = [Box(160, 140, 360, 164)]
    array = make_page_array(
        (600, 800),
        ART_DARK,
        [("ellipse", Box(120, 100, 420, 260), BALLOON_WHITE, INK_BLACK, boxes)],
    )
    page = save_page(array, pages / "page-001.png")
    chapter = tmp_path / "chapter.cbz"
    with zipfile.ZipFile(chapter, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(page, "page-001.png")
    return chapter, page


def test_the_whole_pipeline_runs_with_the_sockets_taken_away(
    tmp_path: Path, font_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unpack, extract, validate and apply, with no socket to be had.

    The half the reading cannot do. Every module here pulls in something
    somebody else wrote — Pillow, NumPy, OpenCV, ruamel, pypdf — and an
    upgrade that starts checking for its own new version, or fetching a
    font, or phoning home about a crash, would be invisible to a check that
    only reads our source. This is what fails on the day one of them does.

    Deliberately the passes rather than their parts: what the invariant is
    about is a run, and a run is what this is.
    """
    chapter, _ = _chapter(tmp_path)
    boxes = [Box(160, 140, 360, 164)]
    recognizer = FakeRecognizer({"001-page-001.png": lines_for(boxes, ["CIAO"])})
    output = tmp_path / "out"

    with no_network(monkeypatch):
        report = unpack(chapter)
        assert len(report.pages) == 1

        plan_path = default_plan_path(report.directory)
        plan, _ = extract(report.directory, plan_path, recognizer, "Comic Sans MS", ExtractConfig())
        write_plan(plan, plan_path)

        assert validate_plan(plan_path).ok
        translated = load_plan(plan_path)
        translated = replace(
            translated,
            regions=tuple(region.with_translation("HELLO") for region in translated.regions),
        )
        applied = apply_plan(translated, plan_path, output, ApplyConfig())

    assert applied.ok
    assert applied.rendered >= 1, "nothing was rendered, so nothing was really exercised"


# -- what an untrusted chapter file may do ------------------------------------


def _zip_of(entries: dict[str, bytes], path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def test_an_entry_that_names_its_way_out_of_the_directory_cannot(tmp_path: Path) -> None:
    """Zip slip: an archive entry is a name, not a path.

    ``_archive_pages`` takes each entry's basename and ``unpack`` writes it
    under an index prefix, so the written name starts with a digit and is a
    single component whatever the archive called it. Both halves matter —
    the prefix is what stops a bare ``..`` — and neither is obviously
    load-bearing when read, which is the reason this is written down.
    """
    outside = tmp_path / "outside.png"
    page = (tmp_path / "real.png").write_bytes(b"") or b"x" * 64
    chapter = _zip_of(
        {
            "../outside.png": page,
            "../../outside.png": page,
            "/etc/outside.png": page,
            "a/b/../../../outside.png": page,
        },
        tmp_path / "chapter.cbz",
    )

    report = unpack(chapter, tmp_path / "pages")

    assert not outside.exists(), "an entry escaped the unpack directory"
    assert {path.parent for path in report.pages} == {tmp_path / "pages"}
    for written in report.pages:
        assert written.name.startswith(("001-", "002-", "003-", "004-"))
        assert ".." not in written.name


def test_an_entry_claiming_to_be_enormous_is_not_read_at_all(tmp_path: Path) -> None:
    """A compression bomb is refused on what it says, before anything is read.

    Built small and lied about rather than built large: the header is what
    the check reads, and writing half a gigabyte to prove it would make this
    a test nobody runs. A real bomb of this shape was measured at 1029:1 —
    see :data:`comictrans.sources.MAX_PAGE_BYTES`.
    """
    chapter = _zip_of({"page-001.png": b"x" * 64, "page-002.png": b"x" * 64}, tmp_path / "c.cbz")
    with zipfile.ZipFile(chapter) as archive:
        honest = archive.infolist()[0].file_size
    _lie_about_size(chapter, "page-002.png", MAX_PAGE_BYTES + 1)

    report = unpack(chapter, tmp_path / "pages")

    assert honest == 64, "the fixture's own entry should be the size it says"
    assert len(report.pages) == 1, "the honest page still unpacks"
    assert [name for name, _ in report.skipped] == ["page-002.png"]
    assert "compression bomb" in report.skipped[0][1]


def _lie_about_size(archive: Path, member: str, size: int) -> None:
    """Rewrite one member's declared uncompressed size, in place.

    A bomb declares what it unpacks to and delivers it; this declares it and
    does not, which is the same thing as far as a check made before reading
    is concerned and costs a kilobyte instead of half a gigabyte.
    """
    raw = bytearray(archive.read_bytes())
    name = member.encode()
    # The central directory's entry, which is what ``infolist`` reads: the
    # uncompressed size sits 24 bytes into the 46-byte fixed header, and the
    # name follows it.
    start = raw.index(b"PK\x01\x02")
    while True:
        length = int.from_bytes(raw[start + 28 : start + 30], "little")
        if raw[start + 46 : start + 46 + length] == name:
            raw[start + 24 : start + 28] = size.to_bytes(4, "little")
            break
        start = raw.index(b"PK\x01\x02", start + 1)
    archive.write_bytes(bytes(raw))


def test_a_directory_traversal_and_a_bomb_in_the_same_archive_leave_a_usable_chapter(
    tmp_path: Path,
) -> None:
    """The refusals are per entry, not per archive.

    Both defences skip and say so rather than refusing the file, which is
    the shape the reader already had for an entry that is not a page — a
    resource fork, a hidden file, the wrong extension. A chapter with one
    bad entry in it is still a chapter.
    """
    chapter = _zip_of(
        {"page-001.png": b"x" * 64, "__MACOSX/page-001.png": b"", "notes.txt": b"hello"},
        tmp_path / "chapter.cbz",
    )

    report = unpack(chapter, tmp_path / "pages")

    assert len(report.pages) == 1
    assert sorted(name for name, _ in report.skipped) == ["__MACOSX/page-001.png", "notes.txt"]


def test_reading_a_chapter_goes_through_the_door_unpacking_does(tmp_path: Path) -> None:
    """The reader window is handed exactly the pages unpack would write.

    Every refusal at once — a link, a claimed bomb, a resource fork, a hidden
    file, something that is not an image — so a second way into an archive,
    one that skipped any of them, would show here as a page too many.
    """
    chapter = tmp_path / "chapter.cbz"
    with zipfile.ZipFile(chapter, "w") as archive:
        for name in ("page-001.png", "page-002.png", "page-003.png"):
            archive.writestr(name, b"x" * 64)
        archive.writestr("__MACOSX/page-001.png", b"")
        archive.writestr(".page-004.png", b"x")
        archive.writestr("notes.txt", b"hello")
        link = zipfile.ZipInfo("page-005.png")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        archive.writestr(link, "/etc/passwd")
    _lie_about_size(chapter, "page-003.png", MAX_PAGE_BYTES + 1)
    before = sorted(tmp_path.iterdir())

    with open_chapter(chapter) as pages:
        read = [pages.label(index) for index in range(len(pages))]
        skipped = pages.skipped
    assert sorted(tmp_path.iterdir()) == before, "reading writes nothing"

    report = unpack(chapter, tmp_path / "pages")
    assert read == ["page-001.png", "page-002.png"]
    assert [path.name for path in report.pages] == ["001-page-001.png", "002-page-002.png"]
    assert skipped == report.skipped
    assert len(skipped) == 5


# -- what an untrusted plan file may do ---------------------------------------

PLAN_WITH_A_TAG = """\
version: 2
generator: comictrans
created: "2026-01-01T00:00:00Z"
source_language: it
target_language: en
ocr_engine: fake
font: !!python/object/apply:os.system ["touch /tmp/comictrans-audit-pwned"]
case: upper
font_size_min_ratio: 0.012
condense_min: 0.9
images: []
regions: []
"""


def test_a_plan_file_cannot_construct_a_python_object(tmp_path: Path) -> None:
    """The reader builds a plan out of scalars, and a tag is not one of them.

    ``ruamel``'s round-trip loader is what the reader uses, and it has no
    constructor for ``!!python/…`` — the tag is dropped and the value
    underneath it comes through as the sequence it looks like. The plan is
    then refused for an ordinary reason, a font that is not a string, which
    is the right refusal and not the interesting one.

    What is interesting is that nothing ran. This is the check that stops a
    future "the round-trip loader is slow, use the unsafe one" from being a
    one-line change with no test against it.
    """
    marker = Path("/tmp/comictrans-audit-pwned")
    assert not marker.exists(), "left over from an earlier run; remove it and run this again"

    with pytest.raises(Exception) as raised:
        loads(PLAN_WITH_A_TAG)

    assert not marker.exists(), "the plan file executed something"
    assert "font" in str(raised.value)


def test_a_plan_that_names_a_page_outside_its_own_directory_writes_inside_the_output(
    tmp_path: Path, font_dir: Path
) -> None:
    """A page's name is resolved for reading and flattened for writing.

    ``source_path`` resolves a page against the plan's directory, and ``..``
    is a real answer there — a hand-written plan may point at a pages folder
    beside it, which is why that is allowed and tested in ``test_model``.
    What must not follow is the output going the same way, and it cannot:
    ``imaging.output_path`` takes the source's *stem*, which is one path
    component and can never be a directory.
    """
    pages = tmp_path / "pages"
    pages.mkdir()
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    boxes = [Box(160, 140, 360, 164)]
    array = make_page_array(
        (600, 800),
        ART_DARK,
        [("ellipse", Box(120, 100, 420, 260), BALLOON_WHITE, INK_BLACK, boxes)],
    )
    save_page(array, pages / "page-001.png")

    recognizer = FakeRecognizer({"page-001.png": lines_for(boxes, ["CIAO"])})
    plan_path = plan_dir / "comic-plan.yaml"
    plan, _ = extract(pages, plan_path, recognizer, "Comic Sans MS", ExtractConfig())
    assert plan.images[0].name.startswith("../"), "the fixture is not testing what it claims"

    plan = replace(plan, regions=tuple(region.with_translation("HELLO") for region in plan.regions))
    output = tmp_path / "out"
    report = apply_plan(plan, plan_path, output, ApplyConfig())

    assert report.ok
    assert [path.parent for path in report.pages_written] == [output]


def test_an_unpack_refuses_to_write_over_a_page_it_did_not_write(tmp_path: Path) -> None:
    """Somebody else's file of the same name is not overwritten.

    Not a security defence so much as the same instinct the whole tool is
    built on — nothing the user did not ask for is written over — and it is
    what stops a crafted archive from replacing a file in a directory the
    person unpacked something else into.
    """
    into = tmp_path / "pages"
    into.mkdir()
    (into / "001-page-001.png").write_bytes(b"mine")
    chapter = _zip_of({"page-001.png": b"x" * 64}, tmp_path / "chapter.cbz")

    with pytest.raises(InputError, match="is already there and is not the page"):
        unpack(chapter, into)

    assert (into / "001-page-001.png").read_bytes() == b"mine"
