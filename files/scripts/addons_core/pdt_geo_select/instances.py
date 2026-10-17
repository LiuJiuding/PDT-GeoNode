# SPDX-FileCopyrightText: 2026 PDT Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Instances: the three `Instance Mode`s (L0 `PICK-3`) and the plumbing that writes *instance*
numbers - the instance-origin point cloud, and the realized pick that stores
`pdt_instance_index` before `Realize Instances` so picked elements fold back."""

from __future__ import annotations

import bpy
import bmesh

from .bake import _attribute_int_values
from .utils.constants import DEFAULT_INSTANCE_MODE, INSTANCE_MODES, INSTANCE_MODE_STORE_KEY, MESH_POINT_DOMAIN, PDT_INSTANCE_INDEX_ATTR, REFERENCE_STORE_KEY, blender_attr_domain
from .selection import curve_spline_point_ranges, read_selected_indices, session_pick_kind
from . import state as pick_state

def reference_for_mode(instance_mode: str) -> bool:
    """Does an instance pick in `instance_mode` keep the source object visible? (PICK-3)

    Only instance picks are affected: every other kind bakes the geometry itself, so the
    temporary object *is* what the user sees. A point pick only has points at the instance
    origins, and without the source there is nothing to aim at
    (tools/pdt/docs/archive/08-实例支持评估.md §13).
    """
    return instance_mode in ("POINTS_REFERENCE", "REALIZED")


def stored_instance_mode(node) -> str:
    """The instance mode remembered **on the node**, '' when there is none.

    Nothing writes this any more: the mode is chosen in the pie menu for every pick and the
    session carries it (L0 `明确不做`: the picker does not record which mode a parameter was
    picked with). It is still read so a node touched by an older version keeps behaving, and
    the two-mode spellings are migrated: `REALIZED` maps to itself, `POINTS` becomes
    `POINTS_REFERENCE` when that older session had the separate `Reference` switch on.
    """
    if node is None or INSTANCE_MODE_STORE_KEY not in node:
        return ""
    stored = str(node[INSTANCE_MODE_STORE_KEY]).upper()
    if REFERENCE_STORE_KEY in node:
        # A session from the two-mode era wrote the reference switch in its own property as
        # well: fold it into the mode, which is where it lives now.
        if stored == "REALIZED":
            return "REALIZED"
        if stored == "POINTS":
            return "POINTS_REFERENCE" if bool(node[REFERENCE_STORE_KEY]) else "POINTS"
    if stored in INSTANCE_MODES:
        return stored
    return ""


def store_instance_mode(node, mode: str) -> None:
    if node is not None and mode in INSTANCE_MODES:
        node[INSTANCE_MODE_STORE_KEY] = mode


def reference_enabled(node) -> bool:
    """Does the instance pick remembered on `node` keep the source object visible?"""
    return reference_for_mode(stored_instance_mode(node) or DEFAULT_INSTANCE_MODE)


def _mesh_instance_element_values(mesh) -> dict | None:
    """``{"POINT": [...], "EDGE": [...], "FACE": [...]}`` of instance-index values.

    `mesh.attributes[...]` is **empty while the mesh is in edit mode** (measured: the
    RNA attribute data has length 0 there), so the bmesh int layer is the only way to
    read it in the state the picker works in.
    """
    try:
        bm = bmesh.from_edit_mesh(mesh)
    except Exception:  # noqa: BLE001 - not the edit mesh: read the data-block instead
        bm = None
    if bm is not None:
        layer = bm.verts.layers.int.get(PDT_INSTANCE_INDEX_ATTR)
        if layer is not None:
            bm.verts.ensure_lookup_table()
            bm.edges.ensure_lookup_table()
            bm.faces.ensure_lookup_table()
            return {
                "POINT": [int(vert[layer]) for vert in bm.verts],
                "EDGE": [int(edge.verts[0][layer]) for edge in bm.edges if edge.verts],
                "FACE": [int(face.verts[0][layer]) for face in bm.faces if face.verts],
            }
    attribute = mesh.attributes.get(PDT_INSTANCE_INDEX_ATTR)
    if attribute is None or attribute.domain != "POINT":
        return None
    points = _attribute_int_values(attribute)
    return {
        "POINT": points,
        "EDGE": [points[edge.vertices[0]] for edge in mesh.edges if edge.vertices],
        "FACE": [points[face.vertices[0]] for face in mesh.polygons],
    }


def _realized_element_instance_values(data, session) -> list[int] | None:
    """Raw instance-index value (1-based, 0 = sentinel) per element of the pick domain."""
    kind = session.get("pick_kind", "")
    domain = session.get("pick_domain", MESH_POINT_DOMAIN)
    if kind == "MESH":
        values = _mesh_instance_element_values(data)
        if values is None:
            return None
        # The mesh map is keyed by Blender's own domain (`POINT`/`EDGE`/`FACE`), while
        # the session speaks element kinds (`VERTEX`/...).
        return values.get(blender_attr_domain(domain))
    if kind == "CURVE":
        splines = session.get("spline_instances")
        if not splines:
            return None
        domain = blender_attr_domain(domain)
        if domain == "CURVE":
            return list(splines)
        if domain == "POINT":
            expanded = []
            for index, (_first, count) in enumerate(curve_spline_point_ranges(data)):
                value = splines[index] if index < len(splines) else 0
                expanded.extend([value] * count)
            return expanded
        return None
    if kind == "POINTCLOUD":
        attribute = data.attributes.get(PDT_INSTANCE_INDEX_ATTR)
        if attribute is None or attribute.domain != "POINT":
            return None
        return _attribute_int_values(attribute)
    return None


def _realized_instance_selection(context=None) -> list[int]:
    """Instance numbers selected in a realized pick (the sentinel is dropped)."""
    context = context or bpy.context
    obj = bpy.data.objects.get(pick_state.session.get("temp_object", ""))
    if obj is None:
        raise RuntimeError("the temporary object is gone; cancel the session")
    values = _realized_element_instance_values(obj.data, pick_state.session)
    if values is None:
        raise RuntimeError(
            f"the temporary {session_pick_kind().lower()} lost its "
            f"'{PDT_INSTANCE_INDEX_ATTR}' attribute, so the selection cannot be folded "
            "back into instance numbers"
        )
    elements = read_selected_indices(context, pick_state.session.get("pick_domain", "POINT"))
    return sorted(
        {
            values[index] - 1
            for index in elements
            if 0 <= index < len(values) and values[index] > 0
        }
    )


def _realized_preselect_elements(wanted_instances, data, session) -> set[int]:
    """Element indices belonging to `wanted_instances` (the reverse of the fold)."""
    wanted = {int(index) for index in wanted_instances}
    if not wanted:
        return set()
    values = _realized_element_instance_values(data, session)
    if not values:
        return set()
    return {
        index
        for index, value in enumerate(values)
        if value > 0 and (value - 1) in wanted
    }


def current_session_indices(context=None) -> list[int]:
    """The indices this session writes into the parameter.

    For every kind but a realized instance pick that is the selected elements; a
    realized pick folds the selected elements back into **instance numbers**.
    """
    context = context or bpy.context
    if not pick_state.session:
        return []
    if pick_state.session.get("geometry_kind") == "INSTANCES" and pick_state.session.get("instance_mode") == "REALIZED":
        return _realized_instance_selection(context)
    return read_selected_indices(context, pick_state.session.get("domain", "POINT"))
