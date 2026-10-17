# SPDX-FileCopyrightText: 2026 PDT Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Reading and writing the temporary object's element selection: one accessor pair per
component (mesh points / edges / faces, curve control points / splines, point cloud
points), and the flat element counts a pre-selection is clamped to."""

from __future__ import annotations

import re
import bpy
import bmesh

from .utils.constants import BMESH_COLLECTIONS, DOMAIN_ELEMENTS, EDIT_MODES, MESH_POINT_DOMAIN, POINTCLOUD_SELECTION_ATTR, blender_attr_domain
from . import state as pick_state

def _domain_collection_name(domain: str) -> str:
    return DOMAIN_ELEMENTS.get(domain, DOMAIN_ELEMENTS[MESH_POINT_DOMAIN])[0]


def _domain_select_mode(domain: str):
    return DOMAIN_ELEMENTS.get(domain, DOMAIN_ELEMENTS[MESH_POINT_DOMAIN])[1]


def _domain_element_count(mesh, domain: str) -> int:
    return len(getattr(mesh, _domain_collection_name(domain)))


def _spline_points(spline):
    """The control points of one spline (Bézier points are not `spline.points`)."""
    return spline.bezier_points if spline.type == "BEZIER" else spline.points


def curve_point_count(curve) -> int:
    """Flat control point count - the number the node's Point domain addresses."""
    return sum(len(_spline_points(spline)) for spline in curve.splines)


def curve_spline_count(curve) -> int:
    """Number of splines - the number the node's Spline (`AttrDomain::Curve`) domain
    addresses."""
    return len(curve.splines)


def curve_spline_point_ranges(curve) -> list[tuple[int, int]]:
    """``[(first_control_point, point_count), ...]`` per spline, in spline order.

    Splines are stored one after another in the point domain, so a flat control point
    index maps to a spline through these ranges.
    """
    ranges = []
    offset = 0
    for spline in curve.splines:
        count = len(_spline_points(spline))
        ranges.append((offset, count))
        offset += count
    return ranges


def _set_curve_spline_selection(curve, indices) -> int:
    """Select **every control point** of the given splines.

    Curve edit mode has no "select splines" mode, so the picker works the other way
    round: the user selects points and the parameter stores the splines those points
    belong to (see `_curve_selected_splines`). Pre-selecting a spline therefore means
    selecting all of its control points, which round-trips exactly.
    """
    wanted = {int(index) for index in indices}
    points: list[int] = []
    for index, (first, count) in enumerate(curve_spline_point_ranges(curve)):
        if index in wanted:
            points.extend(range(first, first + count))
    return _set_edit_curve_selection(curve, "POINT", points)


def _curve_selected_splines(curve) -> list[int]:
    """Splines that contain **at least one** selected control point.

    That is the only reading curve edit mode allows (there is no spline selection
    state), and it is the inverse of `_set_curve_spline_selection`.
    """
    selected = set(_edit_curve_selection(curve, "POINT"))
    if not selected:
        return []
    found = []
    for index, (first, count) in enumerate(curve_spline_point_ranges(curve)):
        if selected & set(range(first, first + count)):
            found.append(index)
    return found


def geometry_element_count(data, kind: str, domain: str = MESH_POINT_DOMAIN) -> int:
    """How many elements `domain` (an element kind) has in the temporary data-block."""
    if kind == "CURVE":
        if blender_attr_domain(domain) == "CURVE":
            return curve_spline_count(data)
        return curve_point_count(data)
    if kind == "POINTCLOUD":
        return pointcloud_point_count(data)
    if kind == "INSTANCES":
        # An instance pick bakes one point per instance: internally the data-block's
        # only domain is POINT, while the session speaks INSTANCE
        # (docs/archive/08-实例支持评估.md §4).
        return pointcloud_point_count(data)
    return _domain_element_count(data, domain)


