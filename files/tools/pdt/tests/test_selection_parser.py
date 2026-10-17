"""Cross-check the Python selection parser against the C++ node.

The picker needs to parse an existing parameter value to pre-select it in the
viewport, which requires parsing the same syntax in Python. That duplication is
only acceptable if drift fails loudly, so every expression is evaluated through the
real node (``Delete Geometry`` driven by the node's boolean field) and the
*surviving vertex positions* are compared with the Python parser's index set. Each
test vertex has a unique coordinate, so the comparison checks the exact set, not
just a count.

Scope note (L0 ``PICK-2``): the picker only ever *writes* indices, and it only
*evaluates* indices and ``*``. Group names (``A``, ``arm*``) and attribute patterns
(``@position.y>0``) are **not** parsed into indices here and contribute no
pre-selection points - and neither does anything the picker cannot evaluate, including
the complement of such a token (``!A``) and the value it is subtracted from (``^A``).
The contract is ``预选集合 ⊆ 节点选择集合``: whatever the picker pre-selects, the node
must also select. That subset relation is asserted for those expressions; for plain
index expressions both sides are compared exactly. Group wildcards, ``!``, ``&``,
``{}`` and ``@attr`` remain C++-only behaviour and are pinned down by
``test_selection_patterns.py``.
"""

from __future__ import annotations

import sys

import bpy

sys.path.insert(0, bpy.utils.resource_path("SYSTEM") + "/scripts/addons_core")
sys.path.insert(0, bpy.utils.resource_path("SYSTEM") + "/scripts/modules")

import pdt_geo_select as pdt  # the package: bl_info + register
from pdt_geo_select import parser as pdt_parser  # noqa: E402

pdt.register_once()

VERTEX_COUNT = 12

#: Attributes of the test mesh: (name, member indices, type, domain).
GROUPS = (
    ("group:A", (1, 4, 5, 9), "BOOLEAN", "POINT"),
    ("group:B", (0, 11), "BOOLEAN", "POINT"),
    # An int attribute with a group-ish name is not a group.
    ("group:notbool", (2, 3), "INT", "POINT"),
)

#: Expressions whose value is the same index set in both implementations.
EXPRESSIONS = [
    "*",
    "",
    "0",
    "0-3",
    "4-7",
    "0-11:2",
    "1-9:3",
    "0-1 4-5",
    "0,1,4,5",
    "^0-3",
    "^0-11:2",
    "* ^3",
    "* ^0-1 ^10-11",
    "9-11",
    "11",
    "99",
    "2-2",
    # Left-to-right accumulation (P13).
    "!0-3",
    "!0-3 ^1",
    "0-7 &2-5",
    "0-7 &2-5 &4-6",
    "{0-3}",
    "{0-7 ^0-3}",
    "{0-7 ^0-3} 9",
    "{^0-3}",
    "!*",
    "{0-3} {8-11}",
    "0-2 &^1",
    "* &^0-11:2",
    # Groups and attributes contribute nothing to the picker's evaluation (L0 PICK-2), and
    # neither does an operator on them: see PRESELECT_SUBSET for the pinned values.
    # The node does select the group - see test_selection_patterns.py.
    "A",
    "0-1 A",
    "A ^1-2",
    "0-1 missing",
    "@position.y>0",
    "0-1 @position.y>0",
    # The complement / subtraction cases are where a naive evaluator over-selects: `!A` is
    # "everything the node did not take from the group", not "everything", and `^A` is
    # "everything except the group". Both used to pre-select *every* element.
    "!A",
    "^A",
    "0-7 !A",
    "0-7 &A",
    "{A}",
    "{A ^B}",
    "!{A}",
    "0-1 ^@position.y>0",
]

#: For these expressions the picker's pre-selection is a strict subset of what the
#: node selects, because Python does not expand groups or attribute patterns (PICK-2).
#: What it may claim is bounded on the safe side: an unevaluated token contributes
#: nothing when it is added or intersected, and *everything it might have selected*
#: when it is subtracted or complemented.
PRESELECT_SUBSET = {
    "A": set(),
    "0-1 A": {0, 1},
    "A ^1-2": set(),
    "0-1 missing": {0, 1},
    "@position.y>0": set(),
    "0-1 @position.y>0": {0, 1},
    "!A": set(),
    "^A": set(),
    "0-7 !A": {0, 1, 2, 3, 4, 5, 6, 7},
    "0-7 &A": set(),
    "{A}": set(),
    "{A ^B}": set(),
    "!{A}": set(),
    "0-1 ^@position.y>0": set(),
}

#: Values both implementations reject.
MALFORMED = [
    "5-2",
    "^",
    "1-",
    "0-3:0",
    ":2",
    "-",
    "a-b",
    "A.B",
    "group:A",
    "!",
    "&",
    "{",
    "}",
    "0-3 }",
    "{0-3",
    "@",
    "@>0",
    "@position.y",
    "@position.y>",
    "@position.q>0",
    "@position.y>abc",
]

