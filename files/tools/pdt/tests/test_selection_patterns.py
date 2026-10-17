"""PDT Selection patterns that only the node implements (P13).

The picker writes plain indices, so Python deliberately does not evaluate groups or
attribute patterns. Everything the node supports beyond index editing is therefore
pinned down here, against the real node, on real geometry:

* group name wildcards (`arm*`) and `{ }` sub-expressions;
* the `!` complement, and `&` intersection as a *left-to-right* operation (not a
  symmetric set operator);
* `@attribute` patterns, including component selection (`.y`, `[1]`), value lists
  (`@x=3-5`) and the integer `&` operator;
* the warnings for a group/attribute that the geometry does not provide.

Every case is expressed as "how many points survive Delete Geometry", so the
numbers are derived from the documented sets and a silent change of semantics
fails the run.
"""

from __future__ import annotations

import sys

import bpy

NODE_SELECT = "GeometryNodePDTSelection"
POINT_COUNT = 12

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


def make_tree(selection_text: str) -> bpy.types.NodeTree:
    """Group Input -> PDT Selection -> Delete Geometry -> group output."""
    tree = bpy.data.node_groups.new(
        name=f"pdt_pat_{selection_text or 'empty'}", type="GeometryNodeTree"
    )
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    group_input = tree.nodes.new("NodeGroupInput")
    selection = tree.nodes.new(NODE_SELECT)
    selection.inputs["Selection"].default_value = selection_text
    delete = tree.nodes.new("GeometryNodeDeleteGeometry")
    delete.domain = "POINT"
    out = tree.nodes.new("NodeGroupOutput")

    tree.links.new(group_input.outputs["Geometry"], selection.inputs["Geometry"])
    tree.links.new(selection.outputs["Geometry"], delete.inputs["Geometry"])
    tree.links.new(selection.outputs["Selection"], delete.inputs["Selection"])
    tree.links.new(delete.outputs["Geometry"], out.inputs["Geometry"])
    return tree


