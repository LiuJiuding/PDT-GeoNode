# SPDX-FileCopyrightText: 2026 PDT Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Which geometry a pick works on: the object behind the node tree, the breadcrumb for a node
inside a sub-group, the components that geometry carries, and which component an element
kind addresses.

The target node's own `Domain` property is deliberately **not** read anywhere (L0
`明确不做`): what gets baked follows the element kind the user picked in the pie menu."""

from __future__ import annotations

import bpy

from .utils.constants import CURVE_TYPE_TO_SPLINE, DOMAIN_KINDS, FIELD_LINK_MAX_DEPTH, GEOMETRY_KIND_LABELS, KIND_PRIORITY, PICKABLE_KINDS, TEMP_GROUP_PREFIX, TEMP_MESH_PREFIX, TEMP_OBJECT_PREFIX, _kind_list

def _geometry_sockets(node, in_out):
    return [
        socket
        for socket in (node.inputs if in_out == "INPUT" else node.outputs)
        if socket.bl_idname == "NodeSocketGeometry"
    ]


def _field_sockets(node):
    return [
        socket
        for socket in node.outputs
        if socket.bl_idname in {"NodeSocketBool", "NodeSocketFloat", "NodeSocketInt", "NodeSocketVector"}
    ]


def resolve_geometry_source(node):
    """Find the geometry the user wants to edit, as an output socket descriptor.

    Priority:

    1. the link feeding this node's own geometry **input** - that is the geometry
       the node operates on. `PDT Selection` passes its geometry through, so its
       output usually carries the same thing, but when the geometry input is left
       unconnected - the "only wire the Selection output" style the panel
       documents - that output is *empty*, and using it first produced an empty
       pick mesh with nothing to select;
    2. the geometry input of a downstream consumer of one of this node's fields
       (the same "field only" wiring, where the selected geometry actually lives
       on `Delete Geometry` and friends);
    3. the node's own geometry output, as a last resort.
    """
    for socket in _geometry_sockets(node, "INPUT"):
        for link in socket.links:
            return {
                "node": link.from_node.name,
                "identifier": link.from_socket.identifier,
                "is_output": True,
            }

    # Follow field links downstream (bounded) and use the first geometry input
    # of a consumer that is actually connected.
    queue = [(socket, 0) for socket in _field_sockets(node)]
    seen: set[str] = set()
    while queue:
        socket, depth = queue.pop(0)
        if depth > FIELD_LINK_MAX_DEPTH:
            continue
        for link in socket.links:
            consumer = link.to_node
            if consumer.name in seen:
                continue
            seen.add(consumer.name)
            for geom_socket in _geometry_sockets(consumer, "INPUT"):
                for geom_link in geom_socket.links:
                    return {
                        "node": geom_link.from_node.name,
                        "identifier": geom_link.from_socket.identifier,
                        "is_output": True,
                    }
            queue.extend((out, depth + 1) for out in _field_sockets(consumer))

    for socket in _geometry_sockets(node, "OUTPUT"):
        return {"node": node.name, "identifier": socket.identifier, "is_output": True}

    return None


def geometry_kind_label(kind: str, elements: int | None = None) -> str:
    label = GEOMETRY_KIND_LABELS.get(kind, kind.title())
    if elements is None:
        return label
    if kind == "CURVE":
        unit = "control points"
    elif kind == "INSTANCES":
        unit = "instances"
    else:
        unit = "points"
    return f"{label} ({elements} {unit})"


def list_user_objects(tree, context=None):
    """Every (object, modifier) pair whose Geometry Nodes modifier uses `tree`.

    Ordered the way a user expects to find them: the active object first, then
    the rest of the view layer, then objects outside it. Duplicates (an object
    with several modifiers on the same tree) are kept, since each modifier can
    hold different interface values.
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


