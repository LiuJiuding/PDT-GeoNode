"""Headless tests for the MMB node-info add-on.

    blender.exe -b --factory-startup --python-exit-code 1 \
        --python tools\\pdt\\tests\\test_mmb_node_info.py

Everything the add-on does *outside* a real window is checked here: what a node
reports, when a row counts as "changed", and the three rules from the design doc

  1. a node with a geometry output shows that output stream;
  2. a node with a geometry input *and* output shows both and marks the changed rows;
  3. a node with only a geometry input shows nothing unless the preference is on.

The middle-click gesture itself needs a real window and lives in
``tools/pdt/tests/gui_mmb_node_info.py``.
"""

from __future__ import annotations

import ast
import inspect
import sys
import traceback

import bpy

# The installed copies, exactly like tools/pdt/tests/gui_pick_modal.py: the tests
# must exercise what the build ships, not the source tree.
sys.path.insert(0, bpy.utils.resource_path("SYSTEM") + "/scripts/addons_core")
sys.path.insert(0, bpy.utils.resource_path("SYSTEM") + "/scripts/modules")

import pdt_geo_select as pdt  # noqa: E402
import pdt_mmb_info as mmb  # noqa: E402
from pdt_mmb_info.utils import constants as mmb_constants  # noqa: E402
from pdt_mmb_info import evaluate as mmb_evaluate  # noqa: E402
from pdt_mmb_info import geometry as mmb_geometry  # noqa: E402
from pdt_mmb_info import hit_test as mmb_hit_test  # noqa: E402
from pdt_mmb_info import interface as mmb_interface  # noqa: E402
from pdt_mmb_info import keymap as mmb_keymap  # noqa: E402
from pdt_mmb_info.operators import node_info as mmb_operators  # noqa: E402
from pdt_mmb_info.utils import palette as mmb_palette  # noqa: E402
from pdt_mmb_info import preferences as mmb_preferences  # noqa: E402
from pdt_mmb_info import session as mmb_session  # noqa: E402
from pdt_mmb_info import snapshot as mmb_snapshot  # noqa: E402

CHECKS = 0
FAILURES: list[str] = []


def check(label, actual, expected):
    global CHECKS
    CHECKS += 1
    if actual == expected:
        print(f"OK   {label}: {actual}", flush=True)
    else:
        print(f"FAIL {label}: got {actual!r}, expected {expected!r}", flush=True)
        FAILURES.append(label)


def check_true(label, condition, detail=""):
    check(label, bool(condition), True)
    if not condition and detail:
        print(f"     ({detail})", flush=True)


def addon_modules():
    """Every loaded module of the add-on: the package root and its submodules.

    The add-on is a package (one file per concern, 2026-09-14), so "the module does not do
    X" has to mean *none* of them does - checking only the package root would pass while the
    code sat in `session.py`.
    """
    return [module for name, module in list(sys.modules.items())
            if name == "pdt_mmb_info" or name.startswith("pdt_mmb_info.")]


def imports_picker(module) -> bool:
    """True when `module` really *imports* `pdt_geo_select` (mentions in prose do not count).

    The add-on's docstrings talk about the picker on purpose - they record why the evaluator
    is ours (`evaluate.py`) and why the keymap item is written the way it is (`keymap.py`) -
    so the check has to look at import statements, not at the text.
    """
    try:
        tree = ast.parse(inspect.getsource(module))
    except (OSError, TypeError, SyntaxError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] == "pdt_geo_select" for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "pdt_geo_select":
                return True
    return False


