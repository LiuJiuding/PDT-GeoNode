# SPDX-FileCopyrightText: 2026 PDT Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The trigger's preflight: which socket is under the cursor, and why a pick may not start.

The answer comes from the C++ query operator `pdt.node_under_cursor`
(`source/blender/editors/space_node/node_pdt_query.cc`) - the only way a script can reach
the editor's own hit tests (`node_find_indicated_socket()` for the connection dot, and the
socket *row*: its name and value field, which is what a user actually aims at). Every
refusal is a pure function of the hit dict (`hit_refusal_reason()`), so all of the wording
is testable without a window."""

from __future__ import annotations

import bpy

from .utils.constants import NODE_UNDER_CURSOR_IDNAME
from .geometry import find_user_object, list_user_objects, object_pairs_hint, outermost_tree, resolve_geometry_source
from .targets import choose_string_target, find_string_target

#: Fields `pdt.node_under_cursor` publishes (see its RNA definition). Read through
#: `operator_properties_last()`; a missing one reads as its default.
HIT_FIELDS = (
    "found",
    "node_name",
    "node_idname",
    "socket_identifier",
    "socket_name",
    "socket_idname",
    "is_string",
    "is_input",
    "has_geometry_input",
    "hit_region",
)


#: The fields that are booleans (everything else in `HIT_FIELDS` is text).
HIT_BOOLEAN_FIELDS = frozenset({"found", "is_string", "is_input", "has_geometry_input"})


def pick_hit_test(context=None, region_x=None, region_y=None, ops=None) -> dict:
    """Which node socket is under the cursor, straight from the editor (PICK-1).

    `region_x` / `region_y` are region pixels (what a key event reports as
    `event.mouse_region_x/y`); without them the query falls back to the pointer position
    the event system last saw, which is what a menu search (`F3`) has. `ops` is a test seam:
    the headless suite passes a stand-in, because `pdt.node_under_cursor` needs a real node
    editor region to answer at all.

    Always returns a dict, with ``state`` saying how far the query got:

    ``"ok"``
        The query ran; ``found`` and the fields above describe what is under the cursor.
    ``"no_operator"``
        This Blender has no ``pdt.node_under_cursor`` - the add-on is running on a build
        without this branch's C++ operator, so the picker cannot know what is under the
        cursor and every entry point has to refuse.
    ``"no_editor"``
        No node editor under the cursor (the operator's poll failed), or the query raised.
    ``"no_result"``
        The query ran but published nothing (cannot normally happen).

    `hit_region` says *how* the cursor hit: `"socket"` for the connection dot, `"row"` for the
    socket's name or value field - the two things PICK-1 names. Aiming at the dot is fiddly,
    so the row is the normal case.

    Why an operator and not an RNA property: both hit tests (`node_find_indicated_socket()` and
    this file's row test) are behind `editors/space_node/node_intern.hh`, a module-internal
    header, so the only way a script can reach them is an operator - and an operator can take
    the *event's* coordinates, which the editor's `snode.runtime->cursor` cannot (that one is
    written while drawing, so it is a frame behind).
    """
    context = context or bpy.context
    result = {"state": "ok", "found": False}
    result.update({field: (False if field in HIT_BOOLEAN_FIELDS else "") for field in HIT_FIELDS})

    ops = ops if ops is not None else bpy.ops
    ops_pdt = getattr(ops, "pdt", None)
    query = getattr(ops_pdt, "node_under_cursor", None) if ops_pdt is not None else None
    if query is None:
        result["state"] = "no_operator"
        return result

    kwargs = {}
    if region_x is not None and region_y is not None:
        kwargs = {"region_x": int(region_x), "region_y": int(region_y)}
    try:
        query("EXEC_DEFAULT", **kwargs)
    except (RuntimeError, TypeError) as ex:
        # `RuntimeError` is Blender's "poll failed" (no node editor here); `TypeError` means
        # the operator does not take coordinates (an older build of it).
        print(f"PDT picker: '{NODE_UNDER_CURSOR_IDNAME}' could not answer ({ex})")
        result["state"] = "no_editor"
        return result

    window_manager = getattr(context, "window_manager", None) or bpy.context.window_manager
    properties = window_manager.operator_properties_last(NODE_UNDER_CURSOR_IDNAME)
    if properties is None:
        result["state"] = "no_result"
        return result
    for field in HIT_FIELDS:
        value = getattr(properties, field, None)
        if value is None:
            continue
        result[field] = bool(value) if field in HIT_BOOLEAN_FIELDS else str(value)
    return result


def hit_refusal_reason(hit: dict) -> str:
    """Why this hit cannot start a pick, '' when it can (L0 PICK-1's prompts).

    Kept a pure function of the hit dict so every refusal is testable without a window.
    """
    state = hit.get("state", "ok")
    if state == "no_operator":
        return (
            "this Blender has no '" + NODE_UNDER_CURSOR_IDNAME + "' operator, so the picker "
            "cannot tell which socket the cursor is on (it needs a build from this branch)"
        )
    if state == "no_editor":
        return "hover the node editor (the cursor position is only known there)"
    if state == "no_result":
        return f"'{NODE_UNDER_CURSOR_IDNAME}' reported nothing"
    if not hit.get("found"):
        return (
            "no string parameter under the cursor: put the cursor on the parameter's socket, "
            "name or input field, then press the shortcut"
        )
    if not hit.get("is_input"):
        name = hit.get("socket_name") or hit.get("socket_identifier") or "?"
        kind = hit.get("socket_idname") or "socket"
        return (
            f"the socket under the cursor is the *output* '{name}' ({kind}); a pick writes into "
            "an input parameter"
        )
    if not hit.get("is_string"):
        name = hit.get("socket_name") or hit.get("socket_identifier") or "?"
        kind = hit.get("socket_idname") or "socket"
        return f"the socket under the cursor is '{name}' ({kind}), not a string parameter"
    if not hit.get("has_geometry_input"):
        return (
            f"'{hit.get('node_name') or 'this node'}' has no geometry input, so there is "
            "nothing to pick from"
        )
    return ""


def node_from_hit(tree, hit: dict):
    """The node a hit names, looked up in `tree` (the tree the editor is showing)."""
    name = hit.get("node_name", "")
    if tree is None or not name:
        return None
    return tree.nodes.get(name)


def can_pick(tree, node, context=None, explicit_chain=None, target_identifier=None):
    """Return (ok, reason), so a caller can explain why a pick cannot start.

    `target_identifier` is the string parameter the cursor was on (PICK-1). With it, the check
    does not have to guess between several parameters - which the trigger never does, because
    the pointer already decided. Without it there is nothing to fall back on but the node in
    front of the caller: the picker keeps no note of an earlier choice (L0 明确不做).
    """
    if tree is None or node is None:
        return False, "Select a node in the node editor"
    if target_identifier:
        target = find_string_target(node, target_identifier)
        reason = f"'{node.name}' has no string parameter '{target_identifier}'"
    else:
        target, reason = choose_string_target(node)
    if target is None:
        return False, reason
    if resolve_geometry_source(node) is None:
        return False, f"'{node.name}' has no connected geometry input, geometry consumer, or geometry output"
    top_tree = outermost_tree(context, tree, explicit_chain)
    pairs = list_user_objects(top_tree, context)
    obj, _modifier = find_user_object(top_tree, context)
    if obj is None:
        if not pairs:
            return False, "No object uses this node tree in a Geometry Nodes modifier"
        return False, (
            "Several objects use this node tree; activate the one to pick on "
            f"(candidates: {object_pairs_hint(pairs)})"
        )
    return True, ""


def pick_geometry_hint(tree, node=None, context=None) -> str:
    """A *cheap* hint for source objects whose own data is unusual.

    Deliberately no depsgraph evaluation: which geometry the node actually sees can only be
    known by baking it (the pick reports that precisely, and the pie menu marks the element
    kinds this geometry does not have). What is known for free is the object's own data type -
    and since the picker follows the *node's* geometry, only types that cannot be addressed at
    all are worth a hint. The trigger reports it once, right before the pie opens.
    """
    context = context or bpy.context
    obj, _modifier = find_user_object(tree, context)
    if obj is None or obj.type in {"MESH", "CURVE", "POINTCLOUD"}:
        return ""
    return f"{obj.type.title()} object: the pick uses the geometry at the node, not this object"