def find_user_object(tree, context=None):
    """The (object, modifier) pair whose evaluated geometry the pick bakes.

    Deliberately stateless and deterministic (there is no stored chooser value any
    more, tools/pdt/docs/archive/07 §16):

    * the **active object** wins when a Geometry Nodes modifier of it uses `tree`
      (its first such modifier - the pick always follows the geometry of the object
      the user is looking at);
    * otherwise, when `tree` is used by **exactly one** pair, that pair is used;
    * otherwise the choice is genuinely ambiguous and `(None, None)` comes back, so
      the caller refuses with the candidate list instead of silently baking the
      wrong object (`can_pick()` turns that into the panel's disabled reason).
    """
    context = context or bpy.context
    pairs = list_user_objects(tree, context)
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


def object_pairs_hint(pairs) -> str:
    """`"Cube_A, Grid_B, Sphere_C"`: the distinct object names behind the pairs."""
    names = []
    for obj, _modifier in pairs:
        if obj.name not in names:
            names.append(obj.name)
    return ", ".join(names)


def operator_node(context=None):
    """The node the user is working on in the node editor (if any).

    Only a fallback for the entries that carry no pointer position: the picker's trigger is
    socket-level (PICK-1), so it uses the node the **cursor** is on
    (`node_from_hit()`), not the selected one.
    """
    context = context or bpy.context
    node = getattr(context, "node", None)
    if node is not None:
        return node
    selected = getattr(context, "selected_nodes", None)
    if selected:
        return selected[0]
    return None


def _socket_by_identifier(node, identifier, is_output):
    """Look a socket up in one direction only: input and output identifiers of
    the same node can be equal, and mixing them produces 'same direction' errors."""
    sockets = node.outputs if is_output else node.inputs
    for socket in sockets:
        if socket.identifier == identifier:
            return socket
    return None


def _socket_defaults(node) -> dict:
    """Readable input defaults of `node`, by socket identifier.

    Used to preserve a group node's own interface values across a `node_tree`
    swap (the swap can reset them).
    """
    values = {}
    for socket in node.inputs:
        try:
            values[socket.identifier] = socket.default_value
        except (AttributeError, TypeError, RuntimeError):
            continue  # geometry/menu sockets carry no readable default
    return values


def _restore_socket_defaults(node, values: dict) -> None:
    for socket in node.inputs:
        if socket.identifier not in values:
            continue
        try:
            socket.default_value = values[socket.identifier]
        except (AttributeError, TypeError, ValueError):
            continue


def breadcrumb(context=None, tree=None):
    """The node editor's breadcrumb as ``[(tree, group_node_or_None), ...]``.

    Outermost first: ``path[0]`` is the base tree and each later entry is a tree
    that was entered, with `node` being the group node instance inside the
    previous tree. The last entry is the tree being edited.
    """
    context = context or bpy.context
    space = getattr(context, "space_data", None)
    path = getattr(space, "path", None) if space is not None else None
    levels = []
    if path:
        for item in path:
            node_tree = getattr(item, "node_tree", None)
            if node_tree is not None:
                levels.append((node_tree, getattr(item, "node", None)))
    if not levels and tree is not None:
        levels = [(tree, None)]
    return resolve_breadcrumb_nodes(levels)


def resolve_breadcrumb_nodes(levels):
    """Fill in the group node instance of every entered level.

    ``SpaceNodeEditor.path[i]`` has **no `node` property** in this Blender (the
    `NodeTreePath` RNA exposes only `node_tree`, verified in a live session), so
    reading `item.node` always yields None. `build_evaluation_tree()` then broke
    out of its loop at the first level, evaluated only the innermost tree against
    the object's own geometry, and silently produced a pick mesh of the wrong
    geometry whenever the node lived in a group.

    The instance is recovered from the parent tree instead: the group node whose
    `node_tree` is the child tree. A selected one wins - that is the node the
    user just entered - otherwise the first match is used.
    """
    resolved = []
    for index, (node_tree, node) in enumerate(levels):
        if node is None and index > 0:
            parent_tree = levels[index - 1][0]
            if parent_tree is not None:
                candidates = [
                    candidate
                    for candidate in parent_tree.nodes
                    if getattr(candidate, "node_tree", None) == node_tree
                ]
                if candidates:
                    node = next((c for c in candidates if c.select), candidates[0])
        resolved.append((node_tree, node))
    return resolved


