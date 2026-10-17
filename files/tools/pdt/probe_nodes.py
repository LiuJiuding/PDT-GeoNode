"""Node-API probe: verify that node types are reachable from the Python API.

Runs inside any Blender build (no addons required):

    blender.exe -b --factory-startup --python tools/probe_nodes.py -- \
        --list GeometryNode \
        --check GeometryNodeSwitch GeometryNodeCubeGridTopology \
        --eval GeometryNodeCubeGridTopology

Exit code is 0 only when every requested check passes. Every run prints a
single machine-readable line starting with ``PROBE_JSON:``.
"""

from __future__ import annotations

import json
import sys
import traceback

import bpy


def _argv_after_ddash() -> list[str]:
    if "--" in sys.argv:
        return sys.argv[sys.argv.index("--") + 1:]
    return []


def _parse(argv: list[str]) -> dict:
    out: dict = {"list": [], "check": [], "eval": []}
    mode = None
    for token in argv:
        if token in ("--list", "--check", "--eval"):
            mode = token[2:]
            continue
        if mode is None:
            continue
        out[mode].append(token)
    return out


def _s(value):
    """bpy.app exposes most build fields as bytes; make them JSON-friendly."""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def build_info() -> dict:
    app = bpy.app
    info = {
        "executable": _s(bpy.app.binary_path),
        "version": list(app.version),
        "version_string": _s(app.version_string),
        "python": sys.version.split()[0],
        "build_branch": _s(app.build_branch),
        "build_hash": _s(app.build_hash),
        "build_commit_timestamp": app.build_commit_timestamp,
        "build_commit_date": _s(app.build_commit_date),
        "build_time": _s(app.build_time),
        "build_platform": _s(app.build_platform),
        "build_type": _s(app.build_type),
    }
    return info


def node_types_matching(prefix: str) -> list[str]:
    """RNA node types are created at runtime, so look them up on bpy.types."""
    names = [name for name in dir(bpy.types) if name.startswith(prefix)]
    return sorted(set(names))


def _tree_idname_for(node_type: str) -> str:
    if node_type.startswith("GeometryNode"):
        return "GeometryNodeTree"
    if node_type.startswith("ShaderNode"):
        return "ShaderNodeTree"
    if node_type.startswith("CompositorNode"):
        return "CompositorNodeTree"
    if node_type.startswith("FunctionNode"):
        return "GeometryNodeTree"
    return "GeometryNodeTree"


def check_type(node_type: str) -> dict:
    """Create a node tree, instantiate the node, and dump its socket contract."""
    result: dict = {"node_type": node_type, "ok": False, "errors": []}
    try:
        if not hasattr(bpy.types, node_type):
            result["errors"].append(f"bpy.types.{node_type} does not exist")
            return result

        result["rna_type"] = bpy.types.__getattribute__(node_type).bl_rna.identifier
        result["ui_name"] = bpy.types.__getattribute__(node_type).bl_rna.name
        result["ui_description"] = bpy.types.__getattribute__(node_type).bl_rna.description

        tree_idname = _tree_idname_for(node_type)
        tree = bpy.data.node_groups.new(name=f"probe_{node_type}", type=tree_idname)
        node = tree.nodes.new(node_type)
        result["bl_idname"] = node.bl_idname
        result["inputs"] = [
            {"name": s.name, "type": s.bl_idname, "link_limit": s.link_limit}
            for s in node.inputs
        ]
        result["outputs"] = [
            {"name": s.name, "type": s.bl_idname, "link_limit": s.link_limit}
            for s in node.outputs
        ]
        result["properties"] = sorted(
            p.identifier for p in node.bl_rna.properties
            if not p.is_readonly and p.identifier not in node.bl_rna.base.properties.keys()
        )
        result["can_be_searched"] = node_type.removeprefix("GeometryNode") != node_type
        result["ok"] = True
    except Exception as ex:  # pragma: no cover - probe reports rather than raises
        result["errors"].append(f"{type(ex).__name__}: {ex}")
        result["traceback"] = traceback.format_exc()
    return result


