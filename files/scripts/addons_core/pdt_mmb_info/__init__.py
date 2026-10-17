# SPDX-FileCopyrightText: 2025 Blender Foundation
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""PDT Node Info (MMB): hold a key on a node and read the geometry flowing through it.

Where things live (one file per concern):

    preferences.py       MMB-8's four settings, the panel, "Reset Colours"
    interface.py         the information block: layout, wrapping, `blf`/`gpu` drawing (MMB-5)
    operators/           the modal operator the user binds a key to (MMB-7)
    session.py           session state, the read at trigger time, the draw handler
    hit_test.py          which node is under the cursor (MMB-2/3/4)
    geometry.py          which socket carries the stream at a node
    evaluate.py          our own temporary-tree evaluator (reads that stream)
    snapshot.py          the stream reduced to counts + attributes (MMB-5)
    keymap.py            the single unbound keymap item that makes the operator findable
    utils/               constants, palette, diagnostics log (MMB-9)

The decisions are the user's: `tools/pdt/spec/node_info.md` (L0) -> `docs/manual/operators/
node_info.md` (L1) -> `docs/09-MMB节点信息.md` (what was measured, L2).
"""

import bpy

from . import keymap, operators, preferences
from .utils.constants import REMINDER
from .utils.log import LOG_PATH, _log

bl_info = {
    "name": "PDT Node Info",
    "author": "PDT",
    "version": (1, 2, 0),
    "blender": (5, 2, 0),
    "location": "Node Editor > hold your Node Info shortcut on a node",
    "description": "Houdini-style node information for geometry nodes",
    "category": "Node",
}

#: Every class this add-on registers, in registration order (each module owns its own list).
_CLASSES = operators.classes + preferences.classes


def register():
    operators.register()
    preferences.register()
    added = keymap.register()
    assigned = keymap.binding_is_active(bpy.context)
    _log("register", classes=len(_CLASSES), keymap_items_added=added, log_path=LOG_PATH,
         shortcut_assigned=assigned)
    print(f"PDT node info: {bl_info['name']} registered - {REMINDER} (log: {LOG_PATH})")


def unregister():
    keymap.unregister()
    preferences.unregister()
    operators.unregister()
