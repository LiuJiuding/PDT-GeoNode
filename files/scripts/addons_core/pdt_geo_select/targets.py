# SPDX-FileCopyrightText: 2026 PDT Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Which string parameter a pick writes into.

A node can carry several (`PDT Selection` has one, a group node can have many), so the
trigger names the one under the cursor and this module resolves that identifier - or
refuses rather than guessing (`choose_string_target()`).

Nothing is remembered: the picker keeps **no** per-node note of which parameter was used
last, which is L0 `selection_pick.md`'s 明确不做 ("不记录操作过的字符串参数用了什么选择模式").
The parameter is decided per pick - by the pointer (PICK-1) or, when the caller has no pointer
position, by `choose_string_target()` alone, which reads only the node in front of it."""

from __future__ import annotations

import bpy


_BASE_NODE_PROPERTIES: set[str] | None = None


# Socket names that are almost certainly the one a viewport pick should write.
SELECTION_KEYWORDS = ("selection", "group", "indices", "index", "mask", "range", "pattern")


def _base_node_properties() -> set[str]:
    """Property names every node has (name, label, bl_*, ...). A string parameter
    we may write must not be one of them."""
    global _BASE_NODE_PROPERTIES
    if _BASE_NODE_PROPERTIES is None:
        _BASE_NODE_PROPERTIES = set(bpy.types.Node.bl_rna.properties.keys())
    return _BASE_NODE_PROPERTIES


def list_string_targets(node) -> list[dict]:
    """All writable string parameters of `node`, in display order.

    String sockets come first (that is what the PDT nodes use), then string
    RNA properties defined by that specific node type. Properties inherited from
    the generic Node type (name, label, bl_idname, ...) never qualify, otherwise
    every node would look like it has a string parameter.
    """
    targets: list[dict] = []
    for index, socket in enumerate(node.inputs):
        if socket.bl_idname == "NodeSocketString":
            targets.append(
                {
                    "kind": "socket",
                    "index": index,
                    "identifier": socket.identifier,
                    "name": socket.name,
                    "label": getattr(socket, "label", "") or socket.name,
                }
            )
    base = _base_node_properties()
    for prop in node.bl_rna.properties:
        if prop.type != "STRING" or prop.is_readonly:
            continue
        if prop.identifier.startswith("bl_") or prop.identifier in base:
            continue
        targets.append(
            {
                "kind": "property",
                "index": -1,
                "identifier": prop.identifier,
                "name": prop.name,
                "label": prop.name,
            }
        )
    return targets


def find_string_target(node, wanted: str):
    """The string parameter `wanted` names, or None.

    Matched by socket/property **identifier** first, then by **name/label**: a value
    stored by an older session (or written by hand) may be the visible parameter name,
    and interface sockets get *renumbered* identifiers (`Socket_7`, `Socket_8`) when
    the interface is edited, so a name is the friendlier fallback.
    """
    if node is None or not wanted:
        return None
    for target in list_string_targets(node):
        if target["identifier"] == wanted:
            return target
    for target in list_string_targets(node):
        if wanted in (target["name"], target["label"]):
            return target
    return None


def _keyword_score(target: dict) -> int:
    text = f"{target['name']} {target['label']}".lower()
    for rank, keyword in enumerate(SELECTION_KEYWORDS):
        if keyword in text:
            return len(SELECTION_KEYWORDS) - rank
    return 0


def choose_string_target(node):
    """Pick the parameter to write into, when nothing pointed at one.

    Returns ``(target, reason)`` where `target` is None when the choice would be a guess. The
    picker's normal path never needs this: the cursor is on the socket (PICK-1), so the trigger
    passes that socket's identifier and the pointer decides. This is the fallback for a call
    that carries no pointer position (and for `can_pick()`), and it refuses rather than writing
    into the wrong parameter: one string parameter is unambiguous, several are only decided by a
    single keyword match. It reads nothing but `node` - there is no remembered choice (L0 明确不做).
    """
    targets = list_string_targets(node)
    if not targets:
        return None, f"'{node.name}' has no string parameter"

    if len(targets) == 1:
        return targets[0], ""

    scored = [(target, _keyword_score(target)) for target in targets]
    best = max(score for _target, score in scored)
    winners = [target for target, score in scored if score == best and score > 0]
    if len(winners) == 1:
        return winners[0], ""
    return (
        None,
        f"'{node.name}' has {len(targets)} string parameters; put the cursor on the socket to "
        "pick into and press the shortcut",
    )


def read_target_value(node, target: dict) -> str:
    if target["kind"] == "socket":
        return str(node.inputs[target["index"]].default_value)
    return str(node.get(target["name"], ""))


def write_target_value(node, target: dict, text: str) -> None:
    if target["kind"] == "socket":
        node.inputs[target["index"]].default_value = text
    else:
        node[target["name"]] = text
