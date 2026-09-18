"""Example plugin: writes the same note onto every region in the open plan.

Ships already installed — Plugins > Open Plugin Folder shows where this
lives once it is turned on, and Plugins > Rescan Plugins picks up any change
made to this copy or to a plugin dropped in beside it.

It is a good example of what a plugin is for precisely because it is not
case switching — the plan header already has ``case`` — and because
``notes`` already exists on every region, so nothing about the plan file's
shape has to change for this to work. It is also the smallest possible
plugin folder: one file, no siblings, though a plugin is free to have both.

The note's text is configurable — Plugins > Configure Plugins… — which is
what ``SETTINGS`` below declares. ``PLUGIN_VERSION`` and
``REQUIRES_APP_VERSION`` are both optional; this one declares both to show
what each looks like. The version is just a label Configure Plugins shows;
the app version is checked at discovery time and a real gate — comictrans
older than this bundled copy declares itself needing would refuse to load
it.
"""

from __future__ import annotations

from dataclasses import replace

from comictrans.model import Plan
from comictrans.plugins import SettingField

PLUGIN_NAME = "Add a Note to Every Region"
PLUGIN_VERSION = "1.0.0"
REQUIRES_APP_VERSION = "1.1.0"

SETTINGS = (
    SettingField(
        key="note_text",
        label="Note text",
        default="Checked by the Add a Note example plugin.",
    ),
)


def run(plan: Plan, settings: dict[str, str]) -> Plan:
    text = settings["note_text"]
    return replace(plan, regions=tuple(replace(region, notes=text) for region in plan.regions))
