# SPDX-FileCopyrightText: 2026 PDT Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Constants: the two vocabularies (element kinds and geometry components), the prefix and
size rules, and every operator / keymap / menu idname this add-on uses.

Nothing here imports Blender, so any module may import it, in any order."""

from __future__ import annotations

import re


TEMP_OBJECT_PREFIX = "PDT_PICK_obj_"


TEMP_MESH_PREFIX = "PDT_PICK_mesh_"


TEMP_GROUP_PREFIX = "PDT_PICK_group_"


SESSION_KEY = "pdt_selection_session"


FIELD_LINK_MAX_DEPTH = 4


# bNodeSocketValueString is a fixed char[1024] buffer in DNA.
MAX_SOCKET_STRING = 1000


# Longer selections are stored in an ID property on the node and the socket only
# keeps this token; GeometryNodePDTSelection resolves it (no DNA change, no
# length limit).
LONG_SELECTION_KEY = "pdt_selection_long"


LONG_SELECTION_TOKEN = "@prop"


#: Legacy (two-mode era): during an **instance** pick the source object could stay visible
#: so the instanced geometry itself is the visual reference. The reference is part of the
#: instance *mode* now (`INSTANCE_MODES`); this key is only read to migrate an old session.
REFERENCE_STORE_KEY = "pdt_pick_reference"


#: Attribute namespace of a group: the token ``A`` reads the boolean attribute
#: ``group:A``. Mirrors GROUP_ATTR_PREFIX in node_geo_pdt_selection.cc.
GROUP_ATTR_PREFIX = "group:"


#: The C++ query operator the trigger is built on (L0 PICK-1). It lives in the editor's own
#: module - `source/blender/editors/space_node/node_pdt_query.cc` - because both hit tests it
#: wraps (`node_find_indicated_socket()` for the connection dot, and the socket *row*: its name
#: and value field) are behind headers no add-on may include. Its result is read back with
#: `operator_properties_last()`; see `pick_hit_test()`.
NODE_UNDER_CURSOR_IDNAME = "pdt.node_under_cursor"


#: Every element kind the node can address (`Domain`, L0 `spec/pdt_selection.md` PDS-9).
#: These are **element kinds**, not plain attribute domains: Blender's `AttrDomain::Point`
#: covers mesh vertices, curve control points *and* point cloud points, and the picker has
#: to know which one the user meant. `CURVE` is Blender's `AttrDomain::Curve` (one index
#: per spline); `INSTANCE` is `AttrDomain::Instance` (one index per **top level** instance).
DOMAINS = ("VERTEX", "EDGE", "FACE", "CONTROL_POINT", "SPLINE", "POINT", "INSTANCE")


DOMAIN_LABELS = {
    "VERTEX": "Vertex",
    "EDGE": "Edge",
    "FACE": "Face",
    "CONTROL_POINT": "Control Point",
    "SPLINE": "Spline",
    "POINT": "Point",
    "INSTANCE": "Instance",
}


#: Blender's own icon for each element kind, drawn on the first-level pie's slices
#: (L0 PICK-1: 图标用 blender 自带 icon). Every name is checked against the icon enum this
#: build exposes (`test_pick_operator.py`, "every pie icon is a Blender icon") - a name
#: Blender does not know raises inside `UILayout.operator()`, and a raise inside `draw()`
#: empties the whole menu.
#:
#: The vocabulary is copied from the Spreadsheet's own domain rows
#: (`spreadsheet_dataset_draw.cc`, the `Spreadsheet Data-Block` panel), so a slice here looks
#: like the same element does there:
#:
#: * `MeshDomainViewItem` -> `mesh_domain_to_icon()`: `VERTEXSEL` / `EDGESEL` / `FACESEL`;
#: * `CurvesDomainViewItem` -> `curves_domain_to_icon()`: **`CURVE_BEZCIRCLE` for control
#:   points** and **`CURVE_PATH` for splines** - the object-data icons
#:   (`CURVE_BEZCURVE` / `OUTLINER_DATA_CURVES`) are *not* used there;
#: * `PointsViewItem` (Point Cloud) -> `POINTCLOUD_POINT`;
#: * `InstancesViewItem` -> `EMPTY_AXIS` (not the modifier icon `MOD_INSTANCE`).
DOMAIN_ICONS = {
    "VERTEX": "VERTEXSEL",
    "EDGE": "EDGESEL",
    "FACE": "FACESEL",
    "CONTROL_POINT": "CURVE_BEZCIRCLE",
    "SPLINE": "CURVE_PATH",
    "POINT": "POINTCLOUD_POINT",
    "INSTANCE": "EMPTY_AXIS",
}


#: Icons for the second-level pie, in the same spirit as `DOMAIN_ICONS`: the point modes get
#: point-cloud icons (the reference mode is the plain icon plus the source object), the
#: realized mode the real-geometry one.
INSTANCE_MODE_ICONS = {
    "POINTS": "POINTCLOUD_DATA",
    "POINTS_REFERENCE": "OUTLINER_OB_EMPTY",
    "REALIZED": "PARTICLE_POINT",
}


#: Element kind -> geometry component. The node derives exactly this mapping in C++
#: (`index_domain_filter()`), and it is what lets the picker refuse a pick that the user
#: cannot mean: `Vertex` is a mesh element, so a curve-only stream cannot be meant.
DOMAIN_KINDS = {
    "VERTEX": "MESH",
    "EDGE": "MESH",
    "FACE": "MESH",
    "CONTROL_POINT": "CURVE",
    "SPLINE": "CURVE",
    "POINT": "POINTCLOUD",
    "INSTANCE": "INSTANCES",
}


#: Element kind -> Blender's own attribute domain name. The session speaks element kinds
#: (`VERTEX` / `CONTROL_POINT` / ...); every helper that talks to Blender's mesh, curve or
#: attribute API speaks `AttrDomain` (`POINT` / `CURVE` / ...), so the two vocabularies are
#: translated at those boundaries only (`blender_attr_domain()`).
BLENDER_ATTR_DOMAIN = {
    "VERTEX": "POINT",
    "EDGE": "EDGE",
    "FACE": "FACE",
    "CONTROL_POINT": "POINT",
    "SPLINE": "CURVE",
    "POINT": "POINT",
    "INSTANCE": "INSTANCE",
}


#: How the *mesh* point kind is spelled; the mesh helpers use this one.
MESH_POINT_DOMAIN = "VERTEX"


#: Domains a mesh can be addressed on (the only kind with edges and faces).
MESH_DOMAINS = ("VERTEX", "EDGE", "FACE")


def blender_attr_domain(domain: str) -> str:
    """Blender's `AttrDomain` name for an element kind (the identity for unknowns).

    `POINT` maps to itself, so a call site that already speaks `AttrDomain` keeps working.
    """
    return BLENDER_ATTR_DOMAIN.get(domain, domain)


# Domain -> (mesh collection, edit-mode select mode)
DOMAIN_ELEMENTS = {
    "VERTEX": ("vertices", (True, False, False)),
    "POINT": ("vertices", (True, False, False)),
    "EDGE": ("edges", (False, True, False)),
    "FACE": ("polygons", (False, False, True)),
}


# bmesh spells those collections differently than the Mesh RNA.
BMESH_COLLECTIONS = {
    "VERTEX": "verts",
    "POINT": "verts",
    "EDGE": "edges",
    "FACE": "faces",
}


#: Geometry components the picker can build a temporary object for, in the order
#: they are offered. The node itself keeps selecting on *every* component it is
#: evaluated on (measured, see docs/archive/07-曲线支持评估.md §13); the kind choice
#: decides which component the viewport picker shows and writes indices against.
#: `INSTANCES` is *detected* (so the panel can name it and the refusal can be
#: precise) but not yet pickable - instances have no edit mode and no data-block.
GEOMETRY_KINDS = ("MESH", "CURVE", "POINTCLOUD", "INSTANCES")


#: Kinds the picker can edit today. All four have a Blender edit mode: mesh
#: (`EDIT_MESH`, with point/edge/face sub-modes), curve (`EDIT_CURVE`) and point
#: cloud (`EDIT_POINTCLOUD`, points only - no sub-modes). Instances have no edit mode
#: of their own, so they are picked as a **point cloud with one point per top level
#: instance** (`instances_pointcloud()`), which makes the point index the instance
#: index (docs/archive/08-实例支持评估.md §4).
PICKABLE_KINDS = ("MESH", "CURVE", "POINTCLOUD", "INSTANCES")


GEOMETRY_KIND_LABELS = {
    "MESH": "Mesh",
    "CURVE": "Curve",
    "POINTCLOUD": "Point Cloud",
    "INSTANCES": "Instances",
}


#: Which element kinds each geometry component offers. This is `DOMAIN_KINDS` reversed:
#: the node has one `Domain` menu (L0 PDS-9) that already names the component, so the
#: picker only keeps the reverse view for panels and refusal messages.
KIND_DOMAINS = {
    kind: tuple(domain for domain, domain_kind in DOMAIN_KINDS.items() if domain_kind == kind)
    for kind in GEOMETRY_KINDS
}


#: Preferred kind when the node sees several and nothing is pinned yet. Instances are
#: last on purpose: a pick on them is refused until M2/M3, so they must never win the
#: automatic choice over a kind that can be picked.
KIND_PRIORITY = ("MESH", "CURVE", "POINTCLOUD", "INSTANCES")


#: Point cloud selection state: a `POINT` domain attribute written by Blender's own
#: edit mode (`pointcloud.select_all`, viewport picking). **Missing means everything
#: is selected** - `select_all(SELECT)` even removes it as an optimization, so reads
#: have to treat "no attribute" as "all points" (measured, qa/picker/pointcloud_probe.py).
POINTCLOUD_SELECTION_ATTR = ".selection"


#: How instances are picked (L0 PICK-3, three modes):
#: `POINTS` bakes one point per instance at its origin and hides the source object;
#: `POINTS_REFERENCE` is that same bake with the source object left visible as the visual
#: reference; `REALIZED` realizes the instances and picks the **real geometry**, folding the
#: selected elements back into instance numbers through an attribute.
#: The reference is not a separate switch any more - it is what distinguishes the two point
#: modes (they differ in nothing else), which is why `POINTS_REFERENCE` exists by name.
INSTANCE_MODES = ("POINTS", "POINTS_REFERENCE", "REALIZED")


INSTANCE_MODE_LABELS = {
    "POINTS": "Point Cloud",
    "POINTS_REFERENCE": "Point Cloud with Reference",
    "REALIZED": "Realized Instance",
}


#: The mode a pick uses when nothing else says otherwise: points at the instance origins,
#: with the source object as the visual reference (origins alone are hard to aim at).
DEFAULT_INSTANCE_MODE = "POINTS_REFERENCE"


INSTANCE_MODE_STORE_KEY = "pdt_pick_instance_mode"


#: The attribute a realized pick stores on the instance domain *before* `Realize
#: Instances`: `Index + 1`, so the elements that did **not** come from an instance keep
#: the attribute default 0 and act as a sentinel.
PDT_INSTANCE_INDEX_ATTR = "pdt_instance_index"


#: A realized pick happens in an edit mode, which copies the geometry: measured 4.82 M
#: vertices cost 1.74 s and a 5.5 GB working set (docs/archive/08-实例支持评估.md §5.5). Above
#: this the pick is refused and the point pick is suggested instead.
INSTANCE_REALIZE_MAX_ELEMENTS = 1_000_000


#: The edit modes this picker drives. Mesh has point/edge/face sub-modes; curves and
#: point clouds are points-only (no sub-mode tool setting).
EDIT_MODES = {"EDIT_MESH", "EDIT_CURVE", "EDIT_POINTCLOUD"}


#: Mode each pickable kind enters.
KIND_EDIT_MODE = {"MESH": "EDIT_MESH", "CURVE": "EDIT_CURVE", "POINTCLOUD": "EDIT_POINTCLOUD"}


#: `bke::CurveType` -> legacy `Curve` spline type. Catmull-Rom has no legacy
#: equivalent; it is written as a POLY spline, which keeps the control points (and
#: therefore the indices the node addresses) identical.
CURVE_TYPE_TO_SPLINE = {0: "POLY", 1: "POLY", 2: "BEZIER", 3: "NURBS"}


CURVE_TYPE_NAMES = {0: "CATMULL_ROM", 1: "POLY", 2: "BEZIER", 3: "NURBS"}


# Enter confirms, Esc/Right-Click cancels - the same convention as Blender's own
# modal operators (G/R/S). NOTE: Blender's event *identifier* for the main Return
# key is "RET" (the C enum is EVT_RETURN; `event.type` reports the RNA identifier,
# so a literal "RETURN" never matches). SPACE is accepted as a second confirm key
# because an active input method (IME) can swallow Enter. Everything else is
# passed through to edit mode, so box select, circle select, A/Ctrl+I, Ctrl+Z, ...
# keep working. tests/test_pick_operator.py checks these sets against the event
# identifiers Blender actually reports.
PICK_CONFIRM_KEYS = {"RET", "NUMPAD_ENTER", "SPACE"}


PICK_CANCEL_KEYS = {"ESC", "RIGHTMOUSE"}


PICK_KEYMAP_NAME = "Node Editor"


#: The item this add-on registers is **unbound** (PICK-4: no default shortcut - the user
#: assigns one). `NONE` is what Blender's own Keymap editor writes for a freshly added item,
#: and the only event type that cannot be dispatched to. The item still has to exist: the
#: Keymap editor's name search only filters the items a keymap already has, so an operator
#: that appears in no keymap cannot be found, let alone bound.
PICK_KEYMAP_ITEM = {"type": "NONE", "value": "PRESS"}


# The modal picker and its original (now identical) id. A saved keymap item can
# point at either, so both are treated as "the picker" everywhere.
PICKER_MODAL_IDNAME = "pdt.selection_pick_modal"


PICKER_IDNAMES = (PICKER_MODAL_IDNAME, "pdt.selection_pick")


#: The two pie menus of the trigger (PICK-1). Their *item* operators live in the modes above.
DOMAIN_PIE_IDNAME = "PDT_MT_selection_domain_pie"


INSTANCE_MODE_PIE_IDNAME = "PDT_MT_selection_instance_mode_pie"


#: What the preferences panel prints, verbatim from PICK-4 with the operator's own name
#: substituted (`bl_label` of `pdt.selection_pick_modal`).
OPERATOR_LABEL = "Pick Selection (Viewport)"


REMINDER = f"Assign one shortcut yourself: Preferences>Keymap>{OPERATOR_LABEL}"


STATUS_ACTIVE = "PDT Pick: LMB select | Shift+LMB extend | Enter apply | Esc / RMB cancel"


STATUS_PAUSED = "PDT Pick paused (left edit mode): Tab to resume | Enter apply | Esc / RMB cancel"


STATUS_OTHER = "PDT Pick: another object is in edit mode - Tab back to the pick object | Esc cancels"


def _kind_list(components) -> str:
    return ", ".join(GEOMETRY_KIND_LABELS.get(kind, kind) for kind in components) or "nothing"
