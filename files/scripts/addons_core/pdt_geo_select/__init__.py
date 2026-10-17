# SPDX-FileCopyrightText: 2026 PDT Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Viewport selection for geometry node parameters (the PDT selection picker).

Where things live (one file per concern):

    utils/constants.py   the two vocabularies (element kinds / geometry components), sizes, idnames
    parser.py            the selection-string grammar, and what may be pre-selected from it
    targets.py           which string parameter a pick writes into
    hit_test.py          the trigger's preflight: the socket under the cursor, and every refusal
    geometry.py          the geometry behind the node (object, breadcrumb, components, kinds)
    bake.py              that geometry -> the temporary object the user edits
    selection.py         reading/writing the temporary object's element selection
    instances.py         the three `Instance Mode`s, and folding realized elements back
    session.py           begin/finish/cancel the pick, and the guards around a write
    overlay.py           the status line, the viewport overlay, the breadcrumb
    keymap.py            the one unbound keymap item (PICK-4), and the repair of old damage
    preferences.py       the one shortcut reminder PICK-4 allows
    menus.py             the two pie menus (element kind, then instance mode)
    operators/pick.py    the trigger and the modal pick (the pie slices own its modal loop)
    operators/actions.py Apply / Cancel / Clear / Clean Up Leftovers
    state.py             the mutable state the modules share (the session dict, the pending pick)

