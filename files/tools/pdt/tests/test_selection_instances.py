"""Instance-domain assertions for `PDT Selection` (M1).

Run inside the build:

    blender.exe -b --factory-startup --python-exit-code 1 --python <this file>

The node used to have no instance domain at all: on an instances component
`domain_size()` was 0 for every domain it could address, so it returned all-false
*and said nothing* (measured: qa/picker/instance_probe.py section D). These cases pin the
new semantics:

* `Domain = Instance` addresses **top level** instances, one index per instance;
* the element kind scopes the node by itself (L0 PDS-9): `Instance` makes the mesh side
  select nothing, `Vertex`/`Point`/`Spline`/... make the instance side select nothing -
  and an out-of-scope kind is *not* an error, it simply selects nothing;
* `position` is *derived* on instances (it is not a stored attribute), so
  `@position.x>0` has to work and must not produce a false "attribute not found";
* the instance domain is isolated: an instance-domain field wired into a point-domain
  consumer selects nothing (Blender's `adapt_domain` returns empty for every
  cross-domain conversion), which is exactly what the same node did on instances
  before M1.

Observables: `GeometrySet.instances_pointcloud()` is one point per instance with every
instance attribute copied onto it (the only Python view of the instance domain; it
always reports domain `POINT` because that is where it gathers to), and the mesh
vertex count is read from `geometry.mesh`.

NOTE: a menu socket's `default_value` is the *label* in this Blender version
(`"Point"` / `"Spline"` / `"Point Cloud"`), not the identifier - measured with
qa/picker/instance_menu_probe.py.
"""

from __future__ import annotations

import bpy

NODE_SELECT = "GeometryNodePDTSelection"
INSTANCE_COUNT = 4

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