def outermost_tree(context=None, tree=None, explicit_chain=None):
    """The top level tree of the breadcrumb (what an object's modifier uses)."""
    chain = explicit_chain if explicit_chain is not None else breadcrumb(context, tree)
    if chain:
        return chain[0][0]
    return tree


def build_evaluation_tree(tree, node, context=None, explicit_chain=None):
    """Copies of every breadcrumb level, wired to evaluate the picked node.

    For a node inside a sub-group no object uses that tree, so the geometry has
    to travel outwards: each level is copied, the group node instance on the
    breadcrumb gets its `node_tree` pointed at the inner copy, and its geometry
    output is re-linked to that level's active group output. The outermost copy
    is then evaluated by a temporary object, exactly like the plain case.

    Returns ``(temp_tree, temp_group_names, owner_object, owner_modifier)``.
    """
    context = context or bpy.context
    chain = explicit_chain if explicit_chain is not None else breadcrumb(context, tree)
    source = resolve_geometry_source(node)
    if source is None:
        raise RuntimeError(f"'{node.name}' has no geometry to bake")

    temp_groups: list[str] = []
    try:
        temp_tree = _build_temp_group(tree, source)
        temp_groups.append(temp_tree.name)

        for index in range(len(chain) - 1, 0, -1):
            child_tree, group_node = chain[index]
            parent_tree = chain[index - 1][0]
            if child_tree is None or parent_tree is None:
                break
            if group_node is None:
                # Never degrade silently: skipping a level used to bake the object's
                # own geometry instead of the geometry entering the group.
                raise RuntimeError(
                    f"could not find the group node that enters '{child_tree.name}' inside "
                    f"'{parent_tree.name}'; re-enter the group in the node editor and try again"
                )
            # The group node's geometry output in the parent: same identifier in the
            # copy, because copying a tree preserves its interface.
            output_socket = next(
                (s for s in group_node.outputs if s.bl_idname == "NodeSocketGeometry"), None
            )
            if output_socket is None:
                raise RuntimeError(
                    f"the group node '{group_node.name}' has no geometry output to evaluate"
                )
            temp_tree = _build_temp_group(
                parent_tree,
                {"node": group_node.name,
                 "identifier": output_socket.identifier,
                 "is_output": True},
                node_tree_replace=(group_node.name, temp_tree),
            )
            temp_groups.append(temp_tree.name)
    except Exception:
        # A partially built chain must not survive: every copy made so far is
        # unreferenced and would otherwise stay in the file as `PDT_PICK_group_*`.
        _release_temp_groups(temp_groups)
        raise

    top_tree = chain[0][0] if chain else tree
    owner_object, owner_modifier = find_user_object(top_tree, context)
    return temp_tree, temp_groups, owner_object, owner_modifier


def _build_temp_group(tree, source, node_tree_replace=None):
    """Copy `tree` and connect the resolved geometry to its active output."""
    temp = tree.copy()
    temp.name = TEMP_GROUP_PREFIX + tree.name

    if node_tree_replace is not None:
        replace_name, child_tree = node_tree_replace
        group_node = temp.nodes.get(replace_name)
        if group_node is None:
            raise RuntimeError(f"group node '{replace_name}' not found in the copied tree")
        preserved = _socket_defaults(group_node)
        group_node.node_tree = child_tree
        _restore_socket_defaults(group_node, preserved)

    src_node = temp.nodes.get(source["node"])
    if src_node is None:
        raise RuntimeError(f"node '{source['node']}' not found in the copied tree")
    src_socket = _socket_by_identifier(src_node, source["identifier"], source.get("is_output", True))
    if src_socket is None:
        raise RuntimeError("geometry socket not found in the copied tree")

    output_node = None
    for node in temp.nodes:
        if node.bl_idname == "NodeGroupOutput" and node.is_active_output:
            output_node = node
            break
    if output_node is None:
        output_node = temp.nodes.new("NodeGroupOutput")

    geom_input = next(
        (socket for socket in output_node.inputs if socket.bl_idname == "NodeSocketGeometry"), None
    )
    if geom_input is None:
        temp.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        geom_input = next(
            (socket for socket in output_node.inputs if socket.bl_idname == "NodeSocketGeometry"), None
        )
    if geom_input is None:
        raise RuntimeError("could not create a geometry output on the temporary node group")

    # Linking replaces the existing link into the group output (throwaway copy).
    temp.links.new(src_socket, geom_input)
    return temp


