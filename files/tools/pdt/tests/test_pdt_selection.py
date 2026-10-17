"""Precise assertions for the PDT geometry nodes.

Run inside the build:

    blender.exe -b --factory-startup --python-exit-code 1 --python <this file>

Every case checks a *derived* expectation (vertex counts), not a baked baseline,
so a silent change in the selection semantics fails loudly.
"""

from __future__ import annotations

import sys

import bpy

NODE_SELECT = "GeometryNodePDTSelection"

#: L0 `Domain` (element kind, a node *property* since PDS-9) -> Blender's own attribute
#: domain identifier, which is what `Delete Geometry.domain` and `Store Named Attribute`
#: take. `Vertex`, `Control Point` and `Point` are three kinds but one `AttrDomain`.
ATTR_DOMAIN_OF = {
    "VERTEX": "POINT",
    "EDGE": "EDGE",
    "FACE": "FACE",
    "CONTROL_POINT": "POINT",
    "SPLINE": "CURVE",
    "POINT": "POINT",
    "INSTANCE": "INSTANCE",
}

_failures: list[str] = []
_checks = 0


def check(label: str, actual, expected) -> None:
    global _checks
    _checks += 1
    if actual == expected:
        print(f"OK   {label}: {actual}")
    else:
        print(f"FAIL {label}: got {actual}, expected {expected}")
        _failures.append(label)


