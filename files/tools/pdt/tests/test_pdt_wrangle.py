"""PDT Wrangle acceptance: socket contract, persistence, and the M1 engine.

    blender.exe -b --factory-startup --python-exit-code 1 --python <this file>

**Status of this file**: the L0 spec is `spec/pdt_wrangle.md` (`code-passthrough`…`menu-value-compat`, user-written);
this file is the node-level half of its acceptance (the other half is the standalone engine
self-test, named in that spec's 验收). Detail norms live in
`docs/manual/nodes/pdt_wrangle.md`; the plan and the log are `docs/13-Wrangle节点可行性调研.md`
and `docs/14-Wrangle节点实施记录.md`.

Groups (each line names the `WR-*` item it pins):
  W-1  the node exists with exactly these sockets: Geometry in, Run Over (menu),
       Code (string), Geometry out
  W-2  Run Over offers nine element kinds; Python addresses them by *label*
  W-3  the code round-trips byte for byte; a very long code is clamped (socket DNA buffer)
  W-4  code + Run Over survive save -> reload, and the node does not come back Undefined
       (a channel list rides along: names, types and values)
  W-5  channels are sockets: `node.channels.new(...)` declares a named value socket of that type;
       only float / int / vector are channel types (channel-set)
  W-6  renaming a channel keeps its value, changing its type resets it (no conversion), a duplicate
       name is made unique, and removing the item removes its socket (channel-set, channel-name-unique)
  W-7  a channel nothing reads changes nothing, and no channels is exactly the old behaviour (channel-set)
  E-1  Run Over = Points: arithmetic on @P, per element (at-read, at-p-scope)
  E-2  writing an unknown attribute creates it with the prefix's type (bounded-execution);
       @elemnum-based writes and the 2^24 boundary of run-over-kinds
  E-3  reading attributes that already exist, with a prefix (bounded-execution, at-operator, at-p-scope)
  E-4  @elemnum / @numelem (at-write)
  E-5  control flow and the math library (run-over-kinds)
  E-6  Run Over = Face Corners: corner attributes, and @P is refused there (at-read, element-numbering)
  E-7  a point cloud input runs on its points (at-read)
  E-8  a Run Over kind the stream has no component for reports and passes through (menu-value-compat)
  E-9  code that does not compile reports and leaves empty geometry (code-error-output)
  E-10 a runaway loop is stopped by the step budget and writes nothing (type-conversion)
  E-11 empty / comment-only / declaration-only code is a pass-through (code-passthrough)
  E-12 evaluation is deterministic and thread-independent (code-error-output)
  E-13 a bare attribute name is refused (bounded-execution, one-type-per-attribute)
  E-14 one attribute cannot have two types (position-alias, one-type-per-attribute)
  E-15 an incompatible stored type is refused (at-operator, one-type-per-attribute)
  E-16 Run Over = Edges: an EDGE-domain attribute, one value per edge (at-read)
  E-17 Run Over = Faces: a FACE-domain attribute (at-read)
  E-18 Edges / Faces / Splines have no @P at all (set-prim-attrib)
  E-19 a curve: Points moves the control points and writes their POINT attributes; Splines
       writes the CURVE domain instead (at-read)
  E-20 a stream that carries a mesh *and* a point cloud runs over both (set-point-attrib)
  E-21 Detail runs once but refuses every attribute (at-read, set-prim-attrib)
  E-22 Instances: `@P` is the instance translation, and `@elemnum` counts instances (set-prim-attrib)
  E-23 a narrowed Selection writes only the selected elements (selection-scope)
  E-24 `@elemnum` / `@numelem` ignore the selection (element-numbering)
  E-25 an empty selection runs nothing and creates no attribute (selection-scope)
  E-26 an unwired Selection means every element (selection-scope)
  E-27 a boolean attribute acts as a group (selection-scope)
  E-28 Detail ignores a wired Selection (selection-scope)
  E-29 `@opinput1_*` reads the other input's attribute, and keeps the `P` alias (geometry-inputs/30)
  E-30 an index the other input does not have reads as 0 (indexed-read-silent)
  E-31 only the same component type is read (geometry-inputs)
  E-32 an unconnected input reads as 0 (geometry-inputs)
  E-33 a cross-stream stored-type mismatch is a node error and leaves empty geometry (code-error-output)
  E-34 another input is read-only (geometry-inputs)
  E-35 fpoint reads the element the literal index names (point-read-domain)
  E-36 the index may be the current element, or any expression (point-read-domain)
  E-37 vpoint reads a vector by index (point-read-domain)
  E-38 an index past the end reads 0 (indexed-read-silent)
  E-39 a missing attribute reads 0 and is not created (indexed-read-silent)
  E-40 npoints / nprimitives see the point and face tables (point-read-domain/prim-read-domain)
  E-41 the tables follow the component, not Run Over (point-read-domain)
  E-42 a point cloud reports points and no prims (prim-read-domain)
  E-43 an instance set reports instances and no prims (prim-read-domain)
  E-44 a curve's prim table is its splines (prim-read-domain)
  E-45 fpoint reads another stream by index, and past its end reads 0 (geometry-inputs/indexed-read-silent)
  E-46 an unconnected stream reads 0 (geometry-inputs/indexed-read-silent)
  E-47 a stored-type mismatch is a node error and leaves empty geometry (code-error-output)
  E-48 the explicit reads are read-only (point-read-domain)
  E-49 Detail accepts the explicit reads (at-read/point-read-domain)

The case numbers are the tests' own identity and are not renumbered when a case changes: several
checks may share one number, and E-75..E-79 are unused (they belonged to the M6 era and were never
written). L0 references are **slugs**, never `WR-nn` labels - the L0 has been renumbered more than
once, so a label can point at an unrelated item; `spec/coverage.md` is the label-to-slug mapping and
its snapshot sections record what a label meant at a given time.

The engine's own error *messages* are asserted in `wrangle_engine_selftest.cpp`
(fast, standalone); this file asserts the node-level behaviour.
"""

from __future__ import annotations

import math
import os

import bpy
import mathutils

NODE = "GeometryNodePDTWrangle"
RUN_OVER_LABELS = ["Vertex", "Edges", "Faces", "Face Corners", "Control Point", "Splines",
                   "Point", "Instances", "Detail"]

#: `bNodeSocketValueString.value` is `char[1024]` in DNA, so a String socket caps the
#: code at 1023 bytes + NUL.
CODE_CAP = 1024

CHECKS = 0
FAILURES: list[str] = []


def scratch_dir() -> str:
    override = os.environ.get("PDT_WRANGLE_SCRATCH")
    if override:
        return override
    return os.path.join(os.path.dirname(bpy.app.binary_path), "wrangle_scratch")


def check(label: str, actual, expected) -> None:
    global CHECKS
    CHECKS += 1
    ok = actual == expected
    print(f"{'OK  ' if ok else 'FAIL'} {label}" + ("" if ok else f"   got {actual!r}, want {expected!r}"))
    if not ok:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    check(label + (f" [{detail}]" if detail else ""), bool(condition), True)


def check_close(label: str, actual, expected, eps: float = 1e-4) -> None:
    global CHECKS
    CHECKS += 1
    detail = ""
    ok = actual is not None and expected is not None and len(actual) == len(expected)
    if not ok:
        detail = f"   length {None if actual is None else len(actual)} vs {None if expected is None else len(expected)}"
    else:
        for index, (a, e) in enumerate(zip(actual, expected)):
            if abs(a - e) > eps:
                ok = False
                detail = f"   first difference at [{index}]: got {a!r}, want {e!r}"
                break
    print(f"{'OK  ' if ok else 'FAIL'} {label}{detail}")
    if not ok:
        FAILURES.append(label)


# --------------------------------------------------------------------------- scene

#: (accessor name, components per element) per Blender attribute data type.
ACCESSORS = {
    "FLOAT": ("value", 1),
    "INT": ("value", 1),
    "BOOLEAN": ("value", 1),
    "FLOAT2": ("vector", 2),
    "FLOAT_VECTOR": ("vector", 3),
    "FLOAT_COLOR": ("color", 4),
    # Both transform types expose only `value` in this build (measured: no `quaternion` accessor
    # on a quat element and no `matrix` on a FLOAT4X4 one). `value` is the raw float list -
    # (w, x, y, z) for a rotation, sixteen column-major floats for a matrix.
    "QUATERNION": ("value", 4),
    "FLOAT4X4": ("value", 16),
}


def make_cube(name: str, y_offset: float = 0.0) -> bpy.types.Mesh:
    """A cube with four point attributes and one corner attribute, for read tests."""
    mesh = bpy.data.meshes.new(name)
    coords = [
        (-1, -1, -1 + y_offset), (1, -1, -1 + y_offset), (1, 1, -1 + y_offset), (-1, 1, -1 + y_offset),
        (-1, -1, 1 + y_offset), (1, -1, 1 + y_offset), (1, 1, 1 + y_offset), (-1, 1, 1 + y_offset),
    ]
    faces = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    mesh.from_pydata(coords, [], faces)
    mesh.update()
    weight = mesh.attributes.new("pdt_weight", "FLOAT", "POINT")
    weight.data.foreach_set("value", [i * 0.25 for i in range(8)])
    tag = mesh.attributes.new("pdt_tag", "INT", "POINT")
    tag.data.foreach_set("value", [100 + i for i in range(8)])
    direction = mesh.attributes.new("pdt_dir", "FLOAT_VECTOR", "POINT")
    direction.data.foreach_set("vector", [v for i in range(8) for v in (1.0, 0.0, 0.0)])
    # A boolean attribute is what Blender calls a "group" (selection-scope): points 0..3 are in it.
    group = mesh.attributes.new("pdt_group", "BOOLEAN", "POINT")
    group.data.foreach_set("value", [i < 4 for i in range(8)])
    corner = mesh.attributes.new("pdt_corner", "FLOAT", "CORNER")
    corner.data.foreach_set("value", [i * 0.5 for i in range(24)])
    return mesh