def evaluate(tree, count: int = POINT_COUNT, attributes=()):
    """Evaluate `tree` on `count` loose points at x=i carrying `attributes`.

    `attributes` is a sequence of ``(name, member_indices, data_type, domain)``.
    Returns ``(surviving_vertex_count, warning_and_error_messages)``. **Delete
    Geometry keeps the points the selection does _not_ match**, so
    `remaining == count - len(selected)`.
    """
    mesh = bpy.data.meshes.new("pdt_pattern_mesh")
    mesh.from_pydata([(float(i), 0.0, 0.0) for i in range(count)], [], [])
    mesh.update()
    for name, members, attribute_type, domain in attributes:
        values = mesh.attributes.new(name=name, type=attribute_type, domain=domain)
        for index in range(len(values.data)):
            member = index in members
            if attribute_type == "BOOLEAN":
                values.data[index].value = member
            else:
                values.data[index].value = int(member)

    obj = bpy.data.objects.new("pdt_pattern_obj", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new(name="GeoNodes", type="NODES")
    modifier.node_group = tree

    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    eval_mesh = evaluated.to_mesh()
    remaining = len(eval_mesh.vertices)
    evaluated.to_mesh_clear()
    # Node warnings/errors are exposed on the modifier (NodesModifier.node_warnings).
    messages = [warning.message for warning in modifier.node_warnings]

    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.meshes.remove(mesh, do_unlink=True)
    return remaining, messages


#: Overlapping boolean groups, so a wildcard has to union several of them.
#:   arm   = {0, 1}
#:   arm2  = {2, 3}
#:   armX  = {5}
#:   leg   = {7, 8}
#:   slim  = {10}   (not `arm*`, used for `?*`)
WILD_GROUPS = (
    ("group:arm", (0, 1), "BOOLEAN", "POINT"),
    ("group:arm2", (2, 3), "BOOLEAN", "POINT"),
    ("group:armX", (5,), "BOOLEAN", "POINT"),
    ("group:leg", (7, 8), "BOOLEAN", "POINT"),
    ("group:slim", (10,), "BOOLEAN", "POINT"),
)


def test_group_wildcards() -> None:
    print("== group name wildcards and { } sub-expressions ==")
    cases = [
        # Re-derived by hand from WILD_GROUPS when the P13 semantics were fixed
        # (`!` = union the complement) - the previous numbers were written against a
        # node whose `^`, `&` and `!` tokens did not even parse, so several of them
        # contradicted their own inline comment. Each number below is
        # `12 - |selected|`, spelled out in the comment.
        ("arm*", 12 - 5),          # arm|arm2|armX = {0,1,2,3,5}
        ("arm?", 12 - 3),          # arm2|armX = {2,3,5} (`?` is exactly one char)
        ("arm* ^arm2", 12 - 3),    # {0,1,5}
        ("{arm*}", 12 - 5),
        ("{arm* ^arm2}", 12 - 3),  # {0,1,5}
        ("{arm* ^arm2} leg", 12 - 5),  # {0,1,5} | {7,8}
        ("{arm* &arm2}", 12 - 2),  # only arm2 survives the intersection
        ("!{arm*}", 12 - 7),       # complement of the whole sub-expression
        ("?*", 12 - 8),            # every named group unioned = {0,1,2,3,5,7,8,10}
        ("* ^?*", 12 - 4),         # everything except every named group
        ("{arm* ^arm2} ^1", 12 - 2),  # {0,5}
    ]
    for text, expected in cases:
        remaining, messages = evaluate(make_tree(text), attributes=WILD_GROUPS)
        check(f"{text!r} with arm/leg/slim groups", remaining, expected)
        check(f"{text!r} warns about nothing", messages, [])


def test_left_to_right_operators() -> None:
    print("== left-to-right accumulation: & is an operation, not a set operator ==")
    # `A &B` intersects the *accumulated* set with B; `A &B 0-1` then unions 0-1
    # back in, which a symmetric interpretation could not express.
    groups = (("group:A", (0, 1, 2), "BOOLEAN", "POINT"),
              ("group:B", (1, 2, 3), "BOOLEAN", "POINT"))
    cases = [
        ("A &B", 12 - 2),          # {1,2}
        ("A ^B", 12 - 1),          # {0}
        ("!A", 12 - 9),            # a leading `!` adds the complement: {3..11}
        ("!A &B", 12 - 1),         # {3..11} & B = {3}
        ("A &B 0", 12 - 3),        # union 0 back in: {0,1,2}
        ("0 &A", 12 - 1),          # 0 intersect A = {0}
        ("0 &A &B", 12 - 0),       # {0} & B = {} -> nothing selected
        ("&A", 12 - 3),            # a leading & starts from everything: A
        ("^A", 12 - 9),            # a leading ^ starts from everything
        ("A B", 12 - 4),           # plain union
        # `!P` *adds the complement* of P; it does not toggle anything, so `A !B` is
        # A | !B = {0,1,2,4..11}, not A - B. (The old numbers here encoded the
        # rejected "toggle" semantics.)
        ("A !B", 12 - 11),
        ("B !A", 12 - 11),
        ("!A !B", 12 - 10),        # !A | !B = !(A & B) = {0,3,4..11}
        ("A !B ^2", 12 - 10),      # ... minus {2}
    ]
    for text, expected in cases:
        remaining, _messages = evaluate(make_tree(text), attributes=groups)
        check(f"{text!r} with A={{0,1,2}} B={{1,2,3}}", remaining, expected)


def test_attribute_patterns() -> None:
    print("== attribute patterns on the point positions (x == index) ==")
    # The position is addressed as `@position.x`. Houdini's shorthands are
    # deliberately *not* implemented: `@P`, `@x`, `@y` and `@z` are ordinary
    # attribute names, and one that the geometry does not have warns instead of
    # silently reading the position.
    cases = [
        ("@position.x>8", 12 - 3),   # {9,10,11}
        ("@position.x>=8", 12 - 4),  # {8,9,10,11}
        ("@position.x<2", 12 - 2),   # {0,1}
        ("@position.x<=2", 12 - 3),
        ("@position.x=3", 12 - 1),
        ("@position.x==3", 12 - 1),
        ("@position.x!=3", 12 - 11),
        ("@position.x=3-5", 12 - 3),  # a range value list
        ("@position.x&3", 12 - 9),   # integer bitwise-and: {1,2,3,5,6,7,9,10,11}
        ("@position.x>8", 12 - 3),   # the builtin is called `position`, not `P`
        ("@ptnum>9", 12 - 2),        # the element number, like Houdini
        ("@elemnum<2", 12 - 2),
        ("* &@position.x>8", 12 - 3),  # `*` then intersect with a pattern
        ("!@position.x>8", 12 - 9),  # complement of an attribute pattern
        ("@position.x>8 &@position.x<10", 12 - 1),  # {9}
        ("0-1 @position.x>8", 12 - 5),  # indices unioned with an attribute pattern
    ]
    for text, expected in cases:
        remaining, messages = evaluate(make_tree(text), attributes=())
        check(f"{text!r} on loose points", remaining, expected)
        check(f"{text!r} warns about nothing", messages, [])


def test_component_selection() -> None:
    print("== component selectors: .y / [1] / .g ==")
    def remaining_for(text: str, values: list[tuple[float, float, float]]) -> int:
        mesh = bpy.data.meshes.new("pdt_vec_mesh")
        mesh.from_pydata([(float(i), 0.0, 0.0) for i in range(len(values))], [], [])
        mesh.update()
        attribute = mesh.attributes.new(name="vec", type="FLOAT_VECTOR", domain="POINT")
        for index, value in enumerate(values):
            attribute.data[index].vector = value
        obj = bpy.data.objects.new("pdt_vec_obj", mesh)
        bpy.context.scene.collection.objects.link(obj)
        modifier = obj.modifiers.new(name="GeoNodes", type="NODES")
        modifier.node_group = make_tree(text)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(depsgraph)
        eval_mesh = evaluated.to_mesh()
        count = len(eval_mesh.vertices)
        evaluated.to_mesh_clear()
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh, do_unlink=True)
        return count

    values = [(float(i), 10.0 - float(i), 0.0) for i in range(POINT_COUNT)]
    check("@vec.y>5", remaining_for("@vec.y>5", values), 12 - 5)
    check("@vec[1]>5", remaining_for("@vec[1]>5", values), 12 - 5)
    check("@vec.g>5", remaining_for("@vec.g>5", values), 12 - 5)
    check("bare @vec reads the first component", remaining_for("@vec<5", values), 12 - 5)


