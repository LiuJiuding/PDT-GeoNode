"""Every expression printed in the user manual, run against the real node.

The manual (``tools/pdt/docs/manual/``) is the normative specification of this fork:
when it and the implementation disagree, the implementation is what gets changed. That
only works if the manual cannot drift, so this file executes the example table from
``manual/reference/selection_syntax.md`` §5 verbatim, on the geometry the table
describes.

If a row here fails, either the behaviour changed (then the manual, this table and the
implementation all change together) or the manual is wrong. Do not "fix" a row by
copying whatever the node currently does: derive the expectation from the semantics in
the manual first, the way the P13 tables were re-derived.
"""

from __future__ import annotations

import sys

import bpy

NODE_SELECT = "GeometryNodePDTSelection"
POINT_COUNT = 12

#: The setup printed in the manual's §5 preamble: 12 points, these groups.
GROUPS = (
    ("group:A", (0, 1, 2)),
    ("group:B", (1, 2, 3)),
    ("group:arm", (0, 1)),
    ("group:arm2", (2, 3)),
    ("group:armX", (5,)),
    ("group:leg", (7, 8)),
    ("group:slim", (10,)),
)

ALL = set(range(POINT_COUNT))

#: `manual/reference/selection_syntax.md` §5, row for row.
#: (expression, selected indices, note)
MANUAL_TABLE = [
    ("*", ALL, "everything"),
    ("0-3", {0, 1, 2, 3}, "range, both ends inclusive"),
    ("0-7:2", {0, 2, 4, 6}, "step"),
    ("0-1 4-5", {0, 1, 4, 5}, "whitespace separated union"),
    ("0-1,4-5", {0, 1, 4, 5}, "comma separated union"),
    ("^0-3", {4, 5, 6, 7, 8, 9, 10, 11}, "leading ^ starts from everything"),
    ("* ^3", ALL - {3}, "exclude"),
    ("A", {0, 1, 2}, "group"),
    ("A B", {0, 1, 2, 3}, "two groups"),
    ("A &B", {1, 2}, "intersect"),
    ("A ^B", {0}, "subtract"),
    ("!A", {3, 4, 5, 6, 7, 8, 9, 10, 11}, "leading ! adds the complement"),
    ("&A", {0, 1, 2}, "leading & starts from everything"),
    ("A &B 0", {0, 1, 2}, "left to right: (A & B) | {0}"),
    ("0 &A &B", set(), "{0} & A & B"),
    (
        "A !B",
        {0, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11},
        "A | !B - NOT A - B",
    ),
    (
        "A !B ^2",
        {0, 1, 4, 5, 6, 7, 8, 9, 10, 11},
        "(A | !B) - {2}",
    ),
    ("!A &B", {3}, "!A & B"),
    ("{arm*}", {0, 1, 2, 3, 5}, "brace operand"),
    ("{arm* ^arm2}", {0, 1, 5}, "own accumulator inside the braces"),
    ("{arm* &arm2}", {2, 3}, "intersect inside the braces"),
    ("!{arm*}", {4, 6, 7, 8, 9, 10, 11}, "complement of a brace operand"),
    ("{^0-3}", {4, 5, 6, 7, 8, 9, 10, 11}, "inner starts from everything"),
    ("?*", {0, 1, 2, 3, 5, 7, 8, 10}, "every group unioned"),
    ("* ^?*", {4, 6, 9, 11}, "everything except every group"),
    ("@position.x>8", {9, 10, 11}, "builtin position"),
    ("@ptnum>9", {10, 11}, "element number"),
    (
        "@position.x&3",
        {1, 2, 3, 5, 6, 7, 9, 10, 11},
        "integer bitwise-and, non-zero matches",
    ),
    ('@id="0-4 78"', {0, 1, 2, 3, 4}, "quoted value list"),
    ("0-1 @position.x>8", {0, 1, 9, 10, 11}, "indices unioned with a pattern"),
]

#: Rows the manual lists with a warning instead of a clean answer.
MANUAL_WARNING_TABLE = [
    ("typo*", set(), "typo*"),
    ("0-10 typo", set(range(11)), "typo"),
]

#: The manual's §4 malformed list, plus the §2 quoting caveat.
MANUAL_INVALID = (
    "5-2",
    "0-3:0",
    "1-",
    "-",
    ":2",
    "a-b",
    "A.B",
    "group:A",
    "arm[",
    "{0-3",
    "0-3 }",
    "!",
    "&",
    "^",
    "@",
    "@position.y",
    "@position.y>",
    "@position.q>0",
    "@position.y>abc",
    "@position.y>0-4",
    '@name="hello"',
    '@position.y>"0-4"',
)

_failures: list[str] = []
_checks = 0


def _render(value):
    """Sorted for index sets, verbatim for anything else (bools, None, strings)."""
    try:
        return sorted(value)
    except TypeError:
        return value


