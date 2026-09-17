"""Example plugin: writes the same note onto every region in the open plan.

Ships already installed — Plugins > Open Plugin Folder shows where this
lives once it is turned on, and Plugins > Rescan Plugins picks up any change
made to this copy or to a plugin dropped in beside it.

It is a good example of what a plugin is for precisely because it is not
case switching — the plan header already has ``case`` — and because
``notes`` already exists on every region, so nothing about the plan file's
shape has to change for this to work. It is also the smallest possible
plugin folder: one file, no siblings, though a plugin is free to have both.

There is no settings window for a plugin yet — that is milestone 22 — so for
now the constant below is the whole of "configurable": edit ``NOTE_TEXT`` in
your own installed copy directly.
"""

from __future__ import annotations

from dataclasses import replace

from comictrans.model import Plan

PLUGIN_NAME = "Add a Note to Every Region"

NOTE_TEXT = "Checked by the Add a Note example plugin."


def run(plan: Plan) -> Plan:
    return replace(plan, regions=tuple(replace(region, notes=NOTE_TEXT) for region in plan.regions))
