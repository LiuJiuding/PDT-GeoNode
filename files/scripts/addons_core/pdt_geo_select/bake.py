# SPDX-FileCopyrightText: 2026 PDT Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Turning the geometry at the node into the temporary object the user edits, and releasing it
again - the evaluated mesh / curve / point cloud, the instance-origin point cloud, and the
realized-instance bake (L0 `PICK-3`, `PICK-6`)."""

from __future__ import annotations

import re
import bpy

from .utils.constants import GEOMETRY_KIND_LABELS, PDT_INSTANCE_INDEX_ATTR, TEMP_MESH_PREFIX, TEMP_OBJECT_PREFIX
from .geometry import _copy_interface_overrides, _free_temp_datablock, _geometry_component_kind, _unpickable_reason, _upstream_data, curve_from_curves_geometry, instance_count
from .utils import constants

def _evaluated_mesh(evaluated):
    """``(mesh_copy_or_None, component_kind)`` of an evaluated object.

    Uses `Object.evaluated_geometry()` instead of
    `bpy.data.meshes.new_from_object()`: the RNA conversion refuses a Curve or
    Point Cloud *object* even when the evaluated geometry **is** a mesh, which is
    what made picking on a curve object fail with
    `TypeError: Object.data expected a Curve type, not Mesh`
    (measured, see tools/pdt/docs/archive/07-曲线支持评估.md §3).
    """
    geometry = evaluated.evaluated_geometry()
    kind = _geometry_component_kind(geometry)
    if geometry.mesh is None or len(geometry.mesh.vertices) == 0:
        # An object whose tree outputs instances still carries an empty mesh slot;
        # baking it used to open an empty `EDIT_MESH` session (tools/pdt/docs/archive/08-实例支持评估.md §1).
        return None, kind
    return geometry.mesh.copy(), kind


def _evaluated_curve(evaluated, name: str):
    """``(curve_datablock_or_None, component_kind)`` of an evaluated object."""
    geometry = evaluated.evaluated_geometry()
    kind = _geometry_component_kind(geometry)
    curves = geometry.curves
    if curves is None or len(curves.points) == 0:
        return None, kind
    return curve_from_curves_geometry(curves, name), kind


def _evaluated_pointcloud(evaluated, name: str):
    """``(pointcloud_datablock_or_None, component_kind)`` of an evaluated object.

    A point cloud needs no rebuilding: unlike curves and meshes it *is* an attribute
    container, so a plain `copy()` of the evaluated data-block is a faithful,
    independent temporary object (measured, qa/picker/pointcloud_probe.py).
    """
    geometry = evaluated.evaluated_geometry()
    kind = _geometry_component_kind(geometry)
    pointcloud = geometry.pointcloud
    if pointcloud is None or len(pointcloud.points) == 0:
        return None, kind
    baked = pointcloud.copy()
    baked.name = name
    return baked, kind


def _set_instance_origin_positions(cloud) -> int:
    """Move each point of an instance cloud onto its instance origin.

    `GeometrySet.instances_pointcloud()` promises one point per instance with the
    per-instance data as point attributes, but its **`position` attribute is not the
    instance position** - measured twice: zeros on a fresh cloud and uninitialised
    garbage (9.9e27) on another run. The transform is authoritative, and its location
    is exactly what Blender's `Position` field means on the instance domain
    (`bke::instance_position_varray`), so the positions are rewritten from it.

    Fast path: `foreach_get`/`foreach_set` with one flat buffer (measured 0.044 s for
    100k instances vs 0.132 s for the per-instance loop). A `FLOAT4X4` attribute
    flattens **column major**, so the translation is at +12/+13/+14; that layout is
    verified against the RNA matrix of the first instance before it is used, and the
    plain loop is the fallback.
    """
    transform = cloud.attributes.get("instance_transform")
    position = cloud.attributes.get("position")
    if transform is None or position is None:
        return 0
    total = min(len(transform.data), len(position.data))
    if total == 0:
        return 0

    def translation(matrix):
        return (matrix[0][3], matrix[1][3], matrix[2][3])

    reference = translation(transform.data[0].value)
    try:
        flat = [0.0] * (total * 16)
        transform.data.foreach_get("value", flat)
        if (flat[12], flat[13], flat[14]) == reference:
            vectors = [0.0] * (total * 3)
            for index in range(total):
                base = index * 16
                vectors[index * 3] = flat[base + 12]
                vectors[index * 3 + 1] = flat[base + 13]
                vectors[index * 3 + 2] = flat[base + 14]
            position.data.foreach_set("vector", vectors)
            return total
    except Exception:  # noqa: BLE001 - fall through to the explicit loop
        pass

    for index in range(total):
        matrix = transform.data[index].value
        position.data[index].vector = translation(matrix)
    return total


def _evaluated_instance_points(evaluated, name: str):
    """``(pointcloud_datablock_or_None, component_kind)`` for the INSTANCES kind.

    One point per **top level instance**, at the instance origin, carrying every
    instance attribute (`instance_transform`, `.reference_index`, and any
    `group:`/user attribute) - so the picker edits a real point cloud and the point
    index *is* the instance index the node addresses on `Domain = Instance`.

    `GeometrySet` has no `.instances` member; `instances_pointcloud()` is the only
    Python view of the instance domain (tools/pdt/docs/archive/07-曲线支持评估.md §6 style traps: the
    returned data-block belongs to that temporary wrapper, so it is copied right
    away, and the wrapper has to stay alive while reading).
    """
    geometry = evaluated.evaluated_geometry()
    kind = _geometry_component_kind(geometry)
    if instance_count(geometry) == 0:
        return None, kind
    cloud = geometry.instances_pointcloud()
    if cloud is None:
        return None, kind
    baked = cloud.copy()
    baked.name = name
    _set_instance_origin_positions(baked)
    return baked, kind


def _splice_instance_index_and_realize(tree) -> bool:
    """Insert the instance-index store + `Realize Instances` into a *copy* of the tree.

    The picker already evaluates the geometry at the node on a throwaway copy of the
    tree (`_build_temp_group` re-links the resolved geometry to the group output), so
    the two nodes can be spliced in between - the user's own tree is never touched.

    The index is stored as `Index + 1`: `Realize Instances` gives elements that did
    **not** come from an instance the attribute default 0, which is therefore a usable
    "not an instance" sentinel (measured, qa/instance_probe5.py section M).
    """
    output_node = None
    for node in tree.nodes:
        if node.bl_idname == "NodeGroupOutput" and node.is_active_output:
            output_node = node
            break
    if output_node is None:
        return False
    geometry_input = next(
        (socket for socket in output_node.inputs if socket.bl_idname == "NodeSocketGeometry"), None
    )
    if geometry_input is None or not geometry_input.links:
        return False
    source_socket = geometry_input.links[0].from_socket
    tree.links.remove(geometry_input.links[0])

    store = tree.nodes.new("GeometryNodeStoreNamedAttribute")
    store.data_type = "INT"
    store.domain = "INSTANCE"
    store.inputs["Name"].default_value = PDT_INSTANCE_INDEX_ATTR
    index = tree.nodes.new("GeometryNodeInputIndex")
    add_one = tree.nodes.new("ShaderNodeMath")
    add_one.operation = "ADD"
    add_one.inputs[1].default_value = 1.0
    realize = tree.nodes.new("GeometryNodeRealizeInstances")

    tree.links.new(source_socket, store.inputs["Geometry"])
    tree.links.new(index.outputs["Index"], add_one.inputs[0])
    tree.links.new(add_one.outputs[0], store.inputs["Value"])
    tree.links.new(store.outputs["Geometry"], realize.inputs["Geometry"])
    tree.links.new(realize.outputs["Geometry"], geometry_input)
    return True


def _attribute_int_values(attribute) -> list[int]:
    """All values of an INT attribute as a plain list (fast path: `foreach_get`)."""
    count = len(attribute.data)
    try:
        values = [0] * count
        attribute.data.foreach_get("value", values)
        return [int(value) for value in values]
    except Exception:  # noqa: BLE001 - some attribute types are not foreach-able
        return [int(attribute.data[index].value) for index in range(count)]


#: Component order a realized pick looks at when nothing is pinned: the mesh first,
#: because that is what instances are made of in almost every tree.
REALIZED_PICK_ORDER = (
    ("MESH", "mesh", "POINT"),
    ("CURVE", "curves", "CURVE"),
    ("POINTCLOUD", "pointcloud", "POINT"),
)


def _realized_instance_info(geometry) -> dict:
    """What a realized pick would edit: sub-kind, element count, instance count.

    Returns a dict with `error` set when there is nothing usable. A mixed realized stream is
    resolved by `REALIZED_PICK_ORDER`; nothing about the *node* takes part in that choice
    (L0 `明确不做`: 烤什么完全由 pie menu 决定).
    """
    info = {
        "sub_kind": "",
        "elements": 0,
        "instances": 0,
        "spline_instances": None,
        "attribute_domain": "",
        "error": "",
    }
    for kind, attribute_name, attribute_domain in REALIZED_PICK_ORDER:
        component = getattr(geometry, attribute_name, None)
        if component is None:
            continue
        elements = len(component.vertices) if kind == "MESH" else len(component.points)
        if elements == 0:
            continue
        attribute = component.attributes.get(PDT_INSTANCE_INDEX_ATTR)
        if attribute is None or attribute.domain != attribute_domain:
            continue
        values = _attribute_int_values(attribute)
        instances = max(values) if values else 0
        if instances <= 0:
            info["error"] = (
                "the realized geometry has no instance-derived elements (the instance index "
                "attribute is all zero) - does the geometry at this node actually have instances?"
            )
            return info
        info.update(
            sub_kind=kind,
            elements=elements,
            instances=instances,
            attribute_domain=attribute_domain,
            spline_instances=values if kind == "CURVE" else None,
        )
        return info
    info["error"] = (
        f"could not realize the instances at this node into geometry that carries a "
        f"'{PDT_INSTANCE_INDEX_ATTR}' attribute "
        "(a realized pick needs mesh, curve or point cloud instances)"
    )
    return info


def _evaluated_realized_instance_data(evaluated, name: str):
    """``(data, sub_kind, info)`` for a REALIZED instance pick, or raise with the reason.

    The size gate lives here on purpose: realizing is cheap (0.02 s at 4.8 M vertices),
    the cost is the edit mode that follows (1.7 s and a 5.5 GB working set), so the
    refusal has to happen before the bake hands the data to `EDIT_MESH`.
    """
    geometry = evaluated.evaluated_geometry()
    info = _realized_instance_info(geometry)
    if info["error"]:
        raise RuntimeError(info["error"])
    unit = "control points" if info["sub_kind"] == "CURVE" else "elements"
    if info["elements"] > constants.INSTANCE_REALIZE_MAX_ELEMENTS:
        raise RuntimeError(
            f"the realized instances have {info['elements']} {unit}, over the "
            f"{constants.INSTANCE_REALIZE_MAX_ELEMENTS} limit: entering an edit mode copies the "
            "geometry and can freeze Blender (measured: 4.82 M vertices = 1.74 s and a "
            "5.5 GB working set) - switch the instance pick to 'Points', or reduce the "
            "instance count (tools/pdt/docs/archive/08-实例支持评估.md §5.5)"
        )
    data, present_kind = _evaluated_object_data(evaluated, info["sub_kind"], name)
    if data is None:
        raise RuntimeError(
            f"the realized instances could not be baked as a "
            f"{GEOMETRY_KIND_LABELS.get(present_kind, present_kind)} component"
        )
    return data, info["sub_kind"], info


def _evaluated_object_data(evaluated, kind: str, name: str):
    """``(data_block_or_None, component_kind_present)`` for the requested kind."""
    if kind == "CURVE":
        return _evaluated_curve(evaluated, name)
    if kind == "POINTCLOUD":
        return _evaluated_pointcloud(evaluated, name)
    if kind == "INSTANCES":
        return _evaluated_instance_points(evaluated, name)
    return _evaluated_mesh(evaluated)


def _bake_temp_object(
    src_object,
    modifier,
    temp_tree,
    geometry_kind: str = "MESH",
    instance_mode: str = "POINTS",
    splice_tree_name: str = "",
):
    """Evaluate the temporary tree and turn the result into a real object of `geometry_kind`.

    Returns ``(temp_object, baked_data, realized_info)``; `realized_info` is only set
    for a `REALIZED` instance pick (it carries the instance count and the per-spline
    instance values a curve source needs). What must be pickable is the geometry *at
    the node*, so the temporary object is created from the baked data instead of the
    source object's own data: a Curve/Point Cloud object cannot take a mesh
    data-block at all (`Object.data expected a Curve type, not Mesh`), which made
    every non-mesh source object fail - even when the tree converted the geometry
    to a mesh before the picked node (tools/pdt/docs/archive/07-曲线支持评估.md §3/§4).

    With `geometry_kind="CURVE"` the curve component is baked into a legacy `Curve`,
    so the temporary object can be edited in `EDIT_CURVE` and its control points
    written back as the same flat indices the node addresses (§5). With
    `geometry_kind="INSTANCES"` either the instance origins are baked as a point cloud
    (`POINTS`) or the instances are realized first (`REALIZED`, M3 in
    tools/pdt/docs/archive/08-实例支持评估.md §5).
    """
    realized_info = None
    if geometry_kind == "INSTANCES" and instance_mode == "REALIZED":
        # The two splice nodes have to go into the *innermost* copy: that is the one
        # whose group output the resolved geometry socket was re-linked to.
        target = bpy.data.node_groups.get(splice_tree_name) or temp_tree
        if not _splice_instance_index_and_realize(target):
            raise RuntimeError(
                "could not splice 'Realize Instances' into the temporary tree: the picked "
                "node has no geometry reaching the group output to realize"
            )

    upstream, _upstream_kind = _upstream_data(src_object, modifier)
    base_data = upstream if upstream is not None else src_object.data

    # Phase 1: evaluate the temporary tree on a throwaway object that carries the
    # base geometry (it has to be a real object - the tree's Group Input is fed by
    # the object's own data).
    probe = bpy.data.objects.new(TEMP_OBJECT_PREFIX + src_object.name, base_data)
    bpy.context.scene.collection.objects.link(probe)
    probe.matrix_world = src_object.matrix_world
    probe_modifier = probe.modifiers.new(name="PDT_PICK", type="NODES")
    probe_modifier.node_group = temp_tree
    _copy_interface_overrides(src_object, modifier, probe, probe_modifier)

    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = probe.evaluated_get(depsgraph)
        if geometry_kind == "INSTANCES" and instance_mode == "REALIZED":
            baked, present_kind, realized_info = _evaluated_realized_instance_data(
                evaluated, TEMP_MESH_PREFIX + src_object.name
            )
        else:
            baked, present_kind = _evaluated_object_data(
                evaluated, geometry_kind, TEMP_MESH_PREFIX + src_object.name
            )
    finally:
        bpy.data.objects.remove(probe, do_unlink=True)
        _free_temp_datablock(upstream)

    if baked is None:
        raise RuntimeError(
            f"the geometry at this node cannot be picked as a {geometry_kind.lower()}: "
            + _unpickable_reason(present_kind)
        )

    # Phase 2: the object the user edits, with no modifiers (the evaluated stack is
    # already baked into `baked`). Its data-block type follows `geometry_kind`.
    temp_object = bpy.data.objects.new(TEMP_OBJECT_PREFIX + src_object.name, baked)
    bpy.context.scene.collection.objects.link(temp_object)
    temp_object.matrix_world = src_object.matrix_world
    return temp_object, baked, realized_info
