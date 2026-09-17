"""Example plugin: writes the same note onto every region in the open plan.

Installed by Plugins > Install Example Plugin, or copy this file into the
plugin directory by hand (Plugins > Open Plugin Folder shows where that is).
Then Plugins > Rescan Plugins to see it in the menu.

It is a good example of what a plugin is for precisely because it is not
case switching — the plan header already has ``case`` — and because
``notes`` already exists on every region, so nothing about the plan file's
shape has to change for this to work.

There is no settings window for a plugin yet — that is milestone 22 — so for
now the text below is the whole of "configurable": edit ``NOTE_TEXT`` and
reinstall, or edit your own copy in the plugin directory directly.
"""

from __future__ import annotations

from dataclasses import replace

from comictrans.model import Plan

PLUGIN_NAME = "Add a Note to Every Region"

NOTE_TEXT = "Checked by the Add a Note example plugin."


def run(plan: Plan) -> Plan:
    return replace(plan, regions=tuple(replace(region, notes=NOTE_TEXT) for region in plan.regions))
