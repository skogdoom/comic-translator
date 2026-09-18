#!/usr/bin/env python3
"""What this tool depends on, and whether any of it has a known vulnerability.

    uv run --with pip-audit tools/audit_dependencies.py

The tooling half of the security audit — milestone 7, written up in
``docs/SECURITY.md``. The reading half is a reading and does not repeat; this
is the half that has to be re-run, because the code not changing is no
evidence at all about an advisory published last week.

**This is the one thing in the repository that reaches the network, and it is
not part of the tool.** ``pip-audit`` queries PyPI and the OSV database, which
is what an advisory check is. Nothing under ``src/comictrans`` may do that —
see ``CLAUDE.md``, and ``tests/test_security.py``, which fails if it ever
starts — and nothing here is imported by anything that ships.

**Three things a bare ``pip-audit`` run does not give you**, each found by
running it rather than by reading about it, and together the reason this is
a script rather than a line in a README:

1. *Asked the ordinary way, it audits fewer packages than you gave it and
   does not say so.* ``pip-audit -r`` resolves the requirements inside a
   virtual environment, and two kinds of package never come out the other
   side: one whose environment marker excludes the machine it is running on,
   and one that is already in that environment. Measured on this lock: five
   ``pyobjc`` packages skipped on Linux — the macOS half of a macOS
   application — and ``packaging`` skipped on both platforms, because
   ``packaging`` is a dependency of ``pip-audit`` itself. The second kind is
   the nastier one: no machine anywhere would have audited it, and the auditor
   shadowing what it is auditing leaves no trace in the output.

   Both are the resolver's doing, and this asks for the resolver to be turned
   off. ``--disable-pip`` makes ``pip-audit`` read the requirements itself,
   which is all that is wanted from a file the lock has already pinned in
   full — and with nothing being resolved, the markers can come off too, so a
   run anywhere audits every version the lock names. 18 of 18, measured on
   Linux and on macOS. The two changes only work together: markers stripped
   *with* the resolver still on makes pip try to build pyobjc off a Mac
   ("PyObjC requires macOS to build") and fails the whole run.

2. *It still audits the lock as this interpreter would read it.* Markers are
   gone from the file, but ``pip-audit`` is invoked as ``sys.executable -m
   pip_audit`` so that anything else version-dependent is judged by the
   Python this project actually runs on, rather than by whichever one
   happened to launch the audit.

3. *It cannot see anything that is not a Python package.* Reading a CBR runs
   an external ``unrar`` and writing one runs an external ``rar``; OCR can
   run ``tesseract``. All three are somebody else's binary handed somebody
   else's file, they are the newest attack surface this tool has, and no
   amount of auditing the lock says a word about them. What is here is their
   version, which is what an advisory is about.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent

EXPORT_COMMAND = (
    "uv",
    "export",
    "--no-dev",
    "--all-extras",
    "--no-emit-project",
    "--no-hashes",
    "--format",
    "requirements-txt",
)
"""What the lock pins for somebody who installs this, extras included.

``--no-dev`` because pytest and ruff are not what a user runs; ``--all-extras``
because the review window and the Tesseract engine are, and PySide6 is by
some distance the largest thing here. ``--no-emit-project`` leaves out
comictrans itself, which has no advisories to have and is not on PyPI.
"""

HOW_TO_GET_IT = (
    "pip-audit is not importable here. Run this as:\n"
    "    uv run --with pip-audit tools/audit_dependencies.py\n"
    "which puts it in an overlay for the one command and leaves the project's "
    "own environment alone."
)

EXTERNAL_TOOLS: tuple[tuple[str, str, str], ...] = (
    ("unrar", "COMICTRANS_UNRAR", "reads CBR; sources.py drives it through rarfile"),
    ("rar", "COMICTRANS_RAR", "writes CBR; pack._pack_rar runs it directly"),
    ("tesseract", "", "the OCR fallback, through pytesseract"),
)
"""``(binary, the variable that names it elsewhere, what it is for)``.