#: Sub-properties of a generated node-group input socket that carry an override.
INTERFACE_OVERRIDE_PROPERTIES = ("value", "type", "attribute_name")


def interface_inputs(owner):
    """The generated ``…properties.inputs`` struct of an object or modifier, or None.

    Blender builds this RNA on demand for every Nodes modifier, one property per
    group-interface input socket, named by the socket *identifier* (`Socket_1`).
    """
    inputs = getattr(getattr(owner, "properties", None), "inputs", None)
    if inputs is None or not hasattr(inputs, "bl_rna"):
        return None
    return inputs


def interface_socket_identifiers(inputs):
    """Socket identifiers exposed by a generated inputs struct, in interface order.

    `rna_type` is a pointer property as well and is skipped: it is the struct's own
    back-reference, not a socket.
    """
    return [
        prop.identifier
        for prop in inputs.bl_rna.properties
        if prop.type == "POINTER" and prop.identifier != "rna_type"
    ]


def get_interface_input(owner, identifier: str, default=None):
    """Value of a node-group input override on a modifier (or object), or `default`."""
    inputs = interface_inputs(owner)
    if inputs is None:
        return default
    socket = getattr(inputs, identifier, None)
    if socket is None:
        return default
    return getattr(socket, "value", default)


def set_interface_input(owner, identifier: str, value) -> bool:
    """Set the node-group input override `identifier` of `owner`; True when written.

    Blender builds the generated inputs RNA lazily, so a scene that was just
    assembled in Python may not expose it yet - one depsgraph update is forced
    before giving up.
    """
    for attempt in (0, 1):
        inputs = interface_inputs(owner)
        socket = getattr(inputs, identifier, None) if inputs is not None else None
        if socket is not None:
            try:
                socket.value = value
            except Exception:  # noqa: BLE001 - not a value socket (geometry/attribute only)
                return False
            return True
        if attempt == 0:
            try:
                bpy.context.view_layer.update()
            except Exception:  # noqa: BLE001 - no window/view layer in this context
                return False
    return False


def _copy_interface_overrides(src_object, src_modifier, temp_object, temp_modifier) -> None:
    """Copy per-object / per-modifier node-group input values onto the temporary object.

    Two storage shapes are probed, because the picker has to respect whatever the
    user set on the source:

    * the **generated RNA** Blender 5.x uses for a Nodes modifier's group inputs -
      ``modifier.properties.inputs.<socket identifier>.value`` (the float value of
      an input, the attribute name of an attribute input, its value/attribute
      mode). This is where a real override lives and it is *not* reachable through
      `modifier.keys()`, which raises `TypeError: this type doesn't support
      IDProperties` for a Nodes modifier. Without this copy the temporary tree
      evaluated every input at its **default**, so a pair whose modifier overrides
      an input baked geometry that was not overridden (measured: "Offset Z" = 3
      baked at Z = 0, tools/pdt/docs/archive/07-曲线支持评估.md §16);
    * plain **ID properties** on the modifier or the object, which is how older
      Blender versions stored per-object overrides (still copied for
      compatibility).
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
            except Exception:  # noqa: BLE001 - renamed/missing socket, ignore
                pass

    source_inputs = interface_inputs(src_modifier)
    target_inputs = interface_inputs(temp_modifier)
    if source_inputs is None or target_inputs is None:
        return
    available = set(interface_socket_identifiers(target_inputs))
    for identifier in interface_socket_identifiers(source_inputs):
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


def modifier_index(obj, modifier) -> int:
    """Position of `modifier` in the object's stack (-1 when it is not there)."""
    if obj is None or modifier is None:
        return -1
    for index, candidate in enumerate(obj.modifiers):
        if candidate.name == modifier.name:
            return index
    return -1


