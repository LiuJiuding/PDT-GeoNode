# SPDX-FileCopyrightText: 2026 PDT Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The pick session: bake the temporary object (`begin_pick`), write the selection back
(`finish_pick`), restore everything (`cancel_pick`), and the guards that refuse a write when
the geometry or the object changed under it (L0 `PICK-5` / `PICK-6`)."""

from __future__ import annotations

import re
import json
import time
import bpy
import bmesh

from .bake import _bake_temp_object
from .utils.constants import DEFAULT_INSTANCE_MODE, DOMAINS, DOMAIN_LABELS, EDIT_MODES, GEOMETRY_KIND_LABELS, INSTANCE_MODES, KIND_DOMAINS, LONG_SELECTION_KEY, LONG_SELECTION_TOKEN, MAX_SOCKET_STRING, MESH_POINT_DOMAIN, PDT_INSTANCE_INDEX_ATTR, PICKABLE_KINDS, PICK_CANCEL_KEYS, PICK_CONFIRM_KEYS, SESSION_KEY, STATUS_OTHER, TEMP_GROUP_PREFIX, TEMP_MESH_PREFIX, TEMP_OBJECT_PREFIX, _kind_list
from .geometry import _domain_kind, _release_temp_groups, _remove_temp_datablock, _unpickable_reason, available_geometry_kinds, build_evaluation_tree, geometry_kind_label, resolve_geometry_kind, resolve_geometry_source
from .hit_test import can_pick
from .instances import _realized_preselect_elements, current_session_indices, reference_for_mode, stored_instance_mode
from .keymap import repair_keymaps
from .parser import encode_indices, preselect_indices
from .selection import _domain_select_mode, _set_geometry_selection, _spline_points, geometry_element_count, session_pick_kind
from .targets import choose_string_target, find_string_target, read_target_value, write_target_value
from .overlay import _overlay_text_reset, _pin_node_editor, _unpin_node_editors
from . import state as pick_state

def _copy_state_to_scene(context=None):
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    if scene is not None:
        scene[SESSION_KEY] = json.dumps(pick_state.session)


def _clear_scene_state(context=None):
    context = context or bpy.context
    scene = getattr(context, "scene", None)
    if scene is not None and SESSION_KEY in scene:
        del scene[SESSION_KEY]


def begin_pick(
    tree,
    node,
    context=None,
    explicit_chain=None,
    geometry_kind=None,
    instance_mode=None,
    domain=None,
    target_identifier=None,
) -> dict:
    """Bake the geometry at `node` into a temporary object and enter edit mode.

    `domain` is the **element kind** the user chose in the first-level pie menu (L0 PICK-1):
    it decides which component is baked, which edit mode is entered and which numbers are
    written, and a kind this geometry does not carry is refused with what it does carry
    (PICK-3). `instance_mode` is the second-level choice and only means something for
    `Domain = Instance` (PICK-3's three modes). `target_identifier` is the string parameter
    the cursor was on, so the pointer - not a guess - decides where the indices land.

    Without a `domain` the older automatic choice applies (the per-node choice an older
    version pinned, else the node's own `Domain`, else `Vertex`), and without a
    `target_identifier` the parameter is inferred (`choose_string_target`). Both are used by
    the tests and by any caller that has no pointer position; `geometry_kind` selects a
    component directly and outranks the kind derived from the domain.
    """
    context = context or bpy.context

    if pick_state.session:
        raise RuntimeError(
            f"a selection session is already active for '{pick_state.session.get('node')}'; "
            "apply or cancel it first"
        )
    ok, reason = can_pick(
        tree, node, context, explicit_chain=explicit_chain, target_identifier=target_identifier
    )
    if not ok:
        raise RuntimeError(reason)
    if context.mode != "OBJECT":
        raise RuntimeError("finish the current edit mode first")

    source = resolve_geometry_source(node)
    if target_identifier:
        target = find_string_target(node, target_identifier)
        if target is None:
            raise RuntimeError(
                f"'{node.name}' has no string parameter '{target_identifier}'"
            )
    else:
        target, target_reason = choose_string_target(node)
        if target is None:
            raise RuntimeError(target_reason)

    # The element kind this pick addresses: the pie menu's choice, else the per-node choice an
    # older version pinned, else the node's own Domain, else Vertex. It steers which component
    # is baked and never rewrites the node.
    explicit_domain = domain if domain in DOMAINS else ""
    domain = explicit_domain or ""
    domain_source = "pie" if explicit_domain else "auto"

    available = available_geometry_kinds(tree, node, context, explicit_chain=explicit_chain)
    if not available:
        # The scan swallows failures, so ask again without the guard: an instances upstream
        # has a real, actionable reason ("add Realize Instances").
        available = available_geometry_kinds(
            tree, node, context, explicit_chain=explicit_chain, strict=True
        )
    if explicit_domain:
        # The chosen element kind names the component outright (L0 PDS-9 / `DOMAIN_KINDS`):
        # `Vertex` is a mesh element, `Control Point` a curve one, ...
        kind, kind_source = _domain_kind(explicit_domain), "domain"
        if geometry_kind and geometry_kind != kind:
            raise RuntimeError(
                f"cannot pick {DOMAIN_LABELS.get(explicit_domain, explicit_domain)} elements "
                f"while asking for the {GEOMETRY_KIND_LABELS.get(geometry_kind, geometry_kind)} "
                "component: the element kind already names which component gets baked"
            )
    else:
        kind, kind_source = resolve_geometry_kind(node, available, geometry_kind, None)
        if kind is None and not available:
            strict = available_geometry_kinds(
                tree, node, context, explicit_chain=explicit_chain, strict=True
            )
            if strict:
                kind, kind_source = resolve_geometry_kind(node, strict, geometry_kind, None)
        if kind is not None:
            # No element kind was asked for, so the session speaks the one the baked component
            # has. (The node's own `Domain` is *not* read: L0 明确不做.)
            domain = KIND_DOMAINS.get(kind, (MESH_POINT_DOMAIN,))[0]
    if kind is None:
        raise RuntimeError(kind_source)
    if kind not in PICKABLE_KINDS:
        raise RuntimeError(
            f"the {GEOMETRY_KIND_LABELS.get(kind, kind)} component cannot be picked yet: "
            + _unpickable_reason(kind)
        )
    kinds = [item for item, _elements in available]
    if kind not in kinds:
        # PICK-3: "识别节点输入几何里没有这个组件就拒绝执行并给出提示".
        raise RuntimeError(
            f"cannot pick {DOMAIN_LABELS.get(domain, domain)} elements: this geometry has no "
            f"{GEOMETRY_KIND_LABELS.get(kind, kind)} component (it has: {_kind_list(kinds)})"
        )

    instance_mode = (
        (instance_mode or stored_instance_mode(node) or DEFAULT_INSTANCE_MODE)
        if kind == "INSTANCES"
        else "POINTS"
    )
    if instance_mode not in INSTANCE_MODES:
        instance_mode = DEFAULT_INSTANCE_MODE
    realized = kind == "INSTANCES" and instance_mode == "REALIZED"

    if _domain_kind(domain) != kind:
        # The chosen element kind has to be the component that gets baked: aiming at one kind
        # while baking another would write numbers that cannot mean what the user asked for.
        expected = _domain_kind(domain)
        raise RuntimeError(
            f"cannot pick {DOMAIN_LABELS.get(domain, domain)} elements: this geometry has no "
            f"{GEOMETRY_KIND_LABELS.get(expected, expected)} component (it has: "
            f"{_kind_list(kinds)})"
        )
    if kind == "INSTANCES":
        # The element kind the user clicks in a realized pick is resolved into `pick_domain`
        # below; `domain` stays the session's meaning (the numbers written are instances).
        domain_source = "instance"

    # Works for a node inside a sub-group too: every breadcrumb level is copied
    # and re-linked so the picked geometry can be evaluated at the top level.
    # Everything created here is released again when the bake fails: a curve or
    # point cloud used to leave a visible `PDT_PICK_obj_*` and a
    # `PDT_PICK_group_*` copy behind (both are user-visible, so they must not
    # survive a failed pick).
    temp_groups: list[str] = []
    try:
        temp_tree, temp_groups, src_object, modifier = build_evaluation_tree(
            tree, node, context, explicit_chain=explicit_chain
        )
        if src_object is None:
            raise RuntimeError("No object uses this node tree in a Geometry Nodes modifier")
        temp_object, baked_data, realized_info = _bake_temp_object(
            src_object,
            modifier,
            temp_tree,
            geometry_kind=kind,
            instance_mode=instance_mode,
            splice_tree_name=temp_groups[0] if temp_groups else "",
        )
    except Exception:
        _release_temp_groups(temp_groups)
        raise

    # Which *element* kind a realized instance pick edits. The node's Domain has to stay
    # `Instance` (that is what the written numbers mean), and the elements the user clicks are
    # the ones the realized geometry has: mesh points, or curve control points. Every other pick
    # uses the session's domain.
    pick_kind = kind
    pick_domain = domain
    if realized:
        pick_kind = realized_info["sub_kind"]
        pick_domain = "CONTROL_POINT" if pick_kind == "CURVE" else MESH_POINT_DOMAIN

    # The baked data can inherit the object's own selection state; the parameter is
    # the single source of truth, so rebuild the selection from it (which also lets
    # the user continue from an existing value).
    # A baked mesh also carries the group attributes, so a parameter like
    # `0-10 rim` pre-selects its groups as well (same-domain groups only).
    realized = kind == "INSTANCES" and instance_mode == "REALIZED"
    if realized:
        # The parameter holds *instance* numbers here, so parse them against the
        # instance count. They are turned into mesh elements only after entering edit
        # mode (below): the instance-index attribute is unreadable before that, which is
        # why `preselected` stays empty here and `wanted_instances` is what matters.
        wanted_instances = preselect_indices(
            read_target_value(node, target),
            realized_info["instances"],
            None,
            "INSTANCE",
        )
        preselected = set()
    else:
        preselected = preselect_indices(
            read_target_value(node, target),
            geometry_element_count(baked_data, kind, domain),
            baked_data if kind == "MESH" else None,
            domain,
        )

    # Instance picks can leave the source object visible: a point pick only has points at the
    # instance origins, so the instanced geometry is what tells the user which point is which
    # (PICK-3's `Point Cloud with Reference` / `Realized Instance`; `Point Cloud` hides it).
    # Every other kind bakes the geometry itself and hides the source - the temporary object
    # is all there is.
    keep_source_visible = kind == "INSTANCES" and reference_for_mode(instance_mode)
    was_hidden = src_object.hide_get()
    if not keep_source_visible:
        src_object.hide_set(True)

    pick_state.session.clear()
    pick_state.session.update({
        "tree": tree.name,
        "node": node.name,
        "source": source,
        "target": target,
        "domain": domain,
        "domain_source": domain_source,
        "geometry_kind": kind,
        "geometry_kind_source": kind_source,
        "geometry_kinds": [{"kind": k, "elements": n} for k, n in available],
        "geometry_label": geometry_kind_label(
            kind, dict(available).get(kind)
        ),
        "instance_mode": instance_mode,
        "pick_kind": pick_kind,
        "pick_domain": pick_domain,
        "instance_index_attr": PDT_INSTANCE_INDEX_ATTR,
        "instance_total": realized_info["instances"] if realized else 0,
        "spline_instances": realized_info["spline_instances"] if realized else None,
        "temp_object": temp_object.name,
        "temp_mesh": baked_data.name,
        "temp_mesh_kind": pick_kind,
        "temp_group": temp_tree.name,
        "src_object": src_object.name,
        "src_was_hidden": bool(was_hidden),
        "src_reference": bool(keep_source_visible),
        "preselected": len(preselected),
        "signature": _geometry_signature(baked_data, pick_kind),
        "temp_groups": temp_groups,
        "chain_depth": len(temp_groups),
        "prev_active": context.view_layer.objects.active.name
        if context.view_layer.objects.active
        else "",
    })
    _copy_state_to_scene(context)

    # Keep the node editor from following the temporary object below: that sync
    # would collapse the breadcrumb (see _pin_node_editor).
    _pin_node_editor(context)

    for obj in context.view_layer.objects:
        obj.select_set(False)
    temp_object.select_set(True)
    context.view_layer.objects.active = temp_object
    if pick_kind == "MESH":
        context.tool_settings.mesh_select_mode = _domain_select_mode(pick_domain)
    bpy.ops.object.mode_set(mode="EDIT")
    if realized:
        # Only now can the reverse fold read the attribute: in edit mode the mesh
        # attribute data is empty and the bmesh layer is the only source, and before
        # edit mode the element selection cannot be written at all.
        preselected = _realized_preselect_elements(
            wanted_instances, baked_data, pick_state.session
        )
        pick_state.session["preselected"] = len(preselected)
    # Apply the existing parameter value as the starting selection. Done after
    # entering edit mode: the edit data is the authoritative selection state.
    _set_geometry_selection(baked_data, pick_kind, pick_domain, sorted(preselected))
    # Fingerprint the *edit* data: the object-mode data is only written back when
    # the mode is left, so comparing the two representations could mismatch.
    pick_state.session["signature"] = _geometry_signature(baked_data, pick_kind, use_edit_data=True)
    _copy_state_to_scene(context)
    return dict(pick_state.session)


def finish_pick(explicit_indices=None, context=None):
    """Encode the current selection into the node parameter and clean up.

    Returns ``(count, written_text, note)``; `note` is non-empty when the
    selection had to be stored on the node instead of the socket.
    """
    context = context or bpy.context
    if not pick_state.session:
        raise RuntimeError("no active selection session")

    domain = pick_state.session.get("domain", "POINT")
    if explicit_indices is None:
        # A realized instance pick folds the selected elements back into instance
        # numbers here (docs/archive/08-实例支持评估.md §5.2).
        indices = current_session_indices(context)
    else:
        indices = list(explicit_indices)
    text = encode_indices(indices)
    long_selection = False
    if len(text) > MAX_SOCKET_STRING:
        # A string socket is a char[1024] buffer in DNA. Keep the full spec in an
        # ID property on the node (no size limit, no DNA change) and put the
        # token in the parameter; the node resolves it.
        long_selection = True

    tree = bpy.data.node_groups.get(pick_state.session["tree"])
    node = tree.nodes.get(pick_state.session["node"]) if tree else None
    if node is None:
        cancel_pick(context)
        raise RuntimeError("the node disappeared while picking")

    target = pick_state.session["target"]
    if long_selection and target["kind"] != "socket":
        # RNA string properties have no 1024 byte limit, so they take the text.
        long_selection = False

    if long_selection:
        node[LONG_SELECTION_KEY] = text
        write_target_value(node, target, LONG_SELECTION_TOKEN)
    else:
        write_target_value(node, target, text)
        if LONG_SELECTION_KEY in node:
            del node[LONG_SELECTION_KEY]
    # Nothing about the choice is stored on the node: the picker must not remember which
    # parameter used which pick mode (L0 `selection_pick.md`, 明确不做), and the next pick
    # decides again from the pointer (PICK-1).

    count = len(indices)
    note = ""
    if long_selection:
        note = (
            f"{len(text)} characters exceed the {MAX_SOCKET_STRING} character socket limit, "
            f"so the parameter holds '{LONG_SELECTION_TOKEN}' and the selection is stored "
            f"in the node property '{LONG_SELECTION_KEY}'"
        )
    cancel_pick(context, last_text=text)
    return count, text, note


def cancel_pick(context=None, last_text: str | None = None) -> None:
    """Remove the temporary data and restore the previous state.

    `last_text` keeps a small record in the scene so the panel can show what the
    last successful pick produced (survives until the file is reloaded).
    """
    context = context or bpy.context
    session = dict(pick_state.session)
    pick_state.session.clear()
    pick_state.modal_active = False

    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    temp_object = bpy.data.objects.get(session.get("temp_object", ""))
    if temp_object is not None:
        bpy.data.objects.remove(temp_object, do_unlink=True)
    # The pick data-block is a mesh today (`temp_mesh_kind`), but a curve/point
    # cloud picker would swap the kind here - never guess the collection.
    _remove_temp_datablock(session.get("temp_mesh_kind", "MESH"), session.get("temp_mesh", ""))
    # One copy per breadcrumb level when the node lives in a sub-group. Remove the
    # outermost first: each level references the inner copy, so the inner ones only
    # reach zero users once the outer one is gone.
    for group_name in reversed(session.get("temp_groups") or [session.get("temp_group", "")]):
        temp_group = bpy.data.node_groups.get(group_name)
        if temp_group is not None and temp_group.users == 0:
            bpy.data.node_groups.remove(temp_group)

    src_object = bpy.data.objects.get(session.get("src_object", ""))
    if src_object is not None:
        src_object.hide_set(bool(session.get("src_was_hidden", False)))
        src_object.select_set(True)
        context.view_layer.objects.active = src_object

    if last_text is None:
        _clear_scene_state(context)
    else:
        scene = getattr(context, "scene", None)
        if scene is not None:
            scene[SESSION_KEY] = json.dumps(
                {"node": session.get("node", ""), "last_text": last_text}
            )

    # Give the node editor back its pin and its breadcrumb. Done last, so the
    # active object and the temporary data are already back to normal and the
    # restored path is not collapsed again by the cleanup. Every end path runs
    # through here (the modal, the panel buttons, scripts, tests).
    _unpin_node_editors()


def pick_event_action(event_type: str, event_value: str) -> str:
    """Map a modal event to an action: 'apply', 'cancel' or 'pass'.

    Keyboard events arrive as PRESS/RELEASE; CLICK is accepted as well so an
    unusual keyboard/IME that reports it does not silently swallow Enter.
    """
    if event_value not in {"PRESS", "CLICK"}:
        return "pass"
    if event_type in PICK_CONFIRM_KEYS:
        return "apply"
    if event_type in PICK_CANCEL_KEYS:
        return "cancel"
    return "pass"


def _mesh_signature(mesh, use_edit_mesh: bool = False) -> dict:
    """Cheap fingerprint used to notice that the pick mesh was edited.

    A pure selection change must not trip it, so only element counts and a few
    vertex positions are recorded. In edit mode the authoritative geometry lives
    in the edit mesh - the mesh datablock is only written back when the mode is
    left, so reading it there would hide an edit.
    """
    if use_edit_mesh:
        bm = bmesh.from_edit_mesh(mesh)
        bm.verts.ensure_lookup_table()
        count = len(bm.verts)
        if count == 0:
            return {
                "kind": "MESH",
                "count": 0,
                "edges": len(bm.edges),
                "faces": len(bm.faces),
                "samples": [],
            }
        indices = sorted({0, 1, 2, count // 2, max(0, count - 2), count - 1})
        return {
            "kind": "MESH",
            "count": count,
            "edges": len(bm.edges),
            "faces": len(bm.faces),
            "samples": [[index, *bm.verts[index].co] for index in indices],
        }

    count = len(mesh.vertices)
    if count == 0:
        return {
            "kind": "MESH",
            "count": 0,
            "edges": len(mesh.edges),
            "faces": len(mesh.polygons),
            "samples": [],
        }
    indices = sorted({0, 1, 2, count // 2, max(0, count - 2), count - 1})
    return {
        "kind": "MESH",
        "count": count,
        "edges": len(mesh.edges),
        "faces": len(mesh.polygons),
        "samples": [[index, *mesh.vertices[index].co] for index in indices],
    }


def _curve_signature(curve) -> dict:
    """Curve equivalent of `_mesh_signature` (spline layout + sampled control points).

    In `EDIT_CURVE` the edit data is what `obj.data.splines` exposes, so the same
    read works in and out of edit mode (verified).
    """
    splines = []
    samples = []
    total = 0
    for spline in curve.splines:
        points = _spline_points(spline)
        splines.append([spline.type, len(points)])
        total += len(points)
    if total:
        wanted = {0, 1, 2, total // 2, max(0, total - 2), total - 1}
        flat = 0
        for spline in curve.splines:
            for point in _spline_points(spline):
                if flat in wanted:
                    samples.append([flat, *point.co[:3]])
                flat += 1
    return {"kind": "CURVE", "count": total, "splines": splines, "samples": samples}


def _pointcloud_signature(pointcloud) -> dict:
    """Point cloud fingerprint: point count + sampled positions.

    Point clouds have no edit-mode copy - the same data-block and attributes are used
    in and out of edit mode (measured), so this read is valid in both.
    """
    positions = pointcloud.attributes.get("position")
    count = len(positions.data) if positions is not None else 0
    samples = []
    if count:
        wanted = sorted({0, 1, 2, count // 2, max(0, count - 2), count - 1})
        samples = [[index, *positions.data[index].vector] for index in wanted]
    return {"kind": "POINTCLOUD", "count": count, "samples": samples}


def _instance_signature(pointcloud) -> dict:
    """Instance fingerprint: count + sampled origins + sampled reference indices.

    The temporary data-block is the baked instance cloud (one point per instance), so
    the sampled positions are the instance origins. `.reference_index` is included
    because it identifies *what* is instanced: swapping the instanced object changes
    neither the count nor the origins, but it does change what the indices mean.
    """
    positions = pointcloud.attributes.get("position")
    count = len(positions.data) if positions is not None else 0
    samples = []
    if count:
        wanted = sorted({0, 1, 2, count // 2, max(0, count - 2), count - 1})
        samples = [[index, *positions.data[index].vector] for index in wanted]
    references = []
    attribute = pointcloud.attributes.get(".reference_index")
    if attribute is not None and count:
        wanted = sorted({0, count // 2, count - 1})
        references = [int(attribute.data[index].value) for index in wanted if index < len(attribute.data)]
    return {"kind": "INSTANCES", "count": count, "samples": samples, "references": references}


def _geometry_signature(data, kind: str, use_edit_data: bool = False) -> dict:
    """Fingerprint of the temporary data-block, for any kind."""
    if kind == "CURVE":
        return _curve_signature(data)
    if kind == "INSTANCES":
        return _instance_signature(data)
    if kind == "POINTCLOUD":
        return _pointcloud_signature(data)
    return _mesh_signature(data, use_edit_mesh=use_edit_data)


def _signature_matches(
    signature: dict, data, kind: str = "MESH", use_edit_data: bool = False
) -> bool:
    current = _geometry_signature(data, kind, use_edit_data)
    if signature.get("kind", "MESH") != current["kind"]:
        return False
    if current["count"] != signature.get("count"):
        return False
    if current["kind"] == "CURVE":
        if current["splines"] != signature.get("splines"):
            return False
    elif current["kind"] == "INSTANCES":
        if current.get("references") != signature.get("references"):
            return False
    elif current["kind"] == "MESH":
        for key in ("edges", "faces"):
            if current.get(key) != signature.get(key):
                return False
    for old, new in zip(signature.get("samples", []), current["samples"]):
        if old[0] != new[0]:
            return False
        if any(abs(a - b) > 1e-6 for a, b in zip(old[1:], new[1:])):
            return False
    return True


def pick_session_state(context=None) -> str:
    """'none' | 'ok' | 'paused' | 'other_object' | 'missing'.

    The picker has to survive the user wandering off: leaving edit mode, editing
    a different object, undoing the setup, or deleting the temporary object.
    """
    context = context or bpy.context
    if not pick_state.session:
        return "none"
    tree = bpy.data.node_groups.get(pick_state.session.get("tree", ""))
    node = tree.nodes.get(pick_state.session.get("node", "")) if tree else None
    temp_object = bpy.data.objects.get(pick_state.session.get("temp_object", ""))
    if node is None or temp_object is None:
        return "missing"
    if context.mode not in EDIT_MODES:
        return "paused"
    if context.view_layer.objects.active is not temp_object:
        return "other_object"
    return "ok"


def try_finish_pick(context=None):
    """Apply the current selection, guarding against a session gone wrong.

    Returns ``(ok, message)``. On refusal the session stays alive so the user can
    undo whatever they did (Ctrl+Z) instead of losing their work.
    """
    context = context or bpy.context
    state = pick_session_state(context)
    if state == "none":
        return False, "no active pick session"
    if state == "missing":
        cancel_pick(context)
        return False, "the pick session data disappeared; cleaned up"
    if state == "other_object":
        return False, STATUS_OTHER + "  (or click 'Apply Selection' in the PDT panel to force it)"

    temp_object = bpy.data.objects[pick_state.session["temp_object"]]
    kind = session_pick_kind()
    in_edit_mode = (
        context.mode in EDIT_MODES
        and context.view_layer.objects.active is temp_object
    )
    if not _signature_matches(
        pick_state.session.get("signature", {}), temp_object.data, kind, in_edit_mode
    ):
        print(
            "PDT pick: refusing to apply, topology changed: "
            f"before={pick_state.session.get('signature', {})} "
            f"now={_geometry_signature(temp_object.data, kind, in_edit_mode)}"
        )
        return False, (
            "the pick geometry was edited (its topology changed), so indices no longer match; "
            "undo with Ctrl+Z, press Esc, or click 'Apply Selection' in the PDT panel to force it"
        )

    try:
        count, text, note = finish_pick(context=context)
    except RuntimeError as ex:
        return False, str(ex)
    if note:
        return True, note
    return True, f"{count} element(s) written: {text[:60]}"


def cleanup_leftovers() -> int:
    """Remove temporary data-blocks left behind by a crash or a forced quit."""
    removed = 0
    for obj in list(bpy.data.objects):
        if obj.name.startswith(TEMP_OBJECT_PREFIX):
            bpy.data.objects.remove(obj, do_unlink=True)
            removed += 1
    for collection_name in ("meshes", "curves", "pointclouds"):
        collection = getattr(bpy.data, collection_name, None)
        if collection is None:
            continue
        for datablock in list(collection):
            if datablock.name.startswith(TEMP_MESH_PREFIX) and datablock.users == 0:
                collection.remove(datablock)
                removed += 1
    # Chained copies reference each other, so repeat until nothing is freed
    # anymore instead of relying on the order data-blocks happen to be listed in.
    for _pass in range(8):
        freed = 0
        for group in list(bpy.data.node_groups):
            if group.name.startswith(TEMP_GROUP_PREFIX) and group.users == 0:
                bpy.data.node_groups.remove(group)
                freed += 1
        removed += freed
        if freed == 0:
            break
    # Leftovers can survive a crash, in which case no session UI ever popped the
    # pin we set while picking.
    _unpin_node_editors()
    return removed


def _on_load_post(*_args) -> None:
    """A newly loaded file invalidates every data-block the session referred to.

    This is also the first moment the *user keymaps* exist, so the keymap repair
    runs here (the shortcut itself lives in the addon keyconfig and never needs
    repairing).
    """
    pick_state.session.clear()
    _overlay_text_reset()
    repair_keymaps()


def register() -> None:
    """Watch for a newly loaded file: it invalidates every data-block the session referred to.

    This is also the first moment the *user keymaps* exist, so the keymap repair runs here
    (`keymap.py` explains why the shortcut itself never needs repairing).
    """
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister() -> None:
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
