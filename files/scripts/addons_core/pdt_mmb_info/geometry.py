# SPDX-FileCopyrightText: 2025 Blender Foundation
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Which socket of a node carries *the stream at this node* (and nothing else).

`bpy.types.Node` exposes no geometry, so the choice this module makes is the only thing the
information block needs from the node itself: for a node with a geometry output that output
*is* the stream; a node that only takes geometry is reported through its first live input
(MMB-6). The actual reading lives in `evaluate.py`, which owns its own temporary-tree
evaluator and therefore needs no other add-on.
"""

from __future__ import annotations

import bpy

from .utils.constants import GEOMETRY_SOCKET


# ---------------------------------------------------------------------------
# reading geometry at a node
# ---------------------------------------------------------------------------

def _geometry_sockets(node, is_output: bool):
    """The node's live geometry sockets, or ``[]`` outside a geometry tree.

    `ShaderNodeMath` has an "Attribute" *geometry-typed* socket and a `PDT
    Selection` node has a geometry input - neither is a geometry-nodes socket, and
    a shader tree has no evaluated geometry to report at all. Gating on the owning
    tree being a `GeometryNodeTree` keeps "does this node carry a stream?" honest
    without maintaining a list of node types (measured: without the gate, a
    `ShaderNodeMath` in a geometry tree passed the test and a bare
    `GeometryNodePDTSelection` looked like it had a geometry output).
    """
    tree = getattr(node, "id_data", None)
    if tree is None or getattr(tree, "bl_idname", "") != "GeometryNodeTree":
        return []
    sockets = node.outputs if is_output else node.inputs
    return [socket for socket in sockets if socket.bl_idname == GEOMETRY_SOCKET]


def geometry_source(node):
    """The output socket descriptor whose geometry is *the data at this node*.

    A node's own geometry output wins when it has one (that is literally "the output
    stream"), and only otherwise do we fall back to the geometry input.
    """
    outputs = _geometry_sockets(node, True)
    if outputs:
        enabled = [socket for socket in outputs if socket.enabled] or outputs
        return {"node": node.name, "identifier": enabled[0].identifier, "is_output": True}
    inputs = _geometry_sockets(node, False)
    if inputs:
        # Prefer the input that is actually fed; an unconnected one reports "empty".
        linked = [socket for socket in inputs if socket.is_linked]
        chosen = (linked or inputs)[0]
        if chosen.is_linked:
            link = chosen.links[0]
            return {
                "node": link.from_node.name,
                "identifier": link.from_socket.identifier,
                "is_output": True,
            }
        return {"node": node.name, "identifier": chosen.identifier, "is_output": False}
    return None


def upstream_geometry_source(node):
    """Descriptor of the geometry feeding this node's first live geometry input.

    L0 MMB-5 makes this the **only** input the "added attributes" are compared against
    ("多几何输入的节点如 join，新增只和第一个输入比"), so a join does highlight attributes its
    later inputs brought along - that is the requested behaviour, not an oversight.

    "First" follows what the editor shows: a multi-input socket stacks its links by
    `NodeLink.multi_input_sort_id` (smallest on top), while `socket.links` enumerates them
    newest-first, so `min()` is the topmost input rather than `links[0]`.
    """
    for socket in _geometry_sockets(node, False):
        if socket.is_linked:
            link = min(socket.links, key=lambda item: item.multi_input_sort_id)
            return {
                "node": link.from_node.name,
                "identifier": link.from_socket.identifier,
                "is_output": True,
            }
    return None