def test_missing_references_warn_but_never_fail() -> None:
    print("== missing groups and attributes warn, and never fail the node ==")
    remaining, messages = evaluate(make_tree("arm*"), attributes=())
    check("a wildcard that matches nothing selects nothing", remaining, 12)
    check(
        "a wildcard that matches nothing warns",
        any("arm*" in message for message in messages),
        True,
    )

    # A token that matches nothing must not stop the tokens around it. Note the
    # expression is a *union*: `typo* ^0-1` would subtract from nothing, because
    # `typo*` is a plain add (so the list does not start from "everything") - that
    # case is pinned down separately below.
    remaining, messages = evaluate(make_tree("typo* 0-1"), attributes=WILD_GROUPS)
    check("the other tokens still work next to a missing group", remaining, 12 - 2)
    check("the missing group still warns", len(messages), 1)

    remaining, messages = evaluate(make_tree("typo* ^0-1"), attributes=WILD_GROUPS)
    check("a missing group plus ^ subtracts from nothing", remaining, 12)
    check("the missing group still warns there too", len(messages), 1)

    remaining, messages = evaluate(
        make_tree("A"),
        attributes=(("group:A", (1, 2), "INT", "POINT"),),
    )
    check("a non-boolean attribute is not a group", remaining, 12)
    check(
        "a non-boolean group warns",
        any("non-boolean" in message for message in messages),
        True,
    )

    remaining, messages = evaluate(make_tree("@typo>0"), attributes=WILD_GROUPS)
    check("a missing attribute selects nothing", remaining, 12)
    check(
        "a missing attribute warns with its name",
        any("'typo'" in message for message in messages),
        True,
    )

    remaining, messages = evaluate(make_tree("@x>8"), attributes=WILD_GROUPS)
    check(
        "'@x' is an ordinary attribute name, so it warns",
        any("'x'" in message for message in messages),
        True,
    )

    # `@P` is *not* an alias of the builtin `position` either (the node looks the
    # name up verbatim). The docs used `@P.y>0` as their example for a long time;
    # this pins the real behaviour down so it cannot drift silently again.
    _remaining, messages = evaluate(make_tree("@P.x>8"), attributes=WILD_GROUPS)
    check(
        "'@P' is not an alias of 'position', so it warns",
        any("'P'" in message for message in messages),
        True,
    )

    print("== malformed patterns are errors, not warnings ==")
    for bad in ("arm[", "@position.y", "@position.y>", "@position.q>0", "{arm*", "0-3 }", "arm* &", "^@"):
        remaining, messages = evaluate(make_tree(bad), attributes=WILD_GROUPS)
        check(f"invalid {bad!r} leaves the geometry intact", remaining, 12)
        check(
            f"invalid {bad!r} reports an error",
            any("Invalid selection" in message for message in messages),
            True,
        )


