# SPDX-FileCopyrightText: 2025 Blender Foundation
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The add-on's operators, one module each, plus the registration list."""

from __future__ import annotations

import bpy

from .node_info import PDT_OT_node_info_modal

classes = (
    PDT_OT_node_info_modal,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:  # noqa: BLE001 - already gone (add-on reload, double unregister)
            pass