def check(label: str, actual, expected) -> None:
    global _checks
    _checks += 1
    if actual == expected:
        print(f"OK   {label}")
    else:
        print(f"FAIL {label}: got {_render(actual)}, expected {_render(expected)}")
        _failures.append(label)


def build_tree(selection_text: str) -> bpy.types.NodeTree:
    """Object mesh -> PDT Selection -> Delete Geometry (delete the *un*selected)."""
    tree = bpy.data.node_groups.new(
        name=f"pdt_manual_{abs(hash(selection_text))}", type="GeometryNodeTree"
    )
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    group_input = tree.nodes.new("NodeGroupInput")
    source = tree.nodes.new(NODE_SELECT)
    source.inputs["Selection"].default_value = selection_text
    # `Domain` is a node property now (L0 PDS-9): "Vertex" = mesh vertices.
    source.domain = "VERTEX"
    delete = tree.nodes.new("GeometryNodeDeleteGeometry")
    delete.domain = "POINT"
    invert = tree.nodes.new("FunctionNodeBooleanMath")
    invert.operation = "NOT"
    out = tree.nodes.new("NodeGroupOutput")

    tree.links.new(group_input.outputs["Geometry"], source.inputs["Geometry"])
    tree.links.new(source.outputs["Geometry"], delete.inputs["Geometry"])
    # NOT(selection) into Delete Geometry => the survivors ARE the selection.
    tree.links.new(source.outputs["Selection"], invert.inputs[0])
    tree.links.new(invert.outputs["Boolean"], delete.inputs["Selection"])
    tree.links.new(delete.outputs["Geometry"], out.inputs["Geometry"])
    return tree


def evaluate(tree):
    """Returns (selected indices, warning and error messages)."""
    mesh = bpy.data.meshes.new(f"manual_mesh_{len(bpy.data.meshes)}")
    mesh.from_pydata([(float(i), 0.0, 0.0) for i in range(POINT_COUNT)], [], [])
    mesh.update()
    for name, members in GROUPS:
        values = mesh.attributes.new(name=name, type="BOOLEAN", domain="POINT")
        for index in range(len(values.data)):
            values.data[index].value = index in members
    ids = mesh.attributes.new(name="id", type="INT", domain="POINT")
    for index in range(len(ids.data)):
        ids.data[index].value = index
    # A second attribute whose values deliberately differ from the element index, so
    # the §2 quoting caveat can be told apart (with `id == index` both forms of
    # `@id=0-4 7` would happen to select the same elements).
    codes = mesh.attributes.new(name="code", type="INT", domain="POINT")
    for index in range(len(codes.data)):
        codes.data[index].value = index * 10

    obj = bpy.data.objects.new(f"manual_obj_{len(bpy.data.objects)}", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new(name="GeoNodes", type="NODES")
    modifier.node_group = tree
    bpy.context.view_layer.update()

    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    eval_mesh = evaluated.to_mesh()
    selected = {int(round(vertex.co.x)) for vertex in eval_mesh.vertices}
    messages = [warning.message for warning in modifier.node_warnings]
    evaluated.to_mesh_clear()
    return selected, messages


def test_manual_table() -> None:
    print("== manual/reference/selection_syntax.md §5: results ==")
    for text, expected, note in MANUAL_TABLE:
        selected, messages = evaluate(build_tree(text))
        check(f"{text!r} ({note})", selected, expected)
        check(f"{text!r} reports no warning", messages, [])


def test_manual_warning_table() -> None:
    print("== manual §4/§5: a missing reference warns but still works ==")
    for text, expected, needle in MANUAL_WARNING_TABLE:
        selected, messages = evaluate(build_tree(text))
        check(f"{text!r} still selects the rest", selected, expected)
        check(
            f"{text!r} warns about {needle!r}",
            any(needle in message for message in messages),
            True,
        )


def test_manual_invalid_table() -> None:
    print("== manual §4: malformed input is an error, and selects nothing ==")
    for text in MANUAL_INVALID:
        selected, messages = evaluate(build_tree(text))
        check(f"invalid {text!r} selects nothing", selected, set())
        check(
            f"invalid {text!r} reports an error",
            any("Invalid selection" in message for message in messages),
            True,
        )


def test_quoting_caveat() -> None:
    """Manual §2: an unquoted multi-part value list is TWO operands, not a list.

    `code` is `index * 10`, so "the value list part" and "the extra index" land on
    different elements and the two readings are distinguishable.
    """
    selected, _messages = evaluate(build_tree("@code=0-4 7"))
    check("unquoted '@code=0-4 7' is (code in 0-4) union index 7", selected, {0, 7})
    selected, _messages = evaluate(build_tree('@code="0-4 7"'))
    check("quoted it is the value list {0-4, 7}", selected, {0})


def main() -> int:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    test_manual_table()
    test_manual_warning_table()
    test_manual_invalid_table()
    test_quoting_caveat()
    print(f"\n== summary: {_checks} checks, {len(_failures)} failure(s) ==")
    for label in _failures:
        print(f"  failed: {label}")
    if _failures:
        raise SystemExit(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
