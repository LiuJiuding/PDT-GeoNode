# SPDX-FileCopyrightText: 2025 Blender Foundation
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Our own evaluator: what geometry is at this node, right now.

Blender evaluates the geometry a node produced, but it keeps the answer to itself - the
Viewer node and the Spreadsheet show it, and Python cannot read it. Measured with
`qa/viewer/viewer_api_probe.py` on 5.2.1: `SpaceSpreadsheet.viewer_path` is read-only,
`ViewerPath`/`ViewerPathElem` expose no functions at all (so a path cannot be built or
pointed from Python), `WorkSpace.viewer_path`/`SpaceView3D.viewer_path` are not in RNA at
all, the evaluation log that holds the actual `GeometrySet` (`NOD_eval_log.hh`) is not in
RNA either (only `NodesModifier.node_warnings` is, and that is messages, not geometry), and
the Viewer node's sockets are dynamic items with no RNA type - so a Viewer link cannot even
be *created* from Python. What the Viewer node would need to be switched on (`verbose_log`,
`geometry_nodes_lazy_function.cc`) is available only through the UI's viewer path anyway.

So the stream is reconstructed here, without depending on the picker add-on:

    1. copy the tree,
    2. wire the wanted socket to the copy's active group output,
    3. put a temporary object next to the real one, carrying the *same* upstream data and
       the *same* interface overrides, and run the copy as its modifier,
    4. evaluate it and hand the `GeometrySet` view to `snapshot.py`, which reduces it to
       plain data before the temporary tree is released.

Deliberately *not* here, although `pdt_geo_select` needs them for picking: converting hair
curves into a legacy `Curve` (a pick mesh must have the same indices; a report only needs
the stream), instance/element budgets, and the "is this pickable" bookkeeping. The two
add-ons therefore do not share this code, and this one works with the picker absent - which
is what the decoupling guard in `test_mmb_node_info.py` pins down.
"""

from __future__ import annotations

import bpy

from .utils.constants import GEOMETRY_SOCKET, TEMP_PREFIX

#: Sub-properties of a generated node-group input socket that carry an override.
INTERFACE_OVERRIDE_PROPERTIES = ("value", "type", "attribute_name")


# ---------------------------------------------------------------------------
# who is using this tree
# ---------------------------------------------------------------------------

def modifier_index(obj, modifier) -> int:
    """Position of `modifier` in the object's stack (-1 when it is not there)."""
    if obj is None or modifier is None:
        return -1
    for index, candidate in enumerate(obj.modifiers):
        if candidate == modifier:
            return index
    return -1


def list_user_pairs(tree, context=None):
    """Every `(object, modifier)` whose Nodes modifier uses `tree`.

    Ordered the way the editor is: the active object first, then the rest of the view
    layer, then objects outside it. An object with several modifiers on the same tree is
    listed once per modifier - each one can hold different interface values.
    """
    context = context or bpy.context
    ordered = []
    active = getattr(context, "active_object", None)
    if active is not None:
        ordered.append(active)
    view_layer = getattr(context, "view_layer", None)
    if view_layer is not None:
        ordered.extend(view_layer.objects)
    ordered.extend(bpy.data.objects)

    pairs = []
    seen = set()
    for obj in ordered:
        if obj is None:
            continue
        for modifier in obj.modifiers:
            if modifier.type != "NODES" or modifier.node_group != tree:
                continue
            key = (obj.name, modifier.name)
            if key in seen:
                continue
            seen.add(key)
            pairs.append((obj, modifier))
    return pairs


def find_owner(tree, context=None):
    """The `(object, modifier)` pair this tree is read through, or `(None, None)`.

    The active object wins when one of its Nodes modifiers uses `tree` (that is the object
    the user is looking at); otherwise a single user is used; otherwise the situation is
    genuinely ambiguous and the caller reports that instead of reading a random object.
    """
    context = context or bpy.context
    pairs = list_user_pairs(tree, context)
    if not pairs:
        return None, None
    active = getattr(context, "active_object", None)
    if active is not None:
        for obj, modifier in pairs:
            if obj == active:
                return obj, modifier
    if len(pairs) == 1:
        return pairs[0]
    return None, None


# ---------------------------------------------------------------------------
# the temporary tree
# ---------------------------------------------------------------------------

def _socket_by_identifier(node, identifier, is_output):
    """Look a socket up in one direction only.

    Input and output identifiers of the same node can be equal, and mixing the two
    directions makes `links.new()` fail with a "same direction" error.
    """
    sockets = node.outputs if is_output else node.inputs
    for socket in sockets:
        if socket.identifier == identifier:
            return socket
    return None