def base_positions(mesh: bpy.types.Mesh) -> list[tuple[float, float, float]]:
    """The source mesh's vertex coordinates, read straight from the data-block.

    Expectations are derived from this rather than hand-written: a hand-rolled list of
    cube coordinates is exactly the kind of thing that looks right and is off by one
    vertex (it was, in the first version of this file).
    """
    buf = [0.0] * (len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", buf)
    return [tuple(buf[i:i + 3]) for i in range(0, len(buf), 3)]


def flat(positions) -> tuple:
    return tuple(component for point in positions for component in point)


def make_grid(name: str, side: int = 32) -> bpy.types.Mesh:
    """A `side` x `side` grid of vertices (no faces) with the cube fixture's `pdt_weight` on it.

    For the `ordered-parallelism` scale checks: 1024 elements is enough that a wrong merge order
    would pick a different winner than the last element.
    """
    mesh = bpy.data.meshes.new(name)
    coords = [(float(x), float(y), 0.0) for y in range(side) for x in range(side)]
    mesh.from_pydata(coords, [], [])
    mesh.update()
    weight = mesh.attributes.new("pdt_weight", "FLOAT", "POINT")
    weight.data.foreach_set("value", [0.0] * (side * side))
    return mesh


def make_curve(name: str, splines: int = 2, points: int = 3) -> bpy.types.Curve:
    """A POLY curve whose tessellation is exactly its control points (measured)."""
    curve = bpy.data.curves.new(name, type="CURVE")
    curve.dimensions = "3D"
    for spline_index in range(splines):
        spline = curve.splines.new("POLY")
        spline.points.add(points - 1)
        for point_index in range(points):
            spline.points[point_index].co = (float(point_index), float(spline_index), 0.0, 1.0)
    return curve


def curve_positions(curve: bpy.types.Curve) -> list[tuple[float, float, float]]:
    """Control points, spline by spline - the order `to_mesh()` gives them back in."""
    return [(float(point.co[0]), float(point.co[1]), float(point.co[2]))
            for spline in curve.splines for point in spline.points]


def make_tree(name: str,
              use_wrangle: bool,
              geometry_source: str = "group_input",
              to_mesh_output: bool = False,
              points_to_verts: bool = False,
              selection: str | None = None,
              other_input: str | None = None):
    """`geometry_source` -> [PDT Wrangle] -> [Points to Vertices] -> Group Output.

    `selection` wires a field into the Wrangle's `Selection` socket (selection-scope):
    "index_lt4" / "index_ge4" / "never" / "always" / "group" (a named boolean attribute).
    `other_input` wires its `Input 1` (geometry-inputs): "same" = the same geometry as input 0 (so a
    read must come back identical), "grid4" = a 2x2 grid with `opw = index * 10`, "points" =
    a point cloud (to test the same-component-type rule, ordered-parallelism), "badtype" = a FLOAT_VECTOR
    attribute named `ovec` (to test the cross-stream type mismatch, explicit-index reads).
    """
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_out = tree.nodes.new("NodeGroupOutput")
    group_out.location = (600, 0)

    if geometry_source == "group_input":
        source = tree.nodes.new("NodeGroupInput")
        source.location = (-600, 0)
        stream = source.outputs["Geometry"]
    elif geometry_source == "m2p":
        # Cube -> Mesh to Points: the wrangle then sees a point cloud.
        cube = tree.nodes.new("GeometryNodeMeshCube")
        cube.location = (-800, 0)
        to_points = tree.nodes.new("GeometryNodeMeshToPoints")
        to_points.location = (-600, 0)
        tree.links.new(cube.outputs["Mesh"], to_points.inputs["Mesh"])
        stream = to_points.outputs["Points"]
    elif geometry_source == "instances":
        # Cube -> Mesh to Points -> Instance on Points: 8 instances of a unit cube, so the
        # only component in the stream is an instance set.
        cube = tree.nodes.new("GeometryNodeMeshCube")
        cube.location = (-1200, 0)
        to_points = tree.nodes.new("GeometryNodeMeshToPoints")
        to_points.mode = "VERTICES"
        to_points.location = (-1000, 0)
        instance_cube = tree.nodes.new("GeometryNodeMeshCube")
        instance_cube.location = (-1000, -250)
        on_points = tree.nodes.new("GeometryNodeInstanceOnPoints")
        on_points.location = (-800, 0)
        tree.links.new(cube.outputs["Mesh"], to_points.inputs["Mesh"])
        tree.links.new(to_points.outputs["Points"], on_points.inputs["Points"])
        tree.links.new(instance_cube.outputs["Mesh"], on_points.inputs["Instance"])
        stream = on_points.outputs["Instances"]
    else:
        # Cube -> Join(Cube, Mesh to Points(Cube)): one stream carrying a mesh *and* a
        # point cloud - what set-point-attrib (run over every matching component) is about.
        cube = tree.nodes.new("GeometryNodeMeshCube")
        cube.location = (-1000, 0)
        to_points = tree.nodes.new("GeometryNodeMeshToPoints")
        to_points.mode = "VERTICES"
        to_points.location = (-800, -200)
        join = tree.nodes.new("GeometryNodeJoinGeometry")
        join.location = (-600, 0)
        tree.links.new(cube.outputs["Mesh"], to_points.inputs["Mesh"])
        tree.links.new(cube.outputs["Mesh"], join.inputs["Geometry"])
        tree.links.new(to_points.outputs["Points"], join.inputs["Geometry"])
        stream = join.outputs["Geometry"]

    source_stream = stream
    wrangle = None
    if use_wrangle:
        wrangle = tree.nodes.new(NODE)
        wrangle.location = (-200, 0)
        tree.links.new(stream, wrangle.inputs["Geometry"])
        stream = wrangle.outputs["Geometry"]
        if selection is not None:
            index = tree.nodes.new("GeometryNodeInputIndex")
            index.location = (-500, -250)
            compare = tree.nodes.new("FunctionNodeCompare")
            compare.location = (-350, -250)
            compare.data_type = "INT"
            if selection == "group":
                named = tree.nodes.new("GeometryNodeInputNamedAttribute")
                named.location = (-500, -250)
                named.data_type = "BOOLEAN"
                named.inputs["Name"].default_value = "pdt_group"
                tree.links.new(named.outputs["Attribute"], wrangle.inputs["Selection"])
            else:
                operations = {"index_lt4": ("LESS_THAN", 4),
                              "index_ge4": ("GREATER_EQUAL", 4),
                              "never": ("GREATER_THAN", 1000000),
                              "always": ("GREATER_EQUAL", 0)}
                operation, bound = operations[selection]
                compare.operation = operation
                compare.inputs[1].default_value = bound
                tree.links.new(index.outputs["Index"], compare.inputs[0])
                tree.links.new(compare.outputs["Result"], wrangle.inputs["Selection"])
        if other_input is not None:
            if other_input == "same":
                tree.links.new(source_stream, wrangle.inputs["Input 1"])
            elif other_input == "grid4":
                grid = tree.nodes.new("GeometryNodeMeshGrid")
                grid.location = (-750, -450)
                grid.inputs["Vertices X"].default_value = 2
                grid.inputs["Vertices Y"].default_value = 2
                store = tree.nodes.new("GeometryNodeStoreNamedAttribute")
                store.location = (-500, -450)
                store.data_type = "FLOAT"
                store.domain = "POINT"
                store.inputs["Name"].default_value = "opw"
                index = tree.nodes.new("GeometryNodeInputIndex")
                index.location = (-750, -650)
                multiply = tree.nodes.new("ShaderNodeMath")
                multiply.operation = "MULTIPLY"
                multiply.inputs[1].default_value = 10.0
                multiply.location = (-620, -650)
                tree.links.new(index.outputs["Index"], multiply.inputs[0])
                tree.links.new(multiply.outputs[0], store.inputs["Value"])
                tree.links.new(grid.outputs["Mesh"], store.inputs["Geometry"])
                tree.links.new(store.outputs["Geometry"], wrangle.inputs["Input 1"])
            elif other_input == "points":
                cube = tree.nodes.new("GeometryNodeMeshCube")
                cube.location = (-700, -450)
                to_points = tree.nodes.new("GeometryNodeMeshToPoints")
                to_points.mode = "VERTICES"
                to_points.location = (-500, -450)
                tree.links.new(cube.outputs["Mesh"], to_points.inputs["Mesh"])
                tree.links.new(to_points.outputs["Points"], wrangle.inputs["Input 1"])
            else:  # "badtype"
                cube = tree.nodes.new("GeometryNodeMeshCube")
                cube.location = (-700, -450)
                store = tree.nodes.new("GeometryNodeStoreNamedAttribute")
                store.location = (-500, -450)
                store.data_type = "FLOAT_VECTOR"
                store.domain = "POINT"
                store.inputs["Name"].default_value = "ovec"
                tree.links.new(cube.outputs["Mesh"], store.inputs["Geometry"])
                tree.links.new(store.outputs["Geometry"], wrangle.inputs["Input 1"])

    if geometry_source == "instances":
        # Instances are invisible to to_mesh(), so realize them: the instance translations
        # then show up as the positions of the realized copies (set-prim-attrib).
        realize = tree.nodes.new("GeometryNodeRealizeInstances")
        realize.location = (300, 0)
        tree.links.new(stream, realize.inputs["Geometry"])
        stream = realize.outputs["Geometry"]
    elif geometry_source == "join":
        # The joined stream carries a mesh AND a point cloud. `to_mesh()` renders only mesh
        # components, and `Points to Vertices` drops the mesh one (both measured in
        # qa/wrangle/wrangle_domain_probe2.py), so split them apart, convert the points and
        # join the two meshes back together - then a plain to_mesh() shows both halves.
        separate = tree.nodes.new("GeometryNodeSeparateComponents")
        separate.location = (150, 0)
        to_verts = tree.nodes.new("GeometryNodePointsToVertices")
        to_verts.location = (300, -200)
        rejoin = tree.nodes.new("GeometryNodeJoinGeometry")
        rejoin.location = (450, 0)
        tree.links.new(stream, separate.inputs["Geometry"])
        tree.links.new(separate.outputs["Point Cloud"], to_verts.inputs["Points"])
        tree.links.new(separate.outputs["Mesh"], rejoin.inputs["Geometry"])
        tree.links.new(to_verts.outputs["Mesh"], rejoin.inputs["Geometry"])
        stream = rejoin.outputs["Geometry"]
    elif points_to_verts:
        # add-point: a point cloud this evaluation created has no mesh to read back, and
        # `Points to Vertices` drops the mesh component (measured), so the counts below are the
        # point cloud's own.
        converter = tree.nodes.new("GeometryNodePointsToVertices")
        converter.location = (300, 0)
        tree.links.new(stream, converter.inputs["Points"])
        stream = converter.outputs["Mesh"]
    elif to_mesh_output:
        # A point cloud has no mesh to read back, so convert after the wrangle; the
        # attributes survive the conversion.
        converter = tree.nodes.new("GeometryNodePointsToVertices")
        converter.location = (300, 0)
        tree.links.new(stream, converter.inputs["Points"])
        stream = converter.outputs["Mesh"]

    tree.links.new(stream, group_out.inputs["Geometry"])
    return tree, wrangle


def snapshot(obj: bpy.types.Object, tree: bpy.types.NodeTree) -> dict:
    """Evaluate `tree` on `obj`; returns element counts and every attribute's values."""
    modifier = obj.modifiers.new("pdt_probe", "NODES")
    modifier.node_group = tree

    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    if mesh is None:
        # `code-error-output`: the node reports and streams **empty geometry**, and `to_mesh()` answers that with
        # None rather than with an empty mesh (measured) - which is what "empty" means here.
        obj.modifiers.remove(modifier)
        return {"vertices": 0, "edges": 0, "polygons": 0, "attributes": {}}

    result: dict = {
        "vertices": len(mesh.vertices),
        "edges": len(mesh.edges),
        "polygons": len(mesh.polygons),
        "attributes": {},
    }
    domain_size = {"POINT": len(mesh.vertices), "CORNER": len(mesh.loops), "EDGE": len(mesh.edges),
                   "FACE": len(mesh.polygons)}
    for attr in mesh.attributes:
        entry = ACCESSORS.get(attr.data_type)
        count = domain_size.get(attr.domain, 0)
        if entry is None or count == 0:
            continue
        accessor, dims = entry
        buf = [0] * (count * dims) if attr.data_type == "INT" else [0.0] * (count * dims)
        attr.data.foreach_get(accessor, buf)
        result["attributes"][(attr.name, attr.domain)] = (attr.data_type, tuple(buf))

    evaluated.to_mesh_clear()
    obj.modifiers.remove(modifier)
    return result


def values(snap: dict, name: str, domain: str = "POINT"):
    entry = snap["attributes"].get((name, domain))
    return entry[1] if entry else None


def types(snap: dict, name: str, domain: str = "POINT"):
    entry = snap["attributes"].get((name, domain))
    return entry[0] if entry else None


def polygon_centres(obj: bpy.types.Object, tree: bpy.types.NodeTree) -> list:
    """Evaluate `tree` on `obj` and return every polygon's centre.

    `snapshot` returns counts and attribute values, which is not enough when the *topology* is the
    subject: a face built on wrong point numbers keeps the count and moves the geometry.
    """
    for modifier in list(obj.modifiers):
        obj.modifiers.remove(modifier)
    modifier = obj.modifiers.new("pdt_faces", "NODES")
    modifier.node_group = tree
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    centres = []
    if mesh is not None:
        centres = [tuple(round(value, 4) for value in polygon.center) for polygon in mesh.polygons]
        evaluated.to_mesh_clear()
    obj.modifiers.remove(modifier)
    return centres


def evaluate(code: str, run_over: str = "Vertex", mesh: bpy.types.Mesh | None = None,
             to_points: bool = False, name: str = "eval", source_kind: str | None = None,
             selection: str | None = None, other_input: str | None = None,
             curve_to_mesh: bool = False, points_to_verts: bool = False,
             channels: list | None = None):
    """Builds the tree, evaluates it, returns (snapshot, object, tree).

    `source_kind` picks where the geometry comes from ("group_input" = the object, "m2p" =
    Cube -> Mesh to Points, "join" = Cube + its points joined); `to_points` is the old
    shorthand for "m2p" plus a Points-to-Vertices converter on the output.
    `channels` adds channels before the code runs: a list of `(name, socket_type, value)`.
    """
    # Always a *copy*: the caller's mesh is a fixture reused by several checks, and a
    # cleanup must never free it.
    source = mesh.copy() if mesh is not None else make_cube(f"{name}_src")
    obj = bpy.data.objects.new(f"{name}_obj", source)
    bpy.context.scene.collection.objects.link(obj)
    kind = source_kind or ("m2p" if to_points else "group_input")
    tree, wrangle = make_tree(name,
                              use_wrangle=True,
                              geometry_source=kind,
                              to_mesh_output=to_points,
                              selection=selection,
                              other_input=other_input,
                              points_to_verts=points_to_verts)
    for channel_name, channel_type, channel_value in (channels or []):
        wrangle.channels.new(socket_type=channel_type, name=channel_name)
        wrangle.inputs[channel_name].default_value = channel_value
    wrangle.inputs["Code"].default_value = code
    wrangle.inputs["Run Over"].default_value = run_over
    if curve_to_mesh:
        # The decisive experiment for add-prim's polyline path (see docs/14 §M5): route the wrangle's
        # output through Curve to Mesh and straight into the Group Output, so a curve the wrangle
        # created shows up as vertices even though to_mesh() alone reports only the mesh component.
        to_curve_mesh = tree.nodes.new("GeometryNodeCurveToMesh")
        to_curve_mesh.location = (0, 0)
        output = next(node for node in tree.nodes if node.bl_idname == "NodeGroupOutput")
        tree.links.new(wrangle.outputs["Geometry"], to_curve_mesh.inputs["Curve"])
        for link in list(output.inputs[0].links):
            tree.links.remove(link)
        tree.links.new(to_curve_mesh.outputs["Mesh"], output.inputs[0])
    snap = snapshot(obj, tree)
    return snap, obj, tree


def check_empty(label: str, snap: dict):
    """`code-error-output`: a program that reports leaves **empty geometry** - no vertices, no polygons, and none
    of the input's attributes either (the input must not leak through)."""
    check(label, (snap["vertices"], snap["polygons"], values(snap, "position"), snap["attributes"]),
          (0, 0, None, {}))


def cleanup(*objects):
    for obj in objects:
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            if isinstance(mesh, bpy.types.Mesh):
                bpy.data.meshes.remove(mesh, do_unlink=True)


# --------------------------------------------------------------------------- main


def main() -> int:
    # ---------------------------------------------------------------- W-1 contract
    check_true("W-1 node type registered", hasattr(bpy.types, NODE))
    tree = bpy.data.node_groups.new("pdt_wrangle_contract", "GeometryNodeTree")
    node = tree.nodes.new(NODE)
    check("W-1 input names", [s.name for s in node.inputs],
          ["Geometry", "Input 1", "Input 2", "Input 3", "Selection", "Run Over", "Code", ""])
    check("W-1 input types", [s.bl_idname for s in node.inputs],
          ["NodeSocketGeometry", "NodeSocketGeometry", "NodeSocketGeometry",
           "NodeSocketGeometry", "NodeSocketBool", "NodeSocketMenu", "NodeSocketString",
           "NodeSocketVirtual"])
    check("W-1 output names", [s.name for s in node.outputs], ["Geometry"])
    check("W-1 output types", [s.bl_idname for s in node.outputs], ["NodeSocketGeometry"])
    check("W-1 ui name", getattr(bpy.types, NODE).bl_rna.name, "PDT Wrangle")

    # ---------------------------------------------------------------- W-2 Run Over
    menu = node.inputs["Run Over"]
    check("W-2 default element kind", menu.default_value, "Vertex")
    for label in RUN_OVER_LABELS:
        menu.default_value = label
        check(f"W-2 accepts label {label!r}", menu.default_value, label)
    try:
        menu.default_value = "VERTEX"
        check("W-2 identifier rejected (labels, not identifiers)", "accepted", "TypeError")
    except TypeError:
        check("W-2 identifier rejected (labels, not identifiers)", "TypeError", "TypeError")
    menu.default_value = "Vertex"

    # ------------------------------------------------------------------- W-3 code
    code = node.inputs["Code"]
    check("W-3 default code is empty", code.default_value, "")
    short = "@P.y += sin(@P.x) * 0.5;\nv@Cd = {1, 0, 0};"
    code.default_value = short
    check("W-3 multi-line code round-trips", code.default_value, short)
    long_text = "// " + "x" * 5000
    code.default_value = long_text
    stored = code.default_value
    check_true("W-3 long code is clamped (not corrupted)", long_text.startswith(stored),
               f"wrote {len(long_text)}, stored {len(stored)}")
    check_true("W-3 clamp matches the socket DNA buffer", len(stored) < CODE_CAP,
               f"stored {len(stored)} < {CODE_CAP}")
    code.default_value = short

    # ------------------------------------------------- W-4 save / reload round trip
    scratch = scratch_dir()
    os.makedirs(scratch, exist_ok=True)
    path = os.path.join(scratch, "pdt_wrangle_persist.blend")

    mesh = make_cube("pdt_wrangle_src")
    obj = bpy.data.objects.new("pdt_wrangle_obj", mesh)
    bpy.context.scene.collection.objects.link(obj)
    saved_tree, saved_node = make_tree("pdt_wrangle_persist_tree", use_wrangle=True)
    # A program that is *valid* on Edges: `@P` does not exist there (at-p-scope), so the old `short`
    # would be a compile error and - since code-error-output empties the output - could not show that the
    # reloaded node still evaluates.
    persist_code = "i@edge_tag = @elemnum;"
    saved_node.inputs["Code"].default_value = persist_code
    saved_node.inputs["Run Over"].default_value = "Edges"
    # `channel-set`'s persistence clause: names, types and values all ride along with the file.
    saved_node.channels.new(socket_type="FLOAT", name="height")
    saved_node.inputs["height"].default_value = 1.75
    saved_node.channels.new(socket_type="VECTOR", name="axis")
    saved_node.inputs["axis"].default_value = (0.0, 1.0, 0.0)
    saved_node.channels.new(socket_type="INT", name="count")
    saved_node.inputs["count"].default_value = 7
    modifier = obj.modifiers.new("wrangle", "NODES")
    modifier.node_group = saved_tree

    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)

    reloaded_obj = bpy.data.objects["pdt_wrangle_obj"]
    reloaded_tree = reloaded_obj.modifiers["wrangle"].node_group
    reloaded_nodes = [n for n in reloaded_tree.nodes if n.bl_idname == NODE]
    check("W-4 wrangle node survives the reload", len(reloaded_nodes), 1)
    if reloaded_nodes:
        reloaded = reloaded_nodes[0]
        check("W-4 code survives the reload", reloaded.inputs["Code"].default_value, persist_code)
        check("W-4 Run Over survives the reload", reloaded.inputs["Run Over"].default_value, "Edges")
        check("W-4 channels survive the reload (channel-set)",
              [(item.name, item.socket_type) for item in reloaded.channels],
              [("height", "FLOAT"), ("axis", "VECTOR"), ("count", "INT")])
        check("W-4 and the channel values come back too (channel-set)",
              (reloaded.inputs["height"].default_value,
               tuple(reloaded.inputs["axis"].default_value),
               reloaded.inputs["count"].default_value),
              (1.75, (0.0, 1.0, 0.0), 7))
        # The reloaded node must still *evaluate*: an unreadable storage/RNA type would come back as
        # an Undefined node, and then the edge attribute the code writes would not be there.
        after = snapshot(reloaded_obj, reloaded_tree)
        check("W-4 reloaded node still evaluates",
              (after["vertices"], after["polygons"], types(after, "edge_tag", "EDGE")),
              (8, 6, "INT"))
    else:
        check("W-4 code survives the reload", "node missing", persist_code)
        check("W-4 Run Over survives the reload", "node missing", "Edges")
        check("W-4 channels survive the reload (channel-set)", "node missing",
              [("height", "FLOAT"), ("axis", "VECTOR"), ("count", "INT")])
        check("W-4 and the channel values come back too (channel-set)", "node missing",
              (1.75, (0.0, 1.0, 0.0), 7))
        check("W-4 reloaded node still evaluates", "node missing", (8, 6, "INT"))

    for leftover in (path, path + "1"):
        if os.path.exists(leftover):
            os.remove(leftover)

    # ------------------------------------------------- W-5 channels: the item list *is* the sockets
    # `channel-set` / `channel-name-unique`: a channel is a named value socket the user adds to the
    # node. The item list is the source of truth - adding an item declares a socket, naming the item
    # names the socket, and the item's type is the socket's type.
    ch_tree = bpy.data.node_groups.new("pdt_wrangle_channels", "GeometryNodeTree")
    ch_node = ch_tree.nodes.new(NODE)
    check("W-5 a fresh node has no channels (channel-set)", len(ch_node.channels), 0)
    check("W-5 and no channel sockets either (channel-set)", [s.name for s in ch_node.inputs],
          ["Geometry", "Input 1", "Input 2", "Input 3", "Selection", "Run Over", "Code", ""])

    item = ch_node.channels.new(socket_type="FLOAT", name="height")
    check_true("W-5 new() hands the item back (channel-set)", item is not None)
    check("W-5 the item carries the name (channel-set)", item.name, "height")
    check("W-5 the item carries the type (channel-set)", item.socket_type, "FLOAT")
    check("W-5 one socket appears, named after the item (channel-set)",
          ch_node.inputs["height"].bl_idname, "NodeSocketFloat")
    ch_node.inputs["height"].default_value = 3.5
    check("W-5 the socket holds the value (channel-set)", ch_node.inputs["height"].default_value, 3.5)

    ch_node.channels.new(socket_type="INT", name="count")
    ch_node.channels.new(socket_type="VECTOR", name="axis")
    check("W-5 three channels in the order they were added (channel-set)",
          [i.name for i in ch_node.channels], ["height", "count", "axis"])
    check("W-5 and their socket types (channel-set)",
          [ch_node.inputs[name].bl_idname for name in ("height", "count", "axis")],
          ["NodeSocketFloat", "NodeSocketInt", "NodeSocketVector"])
    # A refused socket type comes back as the report it is: Blender turns the RPT_ERROR into a
    # RuntimeError for the caller (measured), so the refusal is asserted as one.
    try:
        ch_node.channels.new(socket_type="GEOMETRY", name="bad")
        check("W-5 only float / int / vector are channel types (channel-set)",
              "accepted", "RuntimeError")
    except RuntimeError:
        check("W-5 only float / int / vector are channel types (channel-set)",
              "RuntimeError", "RuntimeError")
    check("W-5 and the refused one did not land (channel-set)", len(ch_node.channels), 3)

    # ---------------------- W-6 rename keeps the value, retype resets it, names stay unique
    ch_node.channels[0].name = "h2"
    check("W-6 renaming the item renames the socket (channel-set)",
          [s.name for s in ch_node.inputs][6:], ["h2", "count", "axis", "Code", ""])
    check("W-6 renaming keeps the value (channel-set)", ch_node.inputs["h2"].default_value, 3.5)
    ch_node.channels[0].socket_type = "INT"
    check("W-6 changing the type changes the socket (channel-set)",
          ch_node.inputs["h2"].bl_idname, "NodeSocketInt")
    check("W-6 and the old value is reset, not converted (channel-set)",
          ch_node.inputs["h2"].default_value, 0)
    check("W-6 the item reports the new type (channel-set)", ch_node.channels[0].socket_type, "INT")

    ch_node.inputs["h2"].default_value = 42
    # The duplicate becomes `h2.001` (measured: the shared helper uses Blender's own `.001` suffix),
    # so the second channel's *socket* is renamed too - and the first one keeps name and value.
    ch_node.channels[1].name = "h2"
    check_true("W-6 a duplicate name is made unique (channel-name-unique)",
               ch_node.channels[1].name == "h2.001", f"became {ch_node.channels[1].name!r}")
    check("W-6 the existing channel keeps its name (channel-name-unique)",
          ch_node.channels[0].name, "h2")
    check("W-6 and keeps its value (channel-name-unique)", ch_node.inputs["h2"].default_value, 42)

    ch_node.channels.remove(ch_node.channels[2])
    check("W-6 removing the item removes its socket (channel-set)",
          [s.name for s in ch_node.inputs],
          ["Geometry", "Input 1", "Input 2", "Input 3", "Selection", "Run Over", "h2", "h2.001",
           "Code", ""])
    while len(ch_node.channels):
        ch_node.channels.remove(ch_node.channels[0])
    check("W-6 removing every channel leaves the plain socket list (channel-set)",
          [s.name for s in ch_node.inputs],
          ["Geometry", "Input 1", "Input 2", "Input 3", "Selection", "Run Over", "Code", ""])

    # ------------------------------- W-7 a channel the code does not read changes nothing
    # `channel-set`: "with no channels the node behaves exactly as before" - and, the other way
    # round, adding one cannot change the geometry by itself (nothing reads it until `chf` does).
    w7_mesh = make_cube("w7_src")
    w7_obj = bpy.data.objects.new("w7_obj", w7_mesh)
    bpy.context.scene.collection.objects.link(w7_obj)
    w7_tree, w7_node = make_tree("w7_tree", use_wrangle=True)
    w7_node.inputs["Code"].default_value = "@P.y += 1;"
    before = snapshot(w7_obj, w7_tree)
    w7_node.channels.new(socket_type="FLOAT", name="height")
    w7_node.inputs["height"].default_value = 2.5
    with_channel = snapshot(w7_obj, w7_tree)
    check("W-7 an unread channel changes nothing (channel-set)",
          values(with_channel, "position"), values(before, "position"))
    w7_node.channels.remove(w7_node.channels[0])
    after = snapshot(w7_obj, w7_tree)
    check("W-7 and taking it away again is the old behaviour (channel-set)",
          values(after, "position"), values(before, "position"))
    cleanup(w7_obj)
    bpy.data.node_groups.remove(ch_tree)
    bpy.data.node_groups.remove(w7_tree)

    # ------------------------------------------------------------------ E-1 .. E-5
    base = make_cube("pdt_m1_src")
    src = base_positions(base)
    src_flat = flat(src)

    snap, obj_a, _tree_a = evaluate("@P.y += 1;", mesh=base, name="e1")
    check_close("E-1 @P.y += 1 (all 8 points)",
                values(snap, "position"), flat([(x, y + 1.0, z) for (x, y, z) in src]))
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("f@mask = @P.y * 2;", mesh=base, name="e2f")
    check("E-2 f@mask creates a FLOAT point attribute", types(snap, "mask"), "FLOAT")
    check_close("E-2 f@mask values", values(snap, "mask"), tuple(y * 2.0 for (_x, y, _z) in src))
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("v@Cd = {1, 0, 0};", mesh=base, name="e2v")
    check("E-2 v@Cd creates a FLOAT_VECTOR point attribute", types(snap, "Cd"), "FLOAT_VECTOR")
    check_close("E-2 v@Cd values", values(snap, "Cd"), (1.0, 0.0, 0.0) * 8)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("i@id = @elemnum * 3;", mesh=base, name="e2i")
    check("E-2 i@id creates an INT point attribute", types(snap, "id"), "INT")
    check("E-2 i@id values", values(snap, "id"), tuple(i * 3 for i in range(8)))
    cleanup(obj_a)

    # run-over-kinds's boundary: exact up to 2^24, and documented as inexact above it.
    snap, obj_a, _tree_a = evaluate("i@big = 16777216; i@over = 16777217;", mesh=base, name="e2big")
    check("E-2 2^24 is exact", values(snap, "big"), (16777216,) * 8)
    check("E-2 above 2^24 is not exact (documented)", values(snap, "over"), (16777216,) * 8)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("f@read_back = f@pdt_weight * 10;", mesh=base, name="e3")
    check_close("E-3 reads an existing FLOAT attribute", values(snap, "read_back"),
                tuple(i * 2.5 for i in range(8)))
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("f@read_back = i@pdt_tag + 1;", mesh=base, name="e3i")
    check_close("E-3 reads an existing INT attribute", values(snap, "read_back"),
                tuple(101.0 + i for i in range(8)))
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("f@t = @elemnum / @numelem;", mesh=base, name="e4")
    check_close("E-4 @elemnum / @numelem", values(snap, "t"), tuple(i / 8.0 for i in range(8)))
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate(
        "float a = @P.x * @P.x;\nfloat b = @P.y * @P.y;\n"
        "if (sqrt(a + b) > 1.4) { f@far = 1; } else { f@far = 0; }",
        mesh=base, name="e5if")
    check("E-5 if/else + sqrt()", values(snap, "far"),
          tuple(1.0 if math.sqrt(x * x + y * y) > 1.4 else 0.0 for (x, y, _z) in src))
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate(
        "float acc = 0;\nfor (int i = 1; i <= 4; i++) { acc += i; }\nf@sum = acc;", mesh=base, name="e5for")
    check_close("E-5 for loop", values(snap, "sum"), (10.0,) * 8)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate(
        "v@n = normalize(@P);\nf@d = dot(v@n, {0, 1, 0});\nf@l = length(@P);\nv@c = cross({1,0,0}, {0,1,0});",
        mesh=base, name="e5math")
    check_close("E-5 dot(normalize(P), Y)", values(snap, "d"), (-0.57735, -0.57735, 0.57735, 0.57735) * 2,
                eps=1e-3)
    check_close("E-5 length(P)", values(snap, "l"), (1.7320508,) * 8, eps=1e-4)
    check_close("E-5 cross(x, y) = z", values(snap, "c"), (0.0, 0.0, 1.0) * 8)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("f@r = lerp(0, 10, 0.25) + clamp(5, 0, 1) + fract(-0.25);",
                                    mesh=base, name="e5lib")
    check_close("E-5 lerp + clamp + fract", values(snap, "r"), (2.5 + 1 + 0.75,) * 8)
    cleanup(obj_a)

    # ---------------------------------------------------------------------- E-6
    snap, obj_a, _tree_a = evaluate("f@corner_mask = f@pdt_corner + 1;", run_over="Face Corners",
                                    mesh=base, name="e6corner")
    check("E-6 a corner attribute was created", types(snap, "corner_mask", "CORNER"), "FLOAT")
    check("E-6 it has one value per face corner", len(values(snap, "corner_mask", "CORNER") or ()), 24)
    check_close("E-6 corner values", values(snap, "corner_mask", "CORNER"),
                tuple(i * 0.5 + 1 for i in range(24)))
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("@P.y += 100;", run_over="Face Corners", mesh=base, name="e6p")
    check_empty("E-6 writing @P over Face Corners is refused, so the output is empty (code-error-output)",
                snap)
    cleanup(obj_a)

    # ---------------------------------------------------------------------- E-7
    snap, obj_a, _tree_a = evaluate("f@from_points = 7;", run_over="Point", to_points=True, name="e7")
    check_close("E-7 a point cloud runs on its points",
                values(snap, "from_points"), (7.0,) * snap["vertices"])
    check_true("E-7 the cube became 8 points", snap["vertices"] == 8, str(snap["vertices"]))
    cleanup(obj_a)

    # ---------------------------------------------------------------------- E-8
    snap, obj_a, _tree_a = evaluate("@P.y += 100;", run_over="Instances", mesh=base, name="e8")
    check_close("E-8 a kind with nothing to run over passes through unchanged (at-p-scope)",
                values(snap, "position"), src_flat)
    cleanup(obj_a)

    # ---------------------------------------------------------------------- E-9
    snap, obj_a, _tree_a = evaluate("@P.y += ;", mesh=base, name="e9")
    check_empty("E-9 a syntax error leaves empty geometry (code-error-output)", snap)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("f@r = nosuchfunction(1);", mesh=base, name="e9b")
    check_true("E-9 an unknown function does not create the attribute",
               values(snap, "r") is None)
    cleanup(obj_a)

    # ---------------------------------------------------------------------- E-10
    snap, obj_a, _tree_a = evaluate("for (float i = 0; i < 1e9; i += 1) { f@x = i; }", mesh=base, name="e10")
    # `bounded-execution`: the element that cannot finish is stopped and reported - and `code-error-output` then empties the
    # output, so the node came back (no hang) and nothing it wrote leaked through.
    check_empty("E-10 the runaway loop was stopped and the output is empty (code-error-output/bounded-execution)", snap)
    cleanup(obj_a)

    # ---------------------------------------------------------------------- E-11
    control, control_obj, _t = evaluate("", mesh=base, name="e11empty")
    for label, text in (("empty", ""), ("comment-only", "// nothing here\n/* nor here */"),
                        ("declaration-only", "float x = 1.0;\nvector v = {1, 2, 3};\nint i = 2;")):
        snap, obj_a, _tree_a = evaluate(text, mesh=base, name=f"e11{label}")
        check(f"E-11 {label} code is a pass-through (vertices)", snap["vertices"], control["vertices"])
        check_close(f"E-11 {label} code leaves positions alone",
                    values(snap, "position"), values(control, "position"))
        check_true(f"E-11 {label} code creates no attribute",
                   set(snap["attributes"]) == set(control["attributes"]),
                   str(sorted(set(snap["attributes"]) ^ set(control["attributes"]))))
        cleanup(obj_a)
    cleanup(control_obj)

    # ---------------------------------------------------------------------- E-12
    first, obj_a, tree_a = evaluate("@P.x += @elemnum * 0.5;\nf@r = sin(@P.y);", mesh=base, name="e12a")
    # Share the *copy* the first evaluation used, never the `base` fixture: `cleanup()`
    # frees an object's mesh once its last user is gone, and `base` must survive to the end.
    second_obj = bpy.data.objects.new("e12b_obj", obj_a.data)
    bpy.context.scene.collection.objects.link(second_obj)
    second = snapshot(second_obj, tree_a)
    check_close("E-12 the same tree evaluates identically twice",
                values(second, "position"), values(first, "position"))
    check_close("E-12 and the same attribute values", values(second, "r"), values(first, "r"))
    cleanup(obj_a, second_obj)

    # ------------------------------------------------------------- E-13 .. E-15
    # Compile errors leave empty geometry (`code-error-output`) and create nothing.
    snap, obj_a, _tree_a = evaluate("f@r = @pdt_weight;", mesh=base, name="e13")
    check_empty("E-13 a bare attribute name is refused, so the output is empty (code-error-output)", snap)
    check_true("E-13 and nothing was created", values(snap, "r") is None)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("f@r = 1.0;\nv@r = {1, 2, 3};", mesh=base, name="e14")
    check_empty("E-14 one attribute cannot have two types, so the output is empty (code-error-output)", snap)
    check_true("E-14 and the attribute was not created", values(snap, "r") is None)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("f@pdt_dir = 1.0;", mesh=base, name="e15")
    check_empty("E-15 an incompatible stored type is refused, so the output is empty (code-error-output)", snap)
    check("E-15 and no attribute was created", types(snap, "pdt_dir"), None)
    cleanup(obj_a)

    # ------------------------------------------- E-16 .. E-20 the rest of the kinds
    snap, obj_a, _tree_a = evaluate("i@edge_tag = @elemnum;", run_over="Edges", mesh=base,
                                    name="e16")
    check("E-16 Edge writes an EDGE-domain attribute (at-read)", types(snap, "edge_tag", "EDGE"),
          "INT")
    check("E-16 one value per edge", values(snap, "edge_tag", "EDGE"), tuple(range(12)))
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("f@face_tag = @elemnum * 2;", run_over="Faces", mesh=base,
                                    name="e17")
    check("E-17 Face writes a FACE-domain attribute (at-read)", types(snap, "face_tag", "FACE"),
          "FLOAT")
    check("E-17 one value per face", values(snap, "face_tag", "FACE"),
          (0.0, 2.0, 4.0, 6.0, 8.0, 10.0))
    cleanup(obj_a)

    for kind in ("Edges", "Faces"):
        snap, obj_a, _tree_a = evaluate("@P.x += 1;", run_over=kind, mesh=base, name=f"e18{kind}")
        check_empty(f"E-18 @P does not exist on {kind}, so the code does not compile (code-error-output/at-p-scope)",
                    snap)
        cleanup(obj_a)

    # A POLY curve tessellates to exactly its control points (measured), so `to_mesh()` is a
    # usable read path for the POINT domain. The CURVE domain has none (bpy.types.Curve has
    # no `.attributes`, and its legacy `Spline.*` RNA does not reflect the storage either),
    # so the Splines checks below pin the *domain* instead: @P is refused, and a write does
    # not land on the points.
    curve = make_curve("pdt_m2_curve")
    curve_src = curve_positions(curve)

    snap, obj_a, _tree_a = evaluate("@P.x += 10;", run_over="Control Point", mesh=curve, name="e19move")
    check("E-19 the curve tessellates to its 6 control points", snap["vertices"], 6)
    check_close("E-19 Points moves the curve's control points", values(snap, "position"),
                flat([(x + 10.0, y, z) for (x, y, z) in curve_src]))
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("f@cp = @elemnum;", run_over="Control Point", mesh=curve, name="e19attr")
    check("E-19 Points writes a POINT attribute on the curve", values(snap, "cp"),
          (0.0, 1.0, 2.0, 3.0, 4.0, 5.0))
    cleanup(obj_a)

    # On a curve the Splines kind *does* find a component, so this is the set-prim-attrib refusal
    # itself - with a mesh input the menu-value-compat "nothing has that domain" error fires first.
    snap, obj_a, _tree_a = evaluate("@P.x += 1;", run_over="Splines", mesh=curve, name="e19p")
    check_empty("E-19 Splines has no @P at all, so the code does not compile (code-error-output/at-p-scope)", snap)
    cleanup(obj_a)

    # ------------------ E-19b point-read-domain: the `domain` word names the component, not the pass
    # Run Over = Control Point over the curve fixture: `vpoint(0, "curve", "P", 4)` reads the
    # curve's *own* control point 4 - a component read that the `@` forms cannot express - and a
    # component this stream does not have (the mesh) reads 0 instead of reporting (`indexed-read-silent`).
    snap, obj_a, _tree_a = evaluate(
        'v@here = vpoint(0, "curve", "P", 4);\nv@none = vpoint(0, "mesh", "P", 0);',
        run_over="Control Point", mesh=curve, name="e19b")
    check_close("E-19b an explicit read names the curve it reads (point-read-domain)",
                values(snap, "here"), flat([curve_src[4]] * 6))
    check_close("E-19b and a component the stream does not have reads 0 (indexed-read-silent)",
                values(snap, "none"), (0.0,) * 18)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("f@only_curve = @elemnum;", run_over="Splines", mesh=curve,
                                    name="e19spline")
    # A CURVE-domain attribute is broadcast to each spline's own control points when the
    # curve is turned into a mesh (measured), so the values say *how many times the code
    # ran*: once per spline (0, 0, 0, 1, 1, 1) and not once per point (0..5).
    check("E-19 Splines runs once per spline, not once per point (at-read)",
          values(snap, "only_curve"), (0.0, 0.0, 0.0, 1.0, 1.0, 1.0))
    check("E-19 and the curve still has its 6 points", snap["vertices"], 6)
    cleanup(obj_a)

    # at-p-scope: one kind addresses *one* component. The stream carries a mesh and a point cloud
    # (16 vertices through the rejoin): `Vertex` must move only the mesh half, `Point` only the
    # point cloud half. The fixture's cube is size 1, so its corners are at +-0.5 and +5 puts
    # them at 4.5 / 5.5; the untouched half stays at +-0.5.
    snap, obj_a, _tree_a = evaluate("@P.y += 5;", name="e20mesh", source_kind="join")
    ys = sorted({values(snap, "position")[i * 3 + 1] for i in range(snap["vertices"])})
    check("E-20 `Vertex` runs on the mesh only (at-p-scope)",
          (snap["vertices"], ys), (16, [-0.5, 0.5, 4.5, 5.5]))
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("@P.y += 5;", run_over="Point", name="e20points",
                                    source_kind="join")
    ys = sorted({values(snap, "position")[i * 3 + 1] for i in range(snap["vertices"])})
    check("E-20 and `Point` runs on the point cloud only (at-p-scope)",
          (snap["vertices"], ys), (16, [-0.5, 0.5, 4.5, 5.5]))
    cleanup(obj_a)

    # ------------------------------------------------------ E-21 Detail (at-read)
    # `Detail` has no per-element context, so `@` does not exist there at all (at-read) - not
    # even `@elemnum`, and the retired `@opinput<n>_` form least of all. Attribute access in Detail
    # goes through the explicit-index functions instead (fpoint(0, "P", 0)).
    for index, code in enumerate(("f@e21 = 1;", "@P.y += 1;", "v@e21 = {1, 2, 3};",
                                  "float a = @elemnum + @numelem;",
                                  "v@r = v@opinput1_P;")):
        snap, obj_a, _tree_a = evaluate(code, run_over="Detail", mesh=base, name=f"e21_{index}")
        check_empty(f"E-21 Detail refuses attributes ({code!r}), so the output is empty (code-error-output)",
                    snap)
        check_true(f"E-21 and creates nothing ({code!r})", values(snap, "e21") is None)
        cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("float a = 1.0;\nvector v = {1, 2, 3};", run_over="Detail",
                                    mesh=base, name="e21locals")
    check_close("E-21 locals-only code still runs once in Detail",
                values(snap, "position"), src_flat)
    cleanup(obj_a)

    # --------------------------------------------------------- E-22 Instances (set-prim-attrib)
    # 8 instances of a unit cube. `Realize Instances` turns them into 8 x 8 = 64 vertices,
    # whose y coordinates say where each instance ended up (set-prim-attrib: `@P` is the translation).
    snap, obj_a, _tree_a = evaluate("@P.y += 5;", run_over="Instances", name="e22move",
                                    source_kind="instances")
    ys = sorted({values(snap, "position")[i * 3 + 1] for i in range(snap["vertices"])})
    check("E-22 @P moves every instance", (snap["vertices"], ys), (64, [4.0, 5.0, 6.0]))
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("@P.y += @elemnum;", run_over="Instances", name="e22num",
                                    source_kind="instances")
    ys = sorted({values(snap, "position")[i * 3 + 1] for i in range(snap["vertices"])})
    check("E-22 @elemnum counts instances, not realized vertices", (snap["vertices"], ys),
          (64, [float(v) for v in range(-1, 9)]))
    cleanup(obj_a)

    # ------------------------------------------- E-23 .. E-28 Selection (selection-scope/27/28)
    # A narrowed selection runs the code only there: the first four points move, the other
    # four keep their input positions exactly.
    snap, obj_a, _tree_a = evaluate("@P.y += 1;", mesh=base, name="e23", selection="index_lt4")
    check_close("E-23 only the selected points are written (add-point)", values(snap, "position"),
                flat([(x, y + 1.0, z) if i < 4 else (x, y, z)
                      for i, (x, y, z) in enumerate(src)]))
    cleanup(obj_a)

    # element-numbering: `@elemnum` stays the real element number and `@numelem` the whole domain size,
    # although only the second half ran. The unselected half keeps 0 - the attribute itself
    # is created for the whole domain, which is how upstream's Selection sockets behave too.
    snap, obj_a, _tree_a = evaluate("i@id = @elemnum; i@n = @numelem;", mesh=base, name="e24",
                                    selection="index_ge4")
    check("E-24 @elemnum is not renumbered by the selection (element-numbering)", values(snap, "id"),
          (0, 0, 0, 0, 4, 5, 6, 7))
    check("E-24 @numelem stays the domain size (element-numbering)", values(snap, "n"),
          (0, 0, 0, 0, 8, 8, 8, 8))
    cleanup(obj_a)

    # An empty selection runs nothing at all - not even the binding, so no attribute appears.
    snap, obj_a, _tree_a = evaluate("f@never = 1;", mesh=base, name="e25", selection="never")
    check_close("E-25 an empty selection changes nothing (selection-scope)", values(snap, "position"),
                src_flat)
    check_true("E-25 and creates no attribute", values(snap, "never") is None)
    cleanup(obj_a)

    # The other half of selection-scope: an unwired Selection means every element. Every other check in
    # this file leaves the socket empty, so this row is their gate.
    snap, obj_a, _tree_a = evaluate("i@id = @elemnum;", mesh=base, name="e26")
    check("E-26 an unwired Selection runs on everything (selection-scope)", values(snap, "id"),
          tuple(range(8)))
    cleanup(obj_a)

    # A named boolean attribute is Blender's "group", so no extra input is needed for the
    # Houdini group workflow: `pdt_group` marks points 0..3 (selection-scope).
    snap, obj_a, _tree_a = evaluate("@P.z += 1;", mesh=base, name="e27", selection="group")
    check_close("E-27 a boolean attribute acts as a group (selection-scope)", values(snap, "position"),
                flat([(x, y, z + 1.0) if i < 4 else (x, y, z)
                      for i, (x, y, z) in enumerate(src)]))
    cleanup(obj_a)

    # selection-scope: Detail has no domain to evaluate the field on, so a wired Selection is ignored.
    snap, obj_a, _tree_a = evaluate("float a = 1.0;", run_over="Detail", mesh=base,
                                    name="e28", selection="never")
    check_close("E-28 Detail ignores a wired Selection (selection-scope)", values(snap, "position"),
                src_flat)
    cleanup(obj_a)

    # ---------------------------------- E-29 cross-stream `@opinput` is retired (明确不做)
    # The L0 keeps the four geometry inputs but not this syntax; the legal way to read another
    # input is the explicit-index family with geo 1..3, covered by E-47 below. A compile error
    # means the geometry passes through and nothing is created.
    snap, obj_a, _tree_a = evaluate("f@copy = f@opinput1_pdt_weight;", mesh=base, name="e29",
                                    other_input="same")
    check_empty("E-29 `@opinput1_` is refused, so the output is empty (code-error-output)", snap)
    check_true("E-29 and creates nothing", values(snap, "copy") is None)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("v@there = v@opinput1_P;", mesh=base, name="e29b",
                                    other_input="same")
    check_empty("E-29 writing to another input through the old syntax is refused too (code-error-output)", snap)
    cleanup(obj_a)

    # ------------------------------- E-35 .. E-49 explicit-index reads (point-read-domain/32/35)
    # `pdt_weight` on the fixture is index * 0.25, so a literal index must read *that* element
    # rather than the current one.
    snap, obj_a, _tree_a = evaluate('f@r = fpoint(0, "mesh", "pdt_weight", 5);', mesh=base, name="e35")
    check_close("E-35 fpoint reads the element the literal index names", values(snap, "r"),
                (1.25,) * 8)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate('f@r = fpoint(0, "mesh", "pdt_weight", @elemnum);', mesh=base,
                                    name="e36")
    check_close("E-36 the index may be the current element (point-read-domain)", values(snap, "r"),
                values(snap, "pdt_weight"))
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate('v@there = vpoint(0, "mesh", "position", 7);', mesh=base, name="e37")
    check_close("E-37 vpoint reads a vector by index", values(snap, "there"),
                tuple(values(snap, "position")[21:24]) * 8)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate('f@r = fpoint(0, "mesh", "pdt_weight", 100);', mesh=base, name="e38")
    check_close("E-38 an index past the end reads 0 (indexed-read-silent)", values(snap, "r"), (0.0,) * 8)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate('f@r = fpoint(0, "mesh", "nope", 0);', mesh=base, name="e39")
    check_close("E-39 a missing attribute reads 0 (indexed-read-silent)", values(snap, "r"), (0.0,) * 8)
    check_true("E-39 and is not created", values(snap, "nope") is None)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("f@np = npoints(0, \"mesh\");\nf@nf = nprimitives(0, \"mesh\");", mesh=base,
                                    name="e40")
    check_close("E-40 npoints sees the point table", values(snap, "np"), (8.0,) * 8)
    check_close("E-40 nprimitives sees the face table (prim-read-domain)", values(snap, "nf"), (6.0,) * 8)
    cleanup(obj_a)

    # point-read-domain: the table follows the *component*, not the run-over domain - the same numbers under
    # Run Over = Faces (whose writes land on the Face domain).
    snap, obj_a, _tree_a = evaluate("f@np = npoints(0, \"mesh\");\nf@nf = nprimitives(0, \"mesh\");",
                                    run_over="Faces", mesh=base, name="e41")
    check_close("E-41 the tables do not follow Run Over (point-read-domain)",
                (values(snap, "np", "FACE")[0], values(snap, "nf", "FACE")[0]), (8.0, 6.0))
    cleanup(obj_a)

    # ------------------ E-44b prim-read-domain: a prim read reads the *face* table of the named component
    # A local fixture with a FACE attribute (the shared cube fixture has none), so this is a real
    # read rather than the silent-0 path: face 3 holds 30, and the curve component this stream does
    # not have reads 0.
    mesh = make_cube("e44b_src")
    face_attr = mesh.attributes.new("pdt_face", "INT", "FACE")
    face_attr.data.foreach_set("value", [i * 10 for i in range(6)])
    snap, obj_a, _tree_a = evaluate('i@a = iprim(0, "mesh", "pdt_face", 3);\n'
                                    'f@b = fprim(0, "curve", "pdt_face", 0);',
                                    run_over="Vertex", mesh=mesh, name="e44b")
    check("E-44b iprim reads the face table of the named component (prim-read-domain)",
          values(snap, "a"), (30,) * 8)
    check_close("E-44b and a component the stream does not have reads 0 (indexed-read-silent)",
                values(snap, "b"), (0.0,) * 8)
    cleanup(obj_a)

    # ------------------ E-86 ordered parallelism at 1024 elements (ordered-parallelism)
    # Promise 4 - when several elements write one element, the merge applies them in element order -
    # at a scale where a race would show: 1024 elements all write element 0 through the buffered
    # path, so the last element's value has to win (1023, not whichever thread finished last).
    grid = make_grid("e86_grid")
    snap, obj_a, _tree_a = evaluate('setpointattrib(0, "mesh", "pdt_weight", 0, @elemnum);',
                                    mesh=grid, name="e86")
    check_close("E-86 the element-order merge holds at 1024 elements (ordered-parallelism)",
                values(snap, "pdt_weight")[:2], (1023.0, 0.0))
    check("E-86 and only that one element was written (ordered-parallelism)",
          sum(1 for value in values(snap, "pdt_weight") if value != 0.0), 1)
    cleanup(obj_a)

    # Promise 1 - the same tree evaluated again gives the same numbers, thread scheduling or not.
    snap_b, obj_b, _tree_b = evaluate('setpointattrib(0, "mesh", "pdt_weight", 0, @elemnum);',
                                      mesh=grid, name="e86b")
    check_close("E-86 and a second evaluation agrees (ordered-parallelism)",
                values(snap_b, "pdt_weight")[:2], (1023.0, 0.0))
    cleanup(obj_b)

    # Promise 2 at the same scale: every element adds one point, so the numbers run 1024..2047 in
    # element order (`ordered-parallelism`'s "by source element order, then by call order").
    snap, obj_a, _tree_a = evaluate('i@np = addpoint(0, "mesh", {0, 0, 0});', mesh=grid, name="e86c")
    numbers = values(snap, "np")
    check("E-86 the numbering continues in element order at scale (ordered-parallelism)",
          (snap["vertices"], numbers[0], numbers[1023]), (2048, 1024.0, 2047.0))
    cleanup(obj_a)
    if grid.users == 0:
        bpy.data.meshes.remove(grid, do_unlink=True)

    # A point cloud has points and no prim table at all. Naming the point cloud for a *prim* read is
    # a compile error (the prim vocabulary is `mesh` / `curve` only), so the "no prim table" side is
    # observed the way `indexed-read-silent` states it: naming a component the stream does not have reads 0.
    snap, obj_a, _tree_a = evaluate("f@np = npoints(0, \"pointcloud\");",
                                    run_over="Point", to_points=True, name="e42")
    check("E-42 a point cloud reports its points", values(snap, "np")[0], 8.0)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("f@nf = nprimitives(0, \"mesh\");",
                                    run_over="Point", to_points=True, name="e42b")
    check("E-42 and a component the stream does not have reads 0", values(snap, "nf")[0], 0.0)
    cleanup(obj_a)

    # An instance set's "points" are the instances (set-prim-attrib's reading of @P). The Instance domain
    # is not visible in a converted mesh, so the counts are observed the way E-22 observes an
    # instance: move the instances by the number and read the realized positions.
    snap, obj_a, _tree_a = evaluate("@P.y += npoints(0, \"instance\");", run_over="Instances",
                                    source_kind="instances", name="e43")
    ys = sorted({values(snap, "position")[i * 3 + 1] for i in range(snap["vertices"])})
    check("E-43 an instance set reports its instances as points (point-read-domain)", ys, [7.0, 8.0, 9.0])
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("@P.y += nprimitives(0, \"mesh\") + 5;", run_over="Instances",
                                    source_kind="instances", name="e43b")
    ys = sorted({values(snap, "position")[i * 3 + 1] for i in range(snap["vertices"])})
    check("E-43 and has no prim table at all (point-read-domain)", ys, [4.0, 5.0, 6.0])
    cleanup(obj_a)

    # Curves: the prim table is the spline table. Read it from a Points run, which is the point
    # of point-read-domain - the table comes from the component, not from Run Over.
    curve = make_curve("e44_curve")
    snap, obj_a, _tree_a = evaluate(
        "f@np = npoints(0, \"curve\");\nf@nf = nprimitives(0, \"curve\");",
        run_over="Control Point", mesh=curve, name="e44")
    check_close("E-44 a curve's prim table is its splines (prim-read-domain)",
                (values(snap, "np")[0], values(snap, "nf")[0]), (6.0, 2.0))
    cleanup(obj_a)

    # Cross-stream explicit reads: Input 1 is the 2x2 grid whose `opw` is index * 10 (E-30's
    # fixture), so index 2 must read 20 and index 6 - which that input does not have - reads 0.
    snap, obj_a, _tree_a = evaluate('f@r = fpoint(1, "mesh", "opw", 2);\nf@s = fpoint(1, "mesh", "opw", 6);',
                                    mesh=base, name="e45", other_input="grid4")
    check_close("E-45 fpoint reads another stream by index", values(snap, "r"), (20.0,) * 8)
    check_close("E-45 and past its end reads 0 (indexed-read-silent)", values(snap, "s"), (0.0,) * 8)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate('f@r = fpoint(2, "mesh", "opw", 0);', mesh=base, name="e46")
    check_close("E-46 an unconnected stream reads 0 (geometry-inputs/indexed-read-silent)", values(snap, "r"),
                (0.0,) * 8)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate('f@r = fpoint(1, "mesh", "ovec", 0);', mesh=base, name="e47",
                                    other_input="badtype")
    check_empty("E-47 a stored-type mismatch is a node error, so the output is empty (code-error-output)", snap)
    check_true("E-47 and creates nothing", values(snap, "r") is None)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate('fpoint(0, "mesh", "pdt_weight", 0) = 1;', mesh=base, name="e48")
    check_empty("E-48 the explicit reads are read-only, so the code does not compile (code-error-output)", snap)
    cleanup(obj_a)

    # Detail: the explicit reads are the way to reach geometry there (at-read/point-read-domain). A read alone
    # cannot leave a result behind (there is no domain to store one), so a Detail run is only
    # observable as a clean pass-through until M5 adds the write functions.
    snap, obj_a, _tree_a = evaluate('float a = fpoint(0, "mesh", "pdt_weight", 0);', run_over="Detail",
                                    mesh=base, name="e49")
    check_close("E-49 Detail accepts the explicit reads (at-read/point-read-domain)",
                values(snap, "position"), src_flat)
    cleanup(obj_a)

    # ------------------- E-50 .. E-60 buffered cross-element writes (set-point-attrib/34/35)
    # A literal index write: the attribute is created and only that element changes.
    snap, obj_a, _tree_a = evaluate('setpointattrib(0, "mesh", "w", 3, 5.0);', mesh=base, name="e50")
    check_close("E-50 setpointattrib writes the element it names (set-point-attrib)",
                values(snap, "w"), (0.0, 0.0, 0.0, 5.0, 0.0, 0.0, 0.0, 0.0))
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate('setpointattrib(0, "mesh", "w", @elemnum, @elemnum * 2);', mesh=base,
                                    name="e51")
    check_close("E-51 every element can write its own element", values(snap, "w"),
                tuple(float(i * 2) for i in range(8)))
    cleanup(obj_a)

    # ordered-parallelism promise 3: when several elements write the same element, the writes are applied in
    # element order - so the last one wins, whatever order the threads finished in.
    snap, obj_a, _tree_a = evaluate('setpointattrib(0, "mesh", "w", 0, @elemnum);', mesh=base, name="e52")
    check_close("E-52 several elements writing one element apply in element order (ordered-parallelism)",
                values(snap, "w"), (7.0,) + (0.0,) * 7)
    cleanup(obj_a)

    snap_b, obj_b, _tree_b = evaluate('setpointattrib(0, "mesh", "w", 0, @elemnum);', mesh=base,
                                      name="e52b")
    check_close("E-52 and the result is identical when it is evaluated again (ordered-parallelism)",
                values(snap_b, "w"), (7.0,) + (0.0,) * 7)
    cleanup(obj_b)

    # The stored type wins when the attribute already exists (`at-operator`'s conversions apply).
    snap, obj_a, _tree_a = evaluate('setpointattrib(0, "mesh", "pdt_tag", 2, 9.5);', mesh=base, name="e53")
    check("E-53 an existing attribute keeps its type", types(snap, "pdt_tag"), "INT")
    check("E-53 and the value is converted to it", values(snap, "pdt_tag")[2], 9.0)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate('setpointattrib(0, "mesh", "w", 100, 1.0);', mesh=base, name="e54")
    check_close("E-54 an out-of-range write is dropped (ordered-parallelism)", values(snap, "w"), (0.0,) * 8)
    cleanup(obj_a)

    # prim-read-domain: the prim write targets the face table even while the run is over points.
    snap, obj_a, _tree_a = evaluate('setprimattrib(0, "mesh", "fw", 0, 2.5);', mesh=base, name="e55")
    check_close("E-55 setprimattrib writes the prim table, not the run-over domain (prim-read-domain)",
                (values(snap, "fw", "FACE")[0], values(snap, "fw", "FACE")[1]), (2.5, 0.0))
    cleanup(obj_a)

    # A point cloud has no prim table, so the write is dropped instead of being an error.
    snap, obj_a, _tree_a = evaluate('setprimattrib(0, "mesh", "fw", 0, 1.0);\nf@ok = 1;',
                                    run_over="Point", to_points=True, name="e56")
    check_true("E-56 a prim write into a component with no prim table is dropped (prim-read-domain)",
               values(snap, "fw", "FACE") is None and values(snap, "ok") == (1.0,) * 8)
    cleanup(obj_a)

    # `indexed-write-silent`'s silent list: a value whose type does not fit what is stored does nothing at all -
    # the geometry passes through and the attribute keeps its values. (This used to be a node error;
    # the `@` form is the one that still reports, which is E-15.)
    snap, obj_a, _tree_a = evaluate('setpointattrib(0, "mesh", "pdt_weight", 0, {1, 2, 3});',
                                    mesh=base, name="e57")
    check("E-57 a value that cannot fit the stored type does nothing (indexed-write-silent)",
          (snap["vertices"], snap["polygons"]), (8, 6))
    check_close("E-57 and the attribute keeps its values (indexed-write-silent)", values(snap, "pdt_weight"),
                tuple(i * 0.25 for i in range(8)))
    cleanup(obj_a)

    # ---------------------------- E-57b set-point-attrib/indexed-write-error: the domain word and its vocabulary
    # A component the stream does not have: `indexed-write-silent` says do nothing, so the geometry passes through
    # and no attribute appears anywhere.
    snap, obj_a, _tree_a = evaluate('setpointattrib(0, "curve", "w", 0, 5.0);', mesh=base,
                                    name="e57b")
    check("E-57b a write into a component the stream does not have does nothing (indexed-write-silent)",
          (snap["vertices"], snap["polygons"], values(snap, "w")), (8, 6, None))
    cleanup(obj_a)

    # A word outside the table class is a compile error, and `code-error-output` empties the output.
    snap, obj_a, _tree_a = evaluate('setpointattrib(0, "edge", "w", 0, 5.0);', mesh=base, name="e57c")
    check_empty("E-57c a word outside the point vocabulary is a compile error (indexed-write-error)", snap)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate('setprimattrib(0, "pointcloud", "fw", 0, 5.0);', mesh=base,
                                    name="e57d")
    check_empty("E-57d and the prim vocabulary has no point cloud (indexed-write-error)", snap)
    cleanup(obj_a)

    # ---------------------------- E-57e set-point-attrib: a write may name a component the pass does not run
    # The joined stream carries a mesh and an 8-point cloud. Run Over = Vertex runs over the *mesh*,
    # and the write names the point cloud: its point 0 takes 42, the mesh is untouched. The joined
    # read-back shows 16 vertices, one of them 42 (the cloud half keeps the attribute, the mesh half
    # has none, so the join fills it with 0).
    snap, obj_a, _tree_a = evaluate('setpointattrib(0, "pointcloud", "w", 0, 42.0);', name="e57e",
                                    source_kind="join")
    check("E-57e a write can target another component of the stream (set-point-attrib)",
          (snap["vertices"], list(values(snap, "w")).count(42.0)), (16, 1))
    cleanup(obj_a)

    # A spline-domain write on a curve stream (the prim table of a curve): the value is broadcast to
    # that spline's control points when the curve is read back as a mesh.
    curve = make_curve("e57f_curve")
    snap, obj_a, _tree_a = evaluate('setprimattrib(0, "curve", "sp", 1, 7.0);', run_over="Splines",
                                    mesh=curve, name="e57f")
    check_close("E-57f a spline-domain write lands on that spline (set-prim-attrib)",
                values(snap, "sp"), (0.0,) * 3 + (7.0,) * 3)
    cleanup(obj_a)

    # ordered-parallelism promise 2: the write is buffered, so the same element still reads the frozen input
    # view - the attribute did not exist, so it reads 0.
    snap, obj_a, _tree_a = evaluate('setpointattrib(0, "mesh", "w", 0, 5.0);\nf@seen = f@w;',
                                    mesh=base, name="e58")
    check_close("E-58 an element cannot see its own buffered write (ordered-parallelism promise 2)",
                (values(snap, "seen")[0], values(snap, "w")[0]), (0.0, 5.0))
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate('setpointattrib(1, "mesh", "w", 0, 1.0);', mesh=base, name="e59")
    check_true("E-59 the target stream must be the literal 0 (indexed-write-error)",
               values(snap, "w") is None)
    cleanup(obj_a)

    # --------------------------- E-60 addpoint and its numbering (set-point-attrib/34 promise 1)
    snap, obj_a, _tree_a = evaluate('int p = addpoint(0, "mesh", {9, 8, 7});\nsetpointattrib(0, "mesh", "w", p, 42);',
                                    mesh=base, name="e60")
    # `Run Over = Points` means every element runs, so all 8 elements each add one point (16 in
    # total) and each writes 42 to the point it created - element 0's is number 8.
    check("E-60 addpoint grows the point table and the write lands on the new point",
          (snap["vertices"], values(snap, "w")[8]), (16, 42.0))
    cleanup(obj_a)

    # Eight elements, one new point each: the numbering continues after the input's 8 points and
    # follows the element order, so the eight new points are numbered 8..15.
    snap, obj_a, _tree_a = evaluate('i@newpt = addpoint(0, "mesh", {9, 8, 7});', mesh=base, name="e60b")
    check("E-60 every element numbers its own new point in element order (ordered-parallelism promise 1)",
          (snap["vertices"], values(snap, "newpt")[:8]),
          (16, tuple(float(8 + i) for i in range(8))))
    cleanup(obj_a)

    # The new points are real: their position is writable, which also proves the point table was
    # grown in CustomData and not only in the attribute storage.
    snap, obj_a, _tree_a = evaluate('int p = addpoint(0, "mesh", {9, 8, 7});\nsetpointattrib(0, "mesh", "P", p, {1, 2, 3});',
                                    mesh=base, name="e60c")
    check_close("E-60 a new point's position is writable (the point table is really grown)",
                values(snap, "position")[24:27], (1.0, 2.0, 3.0))
    cleanup(obj_a)

    # -------------------------------- E-61 addprim builds mesh faces (add-prim)
    # Run Over = Points on the 8-vertex fixture: every element adds a triangle over points 0,1,2,
    # so the mesh ends with 6 + 8 = 14 faces and the vertex table is untouched.
    snap, obj_a, _tree_a = evaluate('i@p = addprim(0, "mesh", "mesh", 0, 1, 2);', mesh=base, name="e61")
    check("E-61 addprim builds one face per element (add-prim)",
          (snap["vertices"], snap["polygons"]), (8, 14))
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate('i@p = addprim(0, "mesh", "mesh", 0, 1);', mesh=base, name="e61b")
    # `add-prim` knows the point count at compile time, so a two-point face is a compile error - and
    # `code-error-output` empties the output either way.
    check_empty("E-61 a mesh face with two points is refused, so the output is empty (add-prim)", snap)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate('i@p = addprim(0, "mesh", "mesh", 0, 1, 99);', mesh=base, name="e61c")
    check_empty("E-61 a point that does not exist is a node error (code-error-output)", snap)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate('i@p = addprim(0, "curve", "mesh", 0, 1);', mesh=base, name="e61d")
    check("E-61 polyline needs a curve component, not a mesh (add-prim)",
          (snap["vertices"], snap["polygons"]), (8, 6))
    cleanup(obj_a)

    # ------------------------------ E-62 the removal cascades (remove-point)
    # Every element asks for the same face: the requests are validated against the input and the
    # removal is applied once, so the points stay and one face goes - remove-point's mesh rule.
    # ------------------ E-61e add-prim: a mesh target **copies** the points of another component
    # Run Over = Point over a point-cloud stream, and the code builds a mesh whose points come from
    # that cloud: eight elements, three copied points each - so the mesh the copy itself *creates*
    # has 24 points and 8 triangles (the reuse case would have referenced the cloud and built
    # nothing). The positions are the source's own (measured: the tree's cube is size 1, so its
    # first corners are +-0.5).
    snap, obj_a, _tree_a = evaluate('i@p = addprim(0, "mesh", "pointcloud", 0, 1, 2);',
                                    run_over="Point", source_kind="m2p", name="e61e")
    check("E-61e a mesh target copies the points of another component (add-prim)",
          (snap["vertices"], snap["polygons"]), (24, 8))
    check_close("E-61e and the copied points keep the source positions (add-prim)",
                values(snap, "position")[:9],
                (-0.5, -0.5, -0.5, 0.5, -0.5, -0.5, -0.5, 0.5, -0.5))
    cleanup(obj_a)

    # A source component the stream does not have is reported rather than skipped - the L0's silent
    # lists cover the reads and the setters, not addprim.
    snap, obj_a, _tree_a = evaluate('i@p = addprim(0, "mesh", "pointcloud", 0, 1, 2);',
                                    mesh=base, name="e61f")
    check_empty("E-61f a source component the stream does not have is a node error (add-prim)", snap)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate("removeprim(0, \"mesh\", 0);", mesh=base, name="e62")
    check("E-62 removeprim deletes the face and keeps its points (remove-prim)",
          (snap["vertices"], snap["polygons"]), (8, 5))
    cleanup(obj_a)

    # Removing a point takes the faces that reference it with it: Blender cannot hold a dangling
    # point index, so this is the opposite of Houdini (measured 6 -> 3 faces for this fixture).
    snap, obj_a, _tree_a = evaluate("removepoint(0, \"mesh\", 0);", mesh=base, name="e62b")
    check("E-62 removepoint takes the faces that use it (remove-point)",
          (snap["vertices"], snap["polygons"]), (7, 3))
    cleanup(obj_a)

    # `remove-prim`'s silent list: an index the table does not have does nothing - the geometry passes
    # through with its faces intact.
    snap, obj_a, _tree_a = evaluate("removeprim(0, \"mesh\", 99);", mesh=base, name="e62c")
    check("E-62 a removal that names nothing does nothing (remove-prim)",
          (snap["vertices"], snap["polygons"]), (8, 6))
    cleanup(obj_a)

    # ------------------------------ E-63 the curve-side removals (remove-point)
    # A curve cannot hold a spline without control points, so removing a spline takes them with it -
    # the opposite of the mesh rule. The fixture is two splines of three points (6 in total).
    curve = make_curve("e63_curve")
    snap, obj_a, _tree_a = evaluate("removeprim(0, \"curve\", 0);", run_over="Splines",
                                    mesh=curve, name="e63")
    check("E-63 removeprim on a curve takes the spline's control points with it (remove-prim)",
          snap["vertices"], 3)
    cleanup(obj_a)

    curve = make_curve("e63b_curve")
    snap, obj_a, _tree_a = evaluate("removepoint(0, \"curve\", 0);", run_over="Splines",
                                    mesh=curve, name="e63b")
    check("E-63 removepoint on a curve keeps the spline alive (remove-point)", snap["vertices"], 5)
    cleanup(obj_a)

    curve = make_curve("e63c_curve")
    # `remove-prim`'s silent list on the curve side too: index 9 is past the two splines, so nothing
    # happens and the six control points stay.
    snap, obj_a, _tree_a = evaluate("removeprim(0, \"curve\", 9);", run_over="Splines",
                                    mesh=curve, name="e63c")
    check("E-63 a curve removal that names nothing does nothing (remove-prim)", snap["vertices"], 6)
    cleanup(obj_a)
    # ---------------------------- E-63d remove-point: a component the stream does not have is silent
    # Naming the mesh on a curve-only stream is `remove-point`'s "the named component is not there" case:
    # nothing happens, nothing is reported, and the curve keeps all six control points.
    curve = make_curve("e63d_curve")
    snap, obj_a, _tree_a = evaluate("removepoint(0, \"mesh\", 0);", run_over="Splines",
                                    mesh=curve, name="e63d")
    check("E-63d a removal naming a component the stream does not have does nothing (remove-point)",
          snap["vertices"], 6)
    cleanup(obj_a)

    # ---------------------------- E-63g remove-point: a point cloud loses a point
    # `domain = "pointcloud"` deletes that point - and a point cloud has no dependent topology, so
    # nothing else goes with it. Read back through Points to Vertices: eight points become seven.
    snap, obj_a, _tree_a = evaluate('removepoint(0, "pointcloud", 3);', run_over="Point",
                                    source_kind="m2p", to_points=True, name="e63g")
    check("E-63g a point cloud loses the point that was named (remove-point)", snap["vertices"], 7)
    cleanup(obj_a)

    # ---------------------------- E-63h remove-point: an instance set loses an instance
    # `at-p-scope` reads an instance set's "points" as the instances, so this deletes one of the eight
    # instances: the realized stream is seven unit cubes (measured for eight: 64 vertices / 48 faces,
    # so 7 * 8 = 56 and 7 * 6 = 42).
    snap, obj_a, _tree_a = evaluate('removepoint(0, "instance", 1);', run_over="Instances",
                                    source_kind="instances", name="e63h")
    check("E-63h an instance set loses the instance that was named (remove-point)",
          (snap["vertices"], snap["polygons"]), (56, 42))
    cleanup(obj_a)

    # ---------------------------- E-63e remove-point/remove-prim: the word lists, at the node level
    # A word outside the point list (or the prim list) is a compile error, and `code-error-output`
    # empties the output. (Removing from a *point cloud* or an *instance set* is the one piece of
    # this pair still owed: the word is accepted, and a stream that really carries the component is
    # refused with a message rather than doing nothing silently.)
    snap, obj_a, _tree_a = evaluate('removepoint(0, "edge", 0);', mesh=base, name="e63e")
    check_empty("E-63e a word outside the point list is a compile error (remove-point)", snap)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate('removeprim(0, "pointcloud", 0);', mesh=base, name="e63f")
    check_empty("E-63f and the prim list has no point cloud (remove-prim)", snap)
    cleanup(obj_a)

    # -------------------------------- E-64 addprim builds curve splines (add-prim)
    # Run Over = Points on the 8-vertex fixture: 8 elements, each asking for a two-point polyline.
    # The tree routes the wrangle's output through Curve to Mesh and straight into the group output,
    # so the mesh component is bypassed and what is measured is the curve alone: 8 splines of two
    # control points. (The earlier reading of (8, 6) came from to_mesh() preferring the mesh
    # component, not from a missing curve - this is the experiment that told the two apart.)
    snap, obj_a, _tree_a = evaluate('i@p = addprim(0, "curve", "mesh", 0, 1);', mesh=base, name="e64",
                                    curve_to_mesh=True)
    check("E-64 addprim builds one polyline per element (add-prim)",
          (snap["vertices"], snap["polygons"]), (16, 0))
    check("E-64 and the curve this run built is not processed by it (same-run visibility)",
          snap["vertices"], 16)  # with the created curve fed back in, this read 48
    cleanup(obj_a)

    # The mesh side of the same program: the pass used to abort in `apply_mesh_topology` (a stale
    # "polyline in a mesh stream" error from before the curve path existed), so the curve was built
    # and *every* `@` write of the program was lost. `p2` carries `addprim`'s return number, so the
    # values also show that each element built its own spline.
    snap, obj_a, _tree_a = evaluate(
        'i@np = npoints(0, "mesh");\ni@p2 = addprim(0, "curve", "mesh", 0, 1);', mesh=base, name="e64e")
    check("E-64 a mesh stream writes its attributes next to the curve it builds (add-prim/same-run visibility)",
          (snap["vertices"], snap["polygons"], values(snap, "np"), values(snap, "p2")),
          (8, 6, (8.0,) * 8, tuple(float(i) for i in range(8))))
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate('i@p = addprim(0, "curve", "mesh", 0);', mesh=base, name="e64b",
                                    curve_to_mesh=True)
    check("E-64 a one-point spline is legal: eight elements build eight of them (add-prim)",
          (snap["vertices"], snap["polygons"]), (8, 0))
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate('i@p = addprim(0, "curve", "mesh", 0, 99);', mesh=base, name="e64c")
    check_empty("E-64 a polyline point that does not exist is a node error (code-error-output)", snap)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate('i@p = addprim(0, "mesh", "mesh", 0, 1, 2);', run_over="Point", to_points=True,
                                    name="e64d")
    check_empty("E-64 a face whose pointdomain `mesh` is absent is reported (add-prim)", snap)
    cleanup(obj_a)
    # ------------------------------ E-65 append a spline to an existing curve (add-prim)
    # The fixture is a curve of two splines with three control points each. Run Over = Splines means
    # two elements, each asking for a two-point polyline, so four control points are appended.
    curve = make_curve("e65_curve")
    snap, obj_a, _tree_a = evaluate('i@p = addprim(0, "curve", "curve", 0, 1);', run_over="Splines",
                                    mesh=curve, name="e65")
    check("E-65 addprim appends splines to an existing curve (add-prim)",
          snap["vertices"], 10)
    cleanup(obj_a)

    curve = make_curve("e65b_curve")
    snap, obj_a, _tree_a = evaluate('i@p = addprim(0, "curve", "mesh", 0);', run_over="Splines",
                                    mesh=curve, name="e65b")
    check_empty("E-65 an appended polyline with one point is a node error (code-error-output)", snap)
    cleanup(obj_a)
    # ---------------------- E-66 a spline may be left with a single point (remove-point)
    # The fixture is two splines of two control points. Removing point 0 leaves spline 0 with one
    # point, and Blender's own deletion keeps it - which is the rule remove-point now states. This
    # assertion is deliberately here because the opposite rule was implemented once and the
    # experiment (this test) is what showed Blender does not delete short splines.
    curve = make_curve("e66_curve", splines=2, points=2)
    snap, obj_a, _tree_a = evaluate("removepoint(0, \"curve\", 0);", run_over="Splines",
                                    mesh=curve, name="e66")
    check("E-66 a spline left with one control point survives (remove-point)", snap["vertices"], 3)
    cleanup(obj_a)
    # ---------------------- E-67 a point cloud as the polyline source, and addpoint outside a mesh
    # Run Over = Points on a point cloud of eight points: every element asks for a two-point
    # polyline, so the curve has eight splines and sixteen control points. Measured through Curve to
    # Mesh, which also makes the reading independent of which component to_mesh() prefers.
    snap, obj_a, _tree_a = evaluate('i@p = addprim(0, "curve", "pointcloud", 0, 1);',
                                    run_over="Point", to_points=True, name="e67",
                                    curve_to_mesh=True)
    check("E-67 a point cloud can be the source of a polyline (add-prim)",
          (snap["vertices"], snap["polygons"]), (16, 0))
    cleanup(obj_a)

    # add-point: a point cloud has a point table, so `addpoint(0, "pointcloud", pos)` grows it - the
    # eight elements each add one point, and the new ones sit at the position they were given.
    snap, obj_a, _tree_a = evaluate('int p = addpoint(0, "pointcloud", {1, 2, 3});\ni@newpt = p;',
                                    run_over="Point", to_points=True, name="e67b")
    check("E-67 addpoint grows a point cloud's point table (add-point)", snap["vertices"], 16)
    # The numbers land on the *input's* eight points (the pass ran over those, and ordered-parallelism's promise 5
    # keeps the new elements out of this iteration - they read as 0), continuing after the input's
    # own eight points in element order.
    check("E-67 the returned numbers continue after the input's own points (remove-prim)",
          values(snap, "newpt")[:8], tuple(float(8 + i) for i in range(8)))
    check("E-67 and a new element does not take part in this iteration (ordered-parallelism)",
          values(snap, "newpt")[8:], (0.0,) * 8)
    cleanup(obj_a)

    # ------------------- E-80 add-point: a point table the stream does not have is created
    # Run Over = Splines over the two-spline curve fixture, but the code asks for *mesh* points: the
    # node creates a mesh component with one point per element, at the position it was given. The
    # curve component is not a mesh, so `to_mesh()` shows exactly the created mesh.
    curve = make_curve("e80_curve")
    snap, obj_a, _tree_a = evaluate('int p = addpoint(0, "mesh", {1, 2, 3});',
                                    run_over="Splines", mesh=curve, name="e80")
    check("E-80 addpoint creates the mesh the stream did not have (add-point)",
          (snap["vertices"], snap["polygons"]), (2, 0))
    check_close("E-80 and the created points sit where the code asked",
                values(snap, "position"), (1.0, 2.0, 3.0, 1.0, 2.0, 3.0))
    cleanup(obj_a)

    # ------------------- E-81 add-point: the point cloud a stream does not have is created too
    # Run Over = Vertex on the cube, two `addpoint(0, "pointcloud", …)` calls per element: the
    # created cloud has 16 points, and `Points to Vertices` drops the mesh component, so the 16
    # vertices below are the cloud's own (the pass's own mesh is untouched).
    snap, obj_a, _tree_a = evaluate('int a = addpoint(0, "pointcloud", {1, 2, 3});\n'
                                    'int b = addpoint(0, "pointcloud", {4, 5, 6});',
                                    mesh=base, name="e81", points_to_verts=True)
    check("E-81 addpoint creates a point cloud the stream did not have (add-point)",
          snap["vertices"], 16)
    check_close("E-81 with the positions it was given, in call order",
                values(snap, "position")[:6], (1.0, 2.0, 3.0, 4.0, 5.0, 6.0))
    cleanup(obj_a)

    # ------------------ E-88 add-prim's faces use the numbers addpoint handed out
    # `WR-27`: the numbers a face is built from are the ones `addpoint` returned, and those are only
    # final in the *real* pass - `addpoint` answers `base + offset`, and the base exists only after
    # the counts are in. Recording the faces during the counting pass therefore built them on 0-based
    # numbers: measured with eight elements each growing a box, every box landed on the input's own
    # first points. The counts stay right in that case, so this case looks at where the faces are.
    box = ('vector p = @P;\n'
           'int b = addpoint(0, "mesh", {p.x, p.y, 1.0});\n'
           'addpoint(0, "mesh", {p.x, p.y, 2.0});\n'
           'addpoint(0, "mesh", {p.x, p.y, 3.0});\n'
           'addpoint(0, "mesh", {p.x, p.y, 4.0});\n'
           'addprim(0, "mesh", "mesh", b + 0, b + 1, b + 2, b + 3);\n')
    snap, obj_a, tree_a = evaluate(box, mesh=base, name="e88")
    check("E-88 eight elements each build their face on their own four points (add-prim)",
          [centre[2] for centre in polygon_centres(obj_a, tree_a)[6:]], [2.5] * 8)
    cleanup(obj_a)

    # ------------------ E-89 a Detail pass can build topology too
    # `WR-25`/`WR-27`: the `domain` argument names the component, so the run-over kind must not
    # decide whether `addpoint`/`addprim` work. Detail is one element and the compiler already
    # refused every `@`, so it goes through the same two-pass machinery as the other kinds - before
    # that it ran the code once and dropped everything, and a Detail box came back empty.
    snap, obj_a, _tree_a = evaluate('int b = addpoint(0, "mesh", {0, 0, 1});\n'
                                    'addprim(0, "mesh", "mesh", 0, 1, 3, 2);',
                                    run_over="Detail", mesh=base, name="e89")
    check("E-89 a Detail pass adds a point and a face (add-point/add-prim)",
          (snap["vertices"], snap["polygons"]), (9, 7))
    cleanup(obj_a)

    # ------------------ E-90 a Detail pass can build a curve spline too
    # `WR-27`: a polyline names its `pointdomain`, and with no `@` in the program that is the only
    # component a Detail run can reach - so it is also the component the one-element run iterates
    # over. Read through Curve to Mesh (as the other curve cases are): the fixture's two splines of
    # four control points, plus the spline this single call builds.
    curve_fixture = make_curve("e90_curve", splines=2, points=4)
    snap, obj_a, _tree_a = evaluate('addprim(0, "curve", "curve", 0, 1, 2, 3);',
                                    run_over="Detail", mesh=curve_fixture, name="e90",
                                    curve_to_mesh=True)
    check("E-90 a Detail pass builds a spline from the curve's own points (add-prim)",
          (snap["vertices"], snap["edges"], snap["polygons"]), (12, 9, 0))
    cleanup(obj_a)

    # ------------------ E-87 add-prim: the two recorded limits of this item, pinned
    # (a) The spline requests of one program have to name the same `pointdomain`: one copy writes one
    # point range, so two sources are refused with a message instead of being guessed at. The joined
    # stream carries both a mesh and a point cloud, so both sources exist and the refusal is the
    # only thing that can happen here.
    snap, obj_a, _tree_a = evaluate('int a = addprim(0, "curve", "mesh", 0, 1);\n'
                                    'int b = addprim(0, "curve", "pointcloud", 0, 1);',
                                    source_kind="join", name="e87")
    check_empty("E-87 two pointdomains in one program are refused (add-prim)", snap)
    cleanup(obj_a)

    # ------------------ E-84 add-prim: an instance set is a point source too
    # Run Over = Instances over the eight-instance fixture, and each element builds a mesh face from
    # three *instances*: their "points" are the instances and `at-p-scope` reads an instance's `@P` as its
    # translation, so the copied positions are the cube corners. Measured with
    # qa/wrangle/probe_instance_source.py: the realized stream (8 unit cubes = 64 vertices / 48 faces)
    # plus the mesh the copy creates (24 / 8) is 88 / 56.
    snap, obj_a, _tree_a = evaluate('i@p = addprim(0, "mesh", "instance", 0, 1, 2);',
                                    run_over="Instances", source_kind="instances", name="e84")
    check("E-84 an instance set can be the point source of a mesh target (add-prim)",
          (snap["vertices"], snap["polygons"]), (88, 56))
    check("E-84 and the copied points are the instances' translations (at-p-scope/add-prim)",
          sorted({values(snap, "position")[i * 3 + 1] for i in range(snap["vertices"])}),
          [-1.0, -0.5, 0.0, 0.5, 1.0])
    cleanup(obj_a)

    # ------------------ E-85 add-point: addpoint works from an Instances run too
    # The instance pass carries a writable geometry set now, so the code can create a point table
    # from it: eight elements, one point each, all at the position asked for - and the realized
    # instance stream (64 / 48) is untouched. Measured: 72 vertices / 48 faces.
    snap, obj_a, _tree_a = evaluate('int q = addpoint(0, "mesh", {1, 2, 3});', run_over="Instances",
                                    source_kind="instances", name="e85")
    check("E-85 addpoint creates a mesh from an Instances run (add-point)",
          (snap["vertices"], snap["polygons"]), (72, 48))
    positions = values(snap, "position")
    check("E-85 and the created points sit where the code asked (add-point)",
          sum(1 for i in range(snap["vertices"])
              if (positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]) == (1.0, 2.0, 3.0)),
          8)
    cleanup(obj_a)

    # ------------------- E-82 add-point: a table other than the pass's own component grows
    # The joined stream carries a mesh and an 8-point cloud. Run Over = Vertex runs 8 times and each
    # element adds one point to the *point cloud*, so the cloud grows 8 -> 16 while the mesh stays at
    # 8: the joined read-back has 24 vertices, and eight of them are at y = 8 (the position asked
    # for) - none of the input's points are (`size 1` means +-0.5).
    snap, obj_a, _tree_a = evaluate('int p = addpoint(0, "pointcloud", {7, 8, 9});',
                                    name="e82", source_kind="join")
    ys = [values(snap, "position")[i * 3 + 1] for i in range(snap["vertices"])]
    check("E-82 the named table grows even when it is not this pass's component (add-point)",
          (snap["vertices"], ys.count(8.0)), (24, 8))
    cleanup(obj_a)

    # ------------------- E-83 add-point: nothing is created when nothing is added
    # The condition is false for every element, so the counting pass reports no new points and no
    # component is created for them (the converter then has an empty cloud to convert).
    snap, obj_a, _tree_a = evaluate('if (@elemnum < 0) {\n'
                                    '  int p = addpoint(0, "pointcloud", {1, 2, 3});\n}',
                                    mesh=base, name="e83", points_to_verts=True)
    check("E-83 no point is added, so no component is created (add-point)", snap["vertices"], 0)
    cleanup(obj_a)
    # ---------------- E-68 a point created in the same run can be referenced (the frozen input view / M6 option a)
    # Run Over = Points on the 8-vertex fixture: every element creates a point and then asks for a
    # polyline over it. Reading through Curve to Mesh measures the curve alone (8 splines of two
    # control points, the data copied from the new point default values), and the plain snapshot
    # measures the mesh, which grew by the eight new vertices.
    prog = 'int p = addpoint(0, "mesh", {9, 8, 7});\ni@newpt = p;\nint pr = addprim(0, "curve", "mesh", p, p);\ni@newpr = pr;'
    snap, obj_a, _tree_a = evaluate(prog, mesh=base, name="e68", curve_to_mesh=True)
    check("E-68 a polyline can reference a point created in the same run (the frozen input view)",
          (snap["vertices"], snap["polygons"]), (16, 0))
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate(prog, mesh=base, name="e68b")
    check("E-68 and the mesh itself grew by the new points (same-run visibility)",
          (snap["vertices"], snap["polygons"]), (16, 6))
    cleanup(obj_a)
    # ---------------- E-69 the frozen input view: the run does not see the elements it creates
    # Every element creates a point. The eight new points never run, so they keep the default 0 while
    # the original eight carry whatever the run wrote. OPEN (docs/14): the same program also shows
    # npoints(0) reporting the grown table (16) rather than the count at the start of the run (8),
    # which is what the frozen input view says - the growth happens in the counting pass, so the real pass sees it.
    # Asserting the 16 here would bake that deviation in, so only the agreed half is asserted.
    snap, obj_a, _tree_a = evaluate('i@nb = npoints(0, "mesh");\nint p = addpoint(0, "mesh", {9, 8, 7});\nv@rb = vpoint(0, "mesh", "P", p);',
                                    mesh=base, name="e69")
    check("E-69 the new points do not run, so they keep the default (the frozen input view)",
          0.0 in values(snap, "nb"), True)
    cleanup(obj_a)
    # ---------------- E-72 the counts and the read bound are frozen at the start (the frozen input view)
    # M6 option A (docs/14): the counting pass grows the real table before the code runs, so the
    # two numbers `npoints` / `nprimitives` report are recorded *before* that pass and read from
    # the record. Before this the same program answered 8 in the counting pass and 16 in the run,
    # so a branch on the count did one thing while counting and another while running. (The
    # `npoints` half of E-69 was deliberately left unasserted - it is the assertion below, on the
    # implementation that makes it true.)
    prog = 'i@np = npoints(0, "mesh");\nint p = addpoint(0, "mesh", {9, 8, 7});\nif (npoints(0, "mesh") > 8) {\n  i@np = -1;\n}'
    snap, obj_a, _tree_a = evaluate(prog, mesh=base, name="e72")
    # The run's own eight elements carry 8 - not 16 - and the branch above therefore never fires.
    check_close("E-72 npoints(0, \"mesh\") is the count at the start of the run, not the grown table (the frozen input view)",
                values(snap, "np")[:8], (8.0,) * 8)
    # …and the growth really happened, so the 8 above cannot be "nothing was created".
    check("E-72 and the points were created in the same run (same-run visibility/the frozen input view)",
          snap["vertices"], 16)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate(
        'i@np = npoints(0, "mesh");\nint p = addpoint(0, "mesh", {9, 8, 7});\nv@rb = vpoint(0, "mesh", "P", p);',
        mesh=base, name="e72b")
    # `vpoint` checks its index against the same frozen table, so the point this run created reads
    # 0 instead of the grown storage (which nothing has written yet). Only the run's own 8 * 3
    # values are asserted: the grown region belongs to the new, never-executed points.
    check_close("E-72 an index the run itself created reads 0 through vpoint (the frozen input view)",
                values(snap, "rb")[:24], (0.0,) * 24)
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate(
        'i@nf = nprimitives(0, "mesh");\ni@newf = addprim(0, "mesh", "mesh", 0, 1, 2);', mesh=base, name="e72c")
    check_close("E-72 nprimitives(0, \"mesh\") is the face count at the start, not the grown table (the frozen input view)",
                values(snap, "nf"), (6.0,) * 8)
    check("E-72 and the faces were created in the same run (add-prim/the frozen input view)",
          (snap["vertices"], snap["polygons"]), (8, 14))
    cleanup(obj_a)

    # The shrink side (a removal moves the table the other way) is now assertable: the removals are
    # applied *after* the run, so the run works on the complete input table - the count stays the one
    # this evaluation started with, and a write to an element this run removes disappears with that
    # element. Both are what H21 measures (docs/14 §M6 第一步: `R1h` still reports 8 after removing a
    # point, `R1a` leaves `mark` = [0,1,2,4,5,6,7]).
    snap, obj_a, _tree_a = evaluate('i@np = npoints(0, "mesh");\nremovepoint(0, "mesh", 0);', mesh=base, name="e72e")
    check("E-72 a removal in the same run does not move the count either (the frozen input view)",
          (snap["vertices"], values(snap, "np")), (7, (8.0,) * 7))
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate(
        'i@mark = 3;\nsetpointattrib(0, "mesh", "np", @elemnum, 7);\nremovepoint(0, "mesh", 5);', mesh=base,
        name="e72f")
    check("E-72 the writes of a run that also removes land on the elements that survive (the frozen input view)",
          (snap["vertices"], snap["polygons"], values(snap, "mark"), values(snap, "np")),
          (7, 3, (3.0,) * 7, (7.0,) * 7))
    cleanup(obj_a)

    snap, obj_a, _tree_a = evaluate(
        'setpointattrib(0, "mesh", "x", 5, 42);\nremovepoint(0, "mesh", 5);', mesh=base, name="e72g")
    check("E-72 a write to the element this run removes goes away with it (the frozen input view/remove-point)",
          (snap["vertices"], types(snap, "x"), values(snap, "x")), (7, "FLOAT", (0.0,) * 7))
    cleanup(obj_a)

    # ---------------- E-73 add-prim: a curve stream runs its real pass, so its `@` writes land
    # The polyline path replaces the curve component (`join_geometries`), and the pass used to
    # return before the real one - so `i@np = npoints(0);` next to `addprim(0, "polyline", …)` wrote
    # nothing at all. The write is measured through `to_mesh()`, which broadcasts a spline's value to
    # its own control points (E-19's route): the two original splines carry 6, the two appended ones
    # are all-default.
    curve = make_curve("e73_curve")
    snap, obj_a, _tree_a = evaluate(
        'i@np = npoints(0, "curve");\ni@p2 = addprim(0, "curve", "curve", 0, 1);', run_over="Splines",
        mesh=curve, name="e73")
    check("E-73 a curve stream writes its attributes next to the curve it builds (add-prim/same-run visibility)",
          (snap["vertices"], values(snap, "np")), (10, (6.0,) * 6 + (0.0,) * 4))
    cleanup(obj_a)

    # ---------------- E-74 add-prim: the same for a point cloud
    snap, obj_a, _tree_a = evaluate(
        'i@np = npoints(0, "pointcloud");\ni@p2 = addprim(0, "curve", "pointcloud", 0, 1);',
        run_over="Point", to_points=True, name="e74")
    check("E-74 a point cloud stream writes its attributes next to the curve it builds (add-prim)",
          (snap["vertices"], values(snap, "np")), (8, (8.0,) * 8))
    cleanup(obj_a)
    # ---------------- E-70 selection-scope: only the selected elements create
    # `Selection = Index < 4` on the 8-vertex fixture: four elements run, so four points are made
    # (8 -> 12) and the numbering still follows element order (ordered-parallelism promise 1) - the four
    # unselected elements neither write nor create. Houdini measures the same shape on a
    # group-limited run (`R3`: 8 -> 12 points, measured 2026-10-06, qa/wrangle/
    # wrangle_h21_m6_semantics.py).
    snap, obj_a, _tree_a = evaluate('i@np = addpoint(0, "mesh", {9, 8, 7});', selection="index_lt4", mesh=base,
                                    name="e70")
    check("E-70 only the selected elements create (selection-scope)", snap["vertices"], 12)
    check_close("E-70 and the new points are numbered in element order (ordered-parallelism promise 1)",
                values(snap, "np")[:4], (8.0, 9.0, 10.0, 11.0))
    check_close("E-70 the unselected elements created nothing and wrote nothing (selection-scope)",
                values(snap, "np")[4:8], (0.0,) * 4)
    cleanup(obj_a)

    # ---------------- E-71 indexed-write-silent: a negative index is dropped, not an error
    # indexed-write-silent says "越界（含负数）→ 丢弃该次写，不报错"; E-54 pins the too-large half, this is the
    # negative half. The write really is dropped - the attribute is created all-default (which is
    # also what Houdini does: `R2b`, and `R2a` for 999).
    snap, obj_a, _tree_a = evaluate('setpointattrib(0, "mesh", "x", -1, 1.0);', mesh=base, name="e71")
    check("E-71 a negative index is dropped without an error, and the geometry passes through"
          " (indexed-write-silent)", (types(snap, "x"), snap["vertices"], snap["polygons"]), ("FLOAT", 8, 6))
    check_close("E-71 and the dropped write left the attribute at its default (indexed-write-silent)",
                values(snap, "x"), (0.0,) * 8)
    cleanup(obj_a)


    # ------------------------------------------------- channels read: chf / chi / chv (WR-36..WR-39)
    # The code names a channel with a *literal* string; the value is the channel's, not an
    # attribute's, and one evaluation reads it once for every element.
    ch_snap, ch_obj, _ch_tree = evaluate(
        'f@h = chf("height");\ni@c = chi("count");\nv@a = chv("axis");',
        mesh=base, name="e91",
        channels=[("height", "FLOAT", 2.5), ("count", "INT", 7), ("axis", "VECTOR", (0.0, 1.0, 0.0))])
    check("E-91 chf / chi / chv read the three channel types (channel-read)",
          (values(ch_snap, "h")[0], values(ch_snap, "c")[0], values(ch_snap, "a")[0:3]),
          (2.5, 7, (0.0, 1.0, 0.0)))
    check("E-91 every element reads the same channel value (channel-value)",
          (values(ch_snap, "h"), len(set(values(ch_snap, "h")))), ((2.5,) * 8, 1))
    cleanup(ch_obj)

    # E-92 `channel-read`: the name is a literal, so a computed name has nowhere to come from
    snap, obj_a, _tree_a = evaluate('f@h = chf(1);', mesh=base, name="e92",
                                    channels=[("height", "FLOAT", 2.5)])
    check_empty("E-92 a non-literal channel name is a compile error (channel-read)", snap)
    cleanup(obj_a)

    # E-93 `channel-missing-zero`: an unknown name reads 0 - no error, geometry untouched
    snap, obj_a, _tree_a = evaluate('f@h = chf("nope");', mesh=base, name="e93")
    check("E-93 an unknown channel reads 0 and the geometry passes through (channel-missing-zero)",
          (types(snap, "h"), values(snap, "h"), snap["vertices"]), ("FLOAT", (0.0,) * 8, 8))
    cleanup(obj_a)

    # E-94 `channel-missing-zero`: exact, case-sensitive names and no component path
    snap, obj_a, _tree_a = evaluate('f@a = chf("Height");\nf@b = chf("height.y");', mesh=base,
                                    name="e94", channels=[("height", "FLOAT", 2.5)])
    check("E-94 a wrong case and a component path both read 0 (channel-missing-zero)",
          (values(snap, "a")[0], values(snap, "b")[0]), (0.0, 0.0))
    cleanup(obj_a)

    # E-95 `channel-type-mismatch`: the function and the channel type have to line up
    snap, obj_a, _tree_a = evaluate(
        'f@a = chf("axis");\nv@b = chv("height");\ni@c = chi("height");',
        mesh=base, name="e95",
        channels=[("height", "FLOAT", 3.7), ("axis", "VECTOR", (1.0, 2.0, 3.0))])
    check("E-95 a vector channel read as a scalar is 0 (channel-type-mismatch)",
          values(snap, "a")[0], 0.0)
    check_close("E-95 a scalar channel read as a vector is (value, 0, 0) (channel-type-mismatch)",
                values(snap, "b")[0:3], (3.7, 0.0, 0.0))
    check("E-95 chi on a float channel truncates (channel-type-mismatch)",
          values(snap, "c")[0], 3)
    cleanup(obj_a)

    # E-96 `channel-read`: Detail has no `@`, so a channel is the only outside value it can read
    snap, obj_a, _tree_a = evaluate('float h = chf("height");', run_over="Detail", mesh=base,
                                    name="e96", channels=[("height", "FLOAT", 2.5)])
    check("E-96 Detail runs a channel read and passes the geometry through (channel-read)",
          (snap["vertices"], snap["polygons"]), (8, 6))
    cleanup(obj_a)

    # E-97 `channel-value`: a *linked* channel reads the evaluated input, not the stored default
    # A `GeometryNodeSwitch` is the single-value source this tree can use (measured: its unconnected
    # inputs have settable defaults and Blender accepts the link into the channel socket), and the
    # channel socket's own default is deliberately a different number: reading 4.0 rather than 0.25
    # is what "read through the link" means.
    link_tree = bpy.data.node_groups.new("pdt_wrangle_channel_link", "GeometryNodeTree")
    link_tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    link_tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    link_in = link_tree.nodes.new("NodeGroupInput")
    link_node = link_tree.nodes.new(NODE)
    link_out = link_tree.nodes.new("NodeGroupOutput")
    driver = link_tree.nodes.new("GeometryNodeSwitch")
    driver.inputs["False"].default_value = 0.25
    driver.inputs["True"].default_value = 4.0
    driver.inputs["Switch"].default_value = True
    link_node.channels.new(socket_type="FLOAT", name="height")
    link_node.inputs["height"].default_value = 0.25
    link_tree.links.new(driver.outputs["Output"], link_node.inputs["height"])
    link_tree.links.new(link_in.outputs["Geometry"], link_node.inputs["Geometry"])
    link_tree.links.new(link_node.outputs["Geometry"], link_out.inputs["Geometry"])
    link_node.inputs["Code"].default_value = 'f@h = chf("height");'
    link_mesh = make_cube("e97_src")
    link_obj = bpy.data.objects.new("e97_obj", link_mesh)
    bpy.context.scene.collection.objects.link(link_obj)
    linked = snapshot(link_obj, link_tree)
    check("E-97 a linked channel reads the evaluated input (channel-value)",
          values(linked, "h")[0], 4.0)
    cleanup(link_obj)
    bpy.data.node_groups.remove(link_tree)

    # ------------------------------------------------- WR-47…WR-58 rotation and matrix
    # The two Blender attribute types are the whole point of these entries: a rotation is stored
    # as QUATERNION (four floats, w first) and a transform as FLOAT4X4 (sixteen, four columns).

    # E-98 `quaternion-type`: `q@` reads and writes a quat attribute.
    q_snap, q_obj, q_tree = evaluate("q@rot = quaternion(1, 0, 0, 0);", name="e98")
    check("E-98 q@ creates a quat attribute (quaternion-type)",
          types(q_snap, "rot"), "QUATERNION")
    check("E-98 and the identity lands w-first (quaternion-type)",
          tuple(values(q_snap, "rot")[:4]), (1.0, 0.0, 0.0, 0.0))
    cleanup(q_obj)
    bpy.data.node_groups.remove(q_tree)

    # E-99 `quaternion-type`: the components are read back in the same order.
    q2_snap, q2_obj, q2_tree = evaluate(
        "q@r = quaternion(0, 0, 0, 1); f@w = q@r.w; f@z = q@r.z; v@xyz = {q@r.x, q@r.y, 0};",
        name="e99")
    check("E-99 reading a rotation gives w first (quaternion-type)", values(q2_snap, "w")[0], 0.0)
    check("E-99 and .z is the fourth component (quaternion-type)", values(q2_snap, "z")[0], 1.0)
    check("E-99 the middle components are read by name (quaternion-type)",
          tuple(values(q2_snap, "xyz")[:3]), (0.0, 0.0, 0.0))
    cleanup(q2_obj)
    bpy.data.node_groups.remove(q2_tree)

    # E-100 `matrix-type`: `m@` writes a FLOAT4X4 matrix.
    m_snap, m_obj, m_tree = evaluate(
        "m@x = combine_transform({1,2,3}, quaternion(1,0,0,0), {1,1,1});", name="e100")
    check("E-100 m@ creates a FLOAT4X4 attribute (matrix-type)",
          types(m_snap, "x"), "FLOAT4X4")
    check("E-100 and the translation sits in the last column (matrix-type)",
          tuple(values(m_snap, "x")[12:16]), (1.0, 2.0, 3.0, 1.0))
    cleanup(m_obj)
    bpy.data.node_groups.remove(m_tree)

    # E-101 `matrix-type`: a matrix reads back through the ordinary component syntax.
    # `m[row][column]`: the translation sits in column 3, so it reads `[0][3]` `[1][3]` `[2][3]`.
    # `m[i]` alone is a *column* (like Blender's own `float4x4[i]`), which is why `m[3]` is the
    # translation as a vector.
    m2_snap, m2_obj, m2_tree = evaluate(
        "m@x = combine_transform({4,5,6}, quaternion(1,0,0,0), {7,8,9}); "
        "f@a = m@x[0][3]; f@b = m@x[0][0]; v@col = m@x[3];",
        name="e101")
    check("E-101 m[row][column] reads the translation's first number (matrix-type)",
          values(m2_snap, "a")[0], 4.0)
    check("E-101 and m[0][0] is the scale (matrix-type)", values(m2_snap, "b")[0], 7.0)
    check("E-101 m[column] is the column as a vector (matrix-type)",
          tuple(values(m2_snap, "col")[:3]), (4.0, 5.0, 6.0))
    cleanup(m2_obj)
    bpy.data.node_groups.remove(m2_tree)

    # E-102 `matrix-type`: a declared matrix starts as the identity, so `m * v` is `v`.
    m3_snap, m3_obj, m3_tree = evaluate(
        "matrix m; v@out = m * {1,2,3}; m@x = m;", name="e102")
    check("E-102 a declared matrix is the identity (matrix-type)",
          tuple(values(m3_snap, "out")[:3]), (1.0, 2.0, 3.0))
    check("E-102 and it can be stored as an attribute (matrix-type)",
          tuple(values(m3_snap, "x")[12:16]), (0.0, 0.0, 0.0, 1.0))
    cleanup(m3_obj)
    bpy.data.node_groups.remove(m3_tree)

    # E-103 `matrix-type`: reading a rotation or a matrix that is not there gives zeros.
    # Two different names on purpose: one attribute cannot be a rotation *and* a matrix (WR-11),
    # and that rule is asserted separately below.
    zero_snap, zero_obj, zero_tree = evaluate(
        "f@w = q@qmiss.w; f@m = m@mmiss[3][0]; "
        "m@storem = m@mmiss; q@storeq = q@qmiss;",
        name="e103")
    check("E-103 a missing rotation reads 0 (matrix-type)", values(zero_snap, "w")[0], 0.0)
    check("E-103 a missing matrix reads 0 (matrix-type)", values(zero_snap, "m")[0], 0.0)
    check("E-103 and the whole matrix can be stored back as zeros (matrix-type)",
          tuple(values(zero_snap, "storem")[:16]), tuple([0.0] * 16))
    check("E-103 the same for the all-zero rotation (matrix-type)",
          tuple(values(zero_snap, "storeq")[:4]), (0.0, 0.0, 0.0, 0.0))
    cleanup(zero_obj)
    bpy.data.node_groups.remove(zero_tree)

    # E-104 `matrix-type`: one attribute cannot be a rotation and a matrix at once.
    mixed_snap, mixed_obj, mixed_tree = evaluate(
        "f@a = q@same.w; f@b = m@same[3][0];", name="e104a")
    check_empty("E-104 one name cannot be a rotation and a matrix (matrix-type)", mixed_snap)
    cleanup(mixed_obj)
    bpy.data.node_groups.remove(mixed_tree)

    # E-105 `matrix-type`: a stored FLOAT cannot be used as a rotation.
    bad_snap, bad_obj, bad_tree = evaluate("f@w = q@pdt_weight.w;", name="e105a")
    check_empty("E-105 using a FLOAT attribute as a rotation is a node error (matrix-type)",
                bad_snap)
    cleanup(bad_obj)
    bpy.data.node_groups.remove(bad_tree)

    # E-106 `matrix-type`: a stored FLOAT_VECTOR cannot be used as a matrix.
    bad2_snap, bad2_obj, bad2_tree = evaluate("f@w = m@pdt_dir[3][0];", name="e106")
    check_empty("E-106 using a FLOAT_VECTOR as a matrix is a node error (matrix-type)", bad2_snap)
    cleanup(bad2_obj)
    bpy.data.node_groups.remove(bad2_tree)

    # E-107 `rotate-vector`: the rotation functions run on a real attribute.
    rv_snap, rv_obj, rv_tree = evaluate(
        "q@r = axis_angle_rotation({0,0,1}, radians(90)); v@out = rotate_vector({1,0,0}, q@r);",
        name="e107")
    check_close("E-107 rotate_vector on a stored rotation (rotate-vector)",
                tuple(round(value, 5) for value in values(rv_snap, "out")[:3]),
                (0.0, 1.0, 0.0))
    cleanup(rv_obj)
    bpy.data.node_groups.remove(rv_tree)

    # E-108 `func_axes_rotation`: the matrix -> rotation conversion must not mirror the answer.
    ax_snap, ax_obj, ax_tree = evaluate(
        "q@a = axes_rotation(\"z\", \"x\", {1,0,0}, {0,1,0}); "
        "v@z = rotate_vector({0,0,1}, q@a); v@x = rotate_vector({1,0,0}, q@a);",
        name="e108")
    check_close("E-108 the primary axis lands exactly (func_axes_rotation)",
                tuple(round(value, 5) for value in values(ax_snap, "z")[:3]), (1.0, 0.0, 0.0))
    check_close("E-108 and the secondary follows it (func_axes_rotation)",
                tuple(round(value, 5) for value in values(ax_snap, "x")[:3]), (0.0, 1.0, 0.0))
    cleanup(ax_obj)
    bpy.data.node_groups.remove(ax_tree)

    # ------------------------------------------- WR-35/36 transform channels and WR-18/19 reads
    # E-109 `channel-set`/`channel-read`: Rotation and Matrix are channel types, and `chq` / `chm`
    # read them. This goes through `channels.new`, which is what the node's own "+" calls.
    chan_tree = bpy.data.node_groups.new("pdt_wrangle_transform_channels", "GeometryNodeTree")
    chan_tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    chan_tree.interface.new_socket(name="Geometry", in_out="OUTPUT",
                                   socket_type="NodeSocketGeometry")
    chan_in = chan_tree.nodes.new("NodeGroupInput")
    chan_node = chan_tree.nodes.new(NODE)
    chan_out = chan_tree.nodes.new("NodeGroupOutput")
    chan_tree.links.new(chan_in.outputs["Geometry"], chan_node.inputs["Geometry"])
    chan_tree.links.new(chan_node.outputs["Geometry"], chan_out.inputs["Geometry"])
    chan_node.channels.new(socket_type="ROTATION", name="turn")
    chan_node.channels.new(socket_type="MATRIX", name="place")
    # The collection is the truth: `channels.new` hands back a *stale* item whose `socket_type`
    # reads back empty (measured), while the collection already shows the new type.
    stored = {channel.name: channel.socket_type for channel in chan_node.channels}
    check("E-109 a Rotation channel is offered (channel-set)", stored.get("turn"), "ROTATION")
    check("E-109 a Matrix channel too (channel-set)", stored.get("place"), "MATRIX")
    check("E-109 and the node grows a rotation socket (channel-set)",
          chan_node.inputs["turn"].bl_idname, "NodeSocketRotation")
    check("E-109 and a matrix socket (channel-set)",
          chan_node.inputs["place"].bl_idname, "NodeSocketMatrix")
    # A `NodeSocketRotation` carries **Euler** angles (measured: its default is 3 floats), and the
    # host converts them to a quaternion when it reads the channel - so this is the shape a user's
    # rotation channel really has.
    chan_node.inputs["turn"].default_value = (0.0, 0.0, 1.5707963)
    # A `NodeSocketMatrix` has **no** `default_value` in the Python API at all (measured: the
    # property is not on the socket's RNA), so the matrix channel is fed through a link instead -
    # `Object Info` is the one node that publishes a matrix. Its object's world matrix carries the
    # translation this check reads back, which makes the two ends independent.
    chan_driver = bpy.data.objects.new("e109_driver", None)
    chan_driver.matrix_world = mathutils.Matrix.Translation((7.0, 0.0, 0.0))
    bpy.context.scene.collection.objects.link(chan_driver)
    chan_info = chan_tree.nodes.new("GeometryNodeObjectInfo")
    chan_info.transform_space = "RELATIVE"
    chan_info.inputs["Object"].default_value = chan_driver
    chan_tree.links.new(chan_info.outputs["Transform"], chan_node.inputs["place"])
    chan_node.inputs["Code"].default_value = (
        "quat r = chq(\"turn\");\n"
        "matrix x = chm(\"place\");\n"
        "f@w = r.w;\n"
        "v@axis = rotate_vector({1,0,0}, r);\n"
        "f@t = x[0][3];\n"
        "v@moved = x * {0,0,0};")
    chan_mesh = make_cube("e109_src")
    chan_obj = bpy.data.objects.new("e109_obj", chan_mesh)
    bpy.context.scene.collection.objects.link(chan_obj)
    chan_snap = snapshot(chan_obj, chan_tree)
    check("E-109 chq reads the rotation channel's w (channel-read)",
          round(values(chan_snap, "w")[0], 5), 0.70711)
    check_close("E-109 and the rotation really turns a vector (channel-read)",
                tuple(round(v, 4) for v in values(chan_snap, "axis")[:3]), (0.0, 1.0, 0.0))
    check("E-109 chm reads the matrix channel's translation (channel-read)",
          round(values(chan_snap, "t")[0], 4), 7.0)
    cleanup(chan_obj)
    bpy.data.node_groups.remove(chan_tree)

    # E-110 `channel-type-mismatch`: a transform reader on a channel of another type is 0, the same
    # rule the scalars follow.
    mism_snap, mism_obj, mism_tree = evaluate(
        "quat r = chq(\"height\");\nmatrix x = chm(\"axis\");\n"
        "f@w = r.w;\nf@m = x[0][0];\nf@s = chf(\"height\");",
        name="e110",
        channels=[("height", "FLOAT", 2.5), ("axis", "VECTOR", (1.0, 2.0, 3.0))])
    check("E-110 chq on a float channel reads 0 (channel-type-mismatch)",
          values(mism_snap, "w")[0], 0.0)
    check("E-110 chm on a vector channel reads 0 (channel-type-mismatch)",
          values(mism_snap, "m")[0], 0.0)
    check("E-110 the float channel itself still reads (control)", values(mism_snap, "s")[0], 2.5)
    cleanup(mism_obj)
    bpy.data.node_groups.remove(mism_tree)

    # E-111 `point-read-domain`: qpoint / mpoint read a table that is already there.
    # The attributes are seeded on the *input* mesh on purpose: `WR-18` says an explicit-index read
    # sees the frozen input view, so a `q@` written by this same code is NOT visible to `qpoint`
    # (measured: it reads the pre-write value). Seeding first is what a real graph does anyway.
    io_mesh = make_cube("e111_src")
    io_quat = io_mesh.attributes.new("src", "QUATERNION", "POINT")
    io_quat.data[0].value = (0.7071068, 0.0, 0.0, 0.7071068)  # 90 degrees about Z
    io_mat = io_mesh.attributes.new("mat", "FLOAT4X4", "POINT")
    io_mat.data[2].value = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (3, 4, 5, 1))
    io_snap, io_obj, io_tree = evaluate(
        "quat r = qpoint(0, \"mesh\", \"src\", 0);\n"
        "matrix x = mpoint(0, \"mesh\", \"mat\", 2);\n"
        "v@turned = rotate_vector({1,0,0}, r);\n"
        "v@place = {x[0][3], x[1][3], x[2][3]};",
        name="e111",
        mesh=io_mesh)
    check_close("E-111 qpoint reads a stored rotation (point-read-domain)",
                tuple(round(v, 4) for v in values(io_snap, "turned")[:3]), (0.0, 1.0, 0.0))
    check_close("E-111 mpoint reads a stored matrix (point-read-domain)",
                tuple(round(v, 4) for v in values(io_snap, "place")[:3]), (3.0, 4.0, 5.0))
    cleanup(io_obj)
    bpy.data.node_groups.remove(io_tree)

    # E-112 `prim-read-domain`: qprim / mprim read the face table. The mesh is a cube, so there are
    # six faces; the seeded rotation lives on the point domain and `qprim` asks the *face* table,
    # which has no `src` there - so the honest assertion is both ends: reading a transform the face
    # table does have works, and asking a table that lacks it reads 0 without an error (`WR-20`).
    prim_mesh = make_cube("e112_src")
    prim_quat = prim_mesh.attributes.new("face_src", "QUATERNION", "FACE")
    for face in range(len(prim_mesh.polygons)):
        prim_quat.data[face].value = (0.7071068, 0.0, 0.0, 0.7071068)
    prim_snap, prim_obj, prim_tree = evaluate(
        "quat r = qprim(0, \"mesh\", \"face_src\", 0);\n"
        "quat missing = qprim(0, \"mesh\", \"src\", 0);\n"
        "v@turned = rotate_vector({1,0,0}, r);\n"
        "f@w = missing.w;",
        name="e112",
        mesh=prim_mesh)
    check_close("E-112 qprim reads the prim-domain rotation (prim-read-domain)",
                tuple(round(v, 4) for v in values(prim_snap, "turned")[:3]), (0.0, 1.0, 0.0))
    check("E-112 and a name the prim table does not have reads 0 (prim-read-domain)",
          values(prim_snap, "w")[0], 0.0)
    cleanup(prim_obj)
    bpy.data.node_groups.remove(prim_tree)

    # E-113 `set-point-attrib`: the indexed write already carries the transform types (`WR-22`).
    set_snap, set_obj, set_tree = evaluate(
        "quat r = axis_angle_rotation({0,0,1}, radians(90));\n"
        "setpointattrib(0, \"mesh\", \"rot\", 0, r);\n"
        "matrix x = combine_transform({1,2,3}, quaternion(1,0,0,0), {1,1,1});\n"
        "setpointattrib(0, \"mesh\", \"xform\", 0, x);",
        name="e113")
    check("E-113 setpointattrib creates a QUATERNION attribute (set-point-attrib)",
          types(set_snap, "rot"), "QUATERNION")
    check("E-113 setpointattrib creates a FLOAT4X4 attribute (set-point-attrib)",
          types(set_snap, "xform"), "FLOAT4X4")
    check("E-113 and the quaternion lands w-first (set-point-attrib)",
          round(values(set_snap, "rot")[0], 5), 0.70711)
    # The flat 16 floats are column-major, so the translation is at 12/13/14 - the same place
    # `m[0][3]` / `m[1][3]` / `m[2][3]` read.
    check("E-113 and the matrix keeps its translation (set-point-attrib)",
          tuple(round(v, 4) for v in values(set_snap, "xform")[12:15]), (1.0, 2.0, 3.0))
    cleanup(set_obj)
    bpy.data.node_groups.remove(set_tree)

    # E-114 `point-read-domain`: an index the table does not have reads 0 for both transform types.
    oob_mesh = make_cube("e114_src")
    oob_quat = oob_mesh.attributes.new("src", "QUATERNION", "POINT")
    oob_quat.data[0].value = (0.7071068, 0.0, 0.0, 0.7071068)
    oob_mat = oob_mesh.attributes.new("mat", "FLOAT4X4", "POINT")
    oob_mat.data[0].value = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (3, 4, 5, 1))
    oob_snap, oob_obj, oob_tree = evaluate(
        "quat r = qpoint(0, \"mesh\", \"src\", 99);\n"
        "matrix x = mpoint(0, \"mesh\", \"mat\", -1);\n"
        "f@w = r.w;\nf@t = x[0][3];",
        name="e114",
        mesh=oob_mesh)
    check("E-114 an out-of-range qpoint reads 0 (point-read-domain)",
          values(oob_snap, "w")[0], 0.0)
    check("E-114 a negative mpoint index reads 0 (point-read-domain)",
          values(oob_snap, "t")[0], 0.0)
    cleanup(oob_obj)
    bpy.data.node_groups.remove(oob_tree)

    print(f"== test_pdt_wrangle: {CHECKS} checks, {len(FAILURES)} failed")
    if FAILURES:
        for label in FAILURES:
            print(f"   failed: {label}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