#: Data-block collection per pickable geometry kind, so the cleanup paths never
#: have to guess a type (a curve pick stores a `Curve`, not a `Mesh`). An instance
#: pick bakes a *point cloud* (one point per instance), so it shares that collection.
TEMP_DATA_COLLECTIONS = {
    "MESH": "meshes",
    "CURVE": "curves",
    "POINTCLOUD": "pointclouds",
    "INSTANCES": "pointclouds",
}


def _geometry_component_kind(geometry) -> str:
    """Which component an evaluated GeometrySet actually carries.

    A component with **no elements** does not count: an object whose tree outputs
    instances still hands back a 0-vertex mesh data-block, and reporting `MESH` for it
    made the pick open an empty edit session instead of saying that instances cannot
    be picked (measured, tools/pdt/docs/archive/08-实例支持评估.md §1). Same for an empty
    mesh/curve/point cloud: "nothing to pick" is the honest answer.

    The RNA `GeometrySet` is a *temporary* view of the evaluated geometry: keep the
    object returned by `Object.evaluated_geometry()` alive while reading it.
    """
    for kind, _elements in geometry_component_sizes(geometry):
        if kind in {"MESH", "CURVE", "POINTCLOUD", "INSTANCES"}:
            return kind
    for attribute, kind in (
        ("volume", "VOLUME"),
        ("grease_pencil", "GREASE_PENCIL"),
    ):
        try:
            component = getattr(geometry, attribute)
        except Exception:  # noqa: BLE001 - not every build exposes every component
            continue
        if component is not None:
            return kind
    return "EMPTY"


def instance_count(geometry) -> int:
    """Number of **top level** instances in an evaluated GeometrySet (0 when none).

    `GeometrySet` has no `.instances` member - the only Python view of the instance
    domain is `instances_pointcloud()`, which is one point per instance with every
    instance attribute copied onto it (`instance_transform`, `.reference_index`, and
    any user attribute). It is a cached snapshot owned by the GeometrySet wrapper, so
    keep the wrapper alive (or copy the point cloud) while reading.
    """
    try:
        component = geometry.instances_pointcloud()
    except Exception:  # noqa: BLE001 - older builds have no such method
        return 0
    if component is None:
        return 0
    position = component.attributes.get("position")
    return len(position.data) if position is not None else 0


def geometry_component_sizes(geometry) -> list[tuple[str, int]]:
    """``[(kind, element_count), ...]`` of an evaluated GeometrySet, in kind order.

    A GeometrySet can carry several components at once (the live scene has a mesh
    and a curve in the same node - see docs/archive/07-曲线支持评估.md §13), so the picker
    has to know *all* of them, not just the first one. Empty components are left out:
    they cannot be picked and the evaluated object always carries an empty mesh slot
    when the tree outputs instances.
    """
    found = []
    for kind, attribute in (("MESH", "mesh"), ("CURVE", "curves"), ("POINTCLOUD", "pointcloud")):
        try:
            component = getattr(geometry, attribute)
        except Exception:  # noqa: BLE001
            continue
        if component is None:
            continue
        if kind == "MESH":
            elements = len(component.vertices)
        else:
            elements = len(component.points)
        if elements > 0:
            found.append((kind, elements))
    instances = instance_count(geometry)
    if instances > 0:
        found.append(("INSTANCES", instances))
    return found