def build_read_tree(tree, descriptor):
    """Copy `tree` with `descriptor`'s socket wired to the copy's active group output.

    Re-linking replaces whatever fed that output before - which is the point: the copy is
    throwaway, and what it now outputs is exactly the stream at the node we are asked about.
    """
    temp = tree.copy()
    temp.name = TEMP_PREFIX + "group_" + tree.name

    source_node = temp.nodes.get(descriptor["node"])
    if source_node is None:
        raise RuntimeError(f"node '{descriptor['node']}' is no longer in the tree")
    source_socket = _socket_by_identifier(
        source_node, descriptor["identifier"], descriptor.get("is_output", True)
    )
    if source_socket is None:
        raise RuntimeError("the geometry socket vanished from the copied tree")

    output_node = None
    for node in temp.nodes:
        if node.bl_idname == "NodeGroupOutput" and node.is_active_output:
            output_node = node
            break
    if output_node is None:
        output_node = temp.nodes.new("NodeGroupOutput")

    geometry_input = next(
        (socket for socket in output_node.inputs if socket.bl_idname == GEOMETRY_SOCKET), None
    )
    if geometry_input is None:
        temp.interface.new_socket(
            name="Geometry", in_out="OUTPUT", socket_type=GEOMETRY_SOCKET
        )
        geometry_input = next(
            (socket for socket in output_node.inputs if socket.bl_idname == GEOMETRY_SOCKET), None
        )
    if geometry_input is None:
        raise RuntimeError("could not create a geometry output on the temporary node group")

    temp.links.new(source_socket, geometry_input)
    return temp


def release_temp_groups(group_names) -> int:
    """Remove temporary node groups nothing references any more; innermost last.

    Every failure path runs through here: a half-built read used to leave `PDT_MMB_group_*`
    behind in the user's file (MMB-1: temporary products are recycled).
    """
    removed = 0
    for name in reversed(list(group_names or ())):
        group = bpy.data.node_groups.get(name)
        if group is not None and group.users == 0:
            bpy.data.node_groups.remove(group)
            removed += 1
    return removed


# ---------------------------------------------------------------------------
# the same inputs the real modifier has
# ---------------------------------------------------------------------------

def _interface_inputs(owner):
    """The generated ``…properties.inputs`` struct of an object or modifier, or None.

    Blender builds this RNA on demand for every Nodes modifier: one property per
    group-interface input socket, named by the socket *identifier* (`Socket_1`).
    """
    inputs = getattr(getattr(owner, "properties", None), "inputs", None)
    if inputs is None or not hasattr(inputs, "bl_rna"):
        return None
    return inputs


def _interface_socket_identifiers(inputs):
    """Socket identifiers of a generated inputs struct, in interface order.

    `rna_type` is a pointer property as well and is skipped: it is the struct's own
    back-reference, not a socket.
    """
    return [
        prop.identifier
        for prop in inputs.bl_rna.properties
        if prop.type == "POINTER" and prop.identifier != "rna_type"
    ]


def copy_interface_overrides(src_object, src_modifier, temp_object, temp_modifier) -> None:
    """Copy per-object / per-modifier group-input values onto the temporary pair.

    Without this the copied tree would evaluate every input at its **default** and the
    numbers would not be the ones the user sees (measured in the picker's history: an
    "Offset Z" override of 3 baked at 0).

    Two storage shapes are probed: the **generated RNA** Blender 5.x uses
    (``modifier.properties.inputs.<identifier>.value``, plus its `type`/`attribute_name`
    for attribute inputs - *not* reachable through `modifier.keys()`, which raises
    `TypeError` for a Nodes modifier), and plain **ID properties** on the modifier or the
    object, which is how older versions stored per-object overrides.
    """
    for source, target in ((src_modifier, temp_modifier), (src_object, temp_object)):
        try:
            keys = list(source.keys())
        except (TypeError, AttributeError):
            continue
        for key in keys:
            if key.startswith("_"):
                continue
            try:
                target[key] = source[key]
            except Exception:  # noqa: BLE001 - renamed/missing socket, keep going
                pass

    source_inputs = _interface_inputs(src_modifier)
    target_inputs = _interface_inputs(temp_modifier)
    if source_inputs is None or target_inputs is None:
        return
    available = set(_interface_socket_identifiers(target_inputs))
    for identifier in _interface_socket_identifiers(source_inputs):
        if identifier not in available:
            continue
        source_socket = getattr(source_inputs, identifier)
        target_socket = getattr(target_inputs, identifier)
        for name in INTERFACE_OVERRIDE_PROPERTIES:
            if not hasattr(source_socket, name) or not hasattr(target_socket, name):
                continue
            try:
                setattr(target_socket, name, getattr(source_socket, name))
            except Exception:  # noqa: BLE001 - socket type changed, keep the default
                pass