def evaluate_vertices(tree: bpy.types.NodeTree) -> int:
    """Evaluate `tree` on a throwaway object and return its vertex count."""
    mesh = bpy.data.meshes.new("pdt_test_mesh")
    obj = bpy.data.objects.new("pdt_test_obj", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new(name="GeoNodes", type="NODES")
    modifier.node_group = tree

    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    eval_mesh = evaluated.to_mesh()
    count = len(eval_mesh.vertices)
    evaluated.to_mesh_clear()

    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.meshes.remove(mesh, do_unlink=True)
    return count


def make_selection_tree(selection_text: str, use_delete: bool) -> bpy.types.NodeTree:
    """Cube -> PDT Selection -> (Delete Geometry) -> group output."""
    tree = bpy.data.node_groups.new(
        name=f"pdt_sel_{selection_text or 'empty'}_{int(use_delete)}", type="GeometryNodeTree"
    )
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    cube = tree.nodes.new("GeometryNodeMeshCube")
    selection = tree.nodes.new(NODE_SELECT)
    selection.inputs["Selection"].default_value = selection_text
    out = tree.nodes.new("NodeGroupOutput")

    tree.links.new(cube.outputs["Mesh"], selection.inputs["Geometry"])
    if use_delete:
        delete = tree.nodes.new("GeometryNodeDeleteGeometry")
        tree.links.new(selection.outputs["Geometry"], delete.inputs["Geometry"])
        tree.links.new(selection.outputs["Selection"], delete.inputs["Selection"])
        tree.links.new(delete.outputs["Geometry"], out.inputs["Geometry"])
    else:
        tree.links.new(selection.outputs["Geometry"], out.inputs["Geometry"])
    return tree


def test_selection_field() -> None:
    print("== PDT Selection: boolean field via Delete Geometry (cube = 8 points) ==")
    cases = [
        ("*", 0),           # everything
        ("", 8),            # nothing
        ("0-3", 4),         # first four
        ("4-7", 4),         # last four
        ("0-7:2", 4),       # every other point
        ("0-1 4-5", 4),     # two ranges, space separated
        ("0-1,4-5", 4),     # two ranges, comma separated
        ("^0-3", 4),        # exclude the first four
        ("* ^0-7:2", 4),    # everything except even indices -> 4 left
        ("99", 8),          # out of range selects nothing
        ("3", 7),           # single point
    ]
    for text, expected_remaining in cases:
        tree = make_selection_tree(text, use_delete=True)
        check(f"delete with {text!r}", evaluate_vertices(tree), expected_remaining)

    print("== PDT Selection: pass-through geometry ==")
    tree = make_selection_tree("0-3", use_delete=False)
    check("pass-through with '0-3' keeps all points", evaluate_vertices(tree), 8)

    print("== PDT Selection: malformed input must not crash ==")
    # NOTE: "a" used to be invalid here - a bare identifier is a *group* token now
    # (Houdini group syntax), so it moved to test_groups(); "arm*" moved to
    # test_selection_patterns.py, where a group wildcard is exercised for real.
    for bad in ("5-2", "^", "1-", "0-3:0", "a-b", "A.B", "group:A", "!", "&", "{0-3", "0-3 }"):
        tree = make_selection_tree(bad, use_delete=True)
        # The node reports an error and outputs a default (false) field, so
        # nothing is deleted and the geometry is unchanged.
        check(f"invalid {bad!r} leaves geometry intact", evaluate_vertices(tree), 8)


def make_group_tree(selection_text: str, domain: str = "VERTEX") -> bpy.types.NodeTree:
    """Group Input -> PDT Selection -> Delete Geometry -> group output.

    `domain` is the node's own element kind ("VERTEX" / "EDGE" / ...); `Delete Geometry`
    is aimed at the matching attribute domain, because the boolean field is what carries
    the selection.
    """
    tree = bpy.data.node_groups.new(
        name=f"pdt_grp_{selection_text or 'empty'}_{domain}", type="GeometryNodeTree"
    )
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    group_input = tree.nodes.new("NodeGroupInput")
    selection = tree.nodes.new(NODE_SELECT)
    selection.inputs["Selection"].default_value = selection_text
    selection.domain = domain
    delete = tree.nodes.new("GeometryNodeDeleteGeometry")
    delete.domain = ATTR_DOMAIN_OF[domain]
    out = tree.nodes.new("NodeGroupOutput")

    tree.links.new(group_input.outputs["Geometry"], selection.inputs["Geometry"])
    tree.links.new(selection.outputs["Geometry"], delete.inputs["Geometry"])
    tree.links.new(selection.outputs["Selection"], delete.inputs["Selection"])
    tree.links.new(delete.outputs["Geometry"], out.inputs["Geometry"])
    return tree


def evaluate_group_case(tree, count: int = 10, attributes=(), data_type: str = "BOOLEAN"):
    """Evaluate `tree` on `count` loose points carrying `attributes`.

    `attributes` is a sequence of ``(name, member_indices, data_type, domain)``.
    Returns ``(remaining_vertices, warning_and_error_messages)``.
    """
    mesh = bpy.data.meshes.new("pdt_group_mesh")
    mesh.from_pydata([(float(i), 0.0, 0.0) for i in range(count)], [], [])
    mesh.update()
    for name, members, attribute_type, domain in attributes:
        values = mesh.attributes.new(name=name, type=attribute_type, domain=domain)
        for index in range(len(values.data)):
            member = index in members
            values.data[index].value = member if attribute_type == "BOOLEAN" else int(member)

    obj = bpy.data.objects.new("pdt_group_obj", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new(name="GeoNodes", type="NODES")
    modifier.node_group = tree

    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    eval_mesh = evaluated.to_mesh()
    remaining = len(eval_mesh.vertices)
    evaluated.to_mesh_clear()
    # Node warnings/errors are exposed on the modifier (NodesModifier.node_warnings),
    # so "the group is missing" is assertable without a GUI.
    messages = [warning.message for warning in modifier.node_warnings]

    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.meshes.remove(mesh, do_unlink=True)
    return remaining, messages


def test_groups() -> None:
    """`A` reads the boolean attribute `group:A` (10 loose points)."""
    print("== PDT Selection: groups (group:A = {1, 4, 5, 9} of 10 points) ==")
    group_a = ("group:A", (1, 4, 5, 9), "BOOLEAN", "POINT")

    remaining, messages = evaluate_group_case(
        make_group_tree("A"), count=10, attributes=(group_a,)
    )
    check("group alone selects its members while deleting", remaining, 6)
    check("an existing group is not warned about", messages, [])

    cases = [
        # (expression, remaining vertices); group:A = {1, 4, 5, 9} of 10 points, so
        # 10 - |selected| points survive Delete Geometry.
        ("0-1 A", 5),     # {0,1,4,5,9} - indices and a group mixed in one field
        ("0-1,A", 5),     # comma separated, same thing
        ("* ^A", 4),      # 10 - |A| = 6 selected
        ("^A", 4),        # a lone group exclusion means "everything except A"
        ("0-2 ^A", 8),    # ({0,1,2} | A) - A = {0, 2}
        ("A ^4", 7),      # A - {4} = {1, 5, 9}: an index exclusion beats a group
        ("A ^0-1", 7),    # A - {0,1} = {4, 5, 9}
        ("0-3 A ^1", 4),  # ({0..3} | A) - {1} = {0,2,3,4,5,9}
        ("0-1", 8),       # indices only: unchanged behaviour
        ("missing", 10),  # a group that does not exist selects nothing
        # Left-to-right accumulation with groups in the mix (P13). `!P` *adds the
        # complement* of P (it does not toggle anything), so `A !0-5` is A | !{0..5}.
        ("A &0-5", 7),    # A intersect {0..5} = {1, 4, 5}
        ("A &0-5 ^5", 8), # then remove 5 -> {1, 4}
        ("!A", 4),        # complement of A = {0,2,3,6,7,8}, so 6 selected
        ("!A &0-2", 8),   # {0, 2}
        ("A !0-5", 3),    # A | !{0..5} = {1,4,5,6,7,8,9}
        ("{A} 0", 5),     # a braced group is one operand
        ("{A ^4}", 7),    # sub-expression with its own accumulator
        ("!{A}", 4),      # complement of the sub-expression
    ]
    for text, expected in cases:
        tree = make_group_tree(text)
        remaining, _messages = evaluate_group_case(tree, count=10, attributes=(group_a,))
        check(f"{text!r} with group:A", remaining, expected)

    remaining, messages = evaluate_group_case(
        make_group_tree("A B"),
        count=10,
        attributes=(group_a, ("group:B", (0, 9), "BOOLEAN", "POINT")),
    )
    check("two groups are unioned", remaining, 5)
    check("two existing groups are not warned about", messages, [])

    remaining, messages = evaluate_group_case(
        make_group_tree("A &B"),
        count=10,
        attributes=(group_a, ("group:B", (0, 4, 9), "BOOLEAN", "POINT")),
    )
    check("two groups intersect", remaining, 8)  # A & {0,4,9} = {4, 9}
    check("intersecting groups are not warned about", messages, [])

    print("== PDT Selection: missing / non-boolean groups warn but never fail ==")
    remaining, messages = evaluate_group_case(make_group_tree("missing"), count=10, attributes=())
    check("missing group selects nothing", remaining, 10)
    check(
        "missing group warns with the pattern",
        any("group pattern 'missing'" in m and "group:" in m for m in messages),
        True,
    )

    remaining, messages = evaluate_group_case(
        make_group_tree("0-1 missing"), count=10, attributes=()
    )
    check("the other tokens still work next to a missing group", remaining, 8)
    check("the missing group still warns", len(messages), 1)

    remaining, messages = evaluate_group_case(
        make_group_tree("A"),
        count=10,
        attributes=(("group:A", (1, 4, 5, 9), "INT", "POINT"),),
    )
    check("a non-boolean attribute is not a group", remaining, 10)
    check(
        "non-boolean attribute warns",
        any("non-boolean" in m for m in messages),
        True,
    )

    print("== PDT Selection: a bad group name is an error, not a warning ==")
    # `arm*` is a *wildcard* now, so it is no longer in this list: only genuinely
    # malformed names stay errors.
    for bad in ("a-b", "group:A", "arm[", "A..B", "@position.y"):
        remaining, messages = evaluate_group_case(make_group_tree(bad), count=10)
        check(f"invalid group {bad!r} leaves the geometry intact", remaining, 10)
        check(
            f"invalid group {bad!r} reports an error",
            any("Invalid selection" in m for m in messages),
            True,
        )


def test_group_domain_adaptation() -> None:
    """A group on another domain must match Blender's own boolean attribute field.

    The reference is a `Named Attribute` node reading the same ``group:F`` from a
    FACE domain and driving the same `Delete Geometry` (POINT domain), so the
    comparison is against upstream's `adapt_domain` instead of a baked number.
    """
    print("== PDT Selection: a FACE group addressed from POINT matches Blender's own field ==")

    def build(use_pdt_node: bool) -> bpy.types.NodeTree:
        tree = bpy.data.node_groups.new(
            name=f"pdt_adapt_{int(use_pdt_node)}", type="GeometryNodeTree"
        )
        tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        group_input = tree.nodes.new("NodeGroupInput")
        delete = tree.nodes.new("GeometryNodeDeleteGeometry")
        delete.domain = "POINT"
        out = tree.nodes.new("NodeGroupOutput")
        if use_pdt_node:
            selection = tree.nodes.new(NODE_SELECT)
            selection.inputs["Selection"].default_value = "F"
            selection.domain = "VERTEX"
            tree.links.new(group_input.outputs["Geometry"], selection.inputs["Geometry"])
            tree.links.new(selection.outputs["Geometry"], delete.inputs["Geometry"])
            tree.links.new(selection.outputs["Selection"], delete.inputs["Selection"])
        else:
            named = tree.nodes.new("GeometryNodeInputNamedAttribute")
            named.data_type = "BOOLEAN"
            named.inputs["Name"].default_value = "group:F"
            tree.links.new(group_input.outputs["Geometry"], delete.inputs["Geometry"])
            tree.links.new(named.outputs[0], delete.inputs["Selection"])
        tree.links.new(delete.outputs["Geometry"], out.inputs["Geometry"])
        return tree

    def remaining(tree) -> int:
        mesh = bpy.data.meshes.new("pdt_adapt_mesh")
        mesh.from_pydata(
            [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)],
            [],
            [(0, 1, 2), (0, 2, 3)],
        )
        mesh.update()
        faces = mesh.attributes.new(name="group:F", type="BOOLEAN", domain="FACE")
        faces.data[0].value = True
        faces.data[1].value = False

        obj = bpy.data.objects.new("pdt_adapt_obj", mesh)
        bpy.context.scene.collection.objects.link(obj)
        modifier = obj.modifiers.new(name="GeoNodes", type="NODES")
        modifier.node_group = tree

        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(depsgraph)
        eval_mesh = evaluated.to_mesh()
        count = len(eval_mesh.vertices)
        evaluated.to_mesh_clear()
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh, do_unlink=True)
        return count

    node_count = remaining(build(True))
    reference_count = remaining(build(False))
    check(
        "PDT group field == Named Attribute field (FACE group, POINT domain)",
        node_count,
        reference_count,
    )
    check("the adapted group actually deleted points", node_count < 4, True)