def _unpickable_reason(kind: str) -> str:
    """Why a geometry kind cannot be baked, with the way out for each case."""
    if kind == "EMPTY":
        return "the geometry at this node is empty, so there is nothing to pick"
    if kind == "INSTANCES":
        # Instances *are* pickable (as a point cloud, one point per instance); this is
        # the case where they cannot even be fed in: the temporary tree's Group Input
        # takes a data-block, and an "instances" geometry set has none.
        return (
            "the geometry entering this node comes from instances, which cannot be fed to "
            "the temporary evaluation (a group input takes a data-block): add 'Realize "
            "Instances' above this node and pick the realized geometry, or use 'Instances "
            "to Points' - see tools/pdt/docs/archive/08-实例支持评估.md"
        )
    if kind in PICKABLE_KINDS:
        return f"the {GEOMETRY_KIND_LABELS.get(kind, kind)} component could not be baked at this node"
    return f"the geometry at this node is a {kind.lower()}, which cannot be picked"


def curve_from_curves_geometry(curves, name: str):
    """A legacy `Curve` data-block rebuilt from an evaluated `CurvesGeometry`.

    This is the curve equivalent of `bpy.data.meshes.new_from_object()` - which
    does not exist for curves, and `Object.to_curve()` hands back the *original*
    data instead of the evaluated one (both measured, see
    tools/pdt/docs/archive/07-曲线支持评估.md §5.1). Rebuilding the splines keeps the
    control points, their order and the per-spline structure, which is exactly what
    the node addresses by index:

      * POLY / CATMULL_ROM -> POLY spline (a legacy `Curve` has no Catmull-Rom type;
        the control points - and therefore the indices - are identical, only the
        interpolation differs)
      * BEZIER -> BEZIER spline, handles copied as FREE with their stored positions
        so Blender does not recompute and move them
      * NURBS  -> NURBS spline with the stored order, weights and knot mode
    """
    curve = bpy.data.curves.new(name=name, type="CURVE")
    curve.dimensions = "3D"

    positions = curves.attributes["position"]
    types = curves.attributes.get("curve_type")
    cyclic = curves.attributes.get("cyclic")
    resolution = curves.attributes.get("resolution")
    radius = curves.attributes.get("radius")
    tilt = curves.attributes.get("tilt")
    handle_left = curves.attributes.get("handle_left")
    handle_right = curves.attributes.get("handle_right")
    weight = curves.attributes.get("nurbs_weight")
    order = curves.attributes.get("nurbs_order")
    knots_mode = curves.attributes.get("knots_mode")

    offsets = [(entry.first_point_index, entry.points_length) for entry in curves.curves]
    for curve_index, (first, length) in enumerate(offsets):
        type_value = int(types.data[curve_index].value) if types is not None else 1
        spline_type = CURVE_TYPE_TO_SPLINE.get(type_value, "POLY")
        spline = curve.splines.new(spline_type)
        if spline_type == "BEZIER":
            spline.bezier_points.add(length - 1)
            points = spline.bezier_points
        else:
            spline.points.add(length - 1)
            points = spline.points

        for local in range(length):
            source = first + local
            position = positions.data[source].vector
            if spline_type == "BEZIER":
                point = points[local]
                point.co = position
                point.handle_left = (
                    handle_left.data[source].vector if handle_left is not None else position
                )
                point.handle_right = (
                    handle_right.data[source].vector if handle_right is not None else position
                )
                point.handle_left_type = "FREE"
                point.handle_right_type = "FREE"
                if radius is not None:
                    point.radius = float(radius.data[source].value)
                if tilt is not None:
                    point.tilt = float(tilt.data[source].value)
            else:
                w = float(weight.data[source].value) if (spline_type == "NURBS" and weight is not None) else 1.0
                points[local].co = (position[0], position[1], position[2], w)
                if radius is not None:
                    points[local].radius = float(radius.data[source].value)
                if tilt is not None:
                    points[local].tilt = float(tilt.data[source].value)

        if cyclic is not None and bool(cyclic.data[curve_index].value):
            spline.use_cyclic_u = True
        if resolution is not None and spline_type in {"BEZIER", "NURBS"}:
            spline.resolution_u = max(1, int(resolution.data[curve_index].value))
        if spline_type == "NURBS":
            if order is not None:
                spline.order_u = max(2, min(int(order.data[curve_index].value), length))
            if knots_mode is not None:
                spline.use_endpoint_u = int(knots_mode.data[curve_index].value) in {1, 3}
    return curve