def base_points_object(count: int = INSTANCE_COUNT):
    """A mesh with `count` loose vertices at x = 0, 1, 2, ... - one instance each."""
    mesh = bpy.data.meshes.new("pdt_instance_points")
    mesh.from_pydata([(float(i), 0.0, 0.0) for i in range(count)], [], [])
    mesh.update()
    obj = bpy.data.objects.new("PDTInstanceOwner", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def new_tree(name: str):
    tree = bpy.data.node_groups.new(name=name, type="GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    return tree


def add_instance_source(tree):
    """Group Input -> Instance on Points(cube) -> the instances socket."""
    group_in = tree.nodes.new("NodeGroupInput")
    instance = tree.nodes.new("GeometryNodeInstanceOnPoints")
    cube = tree.nodes.new("GeometryNodeMeshCube")
    tree.links.new(group_in.outputs["Geometry"], instance.inputs["Points"])
    tree.links.new(cube.outputs["Mesh"], instance.inputs["Instance"])
    return instance.outputs["Instances"]


def add_cone_join(tree, socket):
    """Join an unrelated cone mesh in, so a mesh *and* instances share the stream."""
    cone = tree.nodes.new("GeometryNodeMeshCone")
    join = tree.nodes.new("GeometryNodeJoinGeometry")
    tree.links.new(socket, join.inputs["Geometry"])
    tree.links.new(cone.outputs["Mesh"], join.inputs["Geometry"])
    return join.outputs["Geometry"]


def add_group_attribute(tree, socket, name="group:arm", threshold=1.0):
    """`group:<name>` on the INSTANCE domain = instances whose x is above `threshold`."""
    store = tree.nodes.new("GeometryNodeStoreNamedAttribute")
    store.data_type = "BOOLEAN"
    store.domain = "INSTANCE"
    store.inputs["Name"].default_value = name
    position = tree.nodes.new("GeometryNodeInputPosition")
    separate = tree.nodes.new("ShaderNodeSeparateXYZ")
    compare = tree.nodes.new("ShaderNodeMath")
    compare.operation = "GREATER_THAN"
    compare.inputs[1].default_value = threshold
    tree.links.new(socket, store.inputs["Geometry"])
    tree.links.new(position.outputs["Position"], separate.inputs["Vector"])
    tree.links.new(separate.outputs["X"], compare.inputs[0])
    tree.links.new(compare.outputs["Value"], store.inputs["Value"])
    return store.outputs["Geometry"]


def add_number_attribute(tree, socket, name="mass"):
    """`<name>` (FLOAT, INSTANCE) = the instance index."""
    store = tree.nodes.new("GeometryNodeStoreNamedAttribute")
    store.data_type = "FLOAT"
    store.domain = "INSTANCE"
    store.inputs["Name"].default_value = name
    index = tree.nodes.new("GeometryNodeInputIndex")
    tree.links.new(socket, store.inputs["Geometry"])
    tree.links.new(index.outputs["Index"], store.inputs["Value"])
    return store.outputs["Geometry"]


def set_menu(socket, label):
    """Set a menu socket; tolerate the two labels an unpatched binary does not have.

    The smoke test runs against an unpatched build too (that is how the harness itself
    gets validated before the 11-minute build), so `Instance` / `Instances` are allowed
    to fall back there. The real instance cases are gated by `instance_domain_available`.
    """
    try:
        socket.default_value = label
    except TypeError:
        if label not in {"Instance", "Instances"}:
            raise


def add_selection(tree, socket, selection_text, domain):
    """PDT Selection with its element kind set, wired to `socket`.

    `domain` is the node's own `Domain` **property** (L0 PDS-9); the old `Component`
    menu is gone, the kind alone says which component is addressed.
    """
    selection = tree.nodes.new(NODE_SELECT)
    selection.inputs["Selection"].default_value = selection_text
    selection.domain = domain
    tree.links.new(socket, selection.inputs["Geometry"])
    return selection, selection.outputs["Geometry"]


def add_store_of_selection(tree, socket, selection, name="sel"):
    """Store the boolean on the INSTANCE domain so it can be read back."""
    store = tree.nodes.new("GeometryNodeStoreNamedAttribute")
    store.data_type = "BOOLEAN"
    store.domain = "INSTANCE"
    store.inputs["Name"].default_value = name
    tree.links.new(socket, store.inputs["Geometry"])
    tree.links.new(selection.outputs["Selection"], store.inputs["Value"])
    return store.outputs["Geometry"]


def instance_domain_available(selection) -> bool:
    """True when this build's node really has the `Domain` property with an Instance kind."""
    prop = selection.bl_rna.properties.get("domain")
    if prop is None:
        return False
    try:
        selection.domain = "INSTANCE"
    except (AttributeError, TypeError):
        return False
    return str(selection.domain) == "INSTANCE"


def make_simple_tree(selection_text, domain="INSTANCE",
                     group_attribute=False, number_attribute=False,
                     translate_instances=False):
    """`selection_text` -> stored boolean on instances, on a 4-instance stream."""
    tree = new_tree(f"pdt_inst_{selection_text or 'empty'}_{domain}")
    group_out = tree.nodes.new("NodeGroupOutput")
    socket = add_instance_source(tree)
    if group_attribute:
        socket = add_group_attribute(tree, socket)
    if number_attribute:
        socket = add_number_attribute(tree, socket)
    selection, socket = add_selection(tree, socket, selection_text, domain)
    if translate_instances:
        translate = tree.nodes.new("GeometryNodeTranslateInstances")
        translate.inputs["Translation"].default_value = (0.0, 0.0, 5.0)
        tree.links.new(socket, translate.inputs["Instances"])
        tree.links.new(selection.outputs["Selection"], translate.inputs["Selection"])
        socket = translate.outputs["Instances"]
    socket = add_store_of_selection(tree, socket, selection)
    tree.links.new(socket, group_out.inputs["Geometry"])
    return tree, selection


def make_point_side_tree(domain="VERTEX", selection_text="0-1", extra_mesh=True):
    """A `Domain = Vertex` node driving `Delete Geometry(Point)`: the mesh side.

    The two sides have to be separate trees: joining them would duplicate the
    instances (both branches carry the same instance component), and one
    `Delete Geometry` node has a single domain.
    """
    tree = new_tree(f"pdt_points_{domain}_{selection_text}")
    group_out = tree.nodes.new("NodeGroupOutput")
    socket = add_instance_source(tree)
    if extra_mesh:
        socket = add_cone_join(tree, socket)
    selection, socket = add_selection(tree, socket, selection_text, domain)
    delete = tree.nodes.new("GeometryNodeDeleteGeometry")
    delete.domain = "POINT"
    tree.links.new(socket, delete.inputs["Geometry"])
    tree.links.new(selection.outputs["Selection"], delete.inputs["Selection"])
    socket = add_store_of_selection(tree, delete.outputs["Geometry"], selection)
    tree.links.new(socket, group_out.inputs["Geometry"])
    return tree, selection


def make_instance_side_tree(domain="INSTANCE", selection_text="0-1", extra_mesh=False):
    """A `Domain = Instance` node driving `Delete Geometry(Instance)`: the other side."""
    tree = new_tree(f"pdt_instances_{domain}_{selection_text}")
    group_out = tree.nodes.new("NodeGroupOutput")
    socket = add_instance_source(tree)
    if extra_mesh:
        socket = add_cone_join(tree, socket)
    selection, socket = add_selection(tree, socket, selection_text, domain)
    delete = tree.nodes.new("GeometryNodeDeleteGeometry")
    delete.domain = "INSTANCE"
    tree.links.new(socket, delete.inputs["Geometry"])
    tree.links.new(selection.outputs["Selection"], delete.inputs["Selection"])
    socket = add_store_of_selection(tree, delete.outputs["Geometry"], selection)
    tree.links.new(socket, group_out.inputs["Geometry"])
    return tree, selection


def evaluate_tree(tree, base_count=INSTANCE_COUNT) -> dict:
    """Per-component result: selected instances, instance count/Z, mesh points."""
    obj = base_points_object(base_count)
    mesh = obj.data
    modifier = obj.modifiers.new(name="GeoNodes", type="NODES")
    modifier.node_group = tree
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    # Keep the GeometrySet RNA alive: every component view dies with it.
    geometry = evaluated.evaluated_geometry()

    cloud = geometry.instances_pointcloud()
    selected = None
    count = 0
    heights = []
    if cloud is not None:
        count = len(cloud.attributes["position"].data)
        attribute = cloud.attributes.get("sel")
        if attribute is not None:
            selected = [i for i, value in enumerate(attribute.data) if value.value]
        transform = cloud.attributes.get("instance_transform")
        if transform is not None:
            heights = [round(transform.data[i].value[2][3], 3) for i in range(len(transform.data))]

    component = geometry.mesh
    points = 0 if component is None else len(component.vertices)

    result = {
        "INSTANCE": selected,
        "INSTANCE_COUNT": count,
        "INSTANCE_Z": heights,
        "MESH_POINTS": points,
        "WARNINGS": [warning.message for warning in modifier.node_warnings],
    }
    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.meshes.remove(mesh, do_unlink=True)
    return result


def test_harness_smoke() -> None:
    """Validates the harness itself: mesh side, instance side, no gating.

    Everything below is gated on the Instance kind, so without this the whole file would
    "pass" on a build without it without evaluating one tree.
    """
    print("== harness smoke: the mesh side and the instance side ==")
    tree, _selection = make_point_side_tree("VERTEX", selection_text="")
    baseline = evaluate_tree(tree)
    check("smoke: 4 instances survive an empty selection", baseline["INSTANCE_COUNT"], 4)
    check("smoke: the joined cone contributes mesh points", baseline["MESH_POINTS"] > 0, True)
    check("smoke: an empty selection stores false everywhere", baseline["INSTANCE"], [])

    tree, _selection = make_point_side_tree("VERTEX", selection_text="0-1")
    result = evaluate_tree(tree)
    check("smoke: deleting 2 mesh points works", result["MESH_POINTS"], baseline["MESH_POINTS"] - 2)

    # An instance-kind node deletes 0-1 of the four instances.
    tree, _selection = make_instance_side_tree("INSTANCE", selection_text="")
    result = evaluate_tree(tree)
    check("smoke: the instance side reports its instances", result["INSTANCE_COUNT"], 4)


def test_instance_domain() -> None:
    """`Domain = Instance` addresses top level instances by index."""
    print("== Domain=Instance: index ranges on top level instances ==")
    tree, selection = make_simple_tree("*")
    if not instance_domain_available(selection):
        print("SKIP this build has no Instance domain yet (M1 not built)")
        return

    tree, _selection = make_simple_tree("*")
    result = evaluate_tree(tree)
    check("the geometry carries 4 instances", result["INSTANCE_COUNT"], 4)
    check("'*' selects every instance", result["INSTANCE"], [0, 1, 2, 3])

    cases = [
        ("", []),
        ("0", [0]),
        ("0-1", [0, 1]),
        ("1-3:2", [1, 3]),
        ("^0", [1, 2, 3]),
        ("* ^1-2", [0, 3]),
        ("99", []),
        ("!0-1", [2, 3]),
        ("1-2 ^2", [1]),
    ]
    for text, expected in cases:
        tree, _selection = make_simple_tree(text)
        result = evaluate_tree(tree)
        check(f"instance selection {text!r}", result["INSTANCE"], expected)
        check(f"instance selection {text!r}: no warnings", result["WARNINGS"], [])


def test_instance_expressions() -> None:
    """`@ptnum` / `@position` / `group:` / `@attr` work on the instance domain."""
    print("== instance domain: element numbers, position, groups, attributes ==")
    tree, selection = make_simple_tree("*")
    if not instance_domain_available(selection):
        print("SKIP this build has no Instance domain yet (M1 not built)")
        return

    cases = [
        ("@elemnum<2", [0, 1]),
        ("@ptnum>=2", [2, 3]),
        ("@position.x>1", [2, 3]),
        ("@position.x=0", [0]),
        ("* &@position.x<2", [0, 1]),
        ("!@position.x>0", [0]),
    ]
    for text, expected in cases:
        tree, _selection = make_simple_tree(text)
        result = evaluate_tree(tree)
        check(f"instance expression {text!r}", result["INSTANCE"], expected)
        check(f"instance expression {text!r}: no warnings", result["WARNINGS"], [])

    tree, _selection = make_simple_tree("arm", group_attribute=True)
    result = evaluate_tree(tree)
    check("group:arm selects the instances it marks", result["INSTANCE"], [2, 3])
    check("and is not warned about", result["WARNINGS"], [])

    tree, _selection = make_simple_tree("arm*", group_attribute=True)
    result = evaluate_tree(tree)
    check("a group wildcard works on instances", result["INSTANCE"], [2, 3])
    check("and is not warned about either", result["WARNINGS"], [])

    tree, _selection = make_simple_tree("@mass>=2", number_attribute=True)
    result = evaluate_tree(tree)
    check("a stored numeric instance attribute is readable", result["INSTANCE"], [2, 3])
    check("and is not warned about", result["WARNINGS"], [])

    # Instances are inspected by the warning pass now, so a missing reference is
    # reported instead of staying quiet (tools/pdt/docs/archive/07-曲线支持评估.md §2.1 used to be a gap).
    tree, _selection = make_simple_tree("does_not_exist")
    result = evaluate_tree(tree)
    check("a missing group on instances selects nothing", result["INSTANCE"], [])
    check(
        "and is warned about",
        any("matches no 'group:' attribute" in message for message in result["WARNINGS"]),
        True,
    )
    tree, _selection = make_simple_tree("@nosuch>0")
    result = evaluate_tree(tree)
    check(
        "a missing attribute is warned about",
        any("'nosuch' not found" in message for message in result["WARNINGS"]),
        True,
    )
    # ... but `position` is derived there, so warning about it would be a false positive.
    tree, _selection = make_simple_tree("@position.x>1")
    result = evaluate_tree(tree)
    check("derived position is not reported as missing", result["WARNINGS"], [])


def test_instance_component_filter() -> None:
    """The element kind alone scopes the expression to one component (L0 PDS-9)."""
    print("== one kind per node, same string, two components ==")
    tree, selection = make_instance_side_tree()
    if not instance_domain_available(selection):
        print("SKIP this build has no Domain property with the Instance kind yet")
        return

    baseline_points = evaluate_tree(make_point_side_tree(selection_text="")[0])["MESH_POINTS"]
    baseline_instances = evaluate_tree(make_instance_side_tree(selection_text="")[0])["INSTANCE_COUNT"]
    check("baseline: 4 instances", baseline_instances, 4)
    check("baseline: the cone contributes mesh points", baseline_points > 0, True)

    # (kind, side, deleted elements)
    # `Vertex` addresses the mesh side only and `Instance` the instance side only;
    # `Point` is a *point cloud* kind, so on a mesh (whose points are vertices) it
    # addresses nothing - that is the PDS-9 split, not an error.
    cases = [
        ("VERTEX", "point", 2),
        ("VERTEX", "instance", 0),
        ("POINT", "point", 0),
        ("SPLINE", "point", 0),
        ("SPLINE", "instance", 0),
        ("CONTROL_POINT", "point", 0),
        ("CONTROL_POINT", "instance", 0),
        ("INSTANCE", "point", 0),
        ("INSTANCE", "instance", 2),
    ]
    for domain, side, deleted in cases:
        tree, _selection = (
            make_point_side_tree(domain, selection_text="0-1")
            if side == "point"
            else make_instance_side_tree(domain, selection_text="0-1")
        )
        result = evaluate_tree(tree)
        if side == "point":
            check(
                f"Domain={domain} ({side}): deleted mesh points",
                baseline_points - result["MESH_POINTS"],
                deleted,
            )
            check(f"Domain={domain} ({side}): kept every instance", result["INSTANCE_COUNT"], 4)
        else:
            check(
                f"Domain={domain} ({side}): deleted instances",
                baseline_instances - result["INSTANCE_COUNT"],
                deleted,
            )
            check(f"Domain={domain} ({side}): untouched mesh", result["MESH_POINTS"], 0)
        check(
            f"Domain={domain} ({side}): out of scope is not an error",
            [m for m in result["WARNINGS"] if "Invalid" in m],
            [],
        )

    # A real instance-domain consumer: Translate Instances moves only the selection.
    tree, _selection = make_simple_tree("1", translate_instances=True)
    result = evaluate_tree(tree)
    check(
        "Translate Instances moved exactly the selected instance",
        result["INSTANCE_Z"],
        [0.0, 5.0, 0.0, 0.0],
    )

    # The same wiring on the point domain moves nothing: the instance domain is
    # isolated (no cross-domain adaptation) - the pre-M1 behaviour, now pinned.
    tree, _selection = make_simple_tree("1", domain="SPLINE", translate_instances=True)
    result = evaluate_tree(tree)
    check(
        "the same field on the point domain moves nothing (domain isolation)",
        result["INSTANCE_Z"],
        [0.0, 0.0, 0.0, 0.0],
    )


def test_instance_domain_mismatches() -> None:
    """A kind that this geometry does not have selects nothing and reports nothing.

    L0 PDS-9: the element kind *is* the component, so there is no second menu to
    disagree with it, and an out-of-scope kind is not an error - PDS-5 keeps "Error"
    for syntax errors only.
    """
    print("== out-of-scope kinds on an instances-only stream select nothing ==")
    tree, selection = make_simple_tree("*")
    if not instance_domain_available(selection):
        print("SKIP this build has no Domain property with the Instance kind yet")
        return

    for domain in ("VERTEX", "EDGE", "FACE", "CONTROL_POINT", "SPLINE", "POINT"):
        tree, _selection = make_simple_tree("*", domain=domain)
        result = evaluate_tree(tree)
        check(f"Domain={domain}: no error", [m for m in result["WARNINGS"] if "Invalid" in m], [])
        check(f"Domain={domain}: selects no instance", result["INSTANCE"], [])

    tree, _selection = make_simple_tree("*", domain="INSTANCE")
    result = evaluate_tree(tree)
    check("Instance is accepted", result["WARNINGS"], [])
    check("and selects", result["INSTANCE"], [0, 1, 2, 3])

    tree, _selection = make_simple_tree("0-1", domain="INSTANCE")
    result = evaluate_tree(tree)
    check("Instance with a range selects on the instances", result["INSTANCE"], [0, 1])


def main() -> int:
    test_harness_smoke()
    test_instance_domain()
    test_instance_expressions()
    test_instance_component_filter()
    test_instance_domain_mismatches()
    print(f"\n== summary: {_checks} checks, {len(_failures)} failure(s) ==")
    if _failures:
        print("failed checks:")
        for label in _failures:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
