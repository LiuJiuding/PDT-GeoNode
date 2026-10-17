# SPDX-FileCopyrightText: 2026 PDT Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The operators that do not need the modal loop: Apply / Cancel (for a session that was
paused), Clear Parameter, and Clean Up Leftovers (L0 `PICK-5`)."""

from __future__ import annotations

import bpy

from ..utils.constants import LONG_SELECTION_KEY
from ..geometry import operator_node
from ..session import cancel_pick, cleanup_leftovers, finish_pick
from ..targets import choose_string_target, write_target_value
from .. import state as pick_state

class PDT_OT_selection_apply(bpy.types.Operator):
    bl_idname = "pdt.selection_apply"
    bl_label = "Apply Selection"
    bl_description = "Write the picked elements into the node's string parameter and clean up"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(pick_state.session)

    def execute(self, context):
        try:
            count, text, note = finish_pick(context=context)
        except RuntimeError as ex:
            self.report({"ERROR"}, str(ex))
            return {"CANCELLED"}
        if note:
            self.report({"WARNING"}, note)
        else:
            self.report({"INFO"}, f"Selection of {count} element(s) written: {text[:60]}")
        return {"FINISHED"}


class PDT_OT_selection_cancel(bpy.types.Operator):
    bl_idname = "pdt.selection_cancel"
    bl_label = "Cancel Selection"
    bl_description = "Discard the pick session and restore the previous state"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return bool(pick_state.session)

    def execute(self, context):
        cancel_pick(context)
        return {"FINISHED"}


class PDT_OT_selection_clear(bpy.types.Operator):
    bl_idname = "pdt.selection_clear"
    bl_label = "Clear Selection Parameter"
    bl_description = "Empty the node's selection parameter (and drop a stored long selection)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        space = getattr(context, "space_data", None)
        return space is not None and space.type == "NODE_EDITOR"

    def execute(self, context):
        node = operator_node(context)
        target, reason = choose_string_target(node) if node is not None else (None, "No node selected")
        if target is None:
            self.report({"ERROR"}, reason)
            return {"CANCELLED"}
        write_target_value(node, target, "")
        if LONG_SELECTION_KEY in node:
            del node[LONG_SELECTION_KEY]
        self.report({"INFO"}, f"'{target['label']}' cleared")
        return {"FINISHED"}


class PDT_OT_selection_cleanup(bpy.types.Operator):
    bl_idname = "pdt.selection_cleanup"
    bl_label = "Clean Up Leftovers"
    bl_description = "Remove temporary objects left behind by an interrupted pick session"
    bl_options = {"REGISTER"}

    def execute(self, context):
        removed = cleanup_leftovers()
        self.report({"INFO"}, f"Removed {removed} leftover data-block(s)")
        return {"FINISHED"}


#: The classes this module contributes to `operators.classes`.
classes = (
    PDT_OT_selection_apply,
    PDT_OT_selection_cancel,
    PDT_OT_selection_clear,
    PDT_OT_selection_cleanup,
)
