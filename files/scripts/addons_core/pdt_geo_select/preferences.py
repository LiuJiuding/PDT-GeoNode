# SPDX-FileCopyrightText: 2026 PDT Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The add-on's preferences: PICK-4's one reminder sentence, and nothing else about the key."""

from __future__ import annotations

import bpy

from .utils.constants import REMINDER
from .keymap import picker_shortcut_items, picker_shortcut_label

class PDT_AP_preferences(bpy.types.AddonPreferences):
    """PICK-4's one reminder, and nothing else.

    The add-on binds **no** default shortcut, so the only thing a preference could report is
    that the user has to assign one - the sentence `spec/selection_pick.md` PICK-4 fixes, with
    this operator's name substituted (and "no other hint text", which is why there is nothing
    else in here).
    """

    bl_idname = __package__ or "pdt_geo_select"

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text=REMINDER)
        if picker_shortcut_items():
            box.label(text=f"Assigned: {picker_shortcut_label()}")


#: Every class this module registers.
classes = (PDT_AP_preferences,)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