def test_group_wildcard_on_other_domain() -> None:
    print("== a wildcard group on another domain matches Blender's own field ==")

    def build(use_pdt_node: bool) -> bpy.types.NodeTree:
        tree = bpy.data.node_groups.new(name=f"pdt_wild_adapt_{int(use_pdt_node)}",
                                        type="GeometryNodeTree")
        tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        group_input = tree.nodes.new("NodeGroupInput")
        delete = tree.nodes.new("GeometryNodeDeleteGeometry")
        delete.domain = "POINT"
        out = tree.nodes.new("NodeGroupOutput")
        if use_pdt_node:
            selection = tree.nodes.new(NODE_SELECT)
            selection.inputs["Selection"].default_value = "f*"
            selection.domain = "VERTEX"
            tree.links.new(group_input.outputs["Geometry"], selection.inputs["Geometry"])
            tree.links.new(selection.outputs["Geometry"], delete.inputs["Geometry"])
            tree.links.new(selection.outputs["Selection"], delete.inputs["Selection"])
        else:
            named = tree.nodes.new("GeometryNodeInputNamedAttribute")
            named.data_type = "BOOLEAN"
            named.inputs["Name"].default_value = "group:face_a"
            tree.links.new(group_input.outputs["Geometry"], delete.inputs["Geometry"])
            tree.links.new(named.outputs[0], delete.inputs["Selection"])
        tree.links.new(delete.outputs["Geometry"], out.inputs["Geometry"])
        return tree

    def remaining(tree) -> int:
        mesh = bpy.data.meshes.new("pdt_wild_mesh")
        mesh.from_pydata(
            [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)],
            [],
            [(0, 1, 2), (0, 2, 3)],
        )
        mesh.update()
        faces = mesh.attributes.new(name="group:face_a", type="BOOLEAN", domain="FACE")
        faces.data[0].value = True
        faces.data[1].value = False
        obj = bpy.data.objects.new("pdt_wild_obj", mesh)
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
    check("wildcard FACE group == Named Attribute field", node_count, reference_count)
    check("the adapted wildcard group deleted points", node_count < 4, True)


def main() -> int:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    test_group_wildcards()
    test_left_to_right_operators()
    test_attribute_patterns()
    test_component_selection()
    test_missing_references_warn_but_never_fail()
    test_group_wildcard_on_other_domain()
    print(f"\n== summary: {_checks} checks, {len(_failures)} failure(s) ==")
    for label in _failures:
        print(f"  failed: {label}")
    if _failures:
        raise SystemExit(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