def _set_edit_curve_selection(curve, domain: str, indices) -> int:
    """Replace the curve's selection flags; flat control-point index across splines.

    Curves have no bmesh, but the RNA selection flags are the same ones Blender's
    own edit mode writes, and they are readable and writable from Python in
    `EDIT_CURVE` (verified headless). Bézier handles are deliberately cleared: they
    are not elements of the node's point domain, so only control points are
    addressed.
    """
    if domain not in {"POINT", "CONTROL_POINT"}:
        return 0
    wanted = {int(index) for index in indices}
    applied = 0
    flat = 0
    for spline in curve.splines:
        for point in _spline_points(spline):
            selected = flat in wanted
            if spline.type == "BEZIER":
                point.select_control_point = selected
                point.select_left_handle = False
                point.select_right_handle = False
            else:
                point.select = selected
            if selected:
                applied += 1
            flat += 1
    return applied


def _edit_curve_selection(curve, domain: str) -> list[int]:
    if domain not in {"POINT", "CONTROL_POINT", MESH_POINT_DOMAIN, "VERTEX"}:
        return []
    found = []
    flat = 0
    for spline in curve.splines:
        for point in _spline_points(spline):
            selected = (
                point.select_control_point if spline.type == "BEZIER" else point.select
            )
            if selected:
                found.append(flat)
            flat += 1
    return found


def pointcloud_point_count(pointcloud) -> int:
    """Number of points - point clouds are a single POINT domain, no sub-modes."""
    positions = pointcloud.attributes.get("position")
    return len(positions.data) if positions is not None else 0


def _pointcloud_selection_attribute(pointcloud):
    """The `.selection` attribute, or None when Blender's "all selected" state applies."""
    attribute = pointcloud.attributes.get(POINTCLOUD_SELECTION_ATTR)
    if attribute is None or attribute.domain != "POINT":
        return None
    return attribute


def _pointcloud_selected_indices(pointcloud) -> list[int]:
    """Selected points.

    A **missing** `.selection` attribute means "everything is selected" - that is how
    Blender's own edit mode represents it (`select_all(SELECT)` removes the attribute
    instead of filling it), so a freshly baked point cloud reads as fully selected and
    the picker has to overwrite it with the parameter's value (`_set_geometry_selection`
    does exactly that right after entering edit mode).
    """
    attribute = _pointcloud_selection_attribute(pointcloud)
    if attribute is None:
        return list(range(pointcloud_point_count(pointcloud)))
    if attribute.data_type == "BOOLEAN":
        return [i for i in range(len(attribute.data)) if attribute.data[i].value]
    if attribute.data_type == "FLOAT":
        return [i for i in range(len(attribute.data)) if float(attribute.data[i].value) > 0.5]
    return []


def _set_pointcloud_selection(pointcloud, domain: str, indices) -> int:
    """Write the `.selection` attribute explicitly (BOOLEAN on the point domain)."""
    if domain not in {"POINT", "CONTROL_POINT"}:
        return 0
    wanted = {int(index) for index in indices}
    attribute = _pointcloud_selection_attribute(pointcloud)
    if attribute is None or attribute.data_type != "BOOLEAN":
        if attribute is not None:
            # A FLOAT selection (some tools create one) cannot be written as booleans;
            # replace it, the values it held are already folded into `wanted`.
            pointcloud.attributes.remove(attribute)
        attribute = pointcloud.attributes.new(POINTCLOUD_SELECTION_ATTR, "BOOLEAN", "POINT")
    applied = 0
    for index in range(len(attribute.data)):
        selected = index in wanted
        attribute.data[index].value = selected
        if selected:
            applied += 1
    return applied


def _set_geometry_selection(data, kind: str, domain: str, indices) -> int:
    """Apply `indices` to the edit data-block of the session's kind and domain.

    `domain` is an element kind; Blender's own helpers are handed the matching
    `AttrDomain` (`VERTEX` -> `POINT`).
    """
    if kind == "CURVE":
        if blender_attr_domain(domain) == "CURVE":
            return _set_curve_spline_selection(data, indices)
        return _set_edit_curve_selection(data, domain, indices)
    if kind == "POINTCLOUD":
        return _set_pointcloud_selection(data, "POINT", indices)
    if kind == "INSTANCES":
        # The bake is a point cloud: `.selection` lives on its POINT domain, while the
        # session (and the parameter being written) speak INSTANCE. Passing the session
        # domain through would write nothing at all - and a point cloud without
        # `.selection` reads as *everything selected* (measured), so the pick would
        # silently start with every instance selected.
        return _set_pointcloud_selection(data, "POINT", indices)
    return _set_edit_mesh_selection(data, blender_attr_domain(domain), indices)


