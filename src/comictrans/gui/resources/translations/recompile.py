#!/usr/bin/env python3
"""Build the ``.qm`` catalogues the window loads from the ``.ts`` beside them.

Two steps, and they are for different people.

``--extract`` runs ``lupdate`` over ``gui/``, which reads the source for
``tr()`` and ``QCoreApplication.translate`` calls and folds what it finds into
every ``.ts``, keeping the translations already there and marking anything
whose English has changed. That is for whoever added or reworded a string.
It reads the source rather than running it, so a string reached through a
helper — ``_say(text)``, or a short alias for ``translate`` — is a string it
never sees. Measured: such a call extracts nothing at all, silently. Every
call in ``gui/`` therefore writes its own English out in full.

The English catalogue is extracted with ``-pluralonly`` and holds one kind of
message: the ones counting something. Qt substitutes ``%n`` with no
translator loaded but cannot inflect the noun beside it, so ``%n page(s)``
reaches an English window as ``1 page(s)`` unless English is translated like
any other language. Everything else in it would be a string translated to
itself, and ``lrelease`` drops what is left unfinished — so the file stays
short and the rest of English keeps coming from the source.

The default run compiles each ``.ts`` into the ``.qm`` the application
actually loads. That is for whoever translated one.

``--check`` recompiles into a temporary directory and compares bytes —
``lrelease`` is reproducible, measured — so a ``.ts`` edited without
recompiling fails ``pytest`` rather than shipping a window still speaking the
last translation. The same bargain ``recreate-icons.py`` strikes with the
application icon.

The tools come from PySide6 itself (``pyside6-lupdate``, ``pyside6-lrelease``)
rather than from a Qt installation, so they are present wherever the ``gui``
extra is.
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE_LANGUAGE = "en"
"""The language the source strings are written in.

Its catalogue is the ``-pluralonly`` one. A test holds this to
``gui.translations.SOURCE_LANGUAGE``; this file cannot import that one,
being a script run from inside the package rather than a module of it.
"""

HERE = Path(__file__).resolve().parent
GUI = HERE.parent.parent
"""``src/comictrans/gui`` — everything lupdate reads, and nothing else.

The command line is not translated and neither is the pipeline, so pointing
this at the package would collect strings nobody intends to translate and
bury the ones somebody does.
"""


def catalogues() -> list[Path]:
    """Every ``.ts`` in here, sorted. One per language, English included."""
    return sorted(HERE.glob("*.ts"))


def language_of(catalogue: Path) -> str:
    """``comictrans_sv.ts`` → ``sv``."""
    return catalogue.stem.rpartition("_")[2]


def _tool(name: str) -> str:
    found = shutil.which(f"pyside6-{name}")
    if found is None:
        raise SystemExit(
            f"pyside6-{name} is not on PATH. It ships with PySide6: uv sync --extra gui"
        )
    return found


def _run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"{Path(command[0]).name} failed:\n{result.stdout}{result.stderr}")


def extract() -> None:
    """Fold the source's current strings into every ``.ts``."""
    sources = sorted(str(path) for path in GUI.rglob("*.py"))
    for catalogue in catalogues():
        # -no-obsolete: a string nobody uses any more is noise in a file
        # somebody reads by hand, and git remembers it either way.
        options = ["-no-obsolete"]
        if language_of(catalogue) == SOURCE_LANGUAGE:
            options.append("-pluralonly")
        _run([_tool("lupdate"), *sources, *options, "-ts", str(catalogue)])
        print(f"extracted into {catalogue.name}")


def compile_into(directory: Path) -> list[Path]:
    """Compile every ``.ts`` into ``directory`` and return what was written."""
    written = []
    for catalogue in catalogues():
        target = directory / f"{catalogue.stem}.qm"
        _run([_tool("lrelease"), str(catalogue), "-qm", str(target)])
        written.append(target)
    return written


def check() -> int:
    """0 if every committed ``.qm`` is what its ``.ts`` compiles to now."""
    stale: list[str] = []
    with tempfile.TemporaryDirectory() as scratch:
        for fresh in compile_into(Path(scratch)):
            committed = HERE / fresh.name
            if not committed.is_file():
                stale.append(f"{fresh.name} has never been compiled")
            elif not filecmp.cmp(fresh, committed, shallow=False):
                stale.append(f"{fresh.name} is older than {fresh.stem}.ts")
    if stale:
        print("\n".join(stale))
        print(f"\nRun {Path(__file__).name} to rebuild.")
        return 1
    print(f"{len(catalogues())} catalogue(s) up to date")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--extract",
        action="store_true",
        help="fold the source's strings into every .ts, for whoever added one",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if a committed .qm is older than its .ts",
    )
    args = parser.parse_args(argv)

    if args.extract:
        extract()
        return 0
    if args.check:
        return check()
    for written in compile_into(HERE):
        print(f"compiled {written.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