def build_scene():
    """A small chain: Cube -> Subdivide -> Set Position -> Store Named Attribute."""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for tree in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(tree)

    mesh = bpy.data.meshes.new("cube")
    corners = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
               (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    mesh.from_pydata(corners, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("CubeOwner", mesh)
    bpy.context.scene.collection.objects.link(obj)

    tree = bpy.data.node_groups.new("MMB Test", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_in = tree.nodes.new("NodeGroupInput")
    subdiv = tree.nodes.new("GeometryNodeSubdivisionSurface")
    subdiv.name = "Subdivide"
    set_position_zero = tree.nodes.new("GeometryNodeSetPosition")
    set_position_zero.name = "SetPositionZero"
    set_position = tree.nodes.new("GeometryNodeSetPosition")
    set_position.name = "SetPositionShift"
    store = tree.nodes.new("GeometryNodeStoreNamedAttribute")
    store.name = "StoreAttr"
    store.data_type = "FLOAT_VECTOR"
    store.domain = "POINT"
    store.inputs["Name"].default_value = "my_attr"
    cube = tree.nodes.new("GeometryNodeMeshCube")
    cube.name = "CubePrim"
    group_out = tree.nodes.new("NodeGroupOutput")

    tree.links.new(group_in.outputs["Geometry"], subdiv.inputs["Mesh"])
    tree.links.new(subdiv.outputs["Mesh"], set_position_zero.inputs["Geometry"])
    tree.links.new(set_position_zero.outputs["Geometry"], set_position.inputs["Geometry"])
    tree.links.new(set_position.outputs["Geometry"], store.inputs["Geometry"])
    tree.links.new(cube.outputs["Mesh"], store.inputs["Value"])
    tree.links.new(store.outputs["Geometry"], group_out.inputs["Geometry"])

    modifier = obj.modifiers.new("GeometryNodes", "NODES")
    modifier.node_group = tree
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    pdt.register_once()
    mmb.register()
    bpy.context.view_layer.update()
    return obj, tree


def counts_of(state, label):
    """`[(sub_label, count), ...]` of the component whose label is `label`."""
    for component in state["components"]:
        if component["label"] == label:
            return [(entry["label"], entry["count"]) for entry in component["counts"]]
    return []


def slots_of(state, label):
    """`{sub_label: colour slot}` of the component whose label is `label`."""
    for component in state["components"]:
        if component["label"] == label:
            return {entry["label"]: entry["slot"] for entry in component["counts"]}
    return {}


def cells_of(lines):
    """Every drawn cell of every line: `[(text, font, colour), ...]`."""
    return [cell for line in lines for cell in line["cells"]]


def colour_of(lines, text):
    """The colour of the first cell whose text is exactly `text`, or None."""
    for cell_text, _font, colour in cells_of(lines):
        if cell_text == text:
            return colour
    return None


def texts_of(line):
    """The texts of one drawn line."""
    return [text for text, _font, _colour in line["cells"]]


def attribute_groups(state):
    """``{"Vertex Attributes": {"position": "Vec", ...}, ...}`` from the block state."""
    return {
        group["title"]: {item["name"]: item["type_label"] for item in group["items"]}
        for group in state["attribute_groups"]
    }


def main():
    obj, tree = build_scene()
    settings = mmb_preferences.prefs(bpy.context)
    check_true("preferences are reachable", settings is not None)
    if settings is None:
        return

    # ---- 1. the counts half: the full fixed table, zeros included --------
    # A generator (no geometry input at all): the output stream, counts first.
    state = mmb_snapshot.build_panel_state(bpy.context, tree.nodes["CubePrim"])
    check("cube primitive: one block, from the output", state["stream"], "output")
    # L0 MMB-5: every domain sub-class is listed with 0 where the geometry lacks it, so the
    # rows never move and two nodes can be compared line by line. The component names are
    # *not* drawn any more ("只保留domain子类，不需要mesh/curve等大类了") - the component only
    # decides where the line breaks.
    check(
        "cube primitive: the whole fixed table is there, in order",
        [
            (component["label"], counts_of(state, component["label"]))
            for component in state["components"]
        ],
        [
            ("Mesh", [("Vertex", 8), ("Edge", 12), ("Face", 6), ("Face Corner", 24)]),
            ("Curve", [("Control Point", 0), ("Spline", 0)]),
            ("Point Cloud", [("Point", 0)]),
            ("Instances", [("Instances", 0)]),
            ("Volume", [("Grid", 0)]),
        ],
    )
    check(
        "cube primitive: the domain order is COMPONENT_LAYOUT's",
        [component["label"] for component in state["components"]],
        ["Mesh", "Curve", "Point Cloud", "Instances", "Volume"],
    )
    # Every domain row carries a colour slot, and the slots are the ones the palette defines.
    check(
        "every domain of the fixed table has a colour slot",
        sorted(slot for component in state["components"] for slot in slots_of(state, component["label"]).values()),
        sorted(mmb_constants.DOMAIN_COLOR_SLOTS),
    )
    check("cube primitive: nothing is highlighted", state["added"], set())
    # MMB-5 removed the derived statistics: no triangle count, no UV/colour-attribute
    # counts, and no world bounds or coordinate hash anywhere in the state.
    flat = repr(state)
    for gone in ("Triangles", "UV Maps", "Color Attributes", "bounds", "hash"):
        check_true(f"cube primitive: no {gone!r} in the block", gone not in flat)

    # A node with a geometry input *and* an output: still **one** block - the output.
    state = mmb_snapshot.build_panel_state(bpy.context, tree.nodes["Subdivide"])
    check("subdivide: the block comes from the output", state["stream"], "output")
    check(
        "subdivide: 26 vertices out",
        counts_of(state, "Mesh")[0],
        ("Vertex", 26),
    )
    check(
        "subdivide: nothing is highlighted (it adds no attribute)",
        sorted(name for _kind, _domain, name in state["added"]),
        [],
    )

    # ---- 2. the attribute half: grouped by domain, `name(Type)` ----------
    # The reference layout lists the attributes per domain, types abbreviated.
    state = mmb_snapshot.build_panel_state(bpy.context, tree.nodes["CubePrim"])
    groups = attribute_groups(state)
    check_true(
        "cube primitive: the attributes are grouped by domain",
        "Vertex Attributes" in groups,
        f"groups={sorted(groups)}",
    )
    check(
        "cube primitive: position is listed (the spreadsheet's default attribute)",
        groups.get("Vertex Attributes", {}).get("position"),
        "Vec",
    )
    check_true(
        "no internal attribute is listed",
        all(not name.startswith(".") for group in groups.values() for name in group),
        f"groups={groups}",
    )

    # Set Position with the default (zero) offset: structurally identical geometry.
    state = mmb_snapshot.build_panel_state(bpy.context, tree.nodes["SetPositionZero"])
    check(
        "set position (zero offset): nothing is highlighted",
        sorted(name for _kind, _domain, name in state["added"]),
        [],
    )

    tree.nodes["SetPositionShift"].inputs["Offset"].default_value = (0.0, 0.0, 2.5)
    bpy.context.view_layer.update()
    state = mmb_snapshot.build_panel_state(bpy.context, tree.nodes["SetPositionShift"])
    # Positive control for the L0 边界 "加粗只到属性表": moving every vertex changes the
    # geometry, and it must still highlight nothing - and there is no bounds row left that
    # could have been highlighted instead.
    check(
        "set position (offset applied): still nothing highlighted",
        sorted(name for _kind, _domain, name in state["added"]),
        [],
    )

    # ---- an attribute added by the node is the highlighted one -----------
    # MMB-5: the highlight is the attribute that is *new in the output*. `Store Named
    # Attribute` creates one.
    state = mmb_snapshot.build_panel_state(bpy.context, tree.nodes["StoreAttr"])
    check(
        "store attribute: exactly the added attribute is highlighted",
        sorted(name for _kind, _domain, name in state["added"]),
        ["my_attr"],
    )
    check_true(
        "store attribute: the added attribute is in the block",
        "my_attr" in attribute_groups(state).get("Vertex Attributes", {}),
        f"groups={attribute_groups(state)}",
    )
    check_true(
        "store attribute: attributes present on both sides stay plain",
        all(name != "position" for _kind, _domain, name in state["added"]),
        f"added={state['added']}",
    )

    # ---- 3. an input-only node shows nothing by default -------------------
    # Found by probe rather than hardcoded: "has a geometry input, has no geometry
    # output" is the definition, and the engine's own node set decides who matches.
    input_only = None
    for candidate in (
        "GeometryNodeSampleNearestSurface",
        "GeometryNodeAttributeStatistic",
        "GeometryNodeRaycast",
        "GeometryNodeProximity",
        "GeometryNodeSampleIndex",
    ):
        try:
            node = tree.nodes.new(candidate)
        except RuntimeError:
            continue
        if mmb_geometry._geometry_sockets(node, False) and not mmb_geometry._geometry_sockets(node, True):
            input_only = node
            break
        tree.nodes.remove(node)
    if input_only is None:
        print("SKIP no input-only geometry node is available in this build", flush=True)
    else:
        input_only.name = "InputOnly"
        print(f"     (input-only node: {input_only.bl_idname})", flush=True)
        geometry_input = mmb_geometry._geometry_sockets(input_only, False)[0]
        tree.links.new(tree.nodes["Subdivide"].outputs["Mesh"], geometry_input)
        bpy.context.view_layer.update()
        check("input-only: has no geometry output", mmb_geometry._geometry_sockets(input_only, True), [])
        settings.show_input_only = False
        state = mmb_snapshot.build_panel_state(bpy.context, input_only)
        check("input-only (preference off): no block at all", state["components"], [])
        check_true("input-only (preference off): a reason is given", state["reason"])
        settings.show_input_only = True
        state = mmb_snapshot.build_panel_state(bpy.context, input_only)
        check_true(
            "input-only (preference on): the input stream is reported",
            state["components"],
            f"{state['reason']}",
        )
        check("input-only (preference on): the block is the input stream", state["stream"], "input")
        check(
            "input-only (preference on): 26 vertices in",
            counts_of(state, "Mesh")[0],
            ("Vertex", 26),
        )
        settings.show_input_only = False
        tree.nodes.remove(input_only)

    # ---- robustness: a node with no geometry at all does not raise --------
    math_node = tree.nodes.new("ShaderNodeMath")
    state = mmb_snapshot.build_panel_state(bpy.context, math_node)
    check_true("a non-geometry node reports a reason instead of raising", state["reason"])

    # ---- the block layout model (what the draw callback renders) ---------
    # `_panel_lines` is split out of the renderer precisely so the layout is testable
    # without a GPU. L0 MMB-5 for this pass: no component names, one colour per domain
    # sub-class with the count a size larger than its label, the group title repeating its
    # domain's colour, one colour for all attribute names and types, and a separate colour
    # for the attributes the node added.
    settings = mmb_preferences.prefs(bpy.context)
    palette = mmb_palette.palette_from_settings(settings)
    body, count_font = mmb_interface.font_sizes(1.0)
    check(
        "the two sizes at scale 1.0 are 12 (label & title) and 15 (count)",
        (body, count_font),
        (12, 15),
    )
    check(
        "the count ratio matches the L0 reference image (x1.23)",
        round(count_font / body, 2),
        1.25,
    )
    state = mmb_snapshot.build_panel_state(bpy.context, tree.nodes["StoreAttr"])
    lines, gap = mmb_interface._panel_lines(state, body, count_font, 420, palette)
    check_true("the block has lines to draw", len(lines) >= 3, f"{len(lines)} lines")
    check(
        "the first line is the vertex domain and its count",
        texts_of(lines[0])[:2],
        ["Vertex", str(counts_of(state, "Mesh")[0][1])],
    )
    check(
        "no component name is drawn in the counts half",
        [text for text in texts_of(lines[0]) if text in ("Mesh", "Curve", "Point Cloud",
                                                         "Instances", "Volume")],
        [],
    )
    check_true(
        "the count is a size larger than its domain label",
        lines[0]["cells"][1][1] > lines[0]["cells"][0][1],
        f"fonts={[font for _t, font, _c in lines[0]['cells']]}",
    )
    check(
        "a domain label and its count share one colour",
        [lines[0]["cells"][0][2], lines[0]["cells"][1][2]],
        [palette["vertex"], palette["vertex"]],
    )
    check(
        "the Edge count uses the Edge colour",
        colour_of(lines, "Edge"),
        palette["edge"],
    )
    check_true(
        "the full text width is measurable",
        all(line["width"] > 0 for line in lines if line["cells"]),
    )
    # MMB-5: "domain子类颜色要和上方信息颜色保持一致" - the group title must repeat the exact
    # colour its domain has in the counts half.
    check(
        "the Vertex Attributes title repeats the Vertex colour from the counts half",
        colour_of(lines, "Vertex Attributes"),
        colour_of(lines, "Vertex"),
    )
    check(
        "the Face Attributes title repeats the Face colour from the counts half",
        colour_of(lines, "Face Attributes"),
        colour_of(lines, "Face"),
    )
    # L0 reference v3 shrank the group titles back to the label size (v2 had them a size
    # larger), so a title is exactly as tall as a domain label and shorter than a count.
    check(
        "a group title is drawn at the body size, like a domain label",
        (
            [font for text, font, _c in cells_of(lines) if text == "Vertex Attributes"],
            [font for text, font, _c in cells_of(lines) if text == "Vertex"],
            [font for text, font, _c in cells_of(lines)
             if text == str(counts_of(state, "Mesh")[0][1])],
        ),
        ([body], [body], [count_font]),
    )
    # MMB-5: "属性颜色统一" - every attribute name *and* its type share the one attribute
    # colour, and `name(Type)` is written with no gap (the reference layout).
    check(
        "an attribute is one `name(Type)` cell in the uniform attribute colour",
        colour_of(lines, "position(Vec)"),
        palette["attribute"],
    )
    check_true(
        "no attribute cell is written as `name (Type)`",
        all(" (" not in text for text in (cell[0] for cell in cells_of(lines))),
        f"cells={[cell[0] for cell in cells_of(lines)]}",
    )
    added_cells = [text for text, _font, colour in cells_of(lines) if colour == palette["added"]]
    check(
        "exactly the added attribute uses the added colour, whole cell",
        added_cells,
        ["my_attr(Vec)"],
    )
    check_true(
        "the added colour is not the plain attribute colour",
        palette["added"] != palette["attribute"],
    )
    spacer = [line for line in lines if not line["cells"]]
    check_true("a spacer separates the two halves", len(spacer) == 1, f"{len(spacer)} spacers")

    # ---- a curve component: the API is not what it looks like ------------
    # Blender 5.2's `Curves` data-block has **no** `.splines`: the slices are `Curves.curves`
    # ("all curves in the data-block") while `Curves.points` is every control point, and the
    # curve-domain attributes report `domain == "CURVE"`, not "SPLINE". Assuming `splines`
    # made every curve read fail with a reason line instead of a block - reported from a real
    # session - so this is the regression test for it.
    curve_line = tree.nodes.new("GeometryNodeCurvePrimitiveLine")
    curve_line.name = "CurveLine"
    curve_line.location = (-400, -400)
    state = mmb_snapshot.build_panel_state(bpy.context, curve_line)
    check(
        "curve: the block is built (no failure reason)", state["reason"], ""
    )
    check(
        "curve: control points and splines are counted",
        counts_of(state, "Curve"),
        [("Control Point", 2), ("Spline", 1)],
    )
    # Blender's evaluation of a *mesh* object ensures a mesh component, so a curve-only node
    # also reports an all-zero `Mesh` line. L0 MMB-5 keeps that line (the table is fixed) but
    # says its attributes must not be listed - so there is no `Vertex Attributes` group, and
    # the empty mesh's 0-element `position` is gone with it.
    check(
        "curve: the domains are labelled Control Point / Spline, in the counts order",
        list(attribute_groups(state)),
        ["Control Point Attributes", "Spline Attributes"],
    )
    check_true(
        "curve: an empty component contributes no attribute group",
        "Vertex Attributes" not in attribute_groups(state),
        f"groups={attribute_groups(state)}",
    )
    check(
        "curve: position is listed on the control points",
        attribute_groups(state)["Control Point Attributes"].get("position"),
        "Vec",
    )
    check_true(
        "curve: the spline-domain attributes are there",
        "curve_type" in attribute_groups(state)["Spline Attributes"],
        f"groups={attribute_groups(state)}",
    )
    lines, _gap = mmb_interface._panel_lines(state, body, count_font, 420, palette)
    check(
        "curve: the counts line reads 'Control Point 2 Spline 1' (no component name)",
        texts_of(lines[1]),
        ["Control Point", "2", "Spline", "1"],
    )
    check(
        "curve: the curve domains keep their own colours",
        [colour_of(lines, "Control Point"), colour_of(lines, "Spline")],
        [palette["control_point"], palette["spline"]],
    )
    # The all-zero Mesh line Blender's object evaluation adds: it keeps its domain colours -
    # a `0` says which domain the row is, not that the row is unimportant (L0 MMB-5 keeps
    # "不需要按是否有元素区分颜色" from the previous pass).
    empty_mesh = next(line for line in lines if line["cells"] and line["cells"][0][0] == "Vertex")
    check(
        "curve: the all-zero mesh line still uses its domain colours",
        [colour for _text, _font, colour in empty_mesh["cells"]],
        [palette["vertex"], palette["vertex"], palette["edge"], palette["edge"],
         palette["face"], palette["face"], palette["corner"], palette["corner"]],
    )
    check(
        "curve: its counts are 0 but the colour is not muted",
        (texts_of(empty_mesh)[:2], mmb_interface._COLOR_MUTED not in [c for _t, _f, c in empty_mesh["cells"]]),
        (["Vertex", "0"], True),
    )
    tree.nodes.remove(curve_line)

    # ---- a volume component ---------------------------------------------
    # Volume elements are its grids (measured: `Volume.grids` -> ['density']), and the fixed
    # table means a mesh-only read still lists `Volume Grid 0`.
    volume_cube = tree.nodes.new("GeometryNodeVolumeCube")
    volume_cube.name = "VolumeCube"
    state = mmb_snapshot.build_panel_state(bpy.context, volume_cube)
    check("volume: no failure reason", state["reason"], "")
    check("volume: the grids are counted", counts_of(state, "Volume"), [("Grid", 1)])
    check(
        "volume: the fixed table still lists a mesh line",
        counts_of(state, "Mesh"),
        [("Vertex", 0), ("Edge", 0), ("Face", 0), ("Face Corner", 0)],
    )
    check_true(
        "volume: an empty mesh contributes no attribute group",
        "Vertex Attributes" not in attribute_groups(state),
        f"groups={attribute_groups(state)}",
    )
    tree.nodes.remove(volume_cube)

    # ---- the attribute groups follow the counts half's element order ------
    # Reported from a real session on another machine: the *lower* half came out
    # `Face Corner / Face / Vertex / Edge` while the counts half right above it read
    # `Vertex / Edge / Face / Face Corner`. Cause: `mesh.attributes` enumerates Blender's
    # **storage** order (whatever order the layers were created in), and `_attribute_groups()`
    # used to keep first-seen order. L0 MMB-5's "便于固定显示位置，方便用户比较" is about both
    # halves, so the groups now walk `COMPONENT_LAYOUT` (`_domain_rank`, snapshot.py).
    #
    # The positive control below is the point of the fixture: this mesh's storage order is
    # `Vertex / Face / Face Corner / Edge`, i.e. **not** the expected order - without that,
    # the assertion could not tell the fix from the bug.
    repro_mesh = bpy.data.meshes.new("mmb_corner_first")
    repro_corners = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
                     (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
    repro_faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    repro_mesh.from_pydata(repro_corners, [], repro_faces)
    repro_mesh.update()
    # Corner layers first, then edge, then face, then point: the storage order is scrambled
    # on purpose (Blender stores the layers in creation order).
    repro_mesh.uv_layers.new(name="UVMap")
    repro_mesh.attributes.new(name="corner_col", type="BYTE_COLOR", domain="CORNER")
    repro_mesh.attributes.new(name="edge_val", type="FLOAT", domain="EDGE")
    repro_mesh.attributes.new(name="face_val", type="INT", domain="FACE")
    repro_mesh.attributes.new(name="vert_val", type="FLOAT", domain="POINT")
    repro_obj = bpy.data.objects.new("CornerFirst", repro_mesh)
    bpy.context.scene.collection.objects.link(repro_obj)
    repro_tree = bpy.data.node_groups.new("MMB Order Test", "GeometryNodeTree")
    repro_tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    repro_tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    repro_in = repro_tree.nodes.new("NodeGroupInput")
    repro_out = repro_tree.nodes.new("NodeGroupOutput")
    repro_tree.links.new(repro_in.outputs["Geometry"], repro_out.inputs["Geometry"])
    repro_modifier = repro_obj.modifiers.new("GeometryNodes", "NODES")
    repro_modifier.node_group = repro_tree
    bpy.context.view_layer.objects.active = repro_obj
    obj.select_set(False)
    repro_obj.select_set(True)
    bpy.context.view_layer.update()

    expected_mesh_order = [
        "Vertex Attributes", "Edge Attributes", "Face Attributes", "Face Corner Attributes",
    ]
    state = mmb_snapshot.build_panel_state(bpy.context, repro_in)
    check(
        "the attribute groups follow the counts half's element order",
        [group["title"] for group in state["attribute_groups"]],
        expected_mesh_order,
    )
    repro_geometry = mmb_evaluate.evaluate_descriptor(
        bpy.context, repro_tree, mmb_geometry.geometry_source(repro_in))
    repro_rows = mmb_snapshot._attribute_rows("MESH", repro_geometry.mesh)
    storage_order = []
    for row in repro_rows:
        title = f"{row['label']} Attributes"
        if title not in storage_order:
            storage_order.append(title)
    check_true(
        "positive control: Blender's storage order really is scrambled here",
        storage_order != expected_mesh_order,
        f"storage order is {storage_order} - the fixture no longer reproduces the report",
    )
    check(
        "the block is ordered even though the storage order is not",
        ([group["title"] for group in state["attribute_groups"]], storage_order),
        (expected_mesh_order, ["Vertex Attributes", "Face Attributes",
                               "Face Corner Attributes", "Edge Attributes"]),
    )
    # `_domain_rank` is the sort key itself: a domain Blender grows later must sort to the end
    # (never be dropped, the same rule `domain_slot()` follows for an unknown colour), and the
    # two spellings of the curve's spline sub-layer must share one position.
    check(
        "a domain outside COMPONENT_LAYOUT sorts last, it is not dropped",
        [group["title"] for group in sorted(
            [{"kind": "MESH", "domain": "FACE", "title": "Face Attributes"},
             {"kind": "FUTURE", "domain": "WHATEVER", "title": "Whatever Attributes"},
             {"kind": "MESH", "domain": "POINT", "title": "Vertex Attributes"}],
            key=mmb_snapshot._domain_rank)],
        ["Vertex Attributes", "Face Attributes", "Whatever Attributes"],
    )
    check(
        "the spline domain's two spellings share one position",
        mmb_snapshot._domain_rank({"kind": "CURVES", "domain": "SPLINE"}),
        mmb_snapshot._domain_rank({"kind": "CURVES", "domain": "CURVE"}),
    )
    bpy.data.objects.remove(repro_obj, do_unlink=True)
    bpy.data.node_groups.remove(repro_tree)
    bpy.data.meshes.remove(repro_mesh)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    # ---- one unreadable component must not blank the block ---------------
    # The curve bug above is exactly why `_snapshot_of_geometry_set` reads every component
    # inside its own try: a mismatch between this module's assumptions and Blender's Python
    # API has to degrade into a note on the panel, not into "could not read the output".
    class _FakeMesh:
        vertices = [0, 1, 2]
        edges = [0, 1]
        polygons = [0]
        loops = [0, 1, 2]
        attributes = []

    class _BrokenCurves:
        attributes = []
        points = property(lambda self: (_ for _ in ()).throw(RuntimeError("no points")))

    class _FakeGeometry:
        mesh = _FakeMesh()
        curves = _BrokenCurves()
        pointcloud = None
        volume = None
        grease_pencil = None

        @staticmethod
        def instances_pointcloud():
            return None

    snapshot = mmb_snapshot._snapshot_of_geometry_set(_FakeGeometry())
    check(
        "a broken component does not blank the readable ones",
        [component["label"] for component in snapshot["components"]],
        ["Mesh"],
    )
    check_true(
        "the broken component is reported as a note",
        any("Curve" in note for note in snapshot["notes"]),
        f"notes={snapshot['notes']}",
    )

    # ---- a node that merges geometry compares against its FIRST input ----
    # L0 MMB-5 is explicit: "多几何输入的节点如 join，新增只和第一个输入比". `Join Geometry`
    # has one multi-input socket holding several links, and only the first link is the
    # reference - so attributes the later inputs bring along **are** highlighted. (An earlier
    # revision compared against the union of all inputs and highlighted nothing; the L0 pass
    # asked for the first input, so that is what this asserts.)
    #
    # "First" is the topmost input the editor shows, i.e. the smallest
    # `NodeLink.multi_input_sort_id` - *not* `socket.links[0]`, which enumerates newest-first
    # (measured: after linking mesh, curve, points the ids are 0, 1, 2 while `links` reads
    # points, curve, mesh).
    curve_for_join = tree.nodes.new("GeometryNodeCurvePrimitiveCircle")
    curve_for_join.name = "CurveForJoin"
    join = tree.nodes.new("GeometryNodeJoinGeometry")
    join.name = "JoinAll"
    tree.links.new(tree.nodes["SetPositionZero"].outputs["Geometry"], join.inputs["Geometry"])
    tree.links.new(curve_for_join.outputs["Curve"], join.inputs["Geometry"])
    bpy.context.view_layer.update()
    check(
        "join: the reference input is the topmost link (smallest multi_input_sort_id)",
        (
            mmb_geometry.upstream_geometry_source(join).get("node"),
            [link.from_node.name for link in join.inputs["Geometry"].links],
        ),
        ("SetPositionZero", ["CurveForJoin", "SetPositionZero"]),
    )
    state = mmb_snapshot.build_panel_state(bpy.context, join)
    check("join: no failure reason", state["reason"], "")
    check(
        "join: both components are listed",
        sorted(component["label"] for component in state["components"]),
        ["Curve", "Instances", "Mesh", "Point Cloud", "Volume"],
    )
    check_true(
        "join: the later input's attributes count as added (L0: compare with the first only)",
        ("CURVES", "CURVE", "curve_type") in state["added"],
        f"added={sorted(state['added'])}",
    )
    check_true(
        "join: attributes the first input already had stay plain",
        ("MESH", "POINT", "position") not in state["added"],
        f"added={sorted(state['added'])}",
    )
    tree.nodes.remove(join)
    tree.nodes.remove(curve_for_join)

    # ---- the hit test works in node space, not view space ----------------
    # The editor draws a node at `location * UI_SCALE_FAC` and `View2D.region_to_view()`
    # answers in that same view space, while `node.dimensions` is a *size* in view space and
    # `location_absolute` is a raw node coordinate. Comparing a view-space cursor with a
    # node-space box (what this add-on used to do) drifts by `UI_SCALE_FAC` and stops
    # matching the drawing on any display whose UI scale is not 100%. The numbers below are
    # the ones measured on this machine's 120% display (`ui_scale` 1.1944, `widget_unit` 23),
    # so this doubles as the regression test for that report.
    scale = 1.1944444179534912
    node_dy = 23.0
    location = (-16.09, -567.31)
    dimensions = (167.222, 268.0)
    box = mmb_hit_test.node_box(location, dimensions, ui_scale=scale)
    check_true(
        "node_box keeps the node-space origin",
        # The editor rounds the node origin to a whole *view* unit (`math::round(node_to_view(
        # node.location))`), which is half a node unit at UI_SCALE_FAC 1.19 - hence the 0.5.
        abs(box[0] - location[0]) < 0.5 and abs(box[1] - location[1]) < 0.5,
        f"{box}",
    )
    check_true(
        "node_box divides the drawn size by UI_SCALE_FAC",
        abs(box[2] - dimensions[0] / scale) < 0.01 and abs(box[3] - dimensions[1] / scale) < 0.01,
        f"{box}",
    )
    # The drawn bottom-right corner, expressed the way the editor reports a cursor...
    view_point = (
        location[0] * scale + dimensions[0] - 3.0,
        location[1] * scale - dimensions[1] + 3.0,
    )
    node_point = (view_point[0] / scale, view_point[1] / scale)
    in_box = lambda px, py: box[0] <= px <= box[0] + box[2] and box[1] - box[3] <= py <= box[1]
    check_true(
        "a point on the drawn bottom-right corner is inside the box once converted",
        in_box(*node_point),
        f"box={box} node_point={node_point}",
    )
    # ...and the positive control: the *same* point left in view space misses, which is the
    # bug this test exists for (it made the box shrink towards the tree origin).
    check("the same point in view space misses the box (the old bug)", in_box(*view_point), False)

    collapsed_box = mmb_hit_test.node_box(
        location, (100.0, 50.0), collapsed=True, ui_scale=1.0, node_dy=node_dy
    )
    check_true(
        "a collapsed node is centred on its location, not hung below it",
        abs(collapsed_box[1] - (location[1] + 25.0 - node_dy * 0.5)) < 0.01,
        f"{collapsed_box}",
    )
    check_true("_ui_scale is a usable factor", mmb_hit_test._ui_scale() > 0.0, f"{mmb_hit_test._ui_scale()}")
    check(
        "_c_round matches roundf (half away from zero)",
        (mmb_hit_test._c_round(0.5), mmb_hit_test._c_round(-0.5), mmb_hit_test._c_round(21.49999)),
        (1, -1, 21),
    )

    # ---- ordering: the topmost node wins, a frame never does --------------
    # `_node_at` is driven with patched geometry because `draw_bounds` (and therefore
    # `node.dimensions`) is only filled in while a real editor draws - which is exactly why
    # the checks above take the box apart instead of asking Blender for it.
    hit_tree = bpy.data.node_groups.new("MMB hit test", "GeometryNodeTree")
    under = hit_tree.nodes.new("GeometryNodeMeshCube")
    under.name = "Under"
    over = hit_tree.nodes.new("GeometryNodeMeshCone")
    over.name = "Over"
    frame = hit_tree.nodes.new("NodeFrame")
    frame.name = "Frame"
    framed = hit_tree.nodes.new("GeometryNodeMeshCylinder")
    framed.name = "Framed"
    framed.parent = frame
    for node in hit_tree.nodes:
        # `nodes.new()` leaves every node it creates selected, and selection decides which
        # node is drawn in front - so it has to be cleared before the ordering is tested.
        node.select = False
    boxes = {
        "Under": (0.0, 0.0, 140.0, 100.0),
        "Over": (50.0, 0.0, 140.0, 100.0),        # overlaps Under
        "Frame": (-400.0, 400.0, 1200.0, 1200.0),  # covers everything
        "Framed": (200.0, -200.0, 140.0, 100.0),
    }
    real_extent, real_is_drawn = mmb_hit_test._node_extent, mmb_hit_test._is_drawn
    try:
        mmb_hit_test._node_extent = lambda node: boxes[node.name]
        mmb_hit_test._is_drawn = lambda node: True
        check("a frame alone is never the hit", mmb_hit_test._node_at(hit_tree, -300.0, 300.0), None)
        check(
            "the later (topmost) node wins where two overlap",
            getattr(mmb_hit_test._node_at(hit_tree, 100.0, -30.0), "name", None),
            "Over",
        )
        under.select = True
        check(
            "a selected node is in front, like the editor draws it",
            getattr(mmb_hit_test._node_at(hit_tree, 100.0, -30.0), "name", None),
            "Under",
        )
        under.select = False
        check(
            "a node inside a frame is still hit",
            getattr(mmb_hit_test._node_at(hit_tree, 210.0, -210.0), "name", None),
            "Framed",
        )
        mmb_hit_test._is_drawn = lambda node: node.name != "Over"
        check(
            "a node the editor has not drawn yet is skipped",
            getattr(mmb_hit_test._node_at(hit_tree, 100.0, -30.0), "name", None),
            "Under",
        )
    finally:
        mmb_hit_test._node_extent, mmb_hit_test._is_drawn = real_extent, real_is_drawn
        bpy.data.node_groups.remove(hit_tree)

    # ---- the preferences are reachable and complete ----------------------
    # The settings are drawn by `AddonPreferences.draw`, which is where Blender draws an
    # add-on's own preferences - under its entry in the Add-ons list. A separate
    # `bpy.types.Panel` with `bl_space_type = "PREFERENCES"` is the wrong tool: it lands
    # among the global preference sections instead of under the add-on (that is what the
    # user saw), and one that is missing from `_CLASSES` does not exist at all. Both
    # mistakes were made here; these checks are the regression guard for them.
    check_true(
        "the settings are drawn by AddonPreferences.draw",
        callable(getattr(mmb_preferences.PDTMMBPreferences, "draw", None)),
        "AddonPreferences has no draw()",
    )
    check_true(
        "no standalone preferences Panel is registered",
        not hasattr(bpy.types, "PDT_PT_node_info_preferences"),
        "a PREFERENCES-space Panel draws in the wrong place",
    )
    # `AddonPreferences.bl_idname` reports the *module* name, not the class name.
    check(
        "every class the module registers is in _CLASSES",
        sorted(cls.bl_idname for cls in mmb._CLASSES),
        ["pdt.mmb_reset_colors", "pdt.node_info_modal", "pdt_mmb_info"],
    )
    # MMB-7 dropped the pin, so its two helper operators must be gone with it.
    check_true(
        "the pin operators are gone (MMB-7: 不需要 Pin)",
        not hasattr(bpy.types, "PDT_OT_node_info_pin")
        and not hasattr(bpy.types, "PDT_OT_node_info_close"),
        "a pin/close operator is back",
    )
    check_true(
        "nothing in the session state remembers a pin or a drag",
        not any(hasattr(mmb_session._Session(), name) for name in ("pinned", "close_button", "dragging")),
        "pin/drag state is back in _Session",
    )
    # The modal must be findable in Blender's search and in the Keymap editor's operator
    # field: `INTERNAL` hides an operator from both, which is why "search pdt in Keymap"
    # showed nothing and the operator could not be assigned by hand.
    check_true(
        "the modal is not hidden as INTERNAL",
        "INTERNAL" not in mmb_operators.PDT_OT_node_info_modal.bl_options,
        f"options={sorted(mmb_operators.PDT_OT_node_info_modal.bl_options)}",
    )
    check("the modal's UI label", mmb_operators.PDT_OT_node_info_modal.bl_label, "PDT Node Info")
    # MMB-8: no default shortcut, and no operator that writes one - the user assigns the key
    # in Blender's own Keymap editor. This is the regression guard for that decision.
    check_true(
        "no operator binds a shortcut for the user",
        not hasattr(bpy.types, "PDT_OT_node_info_bind_default"),
        "a bind operator is back - MMB-8 says the user owns the keymap",
    )
    check_true(
        "no module of the add-on writes the user keyconfig",
        not any(hasattr(module, "_bind_in_user_keymap") for module in addon_modules()),
        "_bind_in_user_keymap is back",
    )
    check_true(
        "nothing is scheduled at startup that could bind a key",
        not any(hasattr(module, name) for module in addon_modules()
                for name in ("_bind_when_ready", "_on_load_post")),
        "a startup binding retry is back",
    )

    # ...but MMB-8 also asks the user to *find* the operator in the Keymap editor, and that
    # editor's name search only filters the items a keymap already has. An operator bound in
    # no keymap therefore cannot be searched for at all (measured: factory settings, add-on
    # enabled, "node info" found 0 items - the bug the user reported). So the add-on
    # registers exactly **one unbound** item: no key is bound, but the operator becomes
    # findable. `qa/mmb/mmb_keymap_probe.py` is the real-window evidence.
    check(
        "the registered keymap item carries no key",
        (mmb_keymap.KEYMAP_ITEM["type"], mmb_keymap.KEYMAP_ITEM["value"]),
        ("NONE", "PRESS"),
    )
    check("the keymap item goes into the Node Editor keymap", mmb_keymap.KEYMAP_NAME, "Node Editor")
    check_true(
        "the keymap module can register and remove that item",
        callable(getattr(mmb_keymap, "register_addon_keymap", None))
        and callable(getattr(mmb_keymap, "unregister_addon_keymap", None)),
        "register_addon_keymap/unregister_addon_keymap are gone",
    )
    addon_items = [
        item for keymap in mmb_keymap._node_editor_keymaps(mmb_keymap._keyconfig("addon"))
        for item in keymap.keymap_items if item.idname == mmb_constants.MODAL_IDNAME
    ]
    check("the addon keyconfig holds exactly one item for our operator", len(addon_items), 1)
    check_true(
        "that item is unbound",
        all(mmb_keymap.item_is_bound(item) is False for item in addon_items),
        f"{[(item.type, item.value) for item in addon_items]}",
    )
    check(
        "an unbound placeholder is not reported as an assigned shortcut",
        mmb_keymap.binding_is_active(bpy.context),
        False,
    )
    check(
        "registering again does not add a second item",
        (mmb_keymap.register_addon_keymap(), len([
            item for keymap in mmb_keymap._node_editor_keymaps(mmb_keymap._keyconfig("addon"))
            for item in keymap.keymap_items if item.idname == mmb_constants.MODAL_IDNAME
        ])),
        (0, 1),
    )
    # Positive control for the name search: the item the *user* finds is the operator's
    # label, so the reminder must name exactly that.
    check("the reminder is MMB-8's sentence with the operator name", mmb_constants.REMINDER,
          "Assign one shortcut yourself: Preferences>Keymap>PDT Node Info")
    check("the operator's label is what the reminder names",
          mmb_operators.PDT_OT_node_info_modal.bl_label, mmb_constants.OPERATOR_LABEL)
    check_true(
        "the preferences panel prints that reminder",
        "REMINDER" in inspect.getsource(mmb_preferences.PDTMMBPreferences.draw),
        "the panel no longer prints REMINDER",
    )
    check_true(
        "no other shortcut hint is added (MMB-8: no other hint text)",
        not any(hint in inspect.getsource(mmb_preferences.PDTMMBPreferences.draw) for hint in (
            "No shortcut assigned yet", "Shortcut is assigned",
            "an add-on item never reaches dispatch", "Middle Mouse",
        )),
        inspect.getsource(mmb_preferences.PDTMMBPreferences.draw),
    )
    # A *bound* item must still be reported as assigned (that is what the startup log says).
    # Registered next to the placeholder and removed again: `type` cannot be set back to
    # `NONE` on a MOUSE item, so the control is a second item rather than a mutation.
    placeholder_keymap = mmb_keymap._addon_keymap()
    bound_item = placeholder_keymap.keymap_items.new(
        mmb_constants.MODAL_IDNAME, type="MIDDLEMOUSE", value="PRESS"
    )
    bound_item.alt = True
    check("a bound item is reported as an assigned shortcut",
          mmb_keymap.binding_is_active(bpy.context), True)
    placeholder_keymap.keymap_items.remove(bound_item)
    check("the unbound placeholder alone is not an assigned shortcut",
          mmb_keymap.binding_is_active(bpy.context), False)

    # MMB-8 allows four kinds of setting: the input-only toggle, the text-size slider, one
    # colour per domain sub-class, and the attribute colours. Everything else is a module
    # constant, so this check is deliberately an *equality*: a new preference is a spec change.
    preferences = mmb_preferences.PDTMMBPreferences
    pref_ids = {
        prop.identifier for prop in preferences.bl_rna.properties if not prop.is_readonly
    } - {"rna_type", "name", "bl_idname", "bl_rna"}
    colour_prefs = sorted(f"color_{slot}" for slot in mmb_constants.DEFAULT_COLORS)
    check(
        "the add-on offers exactly MMB-8's settings and nothing else",
        sorted(pref_ids),
        sorted(["show_input_only", "text_scale"] + colour_prefs),
    )
    check("the text scale defaults to 1.0", settings.text_scale, 1.0)
    check("input-only nodes are hidden by default", settings.show_input_only, False)
    check(
        "there is one colour preference per domain sub-class plus the two attribute colours",
        (len(mmb_constants.DOMAIN_COLOR_SLOTS), len(mmb_constants.DEFAULT_COLORS)),
        (9, 11),
    )
    check_true(
        "every default colour has a preference and vice versa",
        all(f"color_{slot}" in pref_ids for slot in mmb_constants.DEFAULT_COLORS)
        and set(colour_prefs) == {name for name in pref_ids if name.startswith("color_")},
        f"prefs={sorted(name for name in pref_ids if name.startswith('color_'))}",
    )
    check(
        "the colour preferences are colour pickers of three floats",
        sorted({
            (prop.subtype, prop.type, getattr(prop, "array_length", 0))
            for prop in preferences.bl_rna.properties
            if prop.identifier.startswith("color_")
        }),
        [("COLOR", "FLOAT", 3)],
    )
    check(
        "every colour defaults to the palette",
        {slot: tuple(round(value, 4) for value in getattr(settings, f"color_{slot}"))
         for slot in mmb_constants.DEFAULT_COLORS},
        {slot: tuple(round(value, 4) for value in rgb) for slot, rgb in mmb_constants.DEFAULT_COLORS.items()},
    )
    check_true(
        "the palette has no duplicate colours",
        len({rgb for rgb in mmb_constants.DEFAULT_COLORS.values()}) == len(mmb_constants.DEFAULT_COLORS),
        f"colours={sorted(mmb_constants.DEFAULT_COLORS.values())}",
    )
    check_true(
        "the domain slots and the palette agree",
        sorted(mmb_constants.DOMAIN_COLOR_SLOTS) == sorted(
            slot for slot in mmb_constants.DEFAULT_COLORS if slot not in ("attribute", "added")
        ),
        f"slots={sorted(mmb_constants.DOMAIN_COLOR_SLOTS)}",
    )
    # A colour the user changed must reach the palette, and the reset operator must put it
    # back - otherwise the pickers would be decorative.
    settings.color_vertex = (1.0, 0.0, 0.0)
    check(
        "a changed preference reaches the palette",
        mmb_palette.palette_from_settings(settings)["vertex"][:3],
        (1.0, 0.0, 0.0),
    )
    bpy.ops.pdt.mmb_reset_colors()
    check(
        "the reset operator restores the default palette",
        tuple(round(channel, 4) for channel in mmb_palette.palette_from_settings(settings)["vertex"][:3]),
        tuple(round(channel, 4) for channel in mmb_constants.DEFAULT_COLORS["vertex"]),
    )
    check_true(
        "the palette never returns a malformed entry",
        all(len(rgba) == 4 and all(0.0 <= channel <= 1.0 for channel in rgba)
            for rgba in mmb_palette.palette_from_settings(settings).values()),
        f"palette={mmb_palette.palette_from_settings(settings)}",
    )
    # The values that used to be switches are constants now; assert the ones the code reads.
    check("the body font is a constant", mmb_constants.BASE_FONT, 12)
    check("the wrap width is a constant", mmb_constants.MAX_TEXT_WIDTH, 420)
    check("the per-domain attribute cap is a constant", mmb_constants.MAX_ITEMS_PER_GROUP, 24)
    check("the block offset is a constant", mmb_constants.CURSOR_OFFSET, 18)
    check("console diagnostics are off by default", mmb_constants.VERBOSE_DIAGNOSTICS, False)
    # MMB-5 removed the bounds and the coordinate hash: nothing in the add-on may compute one.
    for gone in ("COMPUTE_HASH", "HASH_VERTEX_BUDGET", "_hash_coordinates",
                 "_bounds_of_coordinates", "_element_coordinates", "MAX_ROWS", "PIN_KEY",
                 "PAN_THRESHOLD_PX", "_COLOR_CHANGED", "_COLOR_TITLE", "_COLOR_TEXT"):
        check_true(f"'{gone}' is gone with the L0 rewrite",
                   not any(hasattr(module, gone) for module in addon_modules()),
                   f"{gone} is back")

    # `draw` itself cannot be called from Python: it needs a real `UILayout`, which cannot
    # be constructed. What is checkable here is that the struct owns every switch the draw
    # body asks for - a typo would raise inside Blender's UI code instead.
    check_true(
        "the preference struct owns every switch the draw body uses",
        {"show_input_only", "text_scale"} <= pref_ids
        and all(mmb_palette.color_pref_name(slot) in pref_ids for slot in mmb_constants.DEFAULT_COLORS),
        f"missing={sorted({'show_input_only', 'text_scale'} - pref_ids)}",
    )
    check_true(
        "the colour group is collapsible and has a reset button",
        "pdt_mmb_info_colors" in inspect.getsource(mmb_preferences.PDTMMBPreferences.draw)
        and "pdt.mmb_reset_colors" in inspect.getsource(mmb_preferences.PDTMMBPreferences.draw),
        "the draw body no longer builds the collapsible colour group",
    )
    check_true(
        "the keymap module can report the shortcut status (read-only)",
        callable(getattr(mmb_keymap, "binding_is_active", None)),
        "binding_is_active is gone - the startup log cannot report it",
    )
    # The split itself is a promise to whoever reads the add-on: one file per concern, and
    # `__init__.py` only carries `bl_info` + registration. Both halves are asserted here so
    # a module quietly disappearing (or being merged back) shows up as a failure.
    #
    # The *display name* in `bl_info` belongs to the user (they renamed it to "PDT Node
    # Info"), so what is pinned here are the fields Blender needs to stay compatible - a
    # hardcoded name would just have to be edited again on the next rename.
    check_true(
        "bl_info is intact enough for the Add-ons list",
        bool(mmb.bl_info.get("name"))
        and mmb.bl_info.get("blender") == (5, 2, 0)
        and mmb.bl_info.get("category") == "Node"
        and isinstance(mmb.bl_info.get("version"), tuple),
        f"{mmb.bl_info}",
    )
    expected_modules = (
        "pdt_mmb_info.preferences", "pdt_mmb_info.interface", "pdt_mmb_info.keymap",
        "pdt_mmb_info.session", "pdt_mmb_info.hit_test", "pdt_mmb_info.geometry",
        "pdt_mmb_info.evaluate", "pdt_mmb_info.snapshot",
        "pdt_mmb_info.operators.node_info",
        "pdt_mmb_info.utils.constants", "pdt_mmb_info.utils.palette", "pdt_mmb_info.utils.log",
    )
    check("the modules the file map promises are the ones that exist",
          [name for name in expected_modules if name not in sys.modules], [])
    check(
        "the package root defines register/unregister and nothing else",
        sorted(name for name, value in vars(mmb).items()
               if inspect.isfunction(value) and getattr(value, "__module__", "") == "pdt_mmb_info"),
        ["register", "unregister"],
    )
    check_true(
        "the package root stays a map, not code",
        len(inspect.getsource(mmb).splitlines()) < 80,
        f"{len(inspect.getsource(mmb).splitlines())} lines in __init__.py",
    )

    # ------------------------------------------- decoupled from the picker add-on
    # Reading geometry is this add-on's own job now (`evaluate.py`): the block must come out
    # identical with `pdt_geo_select` absent, because that is the state a user can be in -
    # the picker disabled, or not shipped at all. Blocking the import is the honest
    # simulation: a module that is None in `sys.modules` raises ImportError on the next
    # `import pdt_geo_select`.
    check_true(
        "no module of the add-on imports the picker add-on",
        not any(imports_picker(module) for module in addon_modules()),
        f"{[m.__name__ for m in addon_modules() if imports_picker(m)]}",
    )
    blocked_node = tree.nodes["Subdivide"]
    baseline = mmb_snapshot.build_panel_state(bpy.context, blocked_node)
    saved_module = sys.modules.get("pdt_geo_select")
    sys.modules["pdt_geo_select"] = None
    try:
        without_picker = mmb_snapshot.build_panel_state(bpy.context, blocked_node)
    finally:
        if saved_module is None:
            del sys.modules["pdt_geo_select"]
        else:
            sys.modules["pdt_geo_select"] = saved_module
    check("with the picker blocked: same reason line", without_picker["reason"], baseline["reason"])
    check("with the picker blocked: same element counts",
          without_picker["components"], baseline["components"])
    check("with the picker blocked: same attribute groups",
          without_picker["attribute_groups"], baseline["attribute_groups"])
    check("with the picker blocked: the same attributes are marked as added",
          without_picker["added"], baseline["added"])
    check_true(
        "with the picker blocked: there is something to compare",
        bool(baseline["components"]) and any(entry["count"] for entry in baseline["components"][0]["counts"]),
        f"components={baseline['components']}",
    )

    mmb.unregister()
    check_true("unregister clears the draw handler", mmb_session._DRAW_HANDLE is None)
    mmb.register()

    print(f"== test_mmb_node_info: {CHECKS} checks, {len(FAILURES)} failure(s) ==", flush=True)
    if FAILURES:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(1)