_failures: list[str] = []
_checks = 0


def _render(value):
    """Sorted for index sets, verbatim for anything else (bools, None, strings)."""
    try:
        return sorted(value)
    except TypeError:
        return value


def check(label, actual, expected) -> None:
    global _checks
    _checks += 1
    if actual == expected:
        print(f"OK   {label}")
    else:
        print(f"FAIL {label}: got {_render(actual)}, expected {_render(expected)}")
        _failures.append(label)


def check_subset(label, subset, superset) -> None:
    global _checks
    _checks += 1
    if subset <= superset:
        print(f"OK   {label}: {sorted(subset)} within {sorted(superset)}")
    else:
        print(f"FAIL {label}: {sorted(subset - superset)} is not selected by the node")
        _failures.append(label)


def build_tree(selection_text: str, keep_selected: bool):
    """Object mesh -> PDT Selection -> Delete Geometry -> group output.

    keep_selected=True deletes the *unselected* points, so the survivors are
    exactly the selected indices; keep_selected=False deletes the selection.
    """
    tree = bpy.data.node_groups.new(
        name=f"pdt_cross_{selection_text or 'empty'}_{int(keep_selected)}",
        type="GeometryNodeTree",
    )
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    group_input = tree.nodes.new("NodeGroupInput")
    source = tree.nodes.new("GeometryNodePDTSelection")
    source.inputs["Selection"].default_value = selection_text
    source.domain = "VERTEX"   # L0 PDS-9: the mesh vertices
    delete = tree.nodes.new("GeometryNodeDeleteGeometry")
    out = tree.nodes.new("NodeGroupOutput")

    tree.links.new(group_input.outputs["Geometry"], source.inputs["Geometry"])
    tree.links.new(source.outputs["Geometry"], delete.inputs["Geometry"])
    if keep_selected:
        invert = tree.nodes.new("FunctionNodeBooleanMath")
        invert.operation = "NOT"
        tree.links.new(source.outputs["Selection"], invert.inputs[0])
        tree.links.new(invert.outputs["Boolean"], delete.inputs["Selection"])
    else:
        tree.links.new(source.outputs["Selection"], delete.inputs["Selection"])
    tree.links.new(delete.outputs["Geometry"], out.inputs["Geometry"])

    mesh = bpy.data.meshes.new(f"cross_mesh_{len(bpy.data.meshes)}")
    mesh.from_pydata([(float(i), 0.0, 0.0) for i in range(VERTEX_COUNT)], [], [])
    mesh.update()
    # Real group attributes, so a group token has something to resolve to.
    for name, members, attribute_type, domain in GROUPS:
        values = mesh.attributes.new(name=name, type=attribute_type, domain=domain)
        for index in range(len(values.data)):
            member = index in members
            values.data[index].value = member if attribute_type == "BOOLEAN" else int(member)
    obj = bpy.data.objects.new(f"cross_obj_{len(bpy.data.objects)}", mesh)
    bpy.context.scene.collection.objects.link(obj)

    modifier = obj.modifiers.new(name="GeoNodes", type="NODES")
    modifier.node_group = tree
    bpy.context.view_layer.update()
    return obj