The decisions are the user's: `tools/pdt/spec/selection_pick.md` (L0) -> `docs/manual/operators/
selection_pick.md` (L1) -> `docs/archive/04-视口选择.md` and this branch's handover documents
(what was measured, L2).

Blender has no way to pick geometry elements from the viewport and feed them into a node
parameter. This adds a modal picker (L0 ``spec/selection_pick.md``, ``PICK-1``..``PICK-6``):

1. Put the cursor on a **string parameter** of a node that has a geometry input and press the
   shortcut the user assigned (the add-on binds none itself, ``PICK-4``). The hit target is
   the socket, its name label or its input field - aiming at the little connection dot alone
   would be needlessly fiddly. ``pdt.selection_pick_modal`` asks the editor which socket is
   under the cursor through the C++ query operator ``pdt.node_under_cursor``
   (``source/blender/editors/space_node/node_pdt_query.cc``, the only way to reach the
   editor's own hit tests), refuses with a reason when that is not a string parameter or the
   node has no geometry input, and otherwise opens the
   **first-level pie menu** (``Domain``: the seven element kinds).
2. ``Domain = Instance`` opens the **second-level pie menu** (``Instance Mode``:
   ``Point Cloud`` / ``Point Cloud with Reference`` / ``Realized Instance``).
3. The pick then bakes the geometry at that node into a temporary object and enters
   the matching edit mode. The original object is hidden, and every touched state is
   recorded. Select elements with the normal edit-mode tools, then either Enter
   (apply: encode the selection into the node's string parameter and restore
   everything) or Esc / Right-Click (cancel: restore and change nothing).
4. ``pdt.selection_apply`` / ``pdt.selection_cancel`` are the same two actions as
   operators, so a pick that was **paused** (the user left edit mode) can still be
   finished or dropped. ``pdt.selection_cleanup`` removes leftovers.

There is deliberately **no node-editor panel** (L0, ``明确不做``): everything the
picker needs is asked for while picking, which is what makes the parameter-level trigger
possible - the parameter the cursor is on *is* the one that gets written. For the same
reason the picker **never reads the target node's ``Domain`` property**: which component is
baked is decided by the element kind the user picks in the pie menu, and by nothing else
(L0 ``明确不做``: 不读取被识别节点的 domain 属性).

The parameter is written as Houdini group syntax, which is what
``GeometryNodePDTSelection`` parses. The picker itself only ever writes **indices**
(``0-10 12 15-99:2``); the node additionally accepts group names and wildcards
(``rim``, ``arm*``, ``{arm* ^arm3*}``), attribute patterns (``@position.y>0``) and the
operators ``^`` (subtract), ``&`` (intersect) and ``!`` (complement), evaluated left
to right.

That split is deliberate (``PICK-2``): because the picker writes indices, the Python
side only *evaluates* index patterns and ``*``. Group names and ``@attribute`` tokens
are not parsed into indices here and contribute **no** pre-selection points - nor does
anything the picker cannot evaluate, including their complements and the values they
are subtracted from. See ``parser.py``; the contract is ``预选集合 ⊆ 节点选择集合``.

The implementation is deliberately split into plain functions
(``begin_pick``/``finish_pick``/``cancel_pick``) plus thin operator wrappers, so
the whole data path can be tested headless without a node editor context.

Limitations (documented, see tools/pdt/docs):
- The edited node tree must be used by an object's Geometry Nodes modifier. Which
  object is picked from is the **active object** (its first Geometry Nodes modifier
  on that tree); a tree used by exactly one modifier uses that one; otherwise the
  pick is refused, naming the candidates (tools/pdt/docs/archive/07-曲线支持评估.md §16).
- Per-object node-group input overrides (`modifier.properties.inputs...`) are copied
  onto the temporary evaluation, so the pick sees the geometry the node sees.
- What gets picked is the geometry **at the node**, and the picker edits it as one
  of its components: a mesh (points / edges / faces), a curve (control points, in
  `EDIT_CURVE`) or a point cloud (points, in `EDIT_POINTCLOUD` - Blender's point
  cloud edit mode has no point/edge/face sub-modes). A GeometrySet can carry several
  at once - the node keeps selecting on *every* component it is evaluated on, so the
  element kind chosen in the pie menu decides which component the temporary object is
  built from and which indices you see, not what the node applies the string to
  (L0 `PICK-3`; a kind this geometry does not have is refused with what it does have).
- The modifiers *above* the picked Geometry Nodes modifier are evaluated (an
  Array, a Mirror, ...), so the pick geometry is what that modifier receives; only
  the base object's own selection/vertex-group state is rebuilt from scratch.
  When that upstream geometry carries several components, only one can be fed to the
  temporary object (its Group Input takes a single data-block); the mesh wins.
- Groups and attribute patterns are not expanded for pre-selection (above).
- **Instances have three picks** (L0 `PICK-3`): `Point Cloud` bakes
  `instances_pointcloud()` (one point per top level instance, at the instance origin,
  carrying every instance attribute) and edits it in `EDIT_POINTCLOUD`, so the point
  index written back **is** the instance index the node addresses on `Domain =
  Instance`; `Point Cloud with Reference` is that same bake with the source object left
  visible (the origins alone are hard to aim at); `Realized Instance` realizes the
  instances and picks the real geometry, folding the selected elements back into
  instance numbers (measurements in tools/pdt/docs/archive/08-实例支持评估.md).
"""

from __future__ import annotations

import bpy

from . import keymap, menus, operators, preferences, session, state
from .utils.constants import OPERATOR_LABEL, REMINDER

bl_info = {
    "name": "PDT Geometry Selection",
    "author": "PDT Blender Authors",
    "description": (
        "Pick geometry elements in the 3D viewport - cursor on a node's string parameter, "
        "then a pie menu for the element kind - and write the indices into that node's "
        "string parameter"
    ),
    "blender": (4, 2, 0),
    "version": (0, 5, 0),
    "location": "Node Editor > (hover a string parameter row)",
    "warning": "",
    "doc_url": "",
    "category": "Node",
}


def register() -> None:
    """Register everything; each module registers what it defines."""
    operators.register()
    menus.register()
    preferences.register()
    keymap.register()
    session.register()
    print(f"PDT selection picker: registered - {REMINDER}")


def unregister() -> None:
    session.unregister()
    keymap.unregister()
    preferences.unregister()
    menus.unregister()
    operators.unregister()


def register_once() -> None:
    """Idempotent registration: the module is also imported by the test suite."""
    if hasattr(bpy.types, "PDT_OT_selection_pick"):
        return
    register()


if __name__ == "__main__":
    register_once()