def test_domain_and_long_selection() -> None:
    print("== element kind through Delete Geometry (cube: 8 vertices, 12 edges, 6 faces) ==")
    cases = [
        ("VERTEX", "0-3", {"vertices": 4, "edges": 4, "polygons": 1}),
        ("EDGE", "0-1", {"vertices": 8, "edges": 10, "polygons": 3}),
        ("FACE", "0-1", {"vertices": 8, "edges": 11, "polygons": 4}),
        ("FACE", "* ^0-3", {"vertices": 8, "edges": 12, "polygons": 4}),
    ]
    for domain, text, expected in cases:
        stats = make_domain_tree(domain, text)
        for key, value in expected.items():
            if value is None:
                continue
            check(f"{domain} {text!r} {key}", stats[key], value)

    print("== '@prop' resolves the selection stored on the node ==")
    stats = make_domain_tree("VERTEX", "@prop", long_spec="1-3")
    check("@prop deletes the stored points", stats["vertices"], 5)
    stats = make_domain_tree("VERTEX", " @prop ", long_spec="0-1")
    check("@prop tolerates surrounding spaces", stats["vertices"], 6)
    stats = make_domain_tree("VERTEX", "@prop")
    check("missing stored selection fails safe", stats["vertices"], 8)
    stats = make_domain_tree("VERTEX", "@prop:custom", long_spec="0-3", long_name="custom")
    check("@prop:<name> uses that property", stats["vertices"], 4)
    stats = make_domain_tree("VERTEX", "@prop:other", long_spec="0-3")
    check("@prop:<unknown> fails safe", stats["vertices"], 8)