def upstream_base_data(obj, modifier):
    """The data-block `modifier` receives as its group input, or None when it is `obj.data`.

    A Nodes modifier sees the geometry the modifiers **above** it produced, so when there
    are any, the stack up to `modifier` is baked on a throwaway copy of the object and the
    first real component is handed back. With nothing above it, `obj.data` already is the
    input - which is why a plain mesh/curve/point-cloud object returns None here.

    Raises when the stack above produces something no data-block can carry (instances, or
    no geometry at all): reading the wrong input would produce confident wrong numbers,
    and the caller turns this into the block's reason line (MMB-9).
    """
    index = modifier_index(obj, modifier)
    if index <= 0:
        return None

    probe = obj.copy()  # copies the modifier stack
    probe.name = TEMP_PREFIX + "input_" + obj.name
    probe.data = obj.data  # same base data, only the stack is evaluated
    # The copy inherits the source's visibility; a "hide viewport" object is not evaluated
    # at all, and this probe must be.
    try:
        probe.hide_viewport = False
        probe.hide_set(False)
    except Exception:  # noqa: BLE001 - not in this view layer, evaluation still works
        pass
    bpy.context.scene.collection.objects.link(probe)

    try:
        for name in [item.name for item in probe.modifiers][index:]:
            probe.modifiers.remove(probe.modifiers[name])
        depsgraph = bpy.context.evaluated_depsgraph_get()
        geometry = probe.evaluated_get(depsgraph).evaluated_geometry()
        # Empty components do not count: an instances-only upstream still reports an empty
        # mesh slot, and feeding *that* would read an empty stream while the node sees
        # instances.
        if len(getattr(getattr(geometry, "mesh", None), "vertices", ())) > 0:
            upstream = geometry.mesh.copy()
        elif len(getattr(getattr(geometry, "curves", None), "points", ())) > 0:
            upstream = geometry.curves.copy()
        elif len(getattr(getattr(geometry, "pointcloud", None), "points", ())) > 0:
            upstream = geometry.pointcloud.copy()
        else:
            upstream = None
    finally:
        leftover = bpy.data.objects.get(probe.name)
        if leftover is not None:
            bpy.data.objects.remove(leftover, do_unlink=True)

    if upstream is None:
        raise RuntimeError(
            f"the modifiers above '{modifier.name}' do not produce geometry this read can "
            "use (they output instances, or nothing), so the numbers would not be the ones "
            "this node sees"
        )
    upstream.name = TEMP_PREFIX + "upstream_" + obj.name
    return upstream


# ---------------------------------------------------------------------------
# the read itself
# ---------------------------------------------------------------------------

def evaluate_descriptor(context, tree, descriptor):
    """Evaluate `tree` with `descriptor`'s socket wired to the group output.

    Returns the RNA `GeometrySet` view over temporary objects: the caller must reduce it to
    plain data before this function's temporary trees are released (`snapshot.py` does that
    inside `_snapshot_geometry`).
    """
    if tree.nodes.get(descriptor["node"]) is None:
        raise RuntimeError(f"node '{descriptor['node']}' is no longer in the tree")

    temp_groups: list[str] = []
    probe = None
    try:
        temp_tree = build_read_tree(tree, descriptor)
        temp_groups.append(temp_tree.name)

        src_object, modifier = find_owner(tree, context)
        if src_object is None:
            raise RuntimeError(
                f"no object uses the node group '{tree.name}'; the information block needs a "
                "geometry source to evaluate"
            )
        base_data = upstream_base_data(src_object, modifier)
        if base_data is None:
            base_data = src_object.data

        probe = bpy.data.objects.new(TEMP_PREFIX + "probe", base_data)
        context.scene.collection.objects.link(probe)
        probe.matrix_world = src_object.matrix_world
        probe.hide_viewport = False
        probe_modifier = probe.modifiers.new(name=TEMP_PREFIX + "mod", type="NODES")
        probe_modifier.node_group = temp_tree
        copy_interface_overrides(src_object, modifier, probe, probe_modifier)

        context.view_layer.update()
        depsgraph = context.evaluated_depsgraph_get()
        return probe.evaluated_get(depsgraph).evaluated_geometry()
    finally:
        if probe is not None:
            leftover = bpy.data.objects.get(probe.name)
            if leftover is not None:
                bpy.data.objects.remove(leftover, do_unlink=True)
        release_temp_groups(temp_groups)