def survivors(obj) -> set[int]:
    """Indices of the vertices that came out of Delete Geometry."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    eval_mesh = evaluated.to_mesh()
    indices = {int(round(vertex.co.x)) for vertex in eval_mesh.vertices}
    evaluated.to_mesh_clear()
    return indices


def all_indices() -> set[int]:
    return set(range(VERTEX_COUNT))


def node_selected(text: str, mesh) -> set[int]:
    """What the picker pre-selects for `text`, straight from the Python parser.

    For the group/attribute expressions this is a *strict subset* of what the node
    selects, because Python deliberately does not expand groups or attribute patterns
    (P13). Those cases are spelled out in PRESELECT_SUBSET and asserted as a
    containment, not an equality - everything else is cross-checked exactly.
    """
    return pdt_parser.resolve_indices(pdt_parser.parse_selection(text), VERTEX_COUNT, mesh, "POINT")


def main() -> int:
    bpy.ops.wm.read_factory_settings(use_empty=True)

    print("== selected set: Python parser vs C++ node ==")
    for text in EXPRESSIONS:
        obj = build_tree(text, keep_selected=True)
        node = survivors(obj)
        picker = node_selected(text, obj.data)
        if text in PRESELECT_SUBSET:
            check(f"picker answer for {text!r} is pinned", picker, PRESELECT_SUBSET[text])
            check_subset(f"picker subset of the node for {text!r}", picker, node)
        else:
            check(f"selected set for {text!r}", node, picker)

    print("== unselected set (complement) ==")
    for text in EXPRESSIONS:
        if text in PRESELECT_SUBSET:
            # The complements cannot agree while the picker's answer is a strict
            # subset; the containment check above is the meaningful claim there.
            continue
        obj = build_tree(text, keep_selected=False)
        check(
            f"unselected set for {text!r}",
            survivors(obj),
            all_indices() - node_selected(text, obj.data),
        )

    print("== malformed input selects nothing in both implementations ==")
    for text in MALFORMED:
        check(f"python parser rejects {text!r}", pdt_parser.parse_selection(text), None)
        obj = build_tree(text, keep_selected=True)
        check(f"node selects nothing for {text!r}", survivors(obj), set())

    print("== picker pre-selection uses the same parser ==")
    check("preselect '0-1 5'", pdt_parser.preselect_indices("0-1 5", 8), {0, 1, 5})
    check("preselect clamps to mesh", pdt_parser.preselect_indices("0-20", 8), set(range(8)))
    check("preselect '*' covers mesh", pdt_parser.preselect_indices("*", 8), set(range(8)))
    check("preselect malformed is empty", pdt_parser.preselect_indices("5-2", 8), set())
    check("preselect '' is empty", pdt_parser.preselect_indices("", 8), set())
    check("preselect '^0-3'", pdt_parser.preselect_indices("^0-3", 8), {4, 5, 6, 7})
    check("preselect '!0-3'", pdt_parser.preselect_indices("!0-3", 8), {4, 5, 6, 7})
    check("preselect '0-7 &2-5'", pdt_parser.preselect_indices("0-7 &2-5", 8), {2, 3, 4, 5})
    check("preselect '{0-3 6}'", pdt_parser.preselect_indices("{0-3 6}", 8), {0, 1, 2, 3, 6})

    print("== the picker does not pre-select groups or attribute patterns (P13) ==")
    group_obj = build_tree("A", keep_selected=True)
    mesh = group_obj.data
    check("the node does select the group", survivors(group_obj), {1, 4, 5, 9})
    check("preselect 'A' is empty", pdt_parser.preselect_indices("A", VERTEX_COUNT, mesh, "POINT"), set())
    check(
        "preselect '0-1 A' keeps the numeric part",
        pdt_parser.preselect_indices("0-1 A", VERTEX_COUNT, mesh, "POINT"),
        {0, 1},
    )
    check(
        "preselect 'arm*' is empty",
        pdt_parser.preselect_indices("arm*", VERTEX_COUNT, mesh, "POINT"),
        set(),
    )
    check(
        "preselect '@position.y>0' is empty",
        pdt_parser.preselect_indices("@position.y>0", VERTEX_COUNT, mesh, "POINT"),
        set(),
    )
    check(
        "preselect '0-1 @position.y>0' keeps the numeric part",
        pdt_parser.preselect_indices("0-1 @position.y>0", VERTEX_COUNT, mesh, "POINT"),
        {0, 1},
    )

    print("== group and attribute tokens contribute nothing to a pre-selection (PICK-2) ==")
    # The picker's evaluator only knows indices and `*`. Group names and `@attribute` tokens
    # are not turned into indices anywhere in the picker, and an operator on one of them stays
    # on the safe side of the unknown: `!A` may claim nothing (the node may select anything the
    # group does not), `^A` may claim nothing either (the node removes an unknown set).
    for text, expected in (
        ("A", set()),
        ("arm[0-9]*", set()),
        ("?*", set()),
        ("@position.y>0", set()),
        # Subtracting a group the picker cannot expand removes an *unknown* set, so nothing of
        # the numeric part can be claimed with certainty.
        ("0-1 ^A", set()),
        ("0-7 &@position.y>0", set()),
        # Complementing an unknown set adds nothing, but the numeric part before it still holds
        # (the node unions the two).
        ("!0-3 !A", {4, 5, 6, 7, 8, 9, 10, 11}),
    ):
        check(
            f"preselect {text!r}",
            pdt_parser.preselect_indices(text, VERTEX_COUNT, mesh, "POINT"),
            expected,
        )
    # Only indices and `*` are expanded: every pre-selection of an expression built from those
    # alone is exact, which is what the cross-check above compares.
    check(
        "indices and '*' are still exact",
        pdt_parser.preselect_indices("0-3 *", VERTEX_COUNT, mesh, "POINT"),
        set(range(VERTEX_COUNT)),
    )
    # Syntax validation of the *node's* grammar is unaffected: a group token is still accepted,
    # it simply carries no index information for the picker.
    for text in ("A", "0-1 A", "@position.y>0"):
        check(f"{text!r} is not a syntax error", pdt_parser.parse_selection(text) is None, False)
    # ... and the invalid ones stay invalid.
    for text in ("A.B", "a-b", "group:A", "@"):
        check(f"{text!r} is still rejected", pdt_parser.parse_selection(text), None)

    print(f"\n== summary: {_checks} checks, {len(_failures)} failure(s) ==")
    for label in _failures:
        print(f"  failed: {label}")
    if _failures:
        raise SystemExit(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