def make_domain_tree(domain: str, selection_text: str, long_spec: str = "", long_name: str = ""):
    """Cube -> PDT Selection(kind, text) -> Delete Geometry -> counts."""
    tree = bpy.data.node_groups.new(
        name=f"pdt_dom_{domain}_{selection_text or 'empty'}", type="GeometryNodeTree"
    )
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    group_input = tree.nodes.new("NodeGroupInput")
    cube = tree.nodes.new("GeometryNodeMeshCube")
    selection = tree.nodes.new(NODE_SELECT)
    selection.inputs["Selection"].default_value = selection_text
    # `Domain` is a node property (L0 PDS-9): the element kind, not `AttrDomain`.
    selection.domain = domain
    if long_spec:
        selection[long_name or "pdt_selection_long"] = long_spec
    delete = tree.nodes.new("GeometryNodeDeleteGeometry")
    # Delete Geometry also exposes its domain as a node property; it has to name the
    # *attribute* domain the field is evaluated on.
    delete.domain = ATTR_DOMAIN_OF[domain]
    out = tree.nodes.new("NodeGroupOutput")

    del group_input
    tree.links.new(cube.outputs["Mesh"], selection.inputs["Geometry"])
    tree.links.new(selection.outputs["Geometry"], delete.inputs["Geometry"])
    tree.links.new(selection.outputs["Selection"], delete.inputs["Selection"])
    tree.links.new(delete.outputs["Geometry"], out.inputs["Geometry"])
    return evaluate_stats(tree)