The two RAR tools are the ones the roadmap named: both take a file somebody
else made, and neither ships with this project — which is the licensing
reason, not a security one, but it does mean the version in use is whatever
the machine has. ``unar``, ``bsdtar`` and ``7z`` are alternatives ``rarfile``
will also drive; they are not listed because naming every possibility is a
worse report than naming the two that are the default.
"""


@dataclass(frozen=True, slots=True)
class Package:
    name: str
    version: str


@dataclass(frozen=True, slots=True)
class Finding:
    """One advisory against one pinned version."""

    package: str
    version: str
    identifier: str
    fixed_in: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Report:
    findings: tuple[Finding, ...] = ()
    audited: tuple[str, ...] = ()
    """Packages ``pip-audit`` actually answered about."""
    unaudited: tuple[Package, ...] = ()
    """Packages the lock names that it did not — see the module docstring."""
    tools: tuple[ExternalTool, ...] = ()


@dataclass(frozen=True, slots=True)
class ExternalTool:
    """A binary this tool runs, as this machine currently has it."""

    name: str
    path: str = ""
    version: str = ""
    named_by: str = ""
    """The environment variable that pointed at it, when one did."""


def _normalised(name: str) -> str:
    """A distribution name as PyPI compares them: PEP 503, roughly.

    ``ruamel.yaml`` in the lock is ``ruamel-yaml`` coming back out of
    ``pip-audit``, and comparing the two lists is the whole point of having
    them.
    """
    return name.lower().replace("_", "-").replace(".", "-")


def export_lock() -> str:
    """The lock as a requirements file, exactly as ``uv`` writes it.

    Markers and all: what is handed to ``pip-audit`` is built from this
    rather than being this — see :func:`requirements_for`.
    """
    done = subprocess.run(EXPORT_COMMAND, cwd=ROOT, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise SystemExit(f"could not read the lock:\n{done.stderr.strip()}")
    return done.stdout


def locked_packages(exported: str) -> tuple[Package, ...]:
    """Every pinned version in an export, in the order it lists them.

    A requirement is at the left margin and everything under it is indented —
    the ``# via …`` block naming what pulled it in, and the hashes when they
    are asked for. Both the margin and the ``==`` are checked, and today
    either alone would do: nothing uv writes is both indented and pinned.
    That is a fact about the current format rather than a promise it makes,
    which is why the narrower check is here as well as the obvious one.
    """
    found: list[Package] = []
    for line in exported.splitlines():
        if not line or line[0].isspace() or line.startswith("#"):
            continue
        requirement = line.split(";")[0].strip()
        if "==" not in requirement:
            continue
        name, _, version = requirement.partition("==")
        found.append(Package(name=name.strip(), version=version.strip()))
    return tuple(found)


def requirements_for(packages: tuple[Package, ...]) -> str:
    """One pinned line per package, with no marker on any of them.

    A marker is a question about the machine — "install this only on a Mac" —
    and this is not installing anything. Keeping them would mean a run on
    Linux never asking about the pyobjc packages and a run on a Mac never
    asking about the ones marked the other way, so what gets audited would
    depend on where the audit happened. Stripping them is only safe because
    the resolver is off; see the module docstring.
    """
    return "".join(f"{package.name}=={package.version}\n" for package in packages)


def _pip_audit(requirements: Path) -> list[dict[str, object]]:
    """Run ``pip-audit`` over a requirements file and hand back its JSON.

    ``--no-deps`` because the file is already the resolved, fully pinned set:
    without it ``pip-audit`` re-resolves and can pick versions the lock does
    not have. ``--disable-pip`` because resolving is not merely unnecessary
    but is what loses packages — see the module docstring, and do not drop
    it without putting the markers back. ``sys.executable -m`` so the
    interpreter answering is this project's.
    """
    command = [
        sys.executable,
        "-m",
        "pip_audit",
        "--requirement",
        str(requirements),
        "--no-deps",
        "--disable-pip",
        "--format",
        "json",
        "--progress-spinner",
        "off",
    ]
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    try:
        payload = json.loads(done.stdout)
    except json.JSONDecodeError:
        detail = (done.stderr or done.stdout).strip()
        raise SystemExit(f"pip-audit could not be read:\n{detail}") from None
    dependencies = payload.get("dependencies", [])
    assert isinstance(dependencies, list)
    return dependencies


def _findings(dependencies: list[dict[str, object]]) -> tuple[Finding, ...]:
    found: list[Finding] = []
    for entry in dependencies:
        name = str(entry.get("name", "?"))
        version = str(entry.get("version", "?"))
        vulnerabilities = entry.get("vulns", [])
        if not isinstance(vulnerabilities, list):
            continue
        for vulnerability in vulnerabilities:
            fixed = vulnerability.get("fix_versions") or []
            found.append(
                Finding(
                    package=name,
                    version=version,
                    identifier=str(vulnerability.get("id", "?")),
                    fixed_in=tuple(str(value) for value in fixed),
                )
            )
    return tuple(found)


def _first_line(command: list[str]) -> str:
    """The first line a binary prints when asked what it is, or why not.

    ``unrar`` and ``rar`` print a banner when run with no arguments and exit
    non-zero doing it, so the return code says nothing here and is not read.
    A timeout because this is a binary off the machine's ``PATH`` and a
    version check that hangs is not a version check.
    """
    try:
        done = subprocess.run(command, capture_output=True, text=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not be run: {exc}"
    for line in (done.stdout or done.stderr).splitlines():
        if line.strip():
            return line.strip()
    return "said nothing"


def external_tools() -> tuple[ExternalTool, ...]:
    """Where each external binary is on this machine, and what version.

    The environment variables are read first because they are what the
    pipeline itself reads first: a Homebrew ``unrar`` that the application
    cannot see on its own ``PATH`` is the case ``COMICTRANS_UNRAR`` exists
    for, and auditing a different binary from the one that runs would be
    worse than auditing none.
    """
    found: list[ExternalTool] = []
    for name, variable, _ in EXTERNAL_TOOLS:
        configured = os.environ.get(variable, "").strip() if variable else ""
        path = configured or (shutil.which(name) or "")
        if not path:
            found.append(ExternalTool(name=name))
            continue
        found.append(
            ExternalTool(
                name=name,
                path=path,
                version=_first_line([path, "--version"] if name == "tesseract" else [path]),
                named_by=variable if configured else "",
            )
        )
    return tuple(found)


def audit(workspace: Path) -> Report:
    """Read the lock, ask about every version in it, and say what was missed."""
    locked = locked_packages(export_lock())
    requirements = workspace / "locked-requirements.txt"
    requirements.write_text(requirements_for(locked), encoding="utf-8")
    dependencies = _pip_audit(requirements)
    answered = {_normalised(str(entry.get("name", ""))) for entry in dependencies}
    return Report(
        findings=_findings(dependencies),
        audited=tuple(sorted(answered)),
        unaudited=tuple(package for package in locked if _normalised(package.name) not in answered),
        tools=external_tools(),
    )


def _print(report: Report) -> None:
    print(
        f"Audited {len(report.audited)} of {len(report.audited) + len(report.unaudited)} "
        f"packages the lock pins.\n"
    )

    if report.findings:
        print(f"{len(report.findings)} advisory/advisories:")
        for finding in report.findings:
            fix = ", ".join(finding.fixed_in) if finding.fixed_in else "no fixed version yet"
            print(f"  {finding.package} {finding.version}: {finding.identifier} (fix: {fix})")
    else:
        print("No known vulnerabilities in what was audited.")

    if report.unaudited:
        print(
            f"\nNot audited ({len(report.unaudited)}). Every version the lock pins is asked\n"
            "about regardless of platform, so this is not the usual 'wrong machine' —\n"
            "pip-audit was handed these and did not answer, which needs looking at:"
        )
        for package in report.unaudited:
            print(f"  {package.name} {package.version}")

    print("\nExternal binaries, which no lock describes:")
    for tool in report.tools:
        if not tool.path:
            print(f"  {tool.name}: not on this machine")
            continue
        via = f" (named by {tool.named_by})" if tool.named_by else ""
        print(f"  {tool.name}: {tool.version}")
        print(f"    {tool.path}{via}")


def main() -> int:
    """Report, and answer non-zero when there is something to act on.

    A finding and a package that could not be audited are both reasons to
    look, so both fail: a scheduled run that only reported findings would go
    green on the day the platform it runs on stops covering half the lock.
    """
    if not _importable("pip_audit"):
        raise SystemExit(HOW_TO_GET_IT)
    # Outside the repository: the only thing written is a copy of the lock,
    # and this tool has no more business leaving a directory behind than
    # anything else here has.
    with TemporaryDirectory(prefix="comictrans-audit-") as workspace:
        report = audit(Path(workspace))
    _print(report)
    return 1 if report.findings or report.unaudited else 0


def _importable(module: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(module) is not None


if __name__ == "__main__":
    sys.exit(main())