def _evaluate(tree: bpy.types.NodeTree, node_type: str, convert_points: bool) -> dict:
    """Evaluate `tree` (which already ends in a group output) and report counts."""
    mesh = bpy.data.meshes.new(f"probe_mesh_{node_type}")
    obj = bpy.data.objects.new(f"probe_obj_{node_type}", mesh)
    bpy.context.scene.collection.objects.link(obj)
    mod = obj.modifiers.new(name="GeoNodes", type="NODES")
    mod.node_group = tree

    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    eval_mesh = evaluated.to_mesh()
    result = {
        "vertices": len(eval_mesh.vertices),
        "edges": len(eval_mesh.edges),
        "polygons": len(eval_mesh.polygons),
        "converted_points": convert_points,
    }
    evaluated.to_mesh_clear()
    # Clean up so repeated probes in one session stay independent.
    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.meshes.remove(mesh, do_unlink=True)
    return result


def check_eval(node_type: str) -> dict:
    """Wire the node's first geometry output into a group output and evaluate it.

    This exercises the full path: RNA -> node type -> geometry-node execute ->
    depsgraph evaluation -> resulting mesh in Python. Nodes whose output is a
    point cloud produce no mesh, so a second attempt inserts `Points to Vertices`
    to make the result observable.
    """
    result: dict = {"node_type": node_type, "evaluated": False, "skipped": False, "errors": []}
    try:
        tree = bpy.data.node_groups.new(name=f"eval_{node_type}", type="GeometryNodeTree")
        tree.interface.new_socket(
            name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
        )
        node = tree.nodes.new(node_type)
        out_node = tree.nodes.new("NodeGroupOutput")

        # Feed unconnected geometry inputs with a cube, so pass-through and
        # geometry-consuming nodes produce something observable.
        fed: list[str] = []
        for socket in node.inputs:
            if socket.bl_idname == "NodeSocketGeometry" and not socket.is_linked:
                cube = tree.nodes.new("GeometryNodeMeshCube")
                cube.location = (node.location.x - 200, node.location.y - 150)
                tree.links.new(cube.outputs["Mesh"], socket)
                fed.append(socket.name)

        geom_sockets = [s for s in node.outputs if s.bl_idname == "NodeSocketGeometry"]
        if not geom_sockets:
            result["skipped"] = True
            result["reason"] = "node has no geometry output to evaluate"
            if fed:
                result["reason"] += f" (fed inputs: {', '.join(fed)})"
            return result

        tree.links.new(geom_sockets[0], out_node.inputs["Geometry"])
        stats = _evaluate(tree, node_type, convert_points=False)

        if stats["vertices"] == 0 and stats["polygons"] == 0:
            # Point cloud (or instance) output: make it visible as a mesh.
            converter = tree.nodes.new("GeometryNodePointsToVertices")
            tree.links.new(geom_sockets[0], converter.inputs["Points"])
            tree.links.new(converter.outputs["Mesh"], out_node.inputs["Geometry"])
            stats = _evaluate(tree, node_type, convert_points=True)

        result.update(stats)
        result["fed_geometry_inputs"] = fed
        result["evaluated"] = True
    except Exception as ex:  # pragma: no cover
        result["errors"].append(f"{type(ex).__name__}: {ex}")
        result["traceback"] = traceback.format_exc()
    return result


def main() -> int:
    args = _parse(_argv_after_ddash())
    report: dict = {"build": build_info(), "checks": [], "evals": [], "lists": {}}
    failures = 0

    for prefix in (args["list"] or ["GeometryNode"]):
        names = node_types_matching(prefix)
        report["lists"][prefix] = {"count": len(names), "names": names}

    for node_type in args["check"]:
        item = check_type(node_type)
        report["checks"].append(item)
        if not item["ok"]:
            failures += 1

    for node_type in args["eval"]:
        item = check_eval(node_type)
        report["evals"].append(item)
        if not item["evaluated"] and not item["skipped"]:
            failures += 1

    report["failures"] = failures
    print("\nPROBE_JSON:" + json.dumps(report, sort_keys=True, default=str), flush=True)
    print(f"PROBE_SUMMARY: build={report['build']['version_string']} "
          f"failures={failures}")
    for prefix, data in report["lists"].items():
        print(f"PROBE_LIST {prefix}: {data['count']} types")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