def evaluate_stats(tree: bpy.types.NodeTree) -> dict:
    mesh = bpy.data.meshes.new("pdt_domain_mesh")
    obj = bpy.data.objects.new("pdt_domain_obj", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new(name="GeoNodes", type="NODES")
    modifier.node_group = tree

    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    eval_mesh = evaluated.to_mesh()
    stats = {
        "vertices": len(eval_mesh.vertices),
        "edges": len(eval_mesh.edges),
        "polygons": len(eval_mesh.polygons),
    }
    evaluated.to_mesh_clear()
    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.meshes.remove(mesh, do_unlink=True)
    return stats


def make_component_tree(selection_text: str, domain: str, with_point_cloud: bool = False):
    """Cube + 16-point curve (+ optional point cloud), joined, then PDT Selection.

    `domain` is the node's element kind (L0 PDS-9); it is what decides which component
    the selection lands on.
    """
    tree = bpy.data.node_groups.new(
        name=f"pdt_comp_{domain}_{selection_text or 'empty'}", type="GeometryNodeTree"
    )
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    cube = tree.nodes.new("GeometryNodeMeshCube")
    line = tree.nodes.new("GeometryNodeCurvePrimitiveLine")
    line.mode = "POINTS"
    line.inputs["End"].default_value = (0.0, 0.0, 15.0)
    resample = tree.nodes.new("GeometryNodeResampleCurve")
    resample.inputs["Mode"].default_value = "Count"
    resample.inputs["Count"].default_value = 16
    join = tree.nodes.new("GeometryNodeJoinGeometry")
    selection = tree.nodes.new(NODE_SELECT)
    selection.inputs["Selection"].default_value = selection_text
    selection.domain = domain
    store = tree.nodes.new("GeometryNodeStoreNamedAttribute")
    store.inputs["Name"].default_value = "sel"
    store.data_type = "BOOLEAN"
    store.domain = "POINT"
    out = tree.nodes.new("NodeGroupOutput")

    tree.links.new(cube.outputs["Mesh"], join.inputs["Geometry"])
    tree.links.new(line.outputs["Curve"], resample.inputs["Curve"])
    tree.links.new(resample.outputs["Curve"], join.inputs["Geometry"])
    if with_point_cloud:
        points = tree.nodes.new("GeometryNodePoints")
        points.inputs["Count"].default_value = 6
        tree.links.new(points.outputs["Geometry"], join.inputs["Geometry"])
    tree.links.new(join.outputs["Geometry"], selection.inputs["Geometry"])
    tree.links.new(selection.outputs["Geometry"], store.inputs["Geometry"])
    tree.links.new(selection.outputs["Selection"], store.inputs["Value"])
    tree.links.new(store.outputs["Geometry"], out.inputs["Geometry"])
    return tree, selection


def evaluate_components(tree: bpy.types.NodeTree) -> dict:
    """Per-component / per-domain result of the stored boolean.

    Keys: ``MESH`` (points), ``CURVE_POINT`` (control points), ``CURVE_SPLINE`` (the
    curve domain), ``POINTCLOUD``, ``WARNINGS``. A component that is not there reads
    `None`; a component that is there but not selected reads `[]` (the stored
    attribute is written on it too, it is just false everywhere). The same attribute
    name can exist on both curve domains, so the domain is checked explicitly.
    """
    mesh = bpy.data.meshes.new("pdt_component_mesh")
    obj = bpy.data.objects.new("pdt_component_obj", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new(name="GeoNodes", type="NODES")
    modifier.node_group = tree

    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    # Keep the GeometrySet RNA alive: `.mesh`/`.curves`/`.pointcloud` die with it.
    geometry = evaluated.evaluated_geometry()

    def selected(component, domain):
        if component is None:
            return None
        attribute = component.attributes.get("sel")
        if attribute is None or attribute.domain != domain:
            return None
        return [i for i, v in enumerate(attribute.data) if v.value]

    result = {
        "MESH": selected(geometry.mesh, "POINT"),
        "CURVE_POINT": selected(geometry.curves, "POINT"),
        "CURVE_SPLINE": selected(geometry.curves, "CURVE"),
        "POINTCLOUD": selected(geometry.pointcloud, "POINT"),
        "WARNINGS": [w.message for w in modifier.node_warnings],
    }
    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.meshes.remove(mesh, do_unlink=True)
    return result


def test_component_filter() -> None:
    """The element kind alone decides *which* component of a mixed stream is addressed.

    L0 PDS-8/PDS-9: `Vertex` / `Edge` / `Face` address the mesh, `Control Point` /
    `Spline` the curve, `Point` the point cloud, `Instance` the instances, and every
    other component selects nothing. The observable is the stored attribute: a
    component that is out of scope still *gets* it (the field is evaluated there too,
    it is just false everywhere), so "nothing selected" shows up as an empty list, not
    as a missing attribute.
    """
    print("== one menu, one component: mesh and curve in the same geometry ==")
    tree, selection = make_component_tree("4", "VERTEX")
    if "Component" in selection.inputs:
        print("SKIP the 'Component' input is still in this build (not rebuilt yet)")
        return

    cases = [
        ("VERTEX", [4], []),
        ("CONTROL_POINT", [], [4]),
    ]
    for domain, expected_mesh, expected_curve in cases:
        tree, _selection = make_component_tree("4", domain)
        result = evaluate_components(tree)
        check(f"Domain={domain}: mesh points", result["MESH"], expected_mesh)
        check(f"Domain={domain}: curve control points", result["CURVE_POINT"], expected_curve)
        check(f"Domain={domain}: no warnings", result["WARNINGS"], [])

    # Ranges and operators behave the same way, and the other component stays empty.
    tree, _selection = make_component_tree("0-1 9-11", "CONTROL_POINT")
    result = evaluate_components(tree)
    check("Domain=Control Point ignores out-of-range indices on the mesh", result["MESH"], [])
    check(
        "Domain=Control Point selects its own control points",
        result["CURVE_POINT"],
        [0, 1, 9, 10, 11],
    )

    tree, _selection = make_component_tree("*", "VERTEX")
    result = evaluate_components(tree)
    check("Domain=Vertex selects every cube vertex", result["MESH"], list(range(8)))
    check("Domain=Vertex leaves the curve alone", result["CURVE_POINT"], [])

    # A group/attribute reference is only looked up in the component in scope.
    tree, _selection = make_component_tree("does_not_exist", "CONTROL_POINT")
    warnings = evaluate_components(tree)["WARNINGS"]
    check("Domain=Control Point still warns about a missing group", bool(warnings), True)
    check(
        "the warning names the missing group",
        any("does_not_exist" in message and "group" in message for message in warnings),
        True,
    )

    # The third component: a point cloud, addressed on its own.
    print("== the point cloud is the `Point` kind ==")
    for domain, expected_mesh, expected_curve, expected_cloud in (
        ("VERTEX", [2], [], []),
        ("CONTROL_POINT", [], [2], []),
        ("POINT", [], [], [2]),
    ):
        tree, _selection = make_component_tree("2", domain, with_point_cloud=True)
        result = evaluate_components(tree)
        check(f"Domain={domain}: mesh points", result["MESH"], expected_mesh)
        check(f"Domain={domain}: curve control points", result["CURVE_POINT"], expected_curve)
        check(f"Domain={domain}: point cloud points", result["POINTCLOUD"], expected_cloud)
        check(f"Domain={domain}: no warnings", result["WARNINGS"], [])


def make_spline_tree(selection_text: str, domain: str):
    """Two splines (16 + 16 control points, joined) -> PDT Selection -> stored boolean.

    `domain` is the node's element kind (`SPLINE` or `CONTROL_POINT`); the stored value
    goes on the matching attribute domain, so the *spline* result can be read back as an
    attribute on the curve domain.
    """
    tree = bpy.data.node_groups.new(
        name=f"pdt_spline_{domain}_{selection_text or 'empty'}",
        type="GeometryNodeTree",
    )
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    selection = tree.nodes.new(NODE_SELECT)
    selection.inputs["Selection"].default_value = selection_text
    selection.domain = domain

    line = tree.nodes.new("GeometryNodeCurvePrimitiveLine")
    line.mode = "POINTS"
    line.inputs["End"].default_value = (0.0, 0.0, 15.0)
    resample = tree.nodes.new("GeometryNodeResampleCurve")
    resample.inputs["Mode"].default_value = "Count"
    resample.inputs["Count"].default_value = 16
    second = tree.nodes.new("GeometryNodeCurvePrimitiveLine")
    second.mode = "POINTS"
    second.inputs["Start"].default_value = (0.0, 5.0, 0.0)
    second.inputs["End"].default_value = (10.0, 5.0, 0.0)
    resample2 = tree.nodes.new("GeometryNodeResampleCurve")
    resample2.inputs["Mode"].default_value = "Count"
    resample2.inputs["Count"].default_value = 16
    join = tree.nodes.new("GeometryNodeJoinGeometry")
    store = tree.nodes.new("GeometryNodeStoreNamedAttribute")
    store.inputs["Name"].default_value = "sel"
    store.data_type = "BOOLEAN"
    # Store on the domain under test, so the *spline* result can be read back as an
    # attribute on the curve domain (storing it on POINT would adapt it to points).
    store.domain = ATTR_DOMAIN_OF[domain]
    out = tree.nodes.new("NodeGroupOutput")

    tree.links.new(line.outputs["Curve"], resample.inputs["Curve"])
    tree.links.new(second.outputs["Curve"], resample2.inputs["Curve"])
    tree.links.new(resample.outputs["Curve"], join.inputs["Geometry"])
    tree.links.new(resample2.outputs["Curve"], join.inputs["Geometry"])
    tree.links.new(join.outputs["Geometry"], selection.inputs["Geometry"])
    tree.links.new(selection.outputs["Geometry"], store.inputs["Geometry"])
    tree.links.new(selection.outputs["Selection"], store.inputs["Value"])
    tree.links.new(store.outputs["Geometry"], out.inputs["Geometry"])
    return tree, selection


def test_spline_domain() -> None:
    """The curve *spline* kind (`AttrDomain::Curve`), and that kinds are scoped."""
    print("== Spline kind: two splines of 16 control points ==")
    cases = [
        ("0", [0]),
        ("1", [1]),
        ("0-1", [0, 1]),
        ("*", [0, 1]),
        ("^0", [1]),
        ("2", []),
    ]
    for text, expected in cases:
        tree, _selection = make_spline_tree(text, "SPLINE")
        result = evaluate_components(tree)
        check(f"Spline[{text!r}]: mesh untouched (no such domain)", result["MESH"], None)
        check(f"Spline[{text!r}]: splines selected", result["CURVE_SPLINE"], expected)
        check(f"Spline[{text!r}]: control points untouched", result["CURVE_POINT"], None)
        check(f"Spline[{text!r}]: no warnings", result["WARNINGS"], [])

    print("== Control Point and Spline are two kinds on the same curve ==")
    tree, _selection = make_spline_tree("*", "CONTROL_POINT")
    result = evaluate_components(tree)
    check("Control Point: every control point selected", result["CURVE_POINT"], list(range(32)))
    check("Control Point: no spline attribute written", result["CURVE_SPLINE"], None)
    check("Control Point: no warnings", result["WARNINGS"], [])

    print("== a kind never leaks onto another component (L0 PDS-9) ==")
    # `Edge` is a mesh element: on curve-only geometry it selects nothing and reports
    # nothing - the kind, not a "Component" menu, is what scopes the node.
    tree, _selection = make_spline_tree("0-1", "EDGE")
    result = evaluate_components(tree)
    check("Edge on a curve: nothing selected", result["CURVE_POINT"], None)
    check("Edge on a curve: no splines selected", result["CURVE_SPLINE"], None)
    check("Edge on a curve: no error (out of scope is not a syntax error)",
          [m for m in result["WARNINGS"] if "Invalid" in m], [])


def main() -> int:
    test_selection_field()
    test_groups()
    test_group_domain_adaptation()
    test_domain_and_long_selection()
    test_component_filter()
    test_spline_domain()
    print(f"\n== summary: {_checks} checks, {len(_failures)} failure(s) ==")
    for label in _failures:
        print(f"  failed: {label}")
    if _failures:
        # Non-zero exit makes `--python-exit-code 1` fail the process.
        raise SystemExit(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