def _release_temp_groups(group_names) -> int:
    """Remove temporary node groups nothing references, innermost last.

    Every failure path in `begin_pick()` and `build_evaluation_tree()` runs through
    here: a half-built pick used to leave `PDT_PICK_group_*` (and a visible
    `PDT_PICK_obj_*`) behind in the user's file.
    """
    removed = 0
    for name in reversed(list(group_names or ())):
        group = bpy.data.node_groups.get(name)
        if group is not None and group.users == 0:
            bpy.data.node_groups.remove(group)
            removed += 1
    return removed


def _remove_temp_datablock(kind: str, name: str) -> bool:
    """Remove a temporary data-block of `kind` when nothing uses it."""
    collection = getattr(bpy.data, TEMP_DATA_COLLECTIONS.get(kind, "meshes"), None)
    if collection is None or not name:
        return False
    datablock = collection.get(name)
    if datablock is None or datablock.users != 0:
        return False
    collection.remove(datablock)
    return True


def _upstream_data(src_object, modifier):
    """Bake the modifier stack *before* `modifier`, or None when there is none.

    A Geometry Nodes modifier receives the geometry produced by the modifiers
    above it, so the pick geometry has to come from that evaluation too - otherwise
    everything the stack already did (an Array, a Mirror, a Subdivision, ...) is
    missing from the picked geometry and the indices do not match what the node
    sees. Only the *viewport* settings are used, exactly like the real stack.

    Returns a data-block of the same *kind* the upstream geometry provides (a curve
    stack stays a curve, so a curve pick can still address its control points).
    When the upstream carries several components only one can be fed to the
    temporary object's Group Input, and the mesh wins - a known boundary, see
    tools/pdt/docs/archive/07-曲线支持评估.md §13.4.
    """
    index = modifier_index(src_object, modifier)
    if index <= 0:
        return None, ""

    probe = src_object.copy()  # copies the modifier stack
    probe.name = TEMP_OBJECT_PREFIX + "input_" + src_object.name
    probe.data = src_object.data  # same base data, only the stack is evaluated
    # The copy inherits the source's visibility; a "hide viewport" object is not
    # evaluated at all, and this probe must be.
    try:
        probe.hide_viewport = False
        probe.hide_set(False)
    except Exception:  # noqa: BLE001 - not in this view layer, evaluation still fine
        pass
    bpy.context.scene.collection.objects.link(probe)

    try:
        for name in [item.name for item in probe.modifiers][index:]:
            probe.modifiers.remove(probe.modifiers[name])
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = probe.evaluated_get(depsgraph)
        geometry = evaluated.evaluated_geometry()
        present = dict(geometry_component_sizes(geometry))
        # Empty components do not count either: an instances-producing upstream still
        # hands back an empty mesh slot, and feeding *that* to the probe would bake an
        # empty pick while the node sees instances (tools/pdt/docs/archive/08-实例支持评估.md §1/§5).
        if present.get("MESH"):
            upstream, kind = geometry.mesh.copy(), "MESH"
        elif present.get("CURVE"):
            upstream, kind = (
                curve_from_curves_geometry(geometry.curves, "upstream"),
                "CURVE",
            )
        elif present.get("POINTCLOUD"):
            upstream, kind = geometry.pointcloud.copy(), "POINTCLOUD"
        else:
            upstream, kind = None, _geometry_component_kind(geometry)
    finally:
        bpy.data.objects.remove(probe, do_unlink=True)

    if upstream is None:
        raise RuntimeError(
            "the modifiers above this one do not produce geometry the picker can bake, so the "
            f"pick mesh would not match this node: {_unpickable_reason(kind)}"
        )

    upstream.name = TEMP_MESH_PREFIX + "input_" + src_object.name
    return upstream, kind


def _free_temp_datablock(datablock) -> None:
    """Release a data-block created by the pick, whatever its type."""
    if datablock is None or datablock.users != 0:
        return
    for collection_name in ("meshes", "curves", "pointclouds"):
        collection = getattr(bpy.data, collection_name, None)
        if collection is None:
            continue
        if collection.get(datablock.name) is not None:
            collection.remove(datablock)
            return