def _geometry_selection(data, kind: str, domain: str, in_edit_mode: bool) -> list[int]:
    """Read the current selection of the pick data-block for `kind`/`domain`.

    In edit mode the edit data is authoritative (a mesh is only written back when
    the mode is left); after leaving it, the object-mode flags are the state. Curves
    and point clouds keep one representation in both modes. On a curve's Spline
    domain the selected control points are folded back into spline indices.
    """
    if kind == "CURVE":
        if blender_attr_domain(domain) == "CURVE":
            return _curve_selected_splines(data)
        return _edit_curve_selection(data, domain)
    if kind in {"POINTCLOUD", "INSTANCES"}:
        # Instances are picked as points at the instance origins, so the selection
        # state is the point cloud's own `.selection` attribute (missing = all).
        return _pointcloud_selected_indices(data)
    attr_domain = blender_attr_domain(domain)
    if in_edit_mode:
        return _edit_mesh_selection(data, attr_domain)
    collection = getattr(data, _domain_collection_name(domain))
    return [element.index for element in collection if element.select]


def _set_edit_mesh_selection(mesh, domain: str, indices) -> int:
    """Replace the edit mesh's selection with `indices` on `domain`.

    This works on the *edit* mesh on purpose: a freshly baked mesh can have no
    `.select_vert/.select_edge/.select_poly` layers at all, and Blender treats a
    missing selection layer as "everything selected" when the mesh is converted
    into the edit mesh. Writing object-mode RNA flags then has no effect, and
    `bm.to_mesh()` does not create those layers either (measured). The edit mesh
    is also the state the user's clicks change, so reads and writes happen there.
    """
    bm = bmesh.from_edit_mesh(mesh)
    for vert in bm.verts:
        vert.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for face in bm.faces:
        face.select_set(False)

    collection = getattr(bm, BMESH_COLLECTIONS.get(domain, "verts"))
    collection.ensure_lookup_table()
    applied = 0
    for index in indices:
        if 0 <= index < len(collection):
            collection[index].select_set(True)
            applied += 1

    # Mirror edit mode's bookkeeping: sub-elements of a selected element are
    # selected as well. Only the domain's own flags are ever read back.
    if domain == "EDGE":
        for edge in bm.edges:
            if edge.select:
                for vert in edge.verts:
                    vert.select_set(True)
    elif domain == "FACE":
        for face in bm.faces:
            if face.select:
                for vert in face.verts:
                    vert.select_set(True)
                for edge in face.edges:
                    edge.select_set(True)

    bmesh.update_edit_mesh(mesh)
    return applied


def _edit_mesh_selection(mesh, domain: str) -> list[int]:
    bm = bmesh.from_edit_mesh(mesh)
    collection = getattr(bm, BMESH_COLLECTIONS.get(domain, "verts"))
    collection.ensure_lookup_table()
    return [element.index for element in collection if element.select]


def read_selected_indices(context=None, domain: str = MESH_POINT_DOMAIN) -> list[int]:
    """Selected element indices of the temporary object for `domain`."""
    context = context or bpy.context
    obj = bpy.data.objects.get(pick_state.session.get("temp_object", ""))
    if obj is None:
        raise RuntimeError("the temporary object is gone; cancel the session")
    kind = session_pick_kind()
    in_edit_mode = (
        context.mode in EDIT_MODES
        and context.view_layer.objects.active is obj
    )
    return _geometry_selection(obj.data, kind, domain, in_edit_mode)


def session_pick_kind() -> str:
    """The kind of the temporary data-block (`INSTANCES` bakes a point cloud)."""
    return pick_state.session.get("pick_kind") or pick_state.session.get("geometry_kind", "MESH")
