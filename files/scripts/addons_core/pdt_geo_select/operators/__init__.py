# SPDX-FileCopyrightText: 2026 PDT Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The add-on's operators, one module per group (see the package docstring)."""

from __future__ import annotations

import bpy

from . import actions, pick

#: Every operator class this add-on registers, in registration order.
classes = pick.classes + actions.classes


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
