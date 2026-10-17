# SPDX-FileCopyrightText: 2025 Blender Foundation
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The evaluated geometry, reduced to what the block draws (MMB-5).

Top half: the element counts of **every** domain sub-class, in a fixed order with `0` where
the geometry does not carry it, so two nodes can be compared line by line. Bottom half: the
attribute table, one group per domain that has attributes, **in that same element order**
(`_DOMAIN_ORDER` - Blender's own attribute order is a storage order and moves around), with
`name(Type)` items and the attributes this node added highlighted. No world bounds, no
coordinate hash, no derived statistics - the L0 rewrite removed all three.

Everything here returns plain data (`build_panel_state()`), which is what makes the panel
testable headlessly: the drawing code never has to be involved.
"""

from __future__ import annotations

import time

from .evaluate import evaluate_descriptor
from .geometry import (
    _geometry_sockets,
    geometry_source,
    upstream_geometry_source,
)
from .preferences import prefs
from .utils.constants import MAX_ITEMS_PER_GROUP
from .utils.palette import domain_slot


# ---------------------------------------------------------------------------
# snapshots
# ---------------------------------------------------------------------------
# What the block draws, reduced to plain data (MMB-5): per component the element counts of
# its domains, and the attribute table. **No world bounds, no coordinate hash, no UV
# section** - those were removed by the L0 rewrite, which is also why this module no longer
# hashes anything (hashing was the only reason to walk vertex coordinates).

#: Domain labels per component, as the reference layout uses them
#: (`spec/node_info_ui_v2.png`): a mesh point is a **Vertex**, a curve point a **Control
#: Point**, and the instance component has no sub-layer at all.
DOMAIN_LABELS = {
    "MESH": {"POINT": "Vertex", "EDGE": "Edge", "FACE": "Face", "CORNER": "Face Corner"},
    # Blender 5.2 reports the curve-domain attributes (curve_type, cyclic, …) as
    # `domain == "CURVE"`, not "SPLINE"; keep both so either naming reads as "Spline".
    "CURVES": {"POINT": "Control Point", "CURVE": "Spline", "SPLINE": "Spline"},
    "POINTCLOUD": {"POINT": "Point"},
    "INSTANCES": {"POINT": "Instances"},
    "GREASE_PENCIL": {"POINT": "Point", "CURVE": "Curve", "LAYER": "Layer"},
}


#: Type abbreviations for the `name(Type)` items, in the reference's style
#: (`position(Vec)`, `mask(Flt)`, `id(Int)`, `cyclic(Bol)`).
TYPE_LABELS = {
    "FLOAT": "Flt",
    "FLOAT2": "Flt2",
    "FLOAT_VECTOR": "Vec",
    "FLOAT_COLOR": "Col",
    "BYTE_COLOR": "Col",
    "INT": "Int",
    "INT8": "Int8",
    "INT32_2D": "Int2",
    "BOOLEAN": "Bol",
    "QUATERNION": "Quat",
    "FLOAT4X4": "Mat4",
    "STRING": "Str",
    "MENU": "Menu",
    "ROTATION": "Rot",
}


def _domain_label(kind, domain):
    """`"Vertex"` / `"Control Point"` / … for one (component, domain) pair."""
    return DOMAIN_LABELS.get(kind, {}).get(domain, str(domain).title())


def _type_label(data_type):
    """`"Vec"` for `FLOAT_VECTOR`; anything unknown keeps its own name, title-cased."""
    return TYPE_LABELS.get(data_type, str(data_type).title())


#: The counts half is a **fixed table** (L0 MMB-5): every domain Blender's geometry has, in
#: a stable order, with `0` where the geometry does not carry it - "便于固定显示位置，方便用户
#: 比较", which is why the rows never move between nodes. L0 then dropped the component names
#: ("只保留domain子类，不需要mesh/curve等大类了"), so a component is now only a *group of
#: domains that share a line*; every entry carries the RNA domain so the colour of a domain is
#: the same here and in the attribute groups below (`domain_slot()`).
#: Grease Pencil is left out for now (L0: "先不管蜡笔").
COMPONENT_LAYOUT = (
    ("MESH", "Mesh", (
        ("POINT", "Vertex"),
        ("EDGE", "Edge"),
        ("FACE", "Face"),
        ("CORNER", "Face Corner"),
    )),
    ("CURVES", "Curve", (
        ("POINT", "Control Point"),
        ("CURVE", "Spline"),
    )),
    ("POINTCLOUD", "Point Cloud", (
        ("POINT", "Point"),
    )),
    # The instance component has no sub-layers, so its own name is the label the reference
    # layout shows ("Instances 0") - the count half has no component names left, and a bare
    # `0` with nothing to name it would be unreadable.
    ("INSTANCES", "Instances", (
        ("POINT", "Instances"),
    )),
    ("VOLUME", "Volume", (
        ("GRID", "Grid"),
    )),
)


#: `(component, RNA domain)` -> its position in the fixed table above. The lower half is
#: ordered by this, **not** by the order `data_block.attributes` happens to iterate in.
#: Measured (2026-09-15, `qa/mmb/mmb_group_order_probe.py`): Blender hands the attributes back
#: in **storage** order - whatever order the layers were created in. A mesh whose corner
#: attributes (`UVMap`, a colour attribute) exist before `sharp_face`/`position` therefore
#: yielded the groups `Vertex / Face / Face Corner / Edge` while the counts half right above
#: read `Vertex / Edge / Face / Face Corner` - reported from another machine as "乱序".
#: L0 MMB-5's reason for the fixed counts table ("便于固定显示位置，方便用户比较") applies to
#: the attribute groups just as much, so both halves now walk the same element order.
_DOMAIN_ORDER = {
    (kind, domain): (component_index, domain_index)
    for component_index, (kind, _label, domains) in enumerate(COMPONENT_LAYOUT)
    for domain_index, (domain, _dlabel) in enumerate(domains)
}

#: A `(component, domain)` that Blender grows later (or Grease Pencil, which L0 leaves out)
#: has no position in the table. It sorts to the end and keeps its first-seen place there -
#: the same "list it, never drop it" rule the colour fallback in `domain_slot()` follows.
_UNRANKED_DOMAIN = (len(COMPONENT_LAYOUT), 0)

#: The same domain spelled two ways, as `DOMAIN_SLOTS` also carries both: Blender 5.2 reports
#: a curve's spline sub-layer as `"CURVE"`, but `"SPLINE"` was the older spelling.
_DOMAIN_ORDER_ALIASES = {("CURVES", "SPLINE"): ("CURVES", "CURVE")}


def _domain_rank(group):
    """Sort key: where this group belongs in `COMPONENT_LAYOUT`, unranked ones last."""
    key = (group["kind"], group["domain"])
    return _DOMAIN_ORDER.get(_DOMAIN_ORDER_ALIASES.get(key, key), _UNRANKED_DOMAIN)


def _component_lines(components):
    """The full, fixed counts table: a domain the geometry lacks reads as 0.

    L0 MMB-5 asks for exactly this - every domain listed, zeros included - and the numbers
    are never dimmed, because the colour of a row is the colour of its **domain** and
    nothing else. The component's name is not drawn; it survives as the line that groups
    its domains together.
    """
    found = {
        component["kind"]: {
            entry["domain"]: entry["count"] for entry in component["counts"]
        }
        for component in components
    }
    lines = []
    for kind, label, domains in COMPONENT_LAYOUT:
        counts = found.get(kind) or {}
        lines.append({
            "kind": kind,
            "label": label,
            "counts": [
                {
                    "domain": domain,
                    "label": domain_label,
                    "count": int(counts.get(domain, 0)),
                    "slot": domain_slot(kind, domain),
                }
                for domain, domain_label in domains
            ],
        })
    return lines


def _attribute_rows(kind, data_block):
    """``[{kind, domain, label, name, type, type_label, count}, ...]`` for one component.

    Internal attributes (a leading ``.``, e.g. ``.corner_vert``) are skipped: MMB-5 asks for
    the attributes the spreadsheet shows by default, and those are the non-internal ones -
    `position`, `id`, `material_index`, and whatever the user created. `position` used to be
    filtered out here because only the bounds and the hash needed it; it is exactly the
    attribute MMB-5 names, so it is listed now.

    An attribute with **0 elements** is skipped as well (L0 MMB-5: "下方属性几何元素数量为 0
    的就不显示") - which is what keeps the attributes of an empty component (the mesh Blender's
    object evaluation adds on a curve-only read) out of the block.
    """
    rows = []
    attributes = getattr(data_block, "attributes", None)
    if attributes is None:
        return rows
    for attribute in attributes:
        name = attribute.name
        if name.startswith("."):
            continue
        try:
            count = len(attribute.data)
        except Exception:  # noqa: BLE001 - some attribute kinds refuse len()
            count = -1
        if count == 0:
            continue
        rows.append({
            "kind": kind,
            "domain": attribute.domain,
            "label": _domain_label(kind, attribute.domain),
            "name": name,
            "type": attribute.data_type,
            "type_label": _type_label(attribute.data_type),
            "count": count,
        })
    return rows


def _snapshot_geometry(context, tree, descriptor):
    """Evaluate one descriptor and reduce it to plain data.

    The evaluated `GeometrySet` is a view over temporary objects, so it is turned
    into a snapshot *inside* this call - nothing outside may hold the view.
    """
    geometry = evaluate_descriptor(context, tree, descriptor)
    return _snapshot_of_geometry_set(geometry)


def _curve_spline_count(curves):
    """How many splines a `Curves` data-block holds.

    Measured on this build (5.2.1): `bpy.types.Curves` has **no** `splines` collection -
    `Curves.points` is the flat list of every control point and the slices are
    `Curves.curves` ("All curves in the data-block"). Assuming `splines` made the whole
    read fail with `'Curves' object has no attribute 'splines'`, which is what the panel
    then showed. The name has moved between releases, so this tries `splines` first, then
    `curves`, and falls back to the element count of a curve-domain attribute
    (`curve_type`) rather than trusting one spelling.
    """
    for name in ("splines", "curves"):
        collection = getattr(curves, name, None)
        if collection is not None:
            return len(collection)
    attributes = getattr(curves, "attributes", None)
    if attributes is not None:
        for attribute in attributes:
            try:
                if attribute.domain in {"CURVE", "SPLINE"}:
                    return len(attribute.data)
            except Exception:  # noqa: BLE001 - keep looking
                continue
    return 0


def _snapshot_of_geometry_set(geometry):
    """Turn an evaluated `GeometrySet` into ``{components, attributes, notes}`` plain data.

    A component is listed when the geometry set **has** it, even when it holds no elements
    (the reference layout shows `Point Cloud  Point 0` and `Instances 0`): "present but
    empty" is information, "absent" is not the same thing.

    Every component is read inside its own `try`: a component whose Python API does not
    match what this module assumes (the `Curves.splines` case) must not blank the whole
    block. What could not be read lands in `notes`, which the panel shows as its muted
    reason line - a visible failure, not a silent one (MMB-9).
    """
    snapshot = {"components": [], "attributes": [], "notes": []}

    def add_component(kind, label, counts, data_block=None):
        """`counts` is a tuple of `(RNA domain, label, count)` triples."""
        snapshot["components"].append({
            "kind": kind,
            "label": label,
            "counts": [
                {"domain": domain, "label": domain_label, "count": int(count)}
                for domain, domain_label, count in counts
            ],
        })
        if data_block is not None:
            snapshot["attributes"].extend(_attribute_rows(kind, data_block))

    def read(label, work):
        try:
            work()
        except Exception as error:  # noqa: BLE001 - one bad component must not blank the block
            snapshot["notes"].append(f"{label}: {type(error).__name__}: {error}")

    mesh = getattr(geometry, "mesh", None)
    if mesh is not None:
        read("Mesh", lambda: add_component("MESH", "Mesh", (
            ("POINT", "Vertex", len(mesh.vertices)),
            ("EDGE", "Edge", len(mesh.edges)),
            ("FACE", "Face", len(mesh.polygons)),
            ("CORNER", "Face Corner", len(mesh.loops)),
        ), mesh))

    curves = getattr(geometry, "curves", None)
    if curves is not None:
        read("Curve", lambda: add_component("CURVES", "Curve", (
            ("POINT", "Control Point", len(curves.points)),
            ("CURVE", "Spline", _curve_spline_count(curves)),
        ), curves))

    pointcloud = getattr(geometry, "pointcloud", None)
    if pointcloud is not None:
        read("Point Cloud", lambda: add_component("POINTCLOUD", "Point Cloud", (
            ("POINT", "Point", len(pointcloud.points)),
        ), pointcloud))

    try:
        instance_cloud = geometry.instances_pointcloud()
    except Exception as error:  # noqa: BLE001 - older builds have no such method
        instance_cloud = None
        snapshot["notes"].append(f"Instances: {type(error).__name__}: {error}")
    if instance_cloud is not None:
        # The instance component has no sub-layers: its line is just `Instances <count>`,
        # which the empty sub-label expresses.
        read("Instances", lambda: add_component("INSTANCES", "Instances", (
            ("POINT", "", len(instance_cloud.points)),
        ), instance_cloud))

    volume = getattr(geometry, "volume", None)
    if volume is not None:
        # A volume's elements are its grids (measured: `Volume.grids` -> ['density']).
        read("Volume", lambda: add_component("VOLUME", "Volume", (
            ("GRID", "Grid", len(getattr(volume, "grids", []))),
        )))

    grease_pencil = getattr(geometry, "grease_pencil", None)
    if grease_pencil is not None:
        read("Grease Pencil", lambda: add_component("GREASE_PENCIL", "Grease Pencil", (
            ("LAYER", "Layer", len(getattr(grease_pencil, "layers", []))),
        )))

    return snapshot


def _attribute_groups(attributes, empty_kinds=frozenset()):
    """Group attribute rows for the lower half: one group per (component, domain).

    The title is the domain's label plus "Attributes" (`Vertex Attributes`, `Control Point
    Attributes`, `Spline Attributes` - exactly the reference layout), and the group carries
    the **colour slot of its domain**, so the lower half repeats the colour of the counts
    line above it (L0 MMB-5: "domain子类颜色要和上方信息颜色保持一致"). A group exists only
    when that domain **has** attributes, which is what "没有对应域的就不显示" asks for, and
    the attributes of an **empty component** are skipped entirely (L0 MMB-5: the empty `Mesh`
    a mesh object's evaluation adds on a curve-only read must not contribute a group).

    The groups are returned in `COMPONENT_LAYOUT`'s element order (`_domain_rank`), so the two
    halves of the block always read the same way round - see `_DOMAIN_ORDER`.
    """
    groups = []
    index = {}
    for row in attributes:
        if row["kind"] in empty_kinds:
            continue
        key = (row["kind"], row["domain"])
        group = index.get(key)
        if group is None:
            group = {
                "kind": row["kind"],
                "domain": row["domain"],
                "slot": domain_slot(row["kind"], row["domain"]),
                "title": f"{row['label']} Attributes",
                "items": [],
                "hidden": 0,
            }
            index[key] = group
            groups.append(group)
        group["items"].append({
            "key": key + (row["name"],),
            "name": row["name"],
            "type_label": row["type_label"],
            "count": row["count"],
        })
    for group in groups:
        if len(group["items"]) > MAX_ITEMS_PER_GROUP:
            group["hidden"] = len(group["items"]) - MAX_ITEMS_PER_GROUP
            del group["items"][MAX_ITEMS_PER_GROUP:]
    # The groups follow the counts half's element order, not the order Blender stored the
    # layers in (`_DOMAIN_ORDER`). `sorted()` is stable, so two groups Blender has no
    # position for keep their first-seen order among themselves. **Inside** a group the items
    # stay in Blender's attribute order: that is the order the Spreadsheet shows them in, and
    # MMB-5 asks for "the attributes the spreadsheet shows by default".
    groups.sort(key=_domain_rank)
    return groups


def build_panel_state(context, node, *, reason=""):
    """Everything the draw callback needs, computed once for one node.

    **One block, not two columns** (L0 MMB-5 + the reference layout): the block shows the
    node's *output* - the counts of every component it carries, then the attribute table
    grouped by domain - and the input stream is read only to answer "which attributes did
    this node **add**", which is what gets highlighted. A node with only a geometry input
    shows its input stream instead (MMB-6), when the preference allows it.
    """
    settings = prefs(context)
    state = {
        "node": node.name,
        "node_label": node.bl_idname.replace("GeometryNode", "").replace("Node", "") or node.bl_idname,
        "node_type": node.bl_idname,
        "stream": "output",
        "title": "",
        "components": [],
        "attribute_groups": [],
        "added": set(),
        "reason": reason,
        "time_ms": 0.0,
    }
    if node is None:
        return state

    started = time.perf_counter()
    tree = node.id_data
    has_output = bool(_geometry_sockets(node, True))
    has_input = bool(_geometry_sockets(node, False))
    output_descriptor = geometry_source(node)

    def read(descriptor, label):
        """Evaluate one descriptor into a snapshot, or report why it failed."""
        if descriptor is None:
            return None
        try:
            snapshot = _snapshot_geometry(context, tree, descriptor)
        except Exception as error:  # noqa: BLE001 - report, never raise into a draw
            if not state["reason"]:
                state["reason"] = f"could not read the {label}: {error}"
            return None
        snapshot_notes.extend(snapshot.get("notes") or [])
        return snapshot

    snapshot_notes: list[str] = []

    def apply_snapshot(snapshot, stream, title):
        """Fill the block from one snapshot: the fixed counts table + the attribute groups."""
        state["stream"] = stream
        state["title"] = title
        state["components"] = _component_lines(snapshot["components"])
        # An all-zero component still gets its line (fixed positions, L0 MMB-5), but it must
        # not contribute attribute groups - that is the empty `Mesh` Blender's object
        # evaluation adds when a node outputs only curves.
        empty_kinds = {
            component["kind"] for component in state["components"]
            if not any(entry["count"] for entry in component["counts"])
        }
        state["attribute_groups"] = _attribute_groups(snapshot["attributes"], empty_kinds)

    if not has_output and not (settings.show_input_only if settings else False):
        state["reason"] = "input-only node — enable “Show input-only nodes” to read it"
        state["time_ms"] = (time.perf_counter() - started) * 1000.0
        return state

    if has_output:
        output_snapshot = read(output_descriptor, "output")
        if output_snapshot is not None:
            apply_snapshot(output_snapshot, "output", f"Out · {output_descriptor['identifier']}")
            # MMB-5: highlight the attributes the node added - present in the output, with no
            # attribute of the same name and domain in its **first** geometry input. L0 is
            # explicit that a multi-input node (`Join Geometry`) compares against the first
            # input only, so a join does highlight what its later inputs brought along.
            # Nothing else is highlighted (L0 边界: "加粗只到属性表").
            if has_input:
                input_snapshot = read(upstream_geometry_source(node), "input")
                if input_snapshot is not None:
                    have = {
                        (row["kind"], row["domain"], row["name"])
                        for row in input_snapshot["attributes"]
                    }
                    state["added"] = {
                        (row["kind"], row["domain"], row["name"])
                        for row in output_snapshot["attributes"]
                        if (row["kind"], row["domain"], row["name"]) not in have
                    }
    else:
        # Only a geometry input: show that stream on its own (MMB-6).
        input_snapshot = read(upstream_geometry_source(node), "input")
        if input_snapshot is not None:
            apply_snapshot(input_snapshot, "input", "In · upstream")

    if not state["components"]:
        state["reason"] = state["reason"] or "no geometry to read at this node"
    elif snapshot_notes and not state["reason"]:
        # A component that could not be read is reported, but the rest of the block stands
        # (MMB-9: the failure is visible, not silent).
        state["reason"] = "; ".join(snapshot_notes)
    state["time_ms"] = (time.perf_counter() - started) * 1000.0
    return state
