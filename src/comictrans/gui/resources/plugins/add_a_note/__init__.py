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
what ``SETTINGS`` below declares.
"""

from __future__ import annotations

from dataclasses import replace

from comictrans.model import Plan
from comictrans.plugins import SettingField

PLUGIN_NAME = "Add a Note to Every Region"

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
