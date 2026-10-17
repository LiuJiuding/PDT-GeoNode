# SPDX-FileCopyrightText: 2025 Blender Foundation
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The modal operator the user binds a key to: `pdt.node_info_modal`.

`bl_options` deliberately excludes `INTERNAL` - that flag hides an operator from Blender's
search and from the Keymap editor's operator field, which is why it once could not be bound
by hand at all. The gesture itself lives in `session.py`; this class is only the thin shell
that `invoke`/`modal` need.
"""

from __future__ import annotations

from bpy.types import Operator

from ..preferences import prefs
from ..session import (
    _begin_session,
    _clear_session,
    _modal_step,
    _remove_draw_handler,
)
from ..utils.constants import MODAL_IDNAME, OPERATOR_LABEL
from ..utils.log import _log


class PDT_OT_node_info_modal(Operator):
    """Hold to read the geometry flowing through a node"""

    bl_idname = MODAL_IDNAME
    bl_label = OPERATOR_LABEL

    # NOT `INTERNAL`: that flag hides the operator from Blender's search and from the
    # Keymap editor's operator field, which is exactly why it could not be found there
    # ("search pdt in Keymap shows nothing"). It is also not `BLOCKING` - that only hides
    # the cursor while the modal runs, and this operator wants the cursor visible.
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "NODE_EDITOR"

    def invoke(self, context, event):
        settings = prefs(context)
        if settings is None:
            _log("invoke-CANCELLED", reason="no preferences")
            self.report({"ERROR"}, "PDT Node Info preferences are unavailable")
            return {"CANCELLED"}
        if not _begin_session(context, event):
            # No node under the cursor: this press is ordinary view navigation.
            return {"PASS_THROUGH"}
        context.window_manager.modal_handler_add(self)
        if context.area is not None:
            context.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        action, need_redraw = _modal_step(context, event)
        if need_redraw and context.area is not None:
            context.area.tag_redraw()
        if action in (None, "RUNNING_MODAL"):
            return {"RUNNING_MODAL"}
        if action == "FINISH":
            self._finish(context)
            return {"FINISHED"}
        self._finish(context)
        return {"CANCELLED"}

    def _finish(self, context):
        _remove_draw_handler()
        _clear_session()
        if context.area is not None:
            context.area.tag_redraw()
