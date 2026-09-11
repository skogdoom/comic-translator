"""Two-pass comic page translation: extract text to a plan file, apply it back."""

__version__ = "1.0.0"
"""The single source. ``pyproject.toml`` has hatch read it from here, the plan
files ``extract`` writes record it, the crash log banner carries it, and the
About dialog asks the installed distribution for it — which is this, one
resolve later. Changing it here changes it everywhere; a test holds the last
of those to this."""