def available_geometry_kinds(tree, node, context=None, explicit_chain=None, strict=False) -> list:
    """``[(kind, elements), ...]`` the node's geometry source actually provides.

    One temporary evaluation, fully cleaned up. This is the only way to know which
    components exist: a GeometrySet can carry a mesh and a curve at the same time
    (the live scene does), and that is only visible after evaluating the tree - a
    panel draw must never do this, so the pick asks for it on demand.

    Failures are swallowed (a panel draw must not raise); with `strict=True` the real
    error is raised instead, which is how `begin_pick()` tells "nothing here" from
    "this geometry cannot be fed to the temporary evaluation" (an instances upstream,
    see tools/pdt/docs/archive/08-实例支持评估.md §11.4).
    """
    context = context or bpy.context
    temp_groups: list[str] = []
    probe = None
    upstream = None
    try:
        temp_tree, temp_groups, src_object, modifier = build_evaluation_tree(
            tree, node, context, explicit_chain=explicit_chain
        )
        if src_object is None:
            return []
        upstream, _kind = _upstream_data(src_object, modifier)
        base_data = upstream if upstream is not None else src_object.data
        probe = bpy.data.objects.new(TEMP_OBJECT_PREFIX + "scan_" + src_object.name, base_data)
        bpy.context.scene.collection.objects.link(probe)
        probe.matrix_world = src_object.matrix_world
        probe_modifier = probe.modifiers.new(name="PDT_PICK", type="NODES")
        probe_modifier.node_group = temp_tree
        _copy_interface_overrides(src_object, modifier, probe, probe_modifier)
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        geometry = probe.evaluated_get(depsgraph).evaluated_geometry()
        return geometry_component_sizes(geometry)
    except Exception:  # noqa: BLE001 - the pick itself reports the real reason
        if strict:
            raise
        return []
    finally:
        if probe is not None:
            leftover = bpy.data.objects.get(probe.name)
            if leftover is not None:
                bpy.data.objects.remove(leftover, do_unlink=True)
        _free_temp_datablock(upstream)
        _release_temp_groups(temp_groups)


def _domain_kind(domain: str) -> str:
    """The geometry component an element kind addresses ('' for an unknown kind)."""
    return DOMAIN_KINDS.get(domain, "")


def resolve_geometry_kind(node, available, explicit: str | None = None, domain: str | None = None):
    """``(kind, source)`` the pick should address, or ``(None, reason)``.

    Priority: an explicit request (operator argument) -> the component the element kind names
    -> the only available component -> the first pickable one in `KIND_PRIORITY`.

    **The node is only used for its error message / its geometry source, never to decide what
    gets baked** (L0 ``明确不做``: 不读取被识别节点的 domain 属性——烤什么完全由 pie menu 决定).
    What *is* enforced is that the geometry actually carries the chosen component, otherwise the
    pick would bake something the written numbers cannot mean.

    `domain` (an element kind, when given) steers the *automatic* choice: `Spline` has to land
    on a curve component even when a mesh is present too.
    """
    kinds = [kind for kind, _elements in available]
    if not kinds:
        return None, "the geometry at this node is empty"

    if explicit:
        if explicit in kinds:
            return explicit, "explicit"
        return None, (
            f"the geometry at this node has no {GEOMETRY_KIND_LABELS.get(explicit, explicit)} "
            f"component (it has: {_kind_list(kinds)})"
        )

    # The element kind the user chose in the pie menu names the component (L0 PDS-9's mapping),
    # and that choice is the only thing that decides what is baked.
    chosen_kind = _domain_kind(domain) if domain else ""
    if chosen_kind and chosen_kind in kinds:
        return chosen_kind, "domain"

    if len(kinds) == 1:
        return kinds[0], "only"

    pickable = [kind for kind in kinds if kind in PICKABLE_KINDS]
    if pickable:
        return min(pickable, key=KIND_PRIORITY.index), "default"
    return kinds[0], "default"
