"""Two-pass comic page translation: extract text to a plan file, apply it back."""

__version__ = "1.2.0.dev0"
"""The single source. ``pyproject.toml`` has hatch read it from here, the plan
files ``extract`` writes record it, the crash log banner carries it, and the
About dialog reads it straight from here rather than asking the installed
distribution, which is a copy taken at install time and stale from the next
edit — see ``gui.about.package_version`` for the bundle that shipped saying
0.1.0. Changing it here changes it everywhere; a test holds the last two of
those to this."""
