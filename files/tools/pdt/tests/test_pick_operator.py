"""Headless assertions for the viewport-selection data path (P1).

Runs the real code path of scripts/addons_core/pdt_geo_select/ without a node
editor context:

    blender.exe -b --factory-startup --python-exit-code 1 --python <this file>

Flow per case: build a node tree with a PDT Selection node, evaluate what the
operator bakes for editing, select vertices on the temporary mesh, apply, and
verify that the string parameter now describes exactly those vertices *and*
that re-evaluating the tree deletes exactly them.
"""

from __future__ import annotations

import inspect
import random
import sys

import bmesh
import bpy

sys.path.insert(0, bpy.utils.resource_path("SYSTEM") + "/scripts/addons_core")
sys.path.insert(0, bpy.utils.resource_path("SYSTEM") + "/scripts/modules")

import pdt_geo_select as pdt  # the package: bl_info + register
from pdt_geo_select.operators import actions as pdt_actions
from pdt_geo_select.utils import constants as pdt_constants
from pdt_geo_select import geometry as pdt_geometry
from pdt_geo_select import hit_test as pdt_hit_test
from pdt_geo_select import instances as pdt_instances
from pdt_geo_select import keymap as pdt_keymap
from pdt_geo_select import menus as pdt_menus
from pdt_geo_select import overlay as pdt_overlay
from pdt_geo_select import parser as pdt_parser
from pdt_geo_select.operators import pick as pdt_pick
from pdt_geo_select import preferences as pdt_preferences
from pdt_geo_select import selection as pdt_selection
from pdt_geo_select import session as pdt_session
from pdt_geo_select import state as pdt_state
from pdt_geo_select import targets as pdt_targets

pdt.register_once()

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


def addon_sources() -> str:
    """The source of **every** module of the add-on, joined.

    The add-on is a package (one file per concern), so "the add-on does not do X" has to mean
    *none* of its modules does - scanning the package root alone would pass while the code sat
    in `session.py` or `keymap.py`.
    """
    parts = []
    for name, module in sorted(sys.modules.items()):
        if name != "pdt_geo_select" and not name.startswith("pdt_geo_select."):
            continue
        try:
            parts.append(inspect.getsource(module))
        except (OSError, TypeError):  # pragma: no cover - a module without source
            continue
    return "\n".join(parts)


def addon_module_names() -> list[str]:
    """The loaded modules of the package (for "which file does that" assertions)."""
    return sorted(
        name for name in sys.modules
        if name == "pdt_geo_select" or name.startswith("pdt_geo_select.")
    )


def build_scene(selection_text: str = "*"):
    """Cube object (8 points) with: Group Input -> PDT Selection -> Delete Geometry."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    tree = bpy.data.node_groups.new(name="PDT Pick Test", type="GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    group_in = tree.nodes.new("NodeGroupInput")
    selection = tree.nodes.new("GeometryNodePDTSelection")
    selection.inputs["Selection"].default_value = selection_text
    delete = tree.nodes.new("GeometryNodeDeleteGeometry")
    group_out = tree.nodes.new("NodeGroupOutput")

    tree.links.new(group_in.outputs["Geometry"], selection.inputs["Geometry"])
    tree.links.new(selection.outputs["Geometry"], delete.inputs["Geometry"])
    tree.links.new(selection.outputs["Selection"], delete.inputs["Selection"])
    tree.links.new(delete.outputs["Geometry"], group_out.inputs["Geometry"])

    mesh = bpy.data.meshes.new("cube")
    # A real cube: 8 vertices, 12 edges, 6 faces (edges are derived from faces).
    corners = [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
        (1.0, 0.0, 1.0),
        (1.0, 1.0, 1.0),
        (0.0, 1.0, 1.0),
    ]
    faces = [
        (0, 1, 2, 3),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 0, 4, 7),
    ]
    mesh.from_pydata(corners, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("CubeOwner", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new(name="GeometryNodes", type="NODES")
    modifier.node_group = tree

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return tree, selection, obj


def select_in_edit_mode(obj, indices, domain: str = "POINT") -> None:
    """Simulate the user's selection, exactly like the UI changes it."""
    bm = bmesh.from_edit_mesh(obj.data)
    for vert in bm.verts:
        vert.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for face in bm.faces:
        face.select_set(False)
    collection = {"POINT": bm.verts, "EDGE": bm.edges, "FACE": bm.faces}[domain]
    collection.ensure_lookup_table()
    for index in indices:
        collection[index].select_set(True)
    if domain == "EDGE":
        for edge in bm.edges:
            if edge.select:
                for vert in edge.verts:
                    vert.select_set(True)
    elif domain == "FACE":
        for face in bm.faces:
            if face.select:
                for vert in face.verts:
                    vert.select_set(True)
                for edge in face.edges:
                    edge.select_set(True)
    bmesh.update_edit_mesh(obj.data)


def edit_mode_selection(obj, domain: str = "POINT") -> list[int]:
    bm = bmesh.from_edit_mesh(obj.data)
    collection = {"POINT": bm.verts, "EDGE": bm.edges, "FACE": bm.faces}[domain]
    collection.ensure_lookup_table()
    return sorted(element.index for element in collection if element.select)


def evaluated_vertex_count() -> int:
    obj = bpy.data.objects["CubeOwner"]
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    eval_mesh = evaluated.to_mesh()
    count = len(eval_mesh.vertices)
    evaluated.to_mesh_clear()
    return count


def test_encode() -> None:
    print("== encoding ==")
    check("empty", pdt_parser.encode_indices([]), "")
    check("single", pdt_parser.encode_indices([7]), "7")
    check("run", pdt_parser.encode_indices([0, 1, 2, 3]), "0-3")
    check("step", pdt_parser.encode_indices([0, 2, 4, 6]), "0-6:2")
    check("mixed", pdt_parser.encode_indices([0, 1, 4, 5, 9]), "0-1 4-5 9")
    check("unsorted+dupes", pdt_parser.encode_indices([5, 0, 5, 1]), "0-1 5")


def test_pick_apply_roundtrip() -> None:
    print("== pick -> edit mode -> select -> apply ==")
    tree, selection, obj = build_scene("*")
    session = pdt_session.begin_pick(tree, selection)
    check("session records the node", session["node"], selection.name)
    check("temporary object exists", session["temp_object"] in bpy.data.objects, True)
    check("original object hidden", obj.hide_get(), True)
    check("entered edit mode", bpy.context.mode, "EDIT_MESH")

    # The operator bakes the geometry *at* the node: a plain cube of 8 points.
    baked = bpy.data.objects[session["temp_object"]].data
    check("baked mesh vertex count", len(baked.vertices), 8)

    # Simulate the user picking vertices 1, 2 and 3 in the viewport.
    temp_obj = bpy.data.objects[session["temp_object"]]
    select_in_edit_mode(temp_obj, {1, 2, 3})

    count, text, _note = pdt_session.finish_pick()
    check("applied element count", count, 3)
    check("written parameter", selection.inputs["Selection"].default_value, "1-3")
    check("temporary object gone", session["temp_object"] in bpy.data.objects, False)
    check("temporary group gone", session["temp_group"] in bpy.data.node_groups, False)
    check("original object hide flag restored", obj.hide_get(), False)
    check("back in object mode", bpy.context.mode, "OBJECT")
    # The pick writes the parameter and nothing else: in particular it must not leave a note on
    # the node of which parameter it used (L0 明确不做: 不记录操作过的字符串参数用了什么选择模式).
    # Before this was removed, `finish_pick()` wrote the `pdt_pick_target` ID property here.
    check("the node carries no property besides the parameter", list(selection.keys()), [])

    # The written parameter must reproduce the picked points exactly.
    check("re-evaluated vertex count", evaluated_vertex_count(), 5)


def test_pick_cancel() -> None:
    print("== pick -> cancel ==")
    tree, selection, obj = build_scene("*")
    original = selection.inputs["Selection"].default_value
    session = pdt_session.begin_pick(tree, selection)
    pdt_session.cancel_pick()
    check("parameter untouched", selection.inputs["Selection"].default_value, original)
    check("temporary object gone", session["temp_object"] in bpy.data.objects, False)
    check("original object hide flag restored", obj.hide_get(), False)


def test_cleanup_leftovers() -> None:
    print("== leftover cleanup ==")
    tree, selection, obj = build_scene("*")
    session = pdt_session.begin_pick(tree, selection)
    temp_name = session["temp_object"]
    # Simulate a crash: forget the session without cleaning up.
    pdt_state.session = {}
    removed = pdt_session.cleanup_leftovers()
    check("leftovers removed", removed >= 1, True)
    check("temporary object gone", temp_name in bpy.data.objects, False)


def test_qualification() -> None:
    print("== qualification ==")
    tree, selection, obj = build_scene("*")
    ok, reason = pdt_hit_test.can_pick(tree, selection)
    check("qualifies", ok, True)
    # Any built-in geometry node: it has no string parameter and nothing to pick into.
    other = tree.nodes.new("GeometryNodeSetPosition")
    ok, reason = pdt_hit_test.can_pick(tree, other)
    check("node without string parameter is refused", ok, False)


def test_existing_object_selection_does_not_leak() -> None:
    print("== object's own selection state must not leak into the pick ==")
    tree, selection, obj = build_scene("")
    # The object mesh already has vertices 5..7 selected by the user.
    for vertex in obj.data.vertices:
        vertex.select = vertex.index >= 5
    obj.data.update()

    session = pdt_session.begin_pick(tree, selection)
    temp_obj = bpy.data.objects[session["temp_object"]]
    check("empty parameter starts with no selection", edit_mode_selection(temp_obj), [])
    check("session reports 0 preselected", session["preselected"], 0)
    pdt_session.cancel_pick()


def test_parameter_preselects_for_continuation() -> None:
    print("== existing parameter value is pre-selected so picking continues ==")
    tree, selection, obj = build_scene("0-1 5")
    session = pdt_session.begin_pick(tree, selection)
    temp_obj = bpy.data.objects[session["temp_object"]]
    check("parameter value pre-selected", edit_mode_selection(temp_obj), [0, 1, 5])
    check("session reports 3 preselected", session["preselected"], 3)

    # User keeps 0,1,5 and additionally picks 3 and 4.
    select_in_edit_mode(temp_obj, {0, 1, 3, 4, 5})

    count, text, _note = pdt_session.finish_pick()
    check("continued selection written", text, "0-1 3-5")
    check("continued selection count", count, 5)
    check("re-evaluated after continuation", evaluated_vertex_count(), 3)


def test_domain_aware_picking() -> None:
    print("== element kind: faces ==")
    tree, selection, obj = build_scene("")
    selection.domain = "FACE"   # the node's property: deliberately *not* read by the picker

    session = pdt_session.begin_pick(tree, selection, domain="FACE")
    check("session stores the domain", session["domain"], "FACE")
    check("edit mode switched to face select", tuple(bpy.context.tool_settings.mesh_select_mode), (False, False, True))
    temp_obj = bpy.data.objects[session["temp_object"]]
    check("cube has 6 faces", len(temp_obj.data.polygons), 6)

    # Pre-select two faces through the parameter, then read them back.
    pdt_session.cancel_pick()

    selection.inputs["Selection"].default_value = "0-1"
    session = pdt_session.begin_pick(tree, selection, domain="FACE")
    check("faces pre-selected", session["preselected"], 2)
    temp_obj = bpy.data.objects[session["temp_object"]]
    check("pre-selected face indices", edit_mode_selection(temp_obj, "FACE"), [0, 1])

    select_in_edit_mode(temp_obj, {0, 1, 5}, "FACE")
    count, text, note = pdt_session.finish_pick()
    check("face selection written", text, "0-1 5")
    check("face selection count", count, 3)
    check("no long-selection note", note, "")


def test_long_selection_fallback() -> None:
    print("== selections longer than the socket limit go to a node property ==")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    tree, selection, obj = build_scene("")
    # A random subset is the worst case for range encoding: most indices need
    # their own token, so the encoded text cannot fit into the char[1024] socket.
    rng = random.Random(1234)
    indices = sorted(rng.sample(range(800), 400))
    text = pdt_parser.encode_indices(indices)
    check("encoded text exceeds the socket limit", len(text) > pdt_constants.MAX_SOCKET_STRING, True)

    session = pdt_session.begin_pick(tree, selection)
    count, _written, note = pdt_session.finish_pick(explicit_indices=indices)
    check("count reported", count, len(indices))
    check("socket value is the token", selection.inputs["Selection"].default_value, pdt_constants.LONG_SELECTION_TOKEN)
    check("full spec stored on the node", selection.get(pdt_constants.LONG_SELECTION_KEY, ""), text)
    check("operator explains the fallback", "stored" in note, True)

    # The node must resolve the token to exactly the same set.
    check(
        "resolved set matches",
        pdt_parser.resolve_indices(pdt_parser.parse_selection(selection[pdt_constants.LONG_SELECTION_KEY]), 800),
        set(indices),
    )

    # A short selection afterwards must clear the stale long selection.
    session = pdt_session.begin_pick(tree, selection)
    pdt_session.finish_pick(explicit_indices=[1, 2, 3])
    check("socket back to plain text", selection.inputs["Selection"].default_value, "1-3")
    check("stale long selection removed", pdt_constants.LONG_SELECTION_KEY in selection, False)


def test_multiple_string_parameters() -> None:
    print("== a node with several string parameters must not be guessed ==")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sub = bpy.data.node_groups.new(name="TwoStrings", type="GeometryNodeTree")
    sub.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    sub.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    sub.interface.new_socket(name="Alpha", in_out="INPUT", socket_type="NodeSocketString")
    sub.interface.new_socket(name="Beta", in_out="INPUT", socket_type="NodeSocketString")
    sub_in = sub.nodes.new("NodeGroupInput")
    sub_out = sub.nodes.new("NodeGroupOutput")
    sub.links.new(sub_in.outputs["Geometry"], sub_out.inputs["Geometry"])

    parent = bpy.data.node_groups.new(name="Parent", type="GeometryNodeTree")
    parent.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_node = parent.nodes.new("GeometryNodeGroup")
    group_node.node_tree = sub
    parent_out = parent.nodes.new("NodeGroupOutput")
    parent.links.new(group_node.outputs["Geometry"], parent_out.inputs["Geometry"])

    mesh = bpy.data.meshes.new("cube2")
    mesh.from_pydata(
        [(x, y, z) for x in (0.0, 1.0) for y in (0.0, 1.0) for z in (0.0, 1.0)], [], []
    )
    mesh.update()
    obj = bpy.data.objects.new("Owner2", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.modifiers.new(name="GeometryNodes", type="NODES").node_group = parent
    bpy.context.view_layer.objects.active = obj

    targets = pdt_targets.list_string_targets(group_node)
    check("both string parameters are found", len(targets), 2)
    check(
        "both are the group's string inputs",
        sorted(target["label"] for target in targets),
        ["Alpha", "Beta"],
    )

    target, reason = pdt_targets.choose_string_target(group_node)
    check("ambiguous choice is refused", target, None)
    check("the refusal does not invent a choice", "string parameters" in reason, True)
    check(
        "and it says to aim at the socket instead",
        "socket" in reason and "panel" not in reason,
        True,
    )

    # The trigger names the parameter outright: the socket the cursor is on *is* the target
    # (PICK-1), so nothing has to guess - and nothing has to remember a choice either.
    check(
        "the hit socket is a valid target identifier",
        pdt_targets.find_string_target(group_node, targets[1]["identifier"])["identifier"],
        targets[1]["identifier"],
    )
    check(
        "a name from an older file still resolves",
        pdt_targets.find_string_target(group_node, targets[1]["label"])["identifier"],
        targets[1]["identifier"],
    )
    check("an unknown name resolves to nothing", pdt_targets.find_string_target(group_node, "Gamma"), None)
    check("and an empty identifier is ignored", pdt_targets.find_string_target(group_node, ""), None)

    # L0 `明确不做`: 不记录操作过的字符串参数用了什么选择模式. The node keeps **nothing** about the
    # choice, so a node with several string parameters cannot be picked by an older picker
    # remembering its way in: with no pointer on a parameter the choice is a guess and is refused.
    ok, reason = pdt_hit_test.can_pick(parent, group_node)
    check("a node with several parameters does not qualify without a pointer", ok, False)
    check("because the automatic choice would be a guess", "string parameters" in reason, True)
    check(
        "and no per-node note of an earlier choice can change that",
        [name for name in group_node.keys() if "pick" in name.lower()],
        [],
    )

    # An explicit target really is what the pick writes into (`begin_pick`'s
    # `target_identifier`), which is what the trigger passes from the hit test. This runs on a
    # fresh scene: `build_scene` replaces everything, so it has to come last.
    tree, selection, _owner = build_scene("")
    identifier = pdt_targets.list_string_targets(selection)[0]["identifier"]
    pdt_session.begin_pick(tree, selection, target_identifier=identifier)
    check(
        "the pick writes into the socket the trigger named",
        pdt_state.session["target"]["identifier"],
        identifier,
    )
    pdt_session.cancel_pick()
    message = ""
    try:
        pdt_session.begin_pick(tree, selection, target_identifier="Gamma")
    except RuntimeError as ex:
        message = str(ex)
    else:
        pdt_session.cancel_pick()
    check("an unknown target identifier is refused", "Gamma" in message, True)


def test_modal_event_mapping() -> None:
    print("== modal events: Enter applies, Esc/RMB cancels, rest passes through ==")
    check("return applies", pdt_session.pick_event_action("RET", "PRESS"), "apply")
    check("numpad enter applies", pdt_session.pick_event_action("NUMPAD_ENTER", "PRESS"), "apply")
    check("esc cancels", pdt_session.pick_event_action("ESC", "PRESS"), "cancel")
    check("right mouse cancels", pdt_session.pick_event_action("RIGHTMOUSE", "PRESS"), "cancel")
    check("key release is ignored", pdt_session.pick_event_action("RET", "RELEASE"), "pass")
    check("click value also applies", pdt_session.pick_event_action("RET", "CLICK"), "apply")
    check("space is a second confirm key", pdt_session.pick_event_action("SPACE", "PRESS"), "apply")
    check("left mouse passes through", pdt_session.pick_event_action("LEFTMOUSE", "PRESS"), "pass")
    check("box select key passes through", pdt_session.pick_event_action("B", "PRESS"), "pass")
    check("tab passes through", pdt_session.pick_event_action("TAB", "PRESS"), "pass")
    check("modal operator is registered", hasattr(bpy.types, "PDT_OT_selection_pick_modal"), True)

    # Both ids must run the same modal picker: a session without a modal loop
    # cannot hear Enter/Esc/RMB, which is exactly how Esc/RMB "stopped working"
    # for a keymap item the user had assigned to the old two step operator.
    # The methods are shared by assignment - inheriting from a registered operator
    # class breaks the base type's RNA poll (see the class comment; the GUI test
    # tools/pdt/tests/gui_pick_modal.py catches that).
    check(
        "the two step id is not an operator subclass",
        pdt_pick.PDT_OT_selection_pick.__bases__,
        (bpy.types.Operator,),
    )
    check(
        "both ids share the modal loop",
        pdt_pick.PDT_OT_selection_pick.modal is pdt_pick.PDT_OT_selection_pick_modal.modal,
        True,
    )
    check(
        "both ids use the same invoke",
        pdt_pick.PDT_OT_selection_pick.invoke is pdt_pick.PDT_OT_selection_pick_modal.invoke,
        True,
    )
    check(
        "both ids use the same cancel",
        pdt_pick.PDT_OT_selection_pick.cancel is pdt_pick.PDT_OT_selection_pick_modal.cancel,
        True,
    )
    check("pick id is unchanged for saved keymaps", pdt_pick.PDT_OT_selection_pick.bl_idname, "pdt.selection_pick")
    check("both pickers poll on the node editor", pdt_pick.PDT_OT_selection_pick.poll(None), False)
    # The load handler is what resets a session when a new file is opened. Assert the *wiring*
    # (register/unregister), not the live list: the tests call `wm.read_factory_settings()`
    # constantly, and that drops every handler - and, in a factory-startup process, does not
    # put this add-on's back (it is registered by this script, not by the user's preferences).
    pdt_session.unregister()
    check(
        "unregister drops the load handler",
        any(handler is pdt_session._on_load_post for handler in bpy.app.handlers.load_post),
        False,
    )
    pdt_session.register()
    check(
        "register adds the load handler",
        any(handler is pdt_session._on_load_post for handler in bpy.app.handlers.load_post),
        True,
    )
    check(
        "and it is registered only once",
        [id(handler) for handler in bpy.app.handlers.load_post].count(
            id(pdt_session._on_load_post)
        ),
        1,
    )

    # The trigger is socket-level (PICK-1) and the pie slices own the modal loop: the session
    # starts from a slice's `execute`, so *that* operator is the one that has to hear Enter/Esc.
    print("== trigger pieces: query operator, pies, and who owns the modal loop ==")
    check(
        "the trigger asks the editor's own hit test",
        "pdt.node_under_cursor" in pdt_constants.NODE_UNDER_CURSOR_IDNAME
        and pdt_constants.NODE_UNDER_CURSOR_IDNAME in inspect.getsource(pdt_hit_test.pick_hit_test),
        True,
    )
    check(
        "the trigger refuses before opening a menu",
        "hit_refusal_reason" in inspect.getsource(pdt_pick.pick_from_cursor),
        True,
    )
    check(
        "the trigger reads the key event's own coordinates",
        "mouse_region_x" in inspect.getsource(pdt_pick.pick_from_cursor),
        True,
    )
    check(
        "the first-level pie is opened by name",
        f'call_menu_pie(name=DOMAIN_PIE_IDNAME)' in inspect.getsource(pdt_pick.pick_from_cursor),
        True,
    )
    check(
        "Domain = Instance asks the second level",
        'INSTANCE_MODE_PIE_IDNAME' in inspect.getsource(pdt_pick.PDT_OT_selection_pick_domain.execute),
        True,
    )
    check(
        "both pie menus are registered",
        hasattr(bpy.types, pdt_constants.DOMAIN_PIE_IDNAME) and hasattr(bpy.types, pdt_constants.INSTANCE_MODE_PIE_IDNAME),
        True,
    )
    check(
        "the pie menus are the names the trigger opens",
        (
            bpy.types.PDT_MT_selection_domain_pie.bl_idname,
            bpy.types.PDT_MT_selection_instance_mode_pie.bl_idname,
        ),
        (pdt_constants.DOMAIN_PIE_IDNAME, pdt_constants.INSTANCE_MODE_PIE_IDNAME),
    )
    slice_source = inspect.getsource(pdt_pick.PDT_OT_selection_pick_domain)
    check(
        "the domain slice is a plain string, not a context-dependent enum",
        "StringProperty" in slice_source and "EnumProperty" not in slice_source,
        True,
    )
    check(
        "and the instance-mode slice too",
        "StringProperty" in inspect.getsource(pdt_pick.PDT_OT_selection_pick_instance_mode),
        True,
    )
    for operator in (
        pdt_pick.PDT_OT_selection_pick_domain,
        pdt_pick.PDT_OT_selection_pick_instance_mode,
        pdt_pick.PDT_OT_selection_pick_modal,
        pdt_pick.PDT_OT_selection_pick,
    ):
        check(
            f"{operator.bl_idname} owns the pick modal loop",
            operator.modal is pdt_pick._PickModalLoop.modal
            and operator._modal_impl is pdt_pick._PickModalLoop._modal_impl
            and operator.cancel is pdt_pick._PickModalLoop.cancel,
            True,
        )
    check(
        "no node-editor panel is registered (PICK-4 / L0 明确不做)",
        hasattr(bpy.types, "NODE_PT_pdt_selection"),
        False,
    )

    # Guard the assumption that bit us once: "RETURN" is the C enum name, while
    # the identifier Blender reports is "RET". These checks are derived from
    # Blender's own event enum, so a rename cannot silently break confirm again.
    print("== key sets match the identifiers Blender reports ==")
    items = [
        (item.identifier, item.name)
        for item in bpy.types.Event.bl_rna.properties["type"].enum_items
    ]
    identifiers = {identifier for identifier, _name in items}
    by_name = {name.lower(): identifier for identifier, name in items}
    for identifier in sorted(pdt_constants.PICK_CONFIRM_KEYS | pdt_constants.PICK_CANCEL_KEYS):
        check(f"{identifier!r} is a real event identifier", identifier in identifiers, True)
    for name, expected in (("return", "apply"), ("numpad enter", "apply"), ("space bar", "apply")):
        identifier = by_name.get(name)
        check(
            f"{name!r} ({identifier}) -> {expected}",
            pdt_session.pick_event_action(identifier, "PRESS"),
            expected,
        )
    for name, expected in (("esc", "cancel"),):
        identifier = by_name.get(name)
        check(
            f"{name!r} ({identifier}) -> {expected}",
            pdt_session.pick_event_action(identifier, "PRESS"),
            expected,
        )


def test_session_guards() -> None:
    print("== session guards: leaving edit mode, other object, edited mesh, deleted mesh ==")

    # 1) Leaving edit mode keeps the session usable ("paused").
    tree, selection, obj = build_scene("")
    session = pdt_session.begin_pick(tree, selection)
    temp_obj = bpy.data.objects[session["temp_object"]]
    select_in_edit_mode(temp_obj, {1, 2, 3})
    bpy.ops.object.mode_set(mode="OBJECT")
    check("state after leaving edit mode", pdt_session.pick_session_state(), "paused")
    check("live count while paused", pdt_overlay.selected_element_count(), 3)
    ok, message = pdt_session.try_finish_pick()
    check("apply from paused state", ok, True)
    check("parameter written from paused state", selection.inputs["Selection"].default_value, "1-3")
    check("session closed after apply", pdt_session.pick_session_state(), "none")

    # 2) A pure selection change must not look like an edit.
    tree, selection, obj = build_scene("")
    session = pdt_session.begin_pick(tree, selection)
    temp_obj = bpy.data.objects[session["temp_object"]]
    select_in_edit_mode(temp_obj, {0, 2})
    check("unmodified mesh passes the guard", pdt_session.try_finish_pick()[0], True)

    # 3) Editing the pick mesh topology is refused, the session survives.
    tree, selection, obj = build_scene("")
    session = pdt_session.begin_pick(tree, selection)
    temp_obj = bpy.data.objects[session["temp_object"]]
    bm = bmesh.from_edit_mesh(temp_obj.data)
    bmesh.ops.delete(bm, geom=[bm.verts[0]], context="VERTS")
    bmesh.update_edit_mesh(temp_obj.data)
    ok, message = pdt_session.try_finish_pick()
    check("edited mesh is refused", ok, False)
    check("message explains the topology change", "topology" in message, True)
    check("session survives a refusal", pdt_session.pick_session_state(), "ok")
    pdt_session.cancel_pick()

    # 4) Editing a different object is refused.
    tree, selection, obj = build_scene("")
    session = pdt_session.begin_pick(tree, selection)
    bpy.ops.object.mode_set(mode="OBJECT")
    other_mesh = bpy.data.meshes.new("other")
    other_mesh.from_pydata([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)], [], [])
    other_mesh.update()
    other = bpy.data.objects.new("Other", other_mesh)
    bpy.context.scene.collection.objects.link(other)
    bpy.context.view_layer.objects.active = other
    bpy.ops.object.mode_set(mode="EDIT")
    check("state while editing another object", pdt_session.pick_session_state(), "other_object")
    ok, message = pdt_session.try_finish_pick()
    check("other object is refused", ok, False)
    check("message mentions the other object", "another object" in message, True)
    bpy.ops.object.mode_set(mode="OBJECT")
    pdt_session.cancel_pick()

    # 5) A vanished temporary object is detected and cleaned up.
    tree, selection, obj = build_scene("")
    session = pdt_session.begin_pick(tree, selection)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.data.objects.remove(bpy.data.objects[session["temp_object"]], do_unlink=True)
    check("state when the pick object is gone", pdt_session.pick_session_state(), "missing")
    ok, message = pdt_session.try_finish_pick()
    check("missing session is refused", ok, False)
    check("message says it was cleaned up", "cleaned up" in message, True)
    check("session cleared after cleanup", pdt_session.pick_session_state(), "none")
    check("original object visible again", obj.hide_get(), False)


def test_shortcut_management() -> None:
    """The add-on registers exactly one item - **unbound** - in the addon keyconfig.

    PICK-4: no default shortcut. The item exists only so the operator can be found in
    Blender's Keymap editor (its search filters the items a keymap already has) and is
    created with `type="NONE"`, which binds nothing. It must never write into the *user*
    keyconfig: when the user keyconfig has no keymap of that name yet (fresh preferences,
    or right after "Restore" in the keymap preferences) writing there leaves a keymap
    holding only our items, which shadows the built-in one - that broke viewport
    navigation and click-select for a user.
    """
    print("== picker shortcut: one unbound addon-level item, user keyconfig untouched ==")
    addon_keymap = pdt_keymap._addon_keymap(pdt_constants.PICK_KEYMAP_NAME, "NODE_EDITOR")
    if addon_keymap is None:
        print("SKIP no addon keyconfig in this session")
        return

    def user_counts():
        keyconfig = pdt_keymap._keyconfig("user")
        if keyconfig is None:
            return None
        return {
            name: sum(len(km.keymap_items) for km in pdt_keymap._keymaps_named(keyconfig, name))
            for name in ("3D View", pdt_constants.PICK_KEYMAP_NAME)
        }

    pdt_keymap.unregister_addon_keymap()
    for item in [i for i in addon_keymap.keymap_items if i.idname in pdt_constants.PICKER_IDNAMES]:
        addon_keymap.keymap_items.remove(item)
    check("starts without a shortcut", len(pdt_keymap.picker_shortcut_items()), 0)

    before = user_counts()
    check("registered", pdt_keymap.register_addon_keymap(), 1)
    item = next(
        (i for i in addon_keymap.keymap_items if i.idname == "pdt.selection_pick_modal"), None
    )
    check("item exists", item is not None, True)
    check("bound to the picker", item.idname, "pdt.selection_pick_modal")
    check("and carries no key at all", item.type, "NONE")
    check("so the add-on reports no shortcut", pdt_keymap.picker_shortcut_label(), "not assigned")
    check("and the unbound item is not reported as one", pdt_keymap.picker_shortcut_items(), [])
    check("user keyconfig untouched", user_counts(), before)

    # Idempotent: enabling the add-on again must not stack a second item.
    check("second register adds nothing", pdt_keymap.register_addon_keymap(), 0)
    check(
        "still exactly one item",
        len([i for i in addon_keymap.keymap_items if i.idname == "pdt.selection_pick_modal"]),
        1,
    )

    # The key the *user* assigns is what gets reported (and is never touched by us).
    item.type = "K"
    item.ctrl = False
    item.shift = True
    check("an assigned key is described", pdt_keymap.describe_shortcut(pdt_keymap.picker_shortcut_items()[0]), "Shift+K")
    check("and becomes the reported shortcut", pdt_keymap.picker_shortcut_label(), "Shift+K")

    check("unregistered", pdt_keymap.unregister_addon_keymap() is None, True)
    check("the item is gone", len(pdt_keymap.picker_shortcut_items()), 0)
    check("and nothing is reported again", pdt_keymap.picker_shortcut_label(), "not assigned")
    check("user keyconfig still untouched", user_counts(), before)

    # PICK-4's reminder text, verbatim apart from the operator name.
    check(
        "the preferences print PICK-4's sentence",
        pdt_constants.REMINDER,
        f"Assign one shortcut yourself: Preferences>Keymap>{pdt_constants.OPERATOR_LABEL}",
    )
    preferences_source = inspect.getsource(pdt_preferences.PDT_AP_preferences.draw)
    check("and nothing else about the key", "REMINDER" in preferences_source, True)
    check(
        "no other shortcut hint text",
        "Enter confirms" not in preferences_source and "Change it in" not in preferences_source,
        True,
    )
    # The add-on never writes the *default* keymap either.
    module_source = addon_sources()
    check("no default keymap is written", '_keyconfig("default")' not in module_source, True)


def test_keymap_repair() -> None:
    """`repair_keymaps()` removes only the shadowing keymaps we caused.

    Removing a keymap or item invalidates its RNA pointer, so every assertion here
    works on names collected while the pointers were still alive.
    """
    print("== keymap repair: our leftover user keymaps go, real ones stay ==")
    user = pdt_keymap._keyconfig("user")
    if user is None:
        print("SKIP no user keyconfig in this session")
        return

    def make_keymap(name, space_type, idnames):
        keymap = user.keymaps.new(name=name, space_type=space_type, region_type="WINDOW")
        for idname in idnames:
            keymap.keymap_items.new(idname, type="J", value="PRESS")
        return keymap

    def keymap_names():
        return [keymap.name for keymap in user.keymaps]

    def damaged_keymaps():
        """Keymaps holding nothing but our own items: the accident signature."""
        return [
            keymap.name
            for keymap in user.keymaps
            if keymap.keymap_items
            and all(item.idname.startswith("pdt.") for item in keymap.keymap_items)
        ]

    # 1) A keymap that only holds our items is the damage we caused: repair it.
    make_keymap("3D View", "VIEW_3D", ["pdt.selection_cancel", "pdt.selection_apply"])
    check("damaged keymap exists", "3D View" in keymap_names(), True)
    check("it is recognised as damage", damaged_keymaps(), ["3D View"])
    removed = pdt_keymap.repair_keymaps()
    check("damaged keymap reported", "3D View" in removed, True)
    # The repair calls `restore_to_default()`, which rebuilds the keymap from the
    # built-in one rather than deleting the object, so assert that the damage
    # signature is gone - not that the name disappeared.
    check("no pdt-only keymap left", damaged_keymaps(), [])

    # 2) A keymap with real user items must survive, but our legacy Enter/Esc/RMB
    #    fallbacks in it are dropped (the add-on no longer registers those).
    mixed = make_keymap("Node Editor", "NODE_EDITOR", ["node.delete", "pdt.selection_apply"])
    mixed.keymap_items.new("pdt.selection_cancel", type="ESC", value="PRESS")
    before = sorted(item.idname for item in mixed.keymap_items)
    check("mixed keymap holds three items", len(before), 3)
    pdt_keymap.repair_keymaps()
    after = sorted(item.idname for item in mixed.keymap_items)
    check("mixed keymap survives", "Node Editor" in keymap_names(), True)
    check("user item kept", "node.delete" in after, True)
    check(
        "non-fallback pdt item kept",
        after.count("pdt.selection_apply"),
        before.count("pdt.selection_apply"),
    )
    check("legacy fallback removed", "pdt.selection_cancel" in after, False)
    user.keymaps.remove(mixed)

    # 3) Idempotent and harmless when there is nothing to repair.
    check("nothing left to repair", pdt_keymap.repair_keymaps(), [])

    # 4) The full register path keeps the user keyconfig untouched.
    keyconfig = pdt_keymap._keyconfig("user")
    before_count = sum(
        len(km.keymap_items) for km in keyconfig.keymaps if km.name in ("3D View", "Node Editor")
    )
    pdt_keymap.register_addon_keymap()
    pdt_keymap.repair_keymaps()
    after_count = sum(
        len(km.keymap_items) for km in keyconfig.keymaps if km.name in ("3D View", "Node Editor")
    )
    check("register does not write into the user keyconfig", after_count, before_count)
    pdt_keymap.unregister_addon_keymap()


def test_keymap_repair_runs_once_keymaps_exist() -> None:
    """The repair must run where the user keyconfig is actually populated.

    Regression (P6.2 was ineffective): `repair_keymaps()` was only called from
    `register()`, where `keyconfigs.user` has no keymaps at all yet - add-ons are
    loaded before the key configuration is built. Measured on this build: 0
    keymaps inside `register()`, 108 by the time the startup file is loaded. So
    the cleanup iterated an empty list and the damaged keymap that the P6
    accident left in `userpref.blend` survived every startup, which is exactly
    the state the hand test was meant to confirm.
    """
    import inspect

    print("== keymap repair runs once the user keyconfig exists ==")
    user = pdt_keymap._keyconfig("user")
    if user is None:
        print("SKIP no user keyconfig in this session")
        return

    def damaged():
        return [
            keymap.name
            for keymap in user.keymaps
            if keymap.keymap_items
            and all(item.idname.startswith("pdt.") for item in keymap.keymap_items)
        ]

    def make_damage():
        keymap = user.keymaps.new(
            name="Node Editor", space_type="NODE_EDITOR", region_type="WINDOW"
        )
        keymap.keymap_items.new("pdt.selection_cancel", type="RIGHTMOUSE", value="PRESS")

    # 1) `load_post` is the hook that fires right after the keymaps appear.
    make_damage()
    check("damage present before the load handler", "Node Editor" in damaged(), True)
    pdt_session._on_load_post()
    check("load handler repairs the keymaps", "Node Editor" in damaged(), False)

    # 2) The one-shot startup timer body does the same (covers a startup with no
    #    file loaded at all) and unregisters itself.
    make_damage()
    check("timer body returns None (one-shot)", pdt_keymap._repair_keymaps_when_ready(), None)
    check("timer body repaired the keymaps", "Node Editor" in damaged(), False)

    # 3) Source guards: the repair is wired into both late paths, and it uses the
    #    durable API. `keymaps.remove()` only drops the runtime keymap; the stored
    #    `U.user_keymaps` entry stays and a non-diff stored keymap overrides the
    #    built-in one again on the next keyconfig rebuild.
    check(
        "load handler calls repair_keymaps",
        "repair_keymaps" in inspect.getsource(pdt_session._on_load_post),
        True,
    )
    check(
        "register schedules the startup timer",
        "_repair_keymaps_when_ready" in inspect.getsource(pdt_keymap.register),
        True,
    )
    check(
        "repair uses the durable restore_to_default",
        "restore_to_default" in inspect.getsource(pdt_keymap.repair_keymaps),
        True,
    )


def test_session_ui_and_guards() -> None:
    print("== session overlay text and the modal's own guards ==")
    tree, selection, _obj = build_scene("")
    pdt_session.begin_pick(tree, selection)
    pdt_overlay._push_session_ui(bpy.context)
    check("overlay text pushed", "PDT Pick" in pdt_overlay._overlay_text, True)
    check("overlay timestamp set", pdt_overlay._overlay_count_at > 0.0, True)
    before = pdt_overlay._overlay_count_at
    pdt_overlay._refresh_overlay_text()
    check("refresh updates the timestamp", pdt_overlay._overlay_count_at >= before, True)
    pdt_overlay._pop_session_ui(bpy.context)
    check("overlay text cleared", pdt_overlay._overlay_text, "")
    pdt_session.cancel_pick()

    # A session that vanished behind the modal's back must end the modal instead of
    # leaving it swallowing every event (the operator itself cannot be instantiated
    # from Python, so this is checked on the source).
    import inspect

    source = inspect.getsource(pdt_pick.PDT_OT_selection_pick_modal._modal_impl)
    check("modal ends when the session vanished", 'state == "none"' in source, True)
    check("modal reports a failure once", "traceback" in inspect.getsource(pdt_pick.PDT_OT_selection_pick_modal.modal), True)
    check(
        "apply/cancel are session-guarded",
        (pdt_actions.PDT_OT_selection_apply.poll(None), pdt_actions.PDT_OT_selection_cancel.poll(None)),
        (False, False),
    )
    pdt_session.begin_pick(tree, selection)
    check(
        "and live during a session",
        (pdt_actions.PDT_OT_selection_apply.poll(None), pdt_actions.PDT_OT_selection_cancel.poll(None)),
        (True, True),
    )
    pdt_session.cancel_pick()


def test_upstream_modifiers() -> None:
    print("== modifiers above the pick modifier are part of the pick mesh ==")
    tree, selection, obj = build_scene("")
    array = obj.modifiers.new(name="Array", type="ARRAY")
    array.count = 3
    check("array starts below the picker", [m.name for m in obj.modifiers], ["GeometryNodes", "Array"])
    obj.modifiers.move(1, 0)
    check("array moved above the picker", [m.name for m in obj.modifiers], ["Array", "GeometryNodes"])
    pick_modifier = obj.modifiers["GeometryNodes"]
    check("modifier index found", pdt_geometry.modifier_index(obj, pick_modifier), 1)
    check("missing modifier is reported", pdt_geometry.modifier_index(obj, None), -1)

    session = pdt_session.begin_pick(tree, selection)
    temp_obj = bpy.data.objects[session["temp_object"]]
    check("pick mesh includes the array", len(temp_obj.data.vertices), 24)
    select_in_edit_mode(temp_obj, {1, 2, 17})
    count, text, _note = pdt_session.finish_pick()
    check("picked indices are the arrayed geometry", text, "1-2 17")
    check("count reported", count, 3)
    # The written indices must land on the same geometry the node sees: the
    # object re-evaluates with the Array applied and those 3 points deleted.
    check("re-evaluated arrayed geometry", evaluated_vertex_count(), 21)
    check("source stack untouched", [m.name for m in obj.modifiers], ["Array", "GeometryNodes"])
    check("probe objects removed", [o.name for o in bpy.data.objects if "input_" in o.name], [])
    check(
        "input mesh freed",
        [m.name for m in bpy.data.meshes if m.users == 0 and m.name.startswith(pdt_constants.TEMP_MESH_PREFIX)],
        [],
    )

    # A modifier *below* the picker is not part of what the node evaluates.
    tree, selection, obj = build_scene("")
    array = obj.modifiers.new(name="Array", type="ARRAY")
    array.count = 3
    obj.modifiers.move(1, 0)
    after = obj.modifiers.new(name="ArrayAfter", type="ARRAY")
    after.count = 5
    check("stack order", [m.name for m in obj.modifiers], ["Array", "GeometryNodes", "ArrayAfter"])
    session = pdt_session.begin_pick(tree, selection)
    check(
        "only the upstream array is baked",
        len(bpy.data.objects[session["temp_object"]].data.vertices),
        24,
    )
    pdt_session.cancel_pick()
    check(
        "cancel leaves the stack alone",
        [m.name for m in obj.modifiers],
        ["Array", "GeometryNodes", "ArrayAfter"],
    )

    # A disabled upstream modifier is skipped, exactly like in the viewport.
    tree, selection, obj = build_scene("")
    array = obj.modifiers.new(name="Array", type="ARRAY")
    array.count = 3
    obj.modifiers.move(1, 0)
    array.show_viewport = False
    session = pdt_session.begin_pick(tree, selection)
    check(
        "a disabled modifier is not baked",
        len(bpy.data.objects[session["temp_object"]].data.vertices),
        8,
    )
    pdt_session.cancel_pick()

    # No upstream modifier at all: the base mesh is used directly (no probe).
    tree, selection, obj = build_scene("")
    check("first modifier reports index 0", pdt_geometry.modifier_index(obj, obj.modifiers["GeometryNodes"]), 0)
    session = pdt_session.begin_pick(tree, selection)
    check("base mesh is used", len(bpy.data.objects[session["temp_object"]].data.vertices), 8)
    pdt_session.cancel_pick()

    # A pick tree used by the *second* Geometry Nodes modifier sees the first one.
    tree, selection, obj = build_scene("")
    first_tree = bpy.data.node_groups.new(name="First GN", type="GeometryNodeTree")
    first_tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    first_tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_in = first_tree.nodes.new("NodeGroupInput")
    group_out = first_tree.nodes.new("NodeGroupOutput")
    transform = first_tree.nodes.new("GeometryNodeTransform")
    transform.inputs["Translation"].default_value = (0.0, 0.0, 10.0)
    first_tree.links.new(group_in.outputs["Geometry"], transform.inputs["Geometry"])
    first_tree.links.new(transform.outputs["Geometry"], group_out.inputs["Geometry"])
    first_modifier = obj.modifiers.new(name="FirstNodes", type="NODES")
    first_modifier.node_group = first_tree
    obj.modifiers.move(1, 0)
    check("two node modifiers", [m.name for m in obj.modifiers], ["FirstNodes", "GeometryNodes"])
    check("only the pick tree is offered", [m.name for _o, m in pdt_geometry.list_user_objects(tree)], ["GeometryNodes"])
    session = pdt_session.begin_pick(tree, selection)
    temp_obj = bpy.data.objects[session["temp_object"]]
    check("second GN modifier sees the first one", len(temp_obj.data.vertices), 8)
    check("upstream node tree was evaluated", round(temp_obj.data.vertices[0].co.z, 4), 10.0)
    pdt_session.cancel_pick()


def build_two_owner_scene():
    """One node tree used by two objects (and later a second modifier too)."""
    tree, selection, first = build_scene("")
    mesh = bpy.data.meshes.new("second")
    mesh.from_pydata([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)], [], [])
    mesh.update()
    second = bpy.data.objects.new("SecondOwner", mesh)
    bpy.context.scene.collection.objects.link(second)
    second.modifiers.new(name="GeoNodes", type="NODES").node_group = tree
    return tree, selection, first, second


def test_multi_object_selection() -> None:
    """Several objects / modifiers on one tree: the **active object** decides.

    There is no stored chooser value any more (tools/pdt/docs/archive/07 §16): the pick follows
    the object the user is looking at, a tree used by exactly one pair falls back to
    that pair, and a genuinely ambiguous tree is refused with the candidate list.
    """
    print("== several objects / modifiers on the same tree ==")
    tree, selection, first, second = build_two_owner_scene()
    names = [obj.name for obj, _modifier in pdt_geometry.list_user_objects(tree)]
    check("both owners are listed", sorted(names), ["CubeOwner", "SecondOwner"])

    bpy.context.view_layer.objects.active = second
    check(
        "the active object is used",
        pdt_geometry.find_user_object(tree)[0].name,
        "SecondOwner",
    )
    bpy.context.view_layer.objects.active = first
    check("and switching the active object switches the source", pdt_geometry.find_user_object(tree)[0].name, "CubeOwner")

    first.modifiers.new(name="GeoNodes2", type="NODES").node_group = tree
    pairs = [(obj.name, modifier.name) for obj, modifier in pdt_geometry.list_user_objects(tree)]
    check("second modifier is listed", ("CubeOwner", "GeoNodes2") in pairs, True)
    check("three owner/modifier pairs", len(pairs), 3)
    check("still qualifies", pdt_hit_test.can_pick(tree, selection)[0], True)
    check(
        "the active object's first modifier is the one baked",
        pdt_geometry.find_user_object(tree)[1].name,
        "GeometryNodes",
    )

    # The evaluation really comes from the active object (8 verts, not 3).
    session = pdt_session.begin_pick(tree, selection)
    check("session records the chosen object", session["src_object"], "CubeOwner")
    check("baked from that object", len(bpy.data.objects[session["temp_object"]].data.vertices), 8)
    pdt_session.cancel_pick()

    # An ambiguous tree with no matching active object is refused, and the reason
    # lists the candidates instead of silently baking the first one.
    bpy.context.view_layer.objects.active = None
    ok, reason = pdt_hit_test.can_pick(tree, selection)
    check("an ambiguous tree does not qualify", ok, False)
    check("and the reason lists the candidates", "CubeOwner" in reason and "SecondOwner" in reason, True)
    check("nothing is resolved", pdt_geometry.find_user_object(tree), (None, None))
    message = ""
    try:
        pdt_session.begin_pick(tree, selection)
    except RuntimeError as error:
        message = str(error)
    else:
        pdt_session.cancel_pick()
    check("begin_pick refuses too", "activate" in message, True)

    # A tree used by exactly one pair needs no active object at all.
    second.modifiers.remove(second.modifiers["GeoNodes"])
    first.modifiers.remove(first.modifiers["GeoNodes2"])
    bpy.context.view_layer.objects.active = None
    check(
        "a single pair is used without an active object",
        pdt_geometry.find_user_object(tree)[0].name,
        "CubeOwner",
    )


def build_shared_override_scene():
    """One tree whose *group input* drives a move, shared by two objects.

    Group Input(Geometry, "Offset Z") -> Combine XYZ(Z) -> Set Position -> PDT Selection
    -> Group Output: the geometry the pick node sees depends on the "Offset Z" group
    input, and each object's modifier overrides it differently (0 and 5).
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    tree = bpy.data.node_groups.new(name="PDT Override Test", type="GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    offset_socket = tree.interface.new_socket(
        name="Offset Z", in_out="INPUT", socket_type="NodeSocketFloat"
    )
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    group_in = tree.nodes.new("NodeGroupInput")
    combine = tree.nodes.new("ShaderNodeCombineXYZ")
    set_position = tree.nodes.new("GeometryNodeSetPosition")
    selection = tree.nodes.new("GeometryNodePDTSelection")
    selection.inputs["Selection"].default_value = "*"
    selection.domain = "VERTEX"
    group_out = tree.nodes.new("NodeGroupOutput")

    tree.links.new(group_in.outputs["Geometry"], set_position.inputs["Geometry"])
    tree.links.new(group_in.outputs["Offset Z"], combine.inputs["Z"])
    tree.links.new(combine.outputs["Vector"], set_position.inputs["Offset"])
    tree.links.new(set_position.outputs["Geometry"], selection.inputs["Geometry"])
    tree.links.new(selection.outputs["Geometry"], group_out.inputs["Geometry"])

    corners = [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
        (1.0, 0.0, 1.0),
        (1.0, 1.0, 1.0),
        (0.0, 1.0, 1.0),
    ]
    objects = []
    for name in ("CubeOwner", "SecondOwner"):
        mesh = bpy.data.meshes.new(name.lower())
        mesh.from_pydata(corners, [], [(0, 1, 2, 3), (4, 5, 6, 7)])
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        objects.append(obj)

    owner, other = objects
    owner_modifier = owner.modifiers.new(name="GeometryNodes", type="NODES")
    owner_modifier.node_group = tree
    other_modifier = other.modifiers.new(name="GeoNodes", type="NODES")
    other_modifier.node_group = tree

    identifier = offset_socket.identifier
    check("an override is written", pdt_geometry.set_interface_input(owner_modifier, identifier, 0.0), True)
    check("and on the other object", pdt_geometry.set_interface_input(other_modifier, identifier, 5.0), True)

    bpy.context.view_layer.objects.active = owner
    owner.select_set(True)
    return tree, selection, owner, other, identifier


def mean_z(obj_data) -> float:
    vertices = obj_data.vertices
    return round(sum(vertex.co.z for vertex in vertices) / len(vertices), 4)


def test_shared_group_input_override() -> None:
    """A pair's node-group input override must reach the pick bake.

    The override lives in Blender's *generated* RNA for a Nodes modifier
    (`modifier.properties.inputs.<identifier>.value`), which is invisible to
    `modifier.keys()` - that raises `TypeError: this type doesn't support
    IDProperties` for a Nodes modifier. Before this was copied, the temporary
    tree evaluated its inputs at the **defaults**, so an object whose modifier
    overrode an input baked geometry that was not overridden (measured: an
    "Offset Z" of 3 baked at Z = 0).
    """
    print("== node-group input overrides of the picked object ==")
    pdt_overlay._editor_state = None
    tree, selection, owner, other, identifier = build_shared_override_scene()

    check(
        "the generated inputs struct is reachable",
        pdt_geometry.interface_inputs(owner.modifiers[0]) is not None,
        True,
    )
    check(
        "its pointer properties are the interface sockets",
        pdt_geometry.interface_socket_identifiers(pdt_geometry.interface_inputs(owner.modifiers[0])),
        ["Socket_0", "Socket_1"],
    )
    check(
        "the override reads back per object",
        (
            pdt_geometry.get_interface_input(owner.modifiers[0], identifier),
            pdt_geometry.get_interface_input(other.modifiers[0], identifier),
        ),
        (0.0, 5.0),
    )
    check(
        "an unknown identifier has no value",
        pdt_geometry.get_interface_input(owner.modifiers[0], "Socket_99", "unset"),
        "unset",
    )

    # A cube's corners mean z=0.5; every bake must carry its own offset.
    bpy.context.view_layer.objects.active = owner
    session = pdt_session.begin_pick(tree, selection)
    temp_object = bpy.data.objects[session["temp_object"]]
    check("the active object's bake: point count", len(temp_object.data.vertices), 8)
    check("the active object's bake: no offset", mean_z(temp_object.data), 0.5)
    pdt_session.cancel_pick()
    check("nothing left behind", temporary_leftovers(), ([], []))
    check("no data-blocks left", temporary_data_leftovers(), [])

    bpy.context.view_layer.objects.active = other
    session = pdt_session.begin_pick(tree, selection)
    temp_object = bpy.data.objects[session["temp_object"]]
    check("the second object's bake: point count", len(temp_object.data.vertices), 8)
    check("the second object's bake: its own Offset Z", mean_z(temp_object.data), 5.5)
    pdt_session.cancel_pick()
    check("nothing left behind after the second bake", temporary_leftovers(), ([], []))
    check("no data-blocks left after the second bake", temporary_data_leftovers(), [])


def test_group_parameter_is_not_preselected() -> None:
    """The picker writes indices, so it does not expand groups (P13).

    A parameter that still contains a group keeps working for the *node*, but the
    viewport pre-selection only contains what Python can evaluate (the numeric part).
    This is the documented boundary: expanding groups in Python would mean
    reimplementing the node's wildcard, `adapt_domain` and attribute rules.
    """
    print("== a group in the parameter is not pre-selected (P13 boundary) ==")
    tree, selection, obj = build_scene("A")
    members = {0, 1, 2}
    attribute = obj.data.attributes.new(name="group:A", type="BOOLEAN", domain="POINT")
    for index in range(len(attribute.data)):
        attribute.data[index].value = index in members

    session = pdt_session.begin_pick(tree, selection)
    check("a group alone pre-selects nothing", session["preselected"], 0)
    temp_obj = bpy.data.objects[session["temp_object"]]
    check(
        "group members are not selected in edit mode",
        edit_mode_selection(temp_obj, "POINT"),
        [],
    )
    check(
        "the group attribute still survives the bake (the node needs it)",
        "group:A" in temp_obj.data.attributes,
        True,
    )
    pdt_session.cancel_pick()

    # The numeric part of a mixed value is still pre-selected, so continuing a pick
    # that was started from plain indices keeps working.
    selection.inputs["Selection"].default_value = "4-5 A"
    session = pdt_session.begin_pick(tree, selection)
    check("only the indices of a mixed value pre-select", session["preselected"], 2)
    check(
        "edit mode selection matches the numeric part",
        edit_mode_selection(bpy.data.objects[session["temp_object"]], "POINT"),
        [4, 5],
    )
    pdt_session.cancel_pick()

    # Attribute patterns are not expanded either.
    selection.inputs["Selection"].default_value = "@position.x>0"
    session = pdt_session.begin_pick(tree, selection)
    check("an attribute pattern pre-selects nothing", session["preselected"], 0)
    pdt_session.cancel_pick()


def test_clear_parameter() -> None:
    print("== clearing the parameter (panel button) ==")
    tree, selection, obj = build_scene("0-1 5")
    selection[pdt_constants.LONG_SELECTION_KEY] = "0-3"
    target, _reason = pdt_targets.choose_string_target(selection)
    pdt_targets.write_target_value(selection, target, "")
    if pdt_constants.LONG_SELECTION_KEY in selection:
        del selection[pdt_constants.LONG_SELECTION_KEY]
    check("parameter emptied", selection.inputs["Selection"].default_value, "")
    check("stored long selection dropped", pdt_constants.LONG_SELECTION_KEY in selection, False)
    check("preselect of an empty parameter is empty", pdt_parser.preselect_indices("", 8), set())


def build_nested_scene():
    """Top -> Group(Middle) -> Group(Inner); the object uses Top.

    The inner tree deletes the two selected points, so the effect of a node
    inside a sub-group is observable at the top level (4 vertices in, 2 out).
    """
    inner = bpy.data.node_groups.new(name="Inner", type="GeometryNodeTree")
    inner.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    inner.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    inner_in = inner.nodes.new("NodeGroupInput")
    inner_transform = inner.nodes.new("GeometryNodeTransform")
    inner_transform.inputs["Translation"].default_value = (0.0, 0.0, 10.0)
    inner_sel = inner.nodes.new("GeometryNodePDTSelection")
    inner_sel.inputs["Selection"].default_value = "0-1"
    inner_out = inner.nodes.new("NodeGroupOutput")
    inner.links.new(inner_in.outputs["Geometry"], inner_transform.inputs["Geometry"])
    inner.links.new(inner_transform.outputs["Geometry"], inner_sel.inputs["Geometry"])
    inner.links.new(inner_sel.outputs["Geometry"], inner_out.inputs["Geometry"])

    middle = bpy.data.node_groups.new(name="Middle", type="GeometryNodeTree")
    middle.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    middle.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    middle_in = middle.nodes.new("NodeGroupInput")
    middle_group = middle.nodes.new("GeometryNodeGroup")
    middle_group.node_tree = inner
    middle_out = middle.nodes.new("NodeGroupOutput")
    middle.links.new(middle_in.outputs["Geometry"], middle_group.inputs["Geometry"])
    middle.links.new(middle_group.outputs["Geometry"], middle_out.inputs["Geometry"])

    top = bpy.data.node_groups.new(name="Top", type="GeometryNodeTree")
    top.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    top.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    top_in = top.nodes.new("NodeGroupInput")
    top_group = top.nodes.new("GeometryNodeGroup")
    top_group.node_tree = middle
    top_out = top.nodes.new("NodeGroupOutput")
    top.links.new(top_in.outputs["Geometry"], top_group.inputs["Geometry"])
    top.links.new(top_group.outputs["Geometry"], top_out.inputs["Geometry"])

    mesh = bpy.data.meshes.new("nested_mesh")
    mesh.from_pydata([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)], [], [(0, 1, 2, 3)])
    mesh.update()
    obj = bpy.data.objects.new("NestedOwner", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.modifiers.new(name="GeoNodes", type="NODES").node_group = top
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    chain = [(top, None), (middle, top_group), (inner, middle_group)]
    return inner, inner_sel, top, top_group, middle, obj, chain


def evaluate_temp_tree(temp_tree, obj):
    """Vertex count when the copied chain is evaluated on a temp object."""
    temp_obj = bpy.data.objects.new("pdt_chain_probe", obj.data)
    bpy.context.scene.collection.objects.link(temp_obj)
    temp_obj.matrix_world = obj.matrix_world
    temp_obj.modifiers.new(name="PDT", type="NODES").node_group = temp_tree
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = temp_obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    count = len(mesh.vertices)
    evaluated.to_mesh_clear()
    bpy.data.objects.remove(temp_obj, do_unlink=True)
    return count


def temp_baked_z(temp_tree, obj):
    """Z of the first baked vertex, to prove the inner geometry was used."""
    temp_obj = bpy.data.objects.new("pdt_chain_probe_z", obj.data)
    bpy.context.scene.collection.objects.link(temp_obj)
    temp_obj.matrix_world = obj.matrix_world
    temp_obj.modifiers.new(name="PDT", type="NODES").node_group = temp_tree
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = temp_obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    z = round(mesh.vertices[0].co.z, 4) if len(mesh.vertices) else None
    evaluated.to_mesh_clear()
    bpy.data.objects.remove(temp_obj, do_unlink=True)
    return z


def test_nested_groups() -> None:
    print("== nodes inside sub-groups (breadcrumb chain) ==")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    inner, inner_sel, top, top_group, middle, obj, chain = build_nested_scene()

    check("no object uses the inner tree", len(pdt_geometry.list_user_objects(inner)), 0)
    check("plain lookup refuses a sub-group", pdt_hit_test.can_pick(inner, inner_sel)[0], False)
    check("outermost tree of the chain", pdt_geometry.outermost_tree(None, inner, chain), top)
    ok, reason = pdt_hit_test.can_pick(inner, inner_sel, explicit_chain=chain)
    check("qualifies through the chain", ok, True)

    temp_tree, temp_groups, owner, _modifier = pdt_geometry.build_evaluation_tree(
        inner, inner_sel, explicit_chain=chain
    )
    check("one copy per level", len(temp_groups), 3)
    check("innermost copy is a copy", temp_groups[0] != inner.name, True)
    check("outermost copy is a copy", temp_groups[-1] != top.name, True)
    check("owner object found", owner.name, "NestedOwner")
    check("user's tree untouched", top.nodes[top_group.name].node_tree, middle)
    check("inner tree untouched", middle.nodes["Group"].node_tree, inner)

    # The node inside the sub-group really drives the result: 4 points in, the
    # node's "0-1" selection deletes two of them.
    check("nested evaluation reaches inside the sub-group", evaluate_temp_tree(temp_tree, obj), 4)
    check("inner geometry really is transformed", temp_baked_z(temp_tree, obj), 10.0)

    session = pdt_session.begin_pick(inner, inner_sel, explicit_chain=chain)
    check("session records the depth", session["chain_depth"], 3)
    check("baked mesh comes through the chain", len(bpy.data.objects[session["temp_object"]].data.vertices), 4)
    temp_obj = bpy.data.objects[session["temp_object"]]
    select_in_edit_mode(temp_obj, {2, 3})
    count, text, _note = pdt_session.finish_pick()
    check("picked inside a sub-group", text, "2-3")
    check("count reported", count, 2)
    check("parameter written on the inner node", inner_sel.inputs["Selection"].default_value, "2-3")
    check(
        "session temp copies cleaned up",
        [n for n in session["temp_groups"] if n in bpy.data.node_groups],
        [],
    )
    check("session closed", len(pdt_state.session), 0)


def test_can_pick_call_forms() -> None:
    """`can_pick()`'s call forms, and the source guards that outlived the panel.

    The original regression was a chained call pasted into the panel's `draw()`, where
    `explicit_chain` does not exist (a `NameError` only visible in the UI). The panel is gone
    (PICK-4), so what is guarded here is that `can_pick()` still takes every form a caller may
    use - and that the keymap discipline is still wired into `register()` / `unregister()`.
    """
    import inspect

    print("== can_pick call forms, keymap discipline, and 'no panel' ==")
    tree, selection, obj = build_scene("")
    check("two-argument form", pdt_hit_test.can_pick(tree, selection)[0], True)
    check("with context", pdt_hit_test.can_pick(tree, selection, bpy.context)[0], True)
    check("with an explicit chain", pdt_hit_test.can_pick(tree, selection, bpy.context, [])[0], True)
    check("with None arguments", pdt_hit_test.can_pick(tree, selection, None, None)[0], True)
    check(
        "with the socket the cursor is on (PICK-1)",
        pdt_hit_test.can_pick(tree, selection, bpy.context, [], "Selection")[0],
        True,
    )

    # No node-editor panel: not defined, not registered, and nothing in the module references
    # one (the sidebar was the old trigger's home, which is what made it node-level).
    check("the panel class is gone", hasattr(pdt, "NODE_PT_pdt_selection"), False)
    check("and no panel is registered", hasattr(bpy.types, "NODE_PT_pdt_selection"), False)
    module_source = addon_sources()
    check("nothing draws a PDT node-editor panel", "bl_space_type" not in module_source, True)
    check(
        "the removed panel-only operators are gone too",
        [
            name
            for name in (
                "PDT_OT_selection_set_target",
                "PDT_OT_selection_set_domain",
                "PDT_OT_selection_set_kind",
                "PDT_OT_selection_set_reference",
                "PDT_OT_selection_set_instance_mode",
            )
            if hasattr(pdt, name)
        ],
        [],
    )
    check(
        "the operators a paused pick still needs are registered",
        [
            name
            for name in ("PDT_OT_selection_apply", "PDT_OT_selection_cancel", "PDT_OT_selection_cleanup")
            if not hasattr(bpy.types, name)
        ],
        [],
    )

    # Keymap discipline, guarded on the source as well: the keymap lives in `keymap.py`, which
    # registers into the addon keyconfig, repairs leftovers, and never writes user keymaps. The
    # package root only delegates (one module per concern).
    check(
        "the add-on registers every module",
        all(
            f"{module}.register()" in inspect.getsource(pdt.register)
            for module in ("operators", "menus", "preferences", "keymap", "session")
        ),
        True,
    )
    check(
        "and unregisters them again",
        all(
            f"{module}.unregister()" in inspect.getsource(pdt.unregister)
            for module in ("operators", "menus", "preferences", "keymap", "session")
        ),
        True,
    )
    keymap_register = inspect.getsource(pdt_keymap.register)
    check("add-on start repairs keymaps first", keymap_register.index("repair_keymaps") <
          keymap_register.index("register_addon_keymap"), True)
    check("add-on start registers the addon keymap", "register_addon_keymap" in keymap_register, True)
    unregister_source = inspect.getsource(pdt_keymap.unregister)
    check("unregister removes the addon keymap", "unregister_addon_keymap" in unregister_source, True)
    # The add-on may create exactly one keymap, and it has to be the addon-level
    # one. This replaced a substring check for the old "_user_keymap" helper,
    # which a *lookup* helper (used by the repair) could trip by accident.
    check("creates exactly one keymap in total", module_source.count("keymaps.new("), 1)
    check(
        "and it is created in the addon keyconfig",
        "keymaps.new(" in inspect.getsource(pdt_keymap._addon_keymap)
        and '_keyconfig("addon")' in inspect.getsource(pdt_keymap._addon_keymap),
        True,
    )
    check(
        "loaded files repair keymaps",
        "repair_keymaps" in inspect.getsource(pdt_session._on_load_post),
        True,
    )


def test_ui_icon_names() -> None:
    """Every `icon="..."` the add-on passes to the UI must be a real icon name.

    Regression: the add-on preferences drew `icon="KEYMAP"`, which does not exist
    in Blender's icon enum, so opening Preferences -> Add-ons -> PDT raised
    `TypeError: UILayout.label(): error with keyword argument "icon"` (followed by
    the full enum dump). Nothing headless runs `draw()`, so only a source scan
    against Blender's own enum catches this class of UI-only failure.
    """
    import inspect
    import re

    print("== icon names used by the UI exist in Blender's icon enum ==")
    parameters = bpy.types.UILayout.bl_rna.functions["label"].parameters
    valid = {item.identifier for item in parameters["icon"].enum_items}
    check("icon enum is available", len(valid) > 100, True)

    used = sorted(set(re.findall(r'icon="([A-Za-z0-9_]+)"', addon_sources())))
    # The sidebar panel (which carried most of the icons) is gone with PICK-4, and the pies take
    # their icons from the tables checked below - so the literal scan only sees the few fixed ones.
    check("the UI still draws a literal icon", len(used) >= 1, True)
    for name in used:
        check(f"icon {name!r} exists", name in valid, True)

    # The pie icons travel through the icon tables into the slices, so a source scan cannot see
    # them - and a name Blender does not know raises inside `draw()`, which empties the whole
    # menu (measured twice). Both vocabularies are checked here, key by key.
    for table_name, table, keys in (
        ("DOMAIN_ICONS", pdt_constants.DOMAIN_ICONS, pdt_constants.DOMAINS),
        ("INSTANCE_MODE_ICONS", pdt_constants.INSTANCE_MODE_ICONS, pdt_constants.INSTANCE_MODES),
    ):
        check(f"{table_name} covers every case", sorted(table), sorted(keys))
        for key, icon in table.items():
            check(f"{table_name}[{key!r}] is a Blender icon", icon in valid, True)

    # The element-kind icons must be the ones the Spreadsheet draws for the same element
    # (`spreadsheet_dataset_draw.cc`): mesh rows `mesh_domain_to_icon()` -> VERTEXSEL / EDGESEL /
    # FACESEL, curve rows `curves_domain_to_icon()` -> **CURVE_BEZCIRCLE** for control points and
    # **CURVE_PATH** for splines (the object-data icons CURVE_BEZCURVE / OUTLINER_DATA_CURVES are
    # not what the Spreadsheet uses), the Point Cloud's `PointsViewItem` -> POINTCLOUD_POINT, and
    # the `InstancesViewItem` -> EMPTY_AXIS. Pinned one by one, because "close enough" here means
    # a slice that looks like a different element.
    check(
        "the curve/point-cloud/instance slices use the Spreadsheet's icons",
        {domain: pdt_constants.DOMAIN_ICONS[domain]
         for domain in ("CONTROL_POINT", "SPLINE", "POINT", "INSTANCE")},
        {
            "CONTROL_POINT": "CURVE_BEZCIRCLE",
            "SPLINE": "CURVE_PATH",
            "POINT": "POINTCLOUD_POINT",
            "INSTANCE": "EMPTY_AXIS",
        },
    )
    check(
        "the mesh slices use the Spreadsheet's mesh icons",
        {domain: pdt_constants.DOMAIN_ICONS[domain] for domain in ("VERTEX", "EDGE", "FACE")},
        {"VERTEX": "VERTEXSEL", "EDGE": "EDGESEL", "FACE": "FACESEL"},
    )
    check(
        "no slice falls back to the object-data or modifier icon",
        [
            domain
            for domain, icon in pdt_constants.DOMAIN_ICONS.items()
            if icon in {"CURVE_BEZCURVE", "OUTLINER_DATA_CURVES", "MOD_INSTANCE"}
        ],
        [],
    )

    tree, selection, _obj = build_mixed_scene(with_point_cloud=True)
    slices = pdt_menus.domain_pie_entries(selection, tree)
    check(
        "every element kind's slice carries its table icon",
        [entry["icon"] for entry in slices],
        [pdt_constants.DOMAIN_ICONS[domain] for domain in pdt_constants.DOMAINS],
    )
    modes = pdt_menus.instance_mode_pie_entries()
    check(
        "every instance mode's slice carries its table icon",
        [mode["icon"] for mode in modes],
        [pdt_constants.INSTANCE_MODE_ICONS[mode] for mode in pdt_constants.INSTANCE_MODES],
    )

    # The preferences are the one that broke; they must still show the reminder (and the
    # reminder itself still has to be PICK-4's sentence).
    check(
        "preferences print the shortcut reminder",
        "REMINDER" in inspect.getsource(pdt_preferences.PDT_AP_preferences.draw)
        and "Preferences>Keymap>" in pdt_constants.REMINDER,
        True,
    )


def test_geometry_source_resolution() -> None:
    """The baked geometry must be what the node sees, or what its field applies to.

    Regression: `resolve_geometry_source()` returned the node's own geometry
    *output* first. For the "only wire the Selection output" style - which the
    add-on's own docs and panel describe - the geometry input is unconnected, so
    that pass-through output is *empty*: the pick mesh had zero points and there
    was nothing to select, while the intended fallback (the geometry input of the
    downstream consumer of the field) was unreachable behind it.
    """
    print("== geometry source: wired input, and the field-only wiring ==")

    # 1) The plain case: the geometry input is wired, so the source is its upstream.
    tree, selection, obj = build_scene("0-1")
    source = pdt_geometry.resolve_geometry_source(selection)
    check("wired input resolves upstream", source["node"], "Group Input")
    check("resolved as an output socket", source["is_output"], True)

    # 2) Field-only, top level: nothing flows through the pick node itself, so the
    #    geometry has to come from the consumer of the Selection field.
    group_in = next(n for n in tree.nodes if n.bl_idname == "NodeGroupInput")
    delete = next(n for n in tree.nodes if n.bl_idname == "GeometryNodeDeleteGeometry")
    for link in list(selection.inputs["Geometry"].links):
        tree.links.remove(link)
    for link in list(delete.inputs["Geometry"].links):
        tree.links.remove(link)
    tree.links.new(group_in.outputs["Geometry"], delete.inputs["Geometry"])
    check("no geometry through the node", len(selection.inputs["Geometry"].links), 0)

    source = pdt_geometry.resolve_geometry_source(selection)
    check("field-only resolves to the consumer's geometry", source["node"], "Group Input")
    session = pdt_session.begin_pick(tree, selection)
    temp_obj = bpy.data.objects[session["temp_object"]]
    check("pick mesh is not empty", len(temp_obj.data.vertices), 8)
    select_in_edit_mode(temp_obj, {2, 3})
    count, text, _note = pdt_session.finish_pick()
    check("field-only pick writes back", text, "2-3")
    check("count reported", count, 2)
    check("parameter written", selection.inputs["Selection"].default_value, "2-3")

    # 3) The same wiring one level down, inside a group: the consumer's geometry
    #    comes through the group's Group Input.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    inner = bpy.data.node_groups.new(name="InnerFieldOnly", type="GeometryNodeTree")
    inner.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    inner.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    inner_in = inner.nodes.new("NodeGroupInput")
    inner_sel = inner.nodes.new("GeometryNodePDTSelection")
    inner_sel.inputs["Selection"].default_value = "0-1"
    inner_delete = inner.nodes.new("GeometryNodeDeleteGeometry")
    inner_out = inner.nodes.new("NodeGroupOutput")
    inner.links.new(inner_in.outputs["Geometry"], inner_delete.inputs["Geometry"])
    inner.links.new(inner_sel.outputs["Selection"], inner_delete.inputs["Selection"])
    inner.links.new(inner_delete.outputs["Geometry"], inner_out.inputs["Geometry"])

    top = bpy.data.node_groups.new(name="TopFieldOnly", type="GeometryNodeTree")
    top.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    top.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    top_in = top.nodes.new("NodeGroupInput")
    top_group = top.nodes.new("GeometryNodeGroup")
    top_group.node_tree = inner
    top_out = top.nodes.new("NodeGroupOutput")
    top.links.new(top_in.outputs["Geometry"], top_group.inputs["Geometry"])
    top.links.new(top_group.outputs["Geometry"], top_out.inputs["Geometry"])

    mesh = bpy.data.meshes.new("quad")
    mesh.from_pydata(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)], [], [(0, 1, 2, 3)]
    )
    mesh.update()
    owner = bpy.data.objects.new("FieldOnlyOwner", mesh)
    bpy.context.scene.collection.objects.link(owner)
    owner.modifiers.new(name="GeometryNodes", type="NODES").node_group = top
    bpy.context.view_layer.objects.active = owner
    owner.select_set(True)
    chain = [(top, None), (inner, top_group)]

    source = pdt_geometry.resolve_geometry_source(inner_sel)
    check("nested field-only resolves inside the group", source["node"], "Group Input")
    session = pdt_session.begin_pick(inner, inner_sel, explicit_chain=chain)
    temp_obj = bpy.data.objects[session["temp_object"]]
    check("nested field-only pick mesh is the group input", len(temp_obj.data.vertices), 4)
    select_in_edit_mode(temp_obj, {1, 2})
    count, text, _note = pdt_session.finish_pick()
    check("nested field-only writes back on the inner node", text, "1-2")
    check("inner parameter written", inner_sel.inputs["Selection"].default_value, "1-2")

    # 4) "The input is the previous modifier's result, so the nodes before the
    #    group must be in there too": a previous GN modifier (Z+10) plus a
    #    Subdivide before the group node, picked two levels down.
    bpy.ops.wm.read_factory_settings(use_empty=True)

    def group_with_pick(name):
        tree_ = bpy.data.node_groups.new(name=name, type="GeometryNodeTree")
        tree_.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        tree_.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        group_in_ = tree_.nodes.new("NodeGroupInput")
        node_ = tree_.nodes.new("GeometryNodePDTSelection")
        node_.inputs["Selection"].default_value = "0-1"
        group_out_ = tree_.nodes.new("NodeGroupOutput")
        tree_.links.new(group_in_.outputs["Geometry"], node_.inputs["Geometry"])
        tree_.links.new(node_.outputs["Geometry"], group_out_.inputs["Geometry"])
        return tree_, node_

    def wrap(name, child):
        tree_ = bpy.data.node_groups.new(name=name, type="GeometryNodeTree")
        tree_.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        tree_.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        group_in_ = tree_.nodes.new("NodeGroupInput")
        group_node_ = tree_.nodes.new("GeometryNodeGroup")
        group_node_.node_tree = child
        group_out_ = tree_.nodes.new("NodeGroupOutput")
        tree_.links.new(group_in_.outputs["Geometry"], group_node_.inputs["Geometry"])
        tree_.links.new(group_node_.outputs["Geometry"], group_out_.inputs["Geometry"])
        return tree_, group_node_

    prev = bpy.data.node_groups.new(name="PrevGN", type="GeometryNodeTree")
    prev.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    prev.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    prev_in = prev.nodes.new("NodeGroupInput")
    prev_tr = prev.nodes.new("GeometryNodeTransform")
    prev_out = prev.nodes.new("NodeGroupOutput")
    prev_tr.inputs["Translation"].default_value = (0.0, 0.0, 10.0)
    prev.links.new(prev_in.outputs["Geometry"], prev_tr.inputs["Geometry"])
    prev.links.new(prev_tr.outputs["Geometry"], prev_out.inputs["Geometry"])

    inner_j, inner_sel_j = group_with_pick("InnerJ")
    mid_j, mid_group_j = wrap("MidJ", inner_j)
    top_j, top_group_j = wrap("TopJ", mid_j)
    top_in_j = next(n for n in top_j.nodes if n.bl_idname == "NodeGroupInput")
    sub = top_j.nodes.new("GeometryNodeSubdivideMesh")
    sub.inputs["Level"].default_value = 1
    for link in list(top_group_j.inputs["Geometry"].links):
        top_j.links.remove(link)
    top_j.links.new(top_in_j.outputs["Geometry"], sub.inputs[0])
    top_j.links.new(sub.outputs[0], top_group_j.inputs["Geometry"])

    mesh_j = bpy.data.meshes.new("quad_j")
    mesh_j.from_pydata(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)], [], [(0, 1, 2, 3)]
    )
    mesh_j.update()
    owner_j = bpy.data.objects.new("ChainOwner", mesh_j)
    bpy.context.scene.collection.objects.link(owner_j)
    owner_j.modifiers.new(name="PrevGN", type="NODES").node_group = prev
    owner_j.modifiers.new(name="GeometryNodes", type="NODES").node_group = top_j
    bpy.context.view_layer.objects.active = owner_j
    owner_j.select_set(True)
    bpy.context.view_layer.update()

    chain_j = [(top_j, None), (mid_j, top_group_j), (inner_j, mid_group_j)]
    session = pdt_session.begin_pick(inner_j, inner_sel_j, explicit_chain=chain_j)
    temp_obj = bpy.data.objects[session["temp_object"]]
    zs = sorted({round(v.co.z, 3) for v in temp_obj.data.vertices})
    check("previous modifier result is in the pick mesh", zs, [10.0])
    check("nodes before the group are in the pick mesh", len(temp_obj.data.vertices), 9)
    pdt_session.cancel_pick()


def test_breadcrumb_recovers_group_nodes() -> None:
    """The node editor's path items carry no group node in this Blender.

    Regression: `breadcrumb()` read `item.node`, but the `NodeTreePath` RNA only
    exposes `node_tree` (verified in a live session: `bl_rna.properties` has just
    that one entry, so the read always produced None). `build_evaluation_tree()`
    then skipped every parent level and baked the object's own geometry through
    the innermost tree - the pick mesh showed the object data instead of the
    geometry that actually enters the group. It went unnoticed because the tests
    pass a hand-built chain with the group nodes filled in.
    """
    print("== breadcrumb: group instances are recovered from the parent tree ==")
    path_properties = {p.identifier for p in bpy.types.NodeTreePath.bl_rna.properties}
    check("path items expose no 'node' property", "node" in path_properties, False)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    inner, inner_sel, top, top_group, middle, obj, chain = build_nested_scene()

    # The top level transforms the geometry before the group, so a skipped parent
    # level is visible in the baked coordinates (10 vs 10 + 5).
    top_in = next(n for n in top.nodes if n.bl_idname == "NodeGroupInput")
    for link in list(top_group.inputs["Geometry"].links):
        top.links.remove(link)
    parent_transform = top.nodes.new("GeometryNodeTransform")
    parent_transform.inputs["Translation"].default_value = (0.0, 0.0, 5.0)
    top.links.new(top_in.outputs["Geometry"], parent_transform.inputs["Geometry"])
    top.links.new(parent_transform.outputs["Geometry"], top_group.inputs["Geometry"])

    class _PathItem:
        """Like the real RNA: only `node_tree`, deliberately no `node`."""

        def __init__(self, node_tree):
            self.node_tree = node_tree

    class _Space:
        def __init__(self, path):
            self.path = path

    class _Context:
        def __init__(self, space):
            self.space_data = space

    from_ui = pdt_geometry.breadcrumb(_Context(_Space([_PathItem(top), _PathItem(middle), _PathItem(inner)])))
    check("every breadcrumb level survives", len(from_ui), 3)
    check("the top level has no parent node", from_ui[0][1], None)
    check("middle level resolved to its group node", from_ui[1][1], top_group)
    check("inner level resolved to its group node", from_ui[2][1], chain[2][1])

    # End to end: picking through the chain the UI produces must bake the geometry
    # that reaches the node, including the parent level's transform.
    session = pdt_session.begin_pick(inner, inner_sel, explicit_chain=from_ui)
    temp_obj = bpy.data.objects[session["temp_object"]]
    check("baked through the whole chain", len(temp_obj.data.vertices), 4)
    check(
        "parent level really was applied",
        sorted({round(v.co.z, 3) for v in temp_obj.data.vertices}),
        [15.0],
    )
    pdt_session.cancel_pick()

    # A level that cannot be resolved must be reported, not silently skipped.
    broken = [(top, None), (middle, None), (inner, None)]
    try:
        pdt_geometry.build_evaluation_tree(inner, inner_sel, explicit_chain=broken)
        check("unresolvable level is reported", "no error", "RuntimeError")
    except RuntimeError as exc:
        check("unresolvable level is reported", "could not find the group node" in str(exc), True)


def test_node_editor_pin() -> None:
    """A pick pins the node editor, so the breadcrumb survives it.

    Regression: `begin_pick()` makes the temporary object active. An unpinned
    node editor re-derives the tree it shows from the active object
    (`geometry_node_tree_get_from_context()` = the active modifier's node group)
    and calls `ED_node_tree_start()`, which rebuilds the breadcrumb as a single
    level - so finishing a pick inside a group dumped the user back at the root
    of the tree instead of the group they were editing.
    """
    import inspect

    print("== node editor pinning around a pick ==")

    class _Space:
        type = "NODE_EDITOR"

        def __init__(self, pin=False):
            self.pin = pin

    class _Workspace:
        def status_text_set(self, _text):
            pass

    class _Context:
        def __init__(self, space):
            self.space_data = space
            self.workspace = _Workspace()

    space = _Space()
    check("the pick pins the editor", pdt_overlay._pin_node_editor(_Context(space)), 1)
    check("editor reports pinned", space.pin, True)
    pdt_overlay._unpin_node_editors()
    check("pin restored afterwards", space.pin, False)

    user_pinned = _Space(pin=True)
    check("a user pin is captured too", pdt_overlay._pin_node_editor(_Context(user_pinned)), 1)
    check("but it is not marked as ours to clear", pdt_overlay._editor_state["pin_set"], False)
    pdt_overlay._unpin_node_editors()
    check("user pin still set", user_pinned.pin, True)

    class _OtherSpace:
        type = "VIEW_3D"
        pin = False

    check("other spaces are ignored", pdt_overlay._pin_node_editor(_Context(_OtherSpace())), 0)

    # The session UI teardown is what puts the flag back on every end path.
    popped = _Space()
    pdt_overlay._pin_node_editor(_Context(popped))
    check("pinned while the session runs", popped.pin, True)
    pdt_overlay._pop_session_ui(_Context(popped))
    check("session teardown unpins", popped.pin, False)

    # ... and the breadcrumb the pick was started from is pushed back: the pick
    # collapses the path to its root (see _pin_node_editor), so the levels the
    # user was inside have to be re-appended.
    root = bpy.data.node_groups.new(name="PinRoot", type="GeometryNodeTree")
    inner = bpy.data.node_groups.new(name="PinInner", type="GeometryNodeTree")
    group_node = root.nodes.new("GeometryNodeGroup")
    group_node.name = "EnteredGroup"
    group_node.node_tree = inner

    class _PathItem:
        def __init__(self, tree):
            self.node_tree = tree

    class _Path:
        """Mirrors `SpaceNodeEditor.path` (iterable, with start/append)."""

        def __init__(self, trees):
            self.items = [_PathItem(tree) for tree in trees]
            self.calls = []

        def __iter__(self):
            return iter(self.items)

        def start(self, tree):
            self.calls.append(("start", tree))
            self.items = [_PathItem(tree)]

        def append(self, tree, node):
            self.calls.append(("append", tree, node))
            self.items.append(_PathItem(tree))

    class _PathSpace:
        type = "NODE_EDITOR"

        def __init__(self, pin, trees):
            self.pin = pin
            self.path = _Path(trees)

    nested = _PathSpace(False, [root, inner])
    check("breadcrumb captured", pdt_overlay._pin_node_editor(_Context(nested)), 1)
    check("two levels remembered", [t for t, _n in pdt_overlay._editor_state["chain"]], [root, inner])
    nested.path.items = [_PathItem(root)]  # what the pick's context sync leaves behind
    pdt_overlay._unpin_node_editors()
    check("the group level is pushed back", nested.path.calls, [("append", inner, group_node)])
    check("editor is inside the group again", len(nested.path.items), 2)
    check("pin released", nested.pin, False)

    # A deliberately different branch must not be yanked back.
    other = bpy.data.node_groups.new(name="PinOther", type="GeometryNodeTree")
    moved = _PathSpace(False, [root, inner])
    pdt_overlay._pin_node_editor(_Context(moved))
    moved.path.items = [_PathItem(other)]
    pdt_overlay._unpin_node_editors()
    check("a different branch is left alone", moved.path.calls, [])

    check(
        "begin_pick pins the editor",
        "_pin_node_editor" in inspect.getsource(pdt_session.begin_pick),
        True,
    )
    check(
        "session teardown unpins",
        "_unpin_node_editors" in inspect.getsource(pdt_overlay._pop_session_ui),
        True,
    )


def make_curve_object(name: str = "CurveOwner", count: int = 5):
    """A POLY curve with `count` control points at x == 0..count-1."""
    curve = bpy.data.curves.new(name=name + "Data", type="CURVE")
    curve.dimensions = "3D"
    spline = curve.splines.new("POLY")
    spline.points.add(count - 1)
    for index in range(count):
        spline.points[index].co = (float(index), 0.0, 0.0, 1.0)
    obj = bpy.data.objects.new(name, curve)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def make_point_cloud_object(name: str = "CloudOwner", count: int = 5):
    """A real Point Cloud object with `count` points.

    `Convert to Point Cloud` builds the cloud from the **mesh vertices**, so the
    source has to be a mesh that actually has that many loose vertices (feeding a
    GN "Points" node through the operator yields a 0-point cloud - measured).
    """
    mesh = bpy.data.meshes.new(name + "Source")
    source = bpy.data.objects.new(name + "Source", mesh)
    bpy.context.scene.collection.objects.link(source)
    bm = bmesh.new()
    for index in range(count):
        bm.verts.new((float(index), 0.0, 0.0))
    bm.to_mesh(mesh)
    bm.free()
    for other in list(bpy.context.view_layer.objects):
        if other is not None:
            other.select_set(False)
    source.select_set(True)
    bpy.context.view_layer.objects.active = source
    bpy.ops.object.convert(target="POINTCLOUD")
    converted = bpy.context.view_layer.objects.active
    converted.name = name
    return converted


def build_curve_scene(convert_to_mesh: bool):
    """Curve object; with `convert_to_mesh` the tree meshes it *above* the node.

    The element kind has to match what the node actually sees (L0 PDS-9): mesh vertices
    when the tree meshes the curve above it, curve control points when it does not.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    tree = bpy.data.node_groups.new(name="PDT Curve Test", type="GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_in = tree.nodes.new("NodeGroupInput")
    group_out = tree.nodes.new("NodeGroupOutput")
    selection = tree.nodes.new("GeometryNodePDTSelection")
    selection.inputs["Selection"].default_value = "*"
    selection.domain = "VERTEX" if convert_to_mesh else "CONTROL_POINT"

    upstream, upstream_name = group_in, "Geometry"
    if convert_to_mesh:
        convert = tree.nodes.new("GeometryNodeCurveToMesh")
        profile = tree.nodes.new("GeometryNodeCurvePrimitiveCircle")
        tree.links.new(group_in.outputs["Geometry"], convert.inputs["Curve"])
        tree.links.new(profile.outputs["Curve"], convert.inputs["Profile Curve"])
        upstream, upstream_name = convert, "Mesh"
    tree.links.new(upstream.outputs[upstream_name], selection.inputs["Geometry"])
    tree.links.new(selection.outputs["Geometry"], group_out.inputs["Geometry"])

    obj = make_curve_object()
    modifier = obj.modifiers.new(name="GeometryNodes", type="NODES")
    modifier.node_group = tree
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return tree, selection, obj


def build_point_cloud_scene(convert_to_vertices: bool):
    """Point cloud object; with `convert_to_vertices` the tree inserts Points to Vertices.

    The element kind follows what the node sees: `Vertex` after the conversion (a mesh),
    `Point` (a point cloud) without it.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    tree = bpy.data.node_groups.new(name="PDT Cloud Test", type="GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_in = tree.nodes.new("NodeGroupInput")
    group_out = tree.nodes.new("NodeGroupOutput")
    selection = tree.nodes.new("GeometryNodePDTSelection")
    selection.inputs["Selection"].default_value = "*"
    selection.domain = "VERTEX" if convert_to_vertices else "POINT"

    upstream, upstream_name = group_in, "Geometry"
    if convert_to_vertices:
        convert = tree.nodes.new("GeometryNodePointsToVertices")
        tree.links.new(group_in.outputs["Geometry"], convert.inputs["Points"])
        upstream, upstream_name = convert, "Mesh"
    tree.links.new(upstream.outputs[upstream_name], selection.inputs["Geometry"])
    tree.links.new(selection.outputs["Geometry"], group_out.inputs["Geometry"])

    obj = make_point_cloud_object()
    modifier = obj.modifiers.new(name="GeometryNodes", type="NODES")
    modifier.node_group = tree
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return tree, selection, obj


def temporary_leftovers() -> tuple[list[str], list[str]]:
    """(objects, node groups) with a `PDT_PICK_*` name - must be empty after a pick."""
    objects = sorted(o.name for o in bpy.data.objects if o.name.startswith(pdt_constants.TEMP_OBJECT_PREFIX))
    groups = sorted(
        g.name for g in bpy.data.node_groups if g.name.startswith(pdt_constants.TEMP_GROUP_PREFIX)
    )
    return objects, groups


def temporary_data_leftovers() -> list[str]:
    """Data-blocks with the pick prefix, whatever their type (mesh or curve)."""
    found = []
    for collection_name in ("meshes", "curves", "pointclouds"):
        collection = getattr(bpy.data, collection_name, None)
        if collection is None:
            continue
        found.extend(d.name for d in collection if d.name.startswith(pdt_constants.TEMP_MESH_PREFIX))
    return sorted(found)


def build_mixed_scene(with_point_cloud: bool = False):
    """Cube mesh **and** a 16-point POLY curve in one stream, then PDT Selection.

    This is the shape of the live scene (`test2.blend`): the node's geometry has
    both components, so the picker has to know which one to build the temporary
    object from (tools/pdt/docs/archive/07-曲线支持评估.md §13). With `with_point_cloud` a
    generated point cloud is joined in as a third component.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    tree = bpy.data.node_groups.new(name="PDT Mixed Test", type="GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_in = tree.nodes.new("NodeGroupInput")
    group_out = tree.nodes.new("NodeGroupOutput")
    selection = tree.nodes.new("GeometryNodePDTSelection")
    selection.inputs["Selection"].default_value = "*"
    # The node addresses curve control points, so this mixed stream defaults to a curve
    # pick; the mesh stays available for an *explicit* request (L0 PDS-9).
    selection.domain = "CONTROL_POINT"

    line = tree.nodes.new("GeometryNodeCurvePrimitiveLine")
    line.mode = "POINTS"
    line.inputs["End"].default_value = (0.0, 0.0, 15.0)
    resample = tree.nodes.new("GeometryNodeResampleCurve")
    resample.inputs["Mode"].default_value = "Count"
    resample.inputs["Count"].default_value = 16
    join = tree.nodes.new("GeometryNodeJoinGeometry")
    tree.links.new(group_in.outputs["Geometry"], join.inputs["Geometry"])
    tree.links.new(line.outputs["Curve"], resample.inputs["Curve"])
    tree.links.new(resample.outputs["Curve"], join.inputs["Geometry"])
    if with_point_cloud:
        points = tree.nodes.new("GeometryNodePoints")
        points.inputs["Count"].default_value = 6
        tree.links.new(points.outputs["Geometry"], join.inputs["Geometry"])
    tree.links.new(join.outputs["Geometry"], selection.inputs["Geometry"])
    tree.links.new(selection.outputs["Geometry"], group_out.inputs["Geometry"])

    mesh = bpy.data.meshes.new("mixed")
    obj = bpy.data.objects.new("MixedOwner", mesh)
    bpy.context.scene.collection.objects.link(obj)
    corners = [
        (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0), (1.0, 0.0, 1.0), (1.0, 1.0, 1.0), (0.0, 1.0, 1.0),
    ]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    mesh.from_pydata(corners, [], faces)
    mesh.update()
    modifier = obj.modifiers.new(name="GeometryNodes", type="NODES")
    modifier.node_group = tree
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return tree, selection, obj


def test_mixed_geometry_components() -> None:
    """M1 + M3: a node that sees a mesh *and* a curve, and the user chooses.

    The node applies the written string to every component it is evaluated on, so
    the component choice is a picker-side decision: which temporary object is built
    (`EDIT_MESH` vs `EDIT_CURVE`) and which indices the user sees.
    """
    print("== mixed geometry (mesh + curve): the component choice drives the pick ==")
    pdt_overlay._editor_state = None
    tree, selection, obj = build_mixed_scene()

    available = pdt_geometry.available_geometry_kinds(tree, selection)
    check("both components are reported", sorted(available), [("CURVE", 16), ("MESH", 8)])
    check("the scan leaves nothing behind", temporary_leftovers(), ([], []))
    check("the scan leaves no data-block behind", temporary_data_leftovers(), [])
    check(
        "the mixed stream offers both components",
        pdt_geometry.resolve_geometry_kind(selection, available, None, "CONTROL_POINT"),
        ("CURVE", "domain"),
    )
    check(
        "an explicit request for the other component is honoured",
        pdt_geometry.resolve_geometry_kind(selection, available, "MESH"),
        ("MESH", "explicit"),
    )
    check(
        "and the node's Domain takes no part in it",
        pdt_geometry.resolve_geometry_kind(selection, available),
        ("MESH", "default"),
    )
    unresolved, reason = pdt_geometry.resolve_geometry_kind(selection, available, "POINTCLOUD")
    check("an impossible request is reported", unresolved, None)
    check("and says what is there", "Mesh" in reason and "Curve" in reason, True)

    # With no kind chosen the *geometry* answers: the first pickable component in
    # KIND_PRIORITY is the mesh, whatever the node's property says.
    session = pdt_session.begin_pick(tree, selection)
    check("an automatic pick uses the first pickable component", session["geometry_kind"], "MESH")
    check("and labels itself automatic", session["domain_source"], "auto")
    pdt_session.cancel_pick()

    # --- choosing Control Point picks the curve -------------------------------
    session = pdt_session.begin_pick(tree, selection, domain="CONTROL_POINT")
    check("choosing Control Point uses the curve component", session["geometry_kind"], "CURVE")
    check("curve pick enters EDIT_CURVE", bpy.context.mode, "EDIT_CURVE")
    temp_object = bpy.data.objects[session["temp_object"]]
    check("the temporary object is a curve", temp_object.type, "CURVE")
    check("curve pick bakes 16 control points", pdt_selection.curve_point_count(temp_object.data), 16)
    check("recorded label", session["geometry_label"], "Curve (16 control points)")
    check(
        "the session lists every component",
        sorted((item["kind"], item["elements"]) for item in session["geometry_kinds"]),
        [("CURVE", 16), ("MESH", 8)],
    )
    pdt_selection._set_edit_curve_selection(temp_object.data, "POINT", {4, 5, 6})
    count, text, _note = pdt_session.finish_pick()
    check("wrote 3 control points", count, 3)
    check("parameter text", text, "4-6")
    check("parameter", selection.inputs["Selection"].default_value, "4-6")
    check("curve pick leaves nothing", temporary_leftovers(), ([], []))
    check("curve pick leaves no data-block", temporary_data_leftovers(), [])

    # --- an explicit component that contradicts the chosen kind is refused ----
    message = ""
    try:
        session = pdt_session.begin_pick(tree, selection, geometry_kind="MESH", domain="CONTROL_POINT")
    except RuntimeError as ex:
        message = str(ex)
    else:
        pdt_session.cancel_pick()
    check("an explicit mesh request under the Control Point kind is refused", bool(message), True)
    check("and says which kind was asked for", "Control Point" in message, True)

    # --- choosing Vertex reaches the mesh ------------------------------------
    session = pdt_session.begin_pick(tree, selection, geometry_kind="MESH", domain="VERTEX")
    check("explicit mesh pick under the Vertex kind", session["geometry_kind"], "MESH")
    check("mesh pick enters EDIT_MESH", bpy.context.mode, "EDIT_MESH")
    check(
        "mesh pick bakes the cube",
        len(bpy.data.objects[session["temp_object"]].data.vertices),
        8,
    )
    pdt_session.cancel_pick()
    check("cancelling the mesh pick leaves nothing", temporary_leftovers(), ([], []))
    check("and no data-block", temporary_data_leftovers(), [])

    # --- no pin and no per-node kind: nothing about the choice is stored ------
    for key in ("pdt_pick_domain", "pdt_pick_kind"):
        check(f"nothing writes '{key}' onto the node", key in selection, False)
    for node_domain, chosen, expected in (
        ("CONTROL_POINT", "VERTEX", "MESH"),
        ("VERTEX", "CONTROL_POINT", "CURVE"),
        ("POINT", "SPLINE", "CURVE"),
    ):
        selection.domain = node_domain
        session = pdt_session.begin_pick(tree, selection, domain=chosen)
        check(
            f"node Domain={node_domain} + chosen {chosen} bakes {expected}",
            session["geometry_kind"],
            expected,
        )
        pdt_session.cancel_pick()

    selection.domain = "CONTROL_POINT"
    kind, reason = pdt_geometry.resolve_geometry_kind(
        selection, pdt_geometry.available_geometry_kinds(tree, selection), "POINTCLOUD"
    )
    check("a kind this geometry lacks is still refused as a request", kind, None)
    check("and says which kind was missing", "Point Cloud" in reason, True)

    check("the source object is visible again", obj.hide_get(), False)
    check("still in object mode", bpy.context.mode, "OBJECT")


def test_non_mesh_source_objects() -> None:
    """Plan A: the pick bakes the geometry *at the node*, whatever the object is.

    A Curve/Point Cloud object cannot take a mesh data-block, which used to fail
    with `TypeError: Object.data expected a Curve type, not Mesh` **after** a
    successful bake - and left the temporary object in the file. Regression guard
    for tools/pdt/docs/archive/07-曲线支持评估.md §4.
    """
    print("== non-mesh source objects: type-agnostic bake, and no leftovers on failure ==")
    # The pin test above leaves a fake node-editor state behind; every scene switch
    # below frees its trees, and restoring that stale breadcrumb is not this test's
    # subject (it has its own test).
    pdt_overlay._editor_state = None

    # --- 1. Curve object whose tree converts to a mesh above the picked node ----
    tree, selection, obj = build_curve_scene(convert_to_mesh=True)
    session = pdt_session.begin_pick(tree, selection)
    check("curve object: entered edit mode", bpy.context.mode, "EDIT_MESH")
    temp_object = bpy.data.objects[session["temp_object"]]
    check("curve object: the temporary object is a MESH object", temp_object.type, "MESH")
    check("curve object: baked the curve-to-mesh result", len(temp_object.data.vertices) > 0, True)
    check(
        "curve object: no modifiers left on the temporary object",
        [m.name for m in temp_object.modifiers],
        [],
    )
    select_in_edit_mode(temp_object, {0, 1})
    count, text, _note = pdt_session.finish_pick()
    check("curve object: picked 2 elements", count, 2)
    check("curve object: parameter written", selection.inputs["Selection"].default_value, "0-1")
    check("curve object: back in object mode", bpy.context.mode, "OBJECT")
    check("curve object: no temporary objects left", temporary_leftovers()[0], [])
    check("curve object: no temporary groups left", temporary_leftovers()[1], [])
    check("curve object: the source curve is untouched", len(obj.data.splines[0].points), 5)
    check("curve object: the source object is visible again", obj.hide_get(), False)

    # --- 2. Curve object whose node still sees the curve: picked as a CURVE ----
    tree, selection, obj = build_curve_scene(convert_to_mesh=False)
    session = pdt_session.begin_pick(tree, selection)
    check("curve geometry: picked the curve component", session["geometry_kind"], "CURVE")
    check("curve geometry: entered EDIT_CURVE", bpy.context.mode, "EDIT_CURVE")
    temp_object = bpy.data.objects[session["temp_object"]]
    check("curve geometry: the temporary object is a CURVE object", temp_object.type, "CURVE")
    check("curve geometry: control points", pdt_selection.curve_point_count(temp_object.data), 5)
    check("curve geometry: spline type", temp_object.data.splines[0].type, "POLY")
    applied = pdt_selection._set_edit_curve_selection(temp_object.data, "POINT", {1, 2})
    check("curve geometry: flags applied", applied, 2)
    count, text, _note = pdt_session.finish_pick()
    check("curve geometry: picked 2 control points", count, 2)
    check("curve geometry: parameter written", selection.inputs["Selection"].default_value, "1-2")
    check("curve geometry: back in object mode", bpy.context.mode, "OBJECT")
    check("curve geometry: no temporary objects left", temporary_leftovers()[0], [])
    check("curve geometry: no temporary groups left", temporary_leftovers()[1], [])
    check("curve geometry: no temporary data-blocks left", temporary_data_leftovers(), [])
    check("curve geometry: the source curve is untouched", len(obj.data.splines[0].points), 5)
    check("curve geometry: the source object is visible again", obj.hide_get(), False)

    # --- 3. Point cloud object whose tree converts to vertices above the node ---
    tree, selection, obj = build_point_cloud_scene(convert_to_vertices=True)
    session = pdt_session.begin_pick(tree, selection)
    check("point cloud: entered edit mode", bpy.context.mode, "EDIT_MESH")
    temp_object = bpy.data.objects[session["temp_object"]]
    check("point cloud: the temporary object is a MESH object", temp_object.type, "MESH")
    check("point cloud: baked one vertex per point", len(temp_object.data.vertices), 5)
    select_in_edit_mode(temp_object, {2, 3})
    count, text, _note = pdt_session.finish_pick()
    check("point cloud: picked 2 points", count, 2)
    check("point cloud: parameter written", selection.inputs["Selection"].default_value, "2-3")
    check("point cloud: no temporary objects left", temporary_leftovers()[0], [])
    check("point cloud: no temporary groups left", temporary_leftovers()[1], [])

    # --- 4. Point cloud object whose node still sees the points: picked as one --
    tree, selection, obj = build_point_cloud_scene(convert_to_vertices=False)
    session = pdt_session.begin_pick(tree, selection)
    check("point cloud geometry: picked the point cloud", session["geometry_kind"], "POINTCLOUD")
    check("point cloud geometry: entered EDIT_POINTCLOUD", bpy.context.mode, "EDIT_POINTCLOUD")
    temp_object = bpy.data.objects[session["temp_object"]]
    check(
        "point cloud geometry: the temporary object is a POINTCLOUD object",
        temp_object.type,
        "POINTCLOUD",
    )
    check("point cloud geometry: points", pdt_selection.pointcloud_point_count(temp_object.data), 5)
    applied = pdt_selection._set_geometry_selection(temp_object.data, "POINTCLOUD", "POINT", {1, 4})
    check("point cloud geometry: points selected", applied, 2)
    count, text, _note = pdt_session.finish_pick()
    check("point cloud geometry: wrote 2 points", count, 2)
    check(
        "point cloud geometry: parameter written (step run for {1,4})",
        selection.inputs["Selection"].default_value,
        "1-4:3",
    )
    check("point cloud geometry: no temporary objects left", temporary_leftovers()[0], [])
    check("point cloud geometry: no temporary data-blocks left", temporary_data_leftovers(), [])
    check("point cloud geometry: the source is visible again", obj.hide_get(), False)

    # --- 5. The panel hint is cheap and only fires for unusual object data -----
    tree, selection, _obj = build_scene("*")
    check("hint: empty for a mesh object", pdt_hit_test.pick_geometry_hint(tree, selection), "")
    tree, selection, _obj = build_curve_scene(convert_to_mesh=False)
    check("hint: empty for a curve object (curves are pickable)", pdt_hit_test.pick_geometry_hint(tree, selection), "")
    tree, selection, _obj = build_point_cloud_scene(convert_to_vertices=False)
    check(
        "hint: empty for a point cloud object (point clouds are pickable)",
        pdt_hit_test.pick_geometry_hint(tree, selection),
        "",
    )


def test_point_cloud_picking() -> None:
    """Point cloud components: `EDIT_POINTCLOUD` and Blender's `.selection` attribute.

    Blender's point cloud edit mode has no point/edge/face sub-modes, and its
    selection state is the `.selection` attribute on the point domain - where a
    **missing** attribute means "everything is selected" (Blender's own
    `select_all(SELECT)` removes it as an optimization). Measured in
    qa/picker/pointcloud_probe.py.
    """
    print("== point cloud component: EDIT_POINTCLOUD and the .selection attribute ==")
    pdt_overlay._editor_state = None
    tree, selection, obj = build_point_cloud_scene(convert_to_vertices=False)

    available = pdt_geometry.available_geometry_kinds(tree, selection)
    check("the scan finds the point cloud", sorted(available), [("POINTCLOUD", 5)])

    session = pdt_session.begin_pick(tree, selection)
    temp_object = bpy.data.objects[session["temp_object"]]
    check("session kind", session["geometry_kind"], "POINTCLOUD")
    check("edit mode", bpy.context.mode, "EDIT_POINTCLOUD")
    # The scene's parameter is `*`, so begin_pick overwrites Blender's "missing means
    # all selected" state with an explicit all-true selection.
    check(
        "an all-covering parameter pre-selects every point",
        pdt_selection._pointcloud_selected_indices(temp_object.data),
        list(range(5)),
    )
    check(
        "the .selection attribute is a BOOLEAN on the point domain",
        (
            temp_object.data.attributes[pdt_constants.POINTCLOUD_SELECTION_ATTR].domain,
            temp_object.data.attributes[pdt_constants.POINTCLOUD_SELECTION_ATTR].data_type,
        ),
        ("POINT", "BOOLEAN"),
    )
    # Blender's own operators must agree with our read path, in edit mode.
    bpy.ops.pointcloud.select_all(action="INVERT")
    check(
        "pointcloud.select_all(INVERT) is visible to the picker",
        pdt_selection._pointcloud_selected_indices(temp_object.data),
        [],
    )
    bpy.ops.pointcloud.select_all(action="SELECT")
    check(
        "select_all(SELECT) removes the attribute, which still reads as 'all'",
        pdt_constants.POINTCLOUD_SELECTION_ATTR in temp_object.data.attributes,
        False,
    )
    check(
        "and the picker reads that as every point",
        pdt_selection._pointcloud_selected_indices(temp_object.data),
        list(range(5)),
    )
    pdt_session.cancel_pick()

    # Round trip: pick two points, then re-open and check the pre-selection.
    session = pdt_session.begin_pick(tree, selection)
    temp_object = bpy.data.objects[session["temp_object"]]
    pdt_selection._set_geometry_selection(temp_object.data, "POINTCLOUD", "POINT", {0, 2})
    count, text, _note = pdt_session.finish_pick()
    check("wrote 2 points", count, 2)
    check("parameter text (step run for {0,2})", text, "0-2:2")
    session = pdt_session.begin_pick(tree, selection)
    check(
        "the parameter re-selects the same points",
        pdt_selection._pointcloud_selected_indices(bpy.data.objects[session["temp_object"]].data),
        [0, 2],
    )
    pdt_session.cancel_pick()
    check("nothing left behind", temporary_leftovers(), ([], []))
    check("no temporary data-blocks left", temporary_data_leftovers(), [])
    check("the source object is visible again", obj.hide_get(), False)
    check("still in object mode", bpy.context.mode, "OBJECT")

    # --- all three components in one stream ---------------------------------
    tree, selection, obj = build_mixed_scene(with_point_cloud=True)
    available = pdt_geometry.available_geometry_kinds(tree, selection)
    check(
        "three components are reported",
        sorted(available),
        [("CURVE", 16), ("MESH", 8), ("POINTCLOUD", 6)],
    )
    check(
        "with no kind chosen the geometry answers (mesh first, not the node's Domain)",
        pdt_geometry.resolve_geometry_kind(selection, available),
        ("MESH", "default"),
    )
    session = pdt_session.begin_pick(tree, selection, geometry_kind="POINTCLOUD")
    temp_object = bpy.data.objects[session["temp_object"]]
    check("point cloud component: edit mode", bpy.context.mode, "EDIT_POINTCLOUD")
    check("point cloud component: temporary object type", temp_object.type, "POINTCLOUD")
    check("point cloud component: points", pdt_selection.pointcloud_point_count(temp_object.data), 6)
    pdt_selection._set_geometry_selection(temp_object.data, "POINTCLOUD", "POINT", {5})
    count, text, _note = pdt_session.finish_pick()
    check("point cloud component: wrote 1 point", count, 1)
    check("point cloud component: parameter", selection.inputs["Selection"].default_value, "5")
    check("point cloud component: nothing left behind", temporary_leftovers(), ([], []))
    check("point cloud component: no data-blocks left", temporary_data_leftovers(), [])


def build_two_spline_scene(spline_sizes=(3, 4)):
    """A curve object with two POLY splines; the node sees the curve component.

    Curve edit mode has no spline selection state, so on the Spline domain the picker
    goes the other way round: the user selects control points and the parameter stores
    the splines those points belong to (see `pdt_selection._curve_selected_splines`).
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    curve = bpy.data.curves.new("two_splines_data", type="CURVE")
    curve.dimensions = "3D"
    offset = 0
    for spline_index, count in enumerate(spline_sizes):
        spline = curve.splines.new("POLY")
        spline.points.add(count - 1)
        for index in range(count):
            spline.points[index].co = (float(offset + index), float(spline_index), 0.0, 1.0)
        offset += count
    obj = bpy.data.objects.new("TwoSplines", curve)
    bpy.context.scene.collection.objects.link(obj)

    tree = bpy.data.node_groups.new(name="PDT Spline Test", type="GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_in = tree.nodes.new("NodeGroupInput")
    group_out = tree.nodes.new("NodeGroupOutput")
    selection = tree.nodes.new("GeometryNodePDTSelection")
    selection.inputs["Selection"].default_value = "*"
    # This scene is a curve, and the node says which element kind its indices address
    # (L0 PDS-9): the Spline kind writes spline indices.
    if selection.bl_rna.properties.get("domain") is not None:
        selection.domain = "SPLINE"

    tree.links.new(group_in.outputs["Geometry"], selection.inputs["Geometry"])
    tree.links.new(selection.outputs["Geometry"], group_out.inputs["Geometry"])

    modifier = obj.modifiers.new(name="GeometryNodes", type="NODES")
    modifier.node_group = tree
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return tree, selection, obj


def test_curve_spline_domain() -> None:
    """Picking on the curve Spline domain: points in, spline indices out."""
    print("== curve Spline domain: select control points, write spline indices ==")
    pdt_overlay._editor_state = None
    tree, selection, obj = build_two_spline_scene()

    check("the node's Domain is the Spline kind (the picker does not read it)", selection.domain, "SPLINE")

    available = pdt_geometry.available_geometry_kinds(tree, selection)
    check("the component is the curve", sorted(available), [("CURVE", 7)])

    session = pdt_session.begin_pick(tree, selection, domain="SPLINE")
    check("session kind", session["geometry_kind"], "CURVE")
    check("session domain", session["domain"], "SPLINE")
    check("edit mode", bpy.context.mode, "EDIT_CURVE")
    temp_object = bpy.data.objects[session["temp_object"]]
    check("control points", pdt_selection.curve_point_count(temp_object.data), 7)
    check("splines", pdt_selection.curve_spline_count(temp_object.data), 2)
    check(
        "spline point ranges",
        pdt_selection.curve_spline_point_ranges(temp_object.data),
        [(0, 3), (3, 4)],
    )
    # `*` covers every spline (all points of both are selected by the pre-selection).
    check(
        "an all-covering parameter pre-selects every spline",
        pdt_selection._curve_selected_splines(temp_object.data),
        [0, 1],
    )
    check(
        "pre-selecting a spline selects all of its control points",
        pdt_selection._edit_curve_selection(temp_object.data, "POINT"),
        list(range(7)),
    )

    # The user selects one point of spline 1 -> the parameter stores spline 1.
    pdt_selection._set_edit_curve_selection(temp_object.data, "POINT", {4})
    check(
        "one selected point folds back to its spline",
        pdt_selection._curve_selected_splines(temp_object.data),
        [1],
    )
    count, text, _note = pdt_session.finish_pick()
    check("wrote 1 spline", count, 1)
    check("parameter text", text, "1")
    check("parameter", selection.inputs["Selection"].default_value, "1")
    check("back in object mode", bpy.context.mode, "OBJECT")
    check("nothing left behind", temporary_leftovers(), ([], []))
    check("no temporary data-blocks left", temporary_data_leftovers(), [])

    # Re-opening pre-selects that spline (all of its points) and round-trips.
    session = pdt_session.begin_pick(tree, selection, domain="SPLINE")
    temp_object = bpy.data.objects[session["temp_object"]]
    check(
        "the stored spline re-selects all of its control points",
        pdt_selection._edit_curve_selection(temp_object.data, "POINT"),
        [3, 4, 5, 6],
    )
    check(
        "and reads back as the same spline",
        pdt_selection._curve_selected_splines(temp_object.data),
        [1],
    )
    pdt_session.cancel_pick()

    # Splines selectable all at once, and a spline whose points are all selected is
    # still just that one spline.
    session = pdt_session.begin_pick(tree, selection, domain="SPLINE")
    temp_object = bpy.data.objects[session["temp_object"]]
    pdt_selection._set_geometry_selection(temp_object.data, "CURVE", "SPLINE", {0})
    check(
        "writing spline 0 selects exactly its points",
        pdt_selection._edit_curve_selection(temp_object.data, "POINT"),
        [0, 1, 2],
    )
    pdt_session.cancel_pick()

    # `Spline` is a *kind* the pie offers, and it names the curve component (L0 PDS-9).
    check(
        "and the picker accepts that combination",
        "SPLINE" in pdt_constants.KIND_DOMAINS["CURVE"],
        True,
    )
    session = pdt_session.begin_pick(tree, selection, domain="SPLINE")
    check(
        "choosing Spline picks the curve",
        (session["geometry_kind"], session["domain"]),
        ("CURVE", "SPLINE"),
    )
    pdt_session.cancel_pick()

    print("== a kind that does not exist on this geometry is refused ==")
    message = ""
    try:
        pdt_session.begin_pick(tree, selection, domain="EDGE")
    except RuntimeError as ex:
        message = str(ex)
    else:
        pdt_session.cancel_pick()
    check("Edge on a curve is refused", bool(message), True)
    check("and names what this geometry has", "Curve" in message, True)
    check("and says which kind was asked for", "Edge" in message, True)
    check("a refused pick leaves no session", bool(pdt_state.session), False)
    check("a refused pick leaves nothing behind", temporary_leftovers(), ([], []))
    check("and the source object is visible", obj.hide_get(), False)

    # The mesh-only kinds work on a mesh.
    tree, selection, _obj = build_scene("0-1")
    session = pdt_session.begin_pick(tree, selection, domain="FACE")
    check("Face on a mesh is accepted", session["domain"], "FACE")
    pdt_session.cancel_pick()


def test_domain_drives_component_choice() -> None:
    """The **chosen element kind** is what the pick follows - and nothing else decides.

    A mixed stream (mesh + curve + point cloud): each kind names exactly one component
    (L0 PDS-9), so the pie menu's choice switches what a pick bakes - and a kind this
    geometry cannot offer is refused. The node's own `Domain` property takes **no** part in
    that (L0 `明确不做`: 不读取被识别节点的 domain 属性).
    """
    print("== the element kind (the pie's choice) decides the component ==")
    pdt_overlay._editor_state = None
    tree, selection, _obj = build_mixed_scene(with_point_cloud=True)
    available = pdt_geometry.available_geometry_kinds(tree, selection)
    check(
        "three components are present",
        sorted(available),
        [("CURVE", 16), ("MESH", 8), ("POINTCLOUD", 6)],
    )
    for domain, expected_kind in (
        ("VERTEX", "MESH"),
        ("FACE", "MESH"),
        ("CONTROL_POINT", "CURVE"),
        ("SPLINE", "CURVE"),
        ("POINT", "POINTCLOUD"),
    ):
        resolved, source = pdt_geometry.resolve_geometry_kind(selection, available, None, domain)
        check(f"{domain} resolves to {expected_kind}", (resolved, source), (expected_kind, "domain"))

    # An explicit request only checks that the geometry has that component.
    check(
        "an explicit request for another component is honoured",
        pdt_geometry.resolve_geometry_kind(selection, available, "POINTCLOUD"),
        ("POINTCLOUD", "explicit"),
    )
    check(
        "an explicit request for a component this geometry lacks is reported",
        pdt_geometry.resolve_geometry_kind(selection, available, "INSTANCES")[0],
        None,
    )

    # End to end: the pie's kind drives the bake.
    session = pdt_session.begin_pick(tree, selection, domain="SPLINE")
    check("the pick follows the kind", session["geometry_kind"], "CURVE")
    check("and the session speaks it", session["domain"], "SPLINE")
    check("edit mode", bpy.context.mode, "EDIT_CURVE")
    temp_object = bpy.data.objects[session["temp_object"]]
    check("splines", pdt_selection.curve_spline_count(temp_object.data), 1)
    pdt_selection._set_geometry_selection(temp_object.data, "CURVE", "SPLINE", {0})
    check(
        "selecting the only spline selects all 16 control points",
        len(pdt_selection._edit_curve_selection(temp_object.data, "POINT")),
        16,
    )
    pdt_session.cancel_pick()
    check("nothing left behind", temporary_leftovers(), ([], []))
    check("no temporary data-blocks left", temporary_data_leftovers(), [])

    # A kind the geometry does not have is refused, not silently ignored.
    tree, selection, _obj = build_scene("0-1")   # mesh only
    message = ""
    try:
        pdt_session.begin_pick(tree, selection, domain="SPLINE")
    except RuntimeError as ex:
        message = str(ex)
    else:
        pdt_session.cancel_pick()
    check("Spline on a mesh-only scene is refused", bool(message), True)
    check("and names the kind that was asked for", "Spline" in message, True)
    check("and the component it would have to bake", "Curve" in message, True)

    # **The node's own `Domain` never decides anything** (L0 明确不做): with no kind chosen,
    # the *geometry* answers, whatever the node's property says.
    tree, selection, _obj = build_mixed_scene(with_point_cloud=True)
    selection.domain = "SPLINE"
    session = pdt_session.begin_pick(tree, selection)
    check("a node saying Spline does not steer an automatic pick", session["geometry_kind"], "MESH")
    check("the session speaks the component's own kind", session["domain"], "VERTEX")
    check("and says it was automatic", session["domain_source"], "auto")
    pdt_session.cancel_pick()
    check(
        "the resolver agrees (no node/Domain source at all)",
        pdt_geometry.resolve_geometry_kind(selection, pdt_geometry.available_geometry_kinds(tree, selection)),
        ("MESH", "default"),
    )
    selection.domain = "POINT"
    check(
        "and a kind this geometry lacks does not steer it either",
        pdt_geometry.resolve_geometry_kind(selection, pdt_geometry.available_geometry_kinds(tree, selection)),
        ("MESH", "default"),
    )
    # A geometry with a single component needs no kind at all - and still ignores the node.
    tree, selection, _obj = build_scene("0-1")
    selection.domain = "SPLINE"
    session = pdt_session.begin_pick(tree, selection)
    check("one component: the pick uses it", session["geometry_kind"], "MESH")
    check("and speaks Vertex", session["domain"], "VERTEX")
    pdt_session.cancel_pick()


def build_instance_scene(
    with_mesh: bool = False,
    domain: str = "INSTANCE",
    source: str = "GeometryNodeMeshCube",
    curve_source: bool = False,
):
    """A node whose geometry is **3 top level instances** (optionally plus a cube).

    This is the shape that used to be mistaken for an empty mesh: the evaluated
    geometry set still carries a 0-vertex mesh slot, so "first component that is not
    None" reported `MESH` and the pick opened an empty `EDIT_MESH` session
    (tools/pdt/docs/archive/08-实例支持评估.md §1).

    `domain` is the node's own `Domain` element kind - `INSTANCE` for an instance pick
    (the default here), `VERTEX` to reproduce a node that has not been switched over
    yet. `source` is the instanced geometry node; `curve_source` instances a 4-point
    POLY curve object instead (created here, *after* the scene reset), which is how a
    curve-instance source is built for the realized pick.

    Returns `(tree, node, object, curve_source_object_or_None)`.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    curve_object = None
    if curve_source:
        curve = bpy.data.curves.new("RealizedCurveSource", type="CURVE")
        curve.dimensions = "3D"
        spline = curve.splines.new("POLY")
        spline.points.add(3)
        for index in range(4):
            spline.points[index].co = (float(index), 0.0, 0.0, 1.0)
        curve_object = bpy.data.objects.new("RealizedCurveSource", curve)
        bpy.context.scene.collection.objects.link(curve_object)

    tree = bpy.data.node_groups.new(name="PDT Instance Test", type="GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    group_in = tree.nodes.new("NodeGroupInput")
    instance = tree.nodes.new("GeometryNodeInstanceOnPoints")
    selection = tree.nodes.new("GeometryNodePDTSelection")
    selection.inputs["Selection"].default_value = "0-1"
    selection.domain = domain
    store = tree.nodes.new("GeometryNodeStoreNamedAttribute")
    store.data_type = "BOOLEAN"
    store.domain = "INSTANCE"
    store.inputs["Name"].default_value = "group:picked"
    group_out = tree.nodes.new("NodeGroupOutput")

    tree.links.new(group_in.outputs["Geometry"], instance.inputs["Points"])
    if curve_object is not None:
        object_info = tree.nodes.new("GeometryNodeObjectInfo")
        object_info.inputs["Object"].default_value = curve_object
        tree.links.new(object_info.outputs["Geometry"], instance.inputs["Instance"])
    else:
        source_node = tree.nodes.new(source)
        tree.links.new(source_node.outputs[0], instance.inputs["Instance"])
    last = instance.outputs["Instances"]
    if with_mesh:
        other = tree.nodes.new("GeometryNodeMeshCone")
        join = tree.nodes.new("GeometryNodeJoinGeometry")
        tree.links.new(last, join.inputs["Geometry"])
        tree.links.new(other.outputs["Mesh"], join.inputs["Geometry"])
        last = join.outputs["Geometry"]
    tree.links.new(last, selection.inputs["Geometry"])
    tree.links.new(selection.outputs["Geometry"], store.inputs["Geometry"])
    tree.links.new(selection.outputs["Selection"], store.inputs["Value"])
    tree.links.new(store.outputs["Geometry"], group_out.inputs["Geometry"])

    mesh = bpy.data.meshes.new("instance_points")
    mesh.from_pydata([(0.0, 0.0, 0.0), (3.0, 0.0, 0.0), (0.0, 3.0, 0.0)], [], [])
    mesh.update()
    obj = bpy.data.objects.new("InstanceOwner", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new(name="GeometryNodes", type="NODES")
    modifier.node_group = tree
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.context.view_layer.update()
    return tree, selection, obj, curve_object


def build_empty_geometry_scene():
    """A node whose incoming geometry really is empty (everything deleted above it)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    tree = bpy.data.node_groups.new(name="PDT Empty Test", type="GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    group_in = tree.nodes.new("NodeGroupInput")
    delete = tree.nodes.new("GeometryNodeDeleteGeometry")
    delete.inputs["Selection"].default_value = True
    selection = tree.nodes.new("GeometryNodePDTSelection")
    group_out = tree.nodes.new("NodeGroupOutput")
    tree.links.new(group_in.outputs["Geometry"], delete.inputs["Geometry"])
    tree.links.new(delete.outputs["Geometry"], selection.inputs["Geometry"])
    tree.links.new(selection.outputs["Geometry"], group_out.inputs["Geometry"])

    mesh = bpy.data.meshes.new("gone")
    mesh.from_pydata([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)], [], [])
    mesh.update()
    obj = bpy.data.objects.new("EmptyOwner", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new(name="GeometryNodes", type="NODES")
    modifier.node_group = tree
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.context.view_layer.update()
    return tree, selection, obj


def build_upstream_instance_scene():
    """Instances *above* the picked modifier, realized again inside its own tree.

    The picker feeds the temporary tree's Group Input with a data-block, and an
    "instances" geometry set has none - so this case can only be refused, with a way
    out (tools/pdt/docs/archive/08-实例支持评估.md §11.4).
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    inner = bpy.data.node_groups.new(name="Instances Above", type="GeometryNodeTree")
    inner.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    inner.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    inner_in = inner.nodes.new("NodeGroupInput")
    inner_instance = inner.nodes.new("GeometryNodeInstanceOnPoints")
    inner_cube = inner.nodes.new("GeometryNodeMeshCube")
    inner_out = inner.nodes.new("NodeGroupOutput")
    inner.links.new(inner_in.outputs["Geometry"], inner_instance.inputs["Points"])
    inner.links.new(inner_cube.outputs["Mesh"], inner_instance.inputs["Instance"])
    inner.links.new(inner_instance.outputs["Instances"], inner_out.inputs["Geometry"])

    tree = bpy.data.node_groups.new(name="Realize Above", type="GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_in = tree.nodes.new("NodeGroupInput")
    realize = tree.nodes.new("GeometryNodeRealizeInstances")
    selection = tree.nodes.new("GeometryNodePDTSelection")
    group_out = tree.nodes.new("NodeGroupOutput")
    tree.links.new(group_in.outputs["Geometry"], realize.inputs["Geometry"])
    tree.links.new(realize.outputs["Geometry"], selection.inputs["Geometry"])
    tree.links.new(selection.outputs["Geometry"], group_out.inputs["Geometry"])

    mesh = bpy.data.meshes.new("upstream_points")
    mesh.from_pydata([(0.0, 0.0, 0.0), (3.0, 0.0, 0.0), (0.0, 3.0, 0.0)], [], [])
    mesh.update()
    obj = bpy.data.objects.new("UpstreamOwner", mesh)
    bpy.context.scene.collection.objects.link(obj)
    above = obj.modifiers.new(name="InstancesAbove", type="NODES")
    above.node_group = inner
    picked = obj.modifiers.new(name="GeometryNodes", type="NODES")
    picked.node_group = tree
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.context.view_layer.update()
    return tree, selection, obj


def evaluated_geometry_of(obj, context=None):
    """The evaluated GeometrySet of `obj` - keep the return value alive while reading."""
    context = context or bpy.context
    depsgraph = context.evaluated_depsgraph_get()
    return obj.evaluated_get(depsgraph).evaluated_geometry()


def test_instance_picking() -> None:
    """M0 + M2: instances are *named* and picked as points (one point per instance).

    Before M0 the same geometry reported `MESH (0 points)`, `begin_pick()` succeeded
    and the user got an empty `EDIT_MESH` session with nothing to click - and the
    addon's own `("instances", "INSTANCES")` probe was dead code (`GeometrySet` has no
    `.instances`; the real API is `instances_pointcloud()`).

    M2 bakes `instances_pointcloud()` into a point cloud object, so the pick happens in
    `EDIT_POINTCLOUD` and the **point index is the instance index**
    (tools/pdt/docs/archive/08-实例支持评估.md §4).
    """
    print("== instances: detected, named, and picked as points ==")
    pdt_overlay._editor_state = None
    tree, selection, obj, _curve = build_instance_scene()

    geometry = evaluated_geometry_of(obj)
    check("the evaluated set carries instances", len(geometry.instance_references()), 1)
    check("M0: the component is reported as instances", pdt_geometry._geometry_component_kind(geometry), "INSTANCES")
    check(
        "M0: and counted, not shown as a 0-vertex mesh",
        pdt_geometry.geometry_component_sizes(geometry),
        [("INSTANCES", 3)],
    )

    available = pdt_geometry.available_geometry_kinds(tree, selection)
    check(
        "the scan reports the instances",
        available,
        [("INSTANCES", 3)],
    )
    check(
        "the only component answers an automatic pick",
        pdt_geometry.resolve_geometry_kind(selection, available),
        ("INSTANCES", "only"),
    )
    check("the scan leaves nothing behind", temporary_leftovers(), ([], []))
    check("the scan leaves no data-block behind", temporary_data_leftovers(), [])
    check("the panel label says instances", pdt_geometry.geometry_kind_label("INSTANCES", 3), "Instances (3 instances)")
    check(
        "the only component is the one an automatic pick takes",
        pdt_geometry.resolve_geometry_kind(selection, available),
        ("INSTANCES", "only"),
    )
    check("M2: instances are pickable now", "INSTANCES" in pdt_constants.PICKABLE_KINDS, True)
    check("and they are a known kind (so a pin round-trips)", "INSTANCES" in pdt_constants.GEOMETRY_KINDS, True)
    check("and the domain exists in the tables", pdt_constants.KIND_DOMAINS["INSTANCES"], ("INSTANCE",))
    check("with a UI label", pdt_constants.DOMAIN_LABELS["INSTANCE"], "Instance")
    check(
        "the baked data-block is released as a point cloud",
        pdt_geometry.TEMP_DATA_COLLECTIONS["INSTANCES"],
        "pointclouds",
    )

    # --- the pick itself -----------------------------------------------------
    session = pdt_session.begin_pick(tree, selection)
    check("the pick uses the instances", session["geometry_kind"], "INSTANCES")
    check("and the instance domain", session["domain"], "INSTANCE")
    check("it enters EDIT_POINTCLOUD", bpy.context.mode, "EDIT_POINTCLOUD")
    temp_object = bpy.data.objects[session["temp_object"]]
    check("the temporary object is a point cloud", temp_object.type, "POINTCLOUD")
    check("one point per instance", pdt_selection.pointcloud_point_count(temp_object.data), 3)
    positions = [
        tuple(round(value, 4) for value in temp_object.data.attributes["position"].data[i].vector)
        for i in range(3)
    ]
    check(
        "the points sit on the instance origins, not on garbage",
        positions,
        [(0.0, 0.0, 0.0), (3.0, 0.0, 0.0), (0.0, 3.0, 0.0)],
    )
    check(
        "the instance attributes survive the bake",
        sorted(
            name
            for name in ("instance_transform", ".reference_index")
            if name in temp_object.data.attributes
        ),
        [".reference_index", "instance_transform"],
    )
    check(
        "the parameter's existing '0-1' pre-selects instances 0 and 1",
        pdt_selection.read_selected_indices(None, "INSTANCE"),
        [0, 1],
    )

    applied = pdt_selection._set_geometry_selection(temp_object.data, "INSTANCES", "INSTANCE", {0, 2})
    check("selected 2 instance points", applied, 2)
    count, text, _note = pdt_session.finish_pick()
    check("wrote 2 instances", count, 2)
    check("parameter text (step run for {0, 2})", text, "0-2:2")
    check("parameter", selection.inputs["Selection"].default_value, "0-2:2")
    check("back in object mode", bpy.context.mode, "OBJECT")
    check("nothing left behind", temporary_leftovers(), ([], []))
    check("no temporary data-blocks left", temporary_data_leftovers(), [])
    check("the source object is visible again", obj.hide_get(), False)

    # the node's own semantics now select exactly those instances
    geometry = evaluated_geometry_of(obj)
    cloud = geometry.instances_pointcloud()
    attribute = cloud.attributes["group:picked"]
    check(
        "the node selects exactly the picked instances",
        [i for i in range(len(attribute.data)) if attribute.data[i].value],
        [0, 2],
    )

    # continuing a pick pre-selects the stored value (point index == instance index)
    session = pdt_session.begin_pick(tree, selection, geometry_kind="INSTANCES")
    check("re-opening pre-selects the stored instances", pdt_selection.read_selected_indices(None, "INSTANCE"), [0, 2])
    check("re-opening recorded them as preselected", session["preselected"], 2)
    pdt_session.cancel_pick()
    check("cancelling leaves nothing behind", temporary_leftovers(), ([], []))
    check("and no data-block", temporary_data_leftovers(), [])

    # --- positive control: put the old rule back, the very same geometry is "MESH"
    original_kind = pdt_geometry._geometry_component_kind
    original_sizes = pdt_geometry.geometry_component_sizes

    def legacy_kind(set_):
        for attribute, kind in (
            ("mesh", "MESH"),
            ("curves", "CURVE"),
            ("pointcloud", "POINTCLOUD"),
            ("instances", "INSTANCES"),
        ):
            try:
                component = getattr(set_, attribute)
            except Exception:  # noqa: BLE001
                continue
            if component is not None:
                return kind
        return "EMPTY"

    def legacy_sizes(set_):
        found = []
        for kind, attribute in (("MESH", "mesh"), ("CURVE", "curves"), ("POINTCLOUD", "pointcloud")):
            component = getattr(set_, attribute, None)
            if component is None:
                continue
            found.append((kind, len(component.vertices if kind == "MESH" else component.points)))
        return found

    pdt_geometry._geometry_component_kind = legacy_kind
    pdt_geometry.geometry_component_sizes = legacy_sizes
    try:
        check("control: the old rule calls the same set MESH", pdt_geometry._geometry_component_kind(geometry), "MESH")
        check(
            "control: and would offer an empty mesh to pick",
            pdt_geometry.available_geometry_kinds(tree, selection),
            [("MESH", 0)],
        )
    finally:
        pdt_geometry._geometry_component_kind = original_kind
        pdt_geometry.geometry_component_sizes = original_sizes
    check("control removed: instances again", pdt_geometry.available_geometry_kinds(tree, selection), [("INSTANCES", 3)])

    # --- a Vertex pick on instances-only geometry is refused ------------------
    # Vertex elements are mesh ones, and this stream has no mesh: the pie's choice is refused
    # with what the geometry does have (PICK-3). The way out is the `Instance` slice.
    tree, selection, _obj, _curve = build_instance_scene(domain="VERTEX")
    message = ""
    try:
        pdt_session.begin_pick(tree, selection, domain="VERTEX")
    except RuntimeError as ex:
        message = str(ex)
    else:
        pdt_session.cancel_pick()
    check("a Vertex pick on instances-only geometry is refused", bool(message), True)
    check("and names the kind that cannot be addressed", "Vertex" in message, True)
    check("and lists what the geometry does have", "Instances" in message, True)
    check("a refused pick leaves nothing behind", temporary_leftovers(), ([], []))
    check("and no data-block", temporary_data_leftovers(), [])

    # ... while choosing **Instance** works on the very same node: the pie's choice is the only
    # thing that decides what is baked (L0 不做: 不读取节点 Domain). Whether the node reads the
    # written numbers as instances is the user's business (it can set its own Domain).
    session = pdt_session.begin_pick(tree, selection, domain="INSTANCE")
    check("choosing Instance picks the instances", session["geometry_kind"], "INSTANCES")
    check("and the session speaks the instance kind", session["domain"], "INSTANCE")
    pdt_session.cancel_pick()
    check("and the node's Domain is still whatever it was", selection.domain, "VERTEX")

    # --- a mixed stream picks the mesh unless Instance is chosen ---------------
    tree, selection, _obj, _curve = build_instance_scene(with_mesh=True, domain="VERTEX")
    available = pdt_geometry.available_geometry_kinds(tree, selection)
    check(
        "a mixed mesh + instances stream reports both",
        sorted(available),
        [("INSTANCES", 3), ("MESH", 33)],
    )
    check(
        "the automatic choice is the first pickable component (mesh)",
        pdt_geometry.resolve_geometry_kind(selection, available),
        ("MESH", "default"),
    )
    check(
        "an explicit request for the instances is honoured too",
        pdt_geometry.resolve_geometry_kind(selection, available, "INSTANCES"),
        ("INSTANCES", "explicit"),
    )
    session = pdt_session.begin_pick(tree, selection)
    check("mixed stream: the pick works as before", session["geometry_kind"], "MESH")
    check("mixed stream: 33 baked vertices", len(bpy.data.objects[session["temp_object"]].data.vertices), 33)
    pdt_session.cancel_pick()
    check("mixed stream: nothing left behind", temporary_leftovers(), ([], []))
    check("mixed stream: no data-block", temporary_data_leftovers(), [])

    # ... and choosing the Instance kind picks the instances instead (L0 PDS-9: the kind names
    # the component), still without the node's own Domain taking part.
    session = pdt_session.begin_pick(tree, selection, domain="INSTANCE")
    check("mixed stream + Instance domain picks instances", session["geometry_kind"], "INSTANCES")
    check("and enters EDIT_POINTCLOUD", bpy.context.mode, "EDIT_POINTCLOUD")
    temp_object = bpy.data.objects[session["temp_object"]]
    check("mixed stream: 3 instance points", pdt_selection.pointcloud_point_count(temp_object.data), 3)
    check(
        "mixed stream: the cone's 33 vertices are not in the point cloud",
        "position" in temp_object.data.attributes and pdt_selection.pointcloud_point_count(temp_object.data),
        3,
    )
    pdt_session.cancel_pick()
    check("mixed stream: nothing left behind", temporary_leftovers(), ([], []))
    check("mixed stream: no data-block", temporary_data_leftovers(), [])

    # --- instances *upstream* of the pick node cannot be fed to the temporary tree
    tree, selection, _obj = build_upstream_instance_scene()
    available = pdt_geometry.available_geometry_kinds(tree, selection)
    check("the scan cannot see through instances upstream", available, [])
    message = ""
    try:
        pdt_session.begin_pick(tree, selection)
    except RuntimeError as ex:
        message = str(ex)
    else:
        pdt_session.cancel_pick()
    check("the upstream case is refused", bool(message), True)
    check("and names the way out", "Realize Instances" in message, True)
    check("and points at the plan", "08-实例支持评估" in message, True)
    check("and not 'empty'", message.strip().endswith("is empty"), False)
    check("a refused pick leaves no session", bool(pdt_state.session), False)
    check("a refused pick leaves nothing behind", temporary_leftovers(), ([], []))
    check("and no data-block", temporary_data_leftovers(), [])

    # --- genuinely empty geometry is "empty", not a 0-vertex pick
    tree, selection, _obj = build_empty_geometry_scene()
    available = pdt_geometry.available_geometry_kinds(tree, selection)
    check("empty geometry reports no component", available, [])
    check(
        "and resolving says so",
        pdt_geometry.resolve_geometry_kind(selection, available)[1],
        "the geometry at this node is empty",
    )
    message = ""
    try:
        pdt_session.begin_pick(tree, selection)
    except RuntimeError as ex:
        message = str(ex)
    else:
        pdt_session.cancel_pick()
    check("an empty pick is refused", "empty" in message, True)
    check("an empty refusal leaves nothing behind", temporary_leftovers(), ([], []))
    check("and no data-block", temporary_data_leftovers(), [])


def test_realized_instance_picking() -> None:
    """M3: realize the instances, pick the real geometry, fold back to instances.

    The temporary tree gets `pdt_instance_index = Index + 1` stored on the instance
    domain and a `Realize Instances` node spliced in (the user's tree is a throwaway
    copy), so the realized geometry carries the instance number per element - and 0
    for elements that did *not* come from an instance
    (tools/pdt/docs/archive/08-实例支持评估.md §5).
    """
    print("== realized instance pick: pick the geometry, write instance numbers ==")
    pdt_overlay._editor_state = None
    check(
        "the three modes are the ones L0 names (PICK-3)",
        pdt_constants.INSTANCE_MODES,
        ("POINTS", "POINTS_REFERENCE", "REALIZED"),
    )
    check("realized is the third label", pdt_constants.INSTANCE_MODE_LABELS["REALIZED"], "Realized Instance")

    tree, selection, obj, _curve = build_instance_scene()
    # The mode is chosen in the pie menu now (PICK-1's second level), and nothing about it is
    # written onto the node (L0 明确不做): the stored value is only read for old files.
    check("a fresh node has no stored mode", pdt_instances.stored_instance_mode(selection), "")
    check("so the pick asks", pdt_constants.DEFAULT_INSTANCE_MODE, "POINTS_REFERENCE")
    pdt_instances.store_instance_mode(selection, "REALIZED")
    check("the mode round-trips", pdt_instances.stored_instance_mode(selection), "REALIZED")

    session = pdt_session.begin_pick(tree, selection, domain="INSTANCE", instance_mode="REALIZED")
    check("the session uses the realized mode", session["instance_mode"], "REALIZED")
    check("the parameter domain is still the instance domain", session["domain"], "INSTANCE")
    check("and it came from the pie", session["domain_source"], "instance")
    check("but the picked component is the realized mesh", session["pick_kind"], "MESH")
    check("with the element domain", session["pick_domain"], "VERTEX")
    check("edit mode", bpy.context.mode, "EDIT_MESH")
    temp_object = bpy.data.objects[session["temp_object"]]
    check("the temporary object is a mesh", temp_object.type, "MESH")
    check("3 cubes realized -> 24 vertices", len(temp_object.data.vertices), 24)
    check("the instance count is known", session["instance_total"], 3)
    check(
        "3 instances x 8 vertices preselected from '0-1'",
        pdt_selection.read_selected_indices(None, "VERTEX"),
        list(range(16)),
    )

    # select 8 vertices of the third cube -> instance 2
    applied = pdt_selection._set_geometry_selection(temp_object.data, "MESH", "VERTEX", set(range(16, 24)))
    check("selected 8 vertices", applied, 8)
    check("the fold reads instance numbers", pdt_instances.current_session_indices(), [2])
    count, text, _note = pdt_session.finish_pick()
    check("wrote 1 instance", count, 1)
    check("parameter", text, "2")
    check("back in object mode", bpy.context.mode, "OBJECT")
    check("nothing left behind", temporary_leftovers(), ([], []))
    check("no temporary data-blocks left", temporary_data_leftovers(), [])

    geometry = evaluated_geometry_of(obj)
    cloud = geometry.instances_pointcloud()
    attribute = cloud.attributes["group:picked"]
    check(
        "the node selects exactly that instance",
        [i for i in range(len(attribute.data)) if attribute.data[i].value],
        [2],
    )

    # --- the element kind a realized pick clicks comes from the realized geometry (PICK-3):
    # mesh points here. A stale pin from an older session no longer switches the click kind
    # (there is no element-kind pin in the pie flow), it is refused with what the pick *can*
    # address - which is the instances.
    # Choosing a mesh kind while the geometry only has instances is refused: the kind has to be
    # the component that gets baked (PICK-3).
    message = ""
    try:
        pdt_session.begin_pick(tree, selection, domain="FACE")
    except RuntimeError as ex:
        message = str(ex)
    else:
        pdt_session.cancel_pick()
    check("choosing Face on instances-only geometry is refused", "cannot pick Face elements" in message, True)
    check("and the refusal names the component that is there", "Instances" in message, True)

    session = pdt_session.begin_pick(tree, selection, domain="INSTANCE", instance_mode="REALIZED")
    check("a realized pick edits the realized mesh", session["pick_kind"], "MESH")
    check("with the mesh point kind", session["pick_domain"], "VERTEX")
    check("and the mesh sub-mode", tuple(bpy.context.tool_settings.mesh_select_mode), (True, False, False))
    temp_object = bpy.data.objects[session["temp_object"]]
    check("6 faces per cube", len(temp_object.data.polygons), 18)
    check(
        "the stored '2' pre-selects the third cube's 8 vertices",
        len(pdt_selection.read_selected_indices(None, "VERTEX")),
        8,
    )
    applied = pdt_selection._set_geometry_selection(temp_object.data, "MESH", "FACE", {12, 13})
    check("selected 2 faces of the third cube", applied, 2)
    face_vertices = sorted(
        {vertex for index in (12, 13) for vertex in temp_object.data.polygons[index].vertices}
    )
    check("selected faces imply their vertices", pdt_selection.read_selected_indices(None, "VERTEX"), face_vertices)
    check("and the fold reads the instance number", pdt_instances.current_session_indices(), [2])
    count, text, _note = pdt_session.finish_pick()
    check("wrote the instance number", text, "2")

    # --- the sentinel: elements that are not part of an instance are dropped
    tree, selection, _obj, _curve = build_instance_scene(with_mesh=True)
    session = pdt_session.begin_pick(tree, selection, domain="INSTANCE", instance_mode="REALIZED")
    check("mixed geometry realizes into one mesh", session["pick_kind"], "MESH")
    temp_object = bpy.data.objects[session["temp_object"]]
    values = pdt_instances._realized_element_instance_values(temp_object.data, pdt_state.session)
    check("the cone's vertices carry the sentinel", values.count(0) > 0, True)
    check("and the instance vertices do not", values.count(0) < len(values), True)
    cone_vertices = {i for i, value in enumerate(values) if value == 0}
    applied = pdt_selection._set_geometry_selection(temp_object.data, "MESH", "POINT", cone_vertices)
    check("selecting only the cone's vertices", pdt_instances.current_session_indices(), [])
    count, text, _note = pdt_session.finish_pick()
    check("writes nothing for non-instance geometry", text, "")
    check("mixed realized pick leaves nothing behind", temporary_leftovers(), ([], []))

    # --- curve instances: the attribute lands on the spline domain
    # The source has to be built *after* the scene reset inside the builder, so the
    # builder makes it when asked (`curve_source=True`).
    tree, selection, _obj, curve_object = build_instance_scene(curve_source=True)
    session = pdt_session.begin_pick(tree, selection, domain="INSTANCE", instance_mode="REALIZED")
    check("curve instances pick the curve component", session["pick_kind"], "CURVE")
    check("per-spline instance values are captured", session["spline_instances"], [1, 2, 3])
    check("the click kind is the curve's own", session["pick_domain"], "CONTROL_POINT")
    check("edit mode", bpy.context.mode, "EDIT_CURVE")
    temp_object = bpy.data.objects[session["temp_object"]]
    check("3 instances x 4 control points", pdt_selection.curve_point_count(temp_object.data), 12)
    pdt_selection._set_geometry_selection(temp_object.data, "CURVE", "CONTROL_POINT", {8, 9, 10, 11})
    check("control points fold to instance 2", pdt_instances.current_session_indices(), [2])
    count, text, _note = pdt_session.finish_pick()
    check("the parameter now holds the instance number", text, "2")

    # Choosing a curve mesh-kind on curve instances is refused for the same reason: the kind
    # has to be the component that gets baked.
    message = ""
    try:
        pdt_session.begin_pick(tree, selection, domain="FACE")
    except RuntimeError as ex:
        message = str(ex)
    else:
        pdt_session.cancel_pick()
    check("choosing Face on curve instances is refused",
          "cannot pick Face elements" in message, True)

    # The spline *fold* itself is still exercised through the element kind the realized curve
    # offers: selecting a spline's control points writes that spline's instance number, and the
    # session's own pick domain is the control points.
    session = pdt_session.begin_pick(tree, selection, domain="INSTANCE", instance_mode="REALIZED")
    check("the curve realized pick edits control points", session["pick_domain"], "CONTROL_POINT")
    temp_object = bpy.data.objects[session["temp_object"]]
    check("3 splines", pdt_selection.curve_spline_count(temp_object.data), 3)
    check(
        "the stored '2' pre-selects the third spline's control points",
        pdt_selection.read_selected_indices(None, "CONTROL_POINT"),
        [8, 9, 10, 11],
    )
    count, text, _note = pdt_session.finish_pick()
    check("spline folds back to the instance number", text, "2")

    # --- the size gate
    original_limit = pdt_constants.INSTANCE_REALIZE_MAX_ELEMENTS
    pdt_constants.INSTANCE_REALIZE_MAX_ELEMENTS = 10
    tree, selection, _obj, _curve = build_instance_scene()
    message = ""
    try:
        pdt_session.begin_pick(tree, selection, domain="INSTANCE", instance_mode="REALIZED")
    except RuntimeError as ex:
        message = str(ex)
    else:
        pdt_session.cancel_pick()
    pdt_constants.INSTANCE_REALIZE_MAX_ELEMENTS = original_limit
    check("an over-budget realize is refused", bool(message), True)
    check("and names the limit", "limit" in message, True)
    check("and suggests the point pick", "Points" in message, True)
    check("a refused realized pick leaves nothing behind", temporary_leftovers(), ([], []))
    check("and no data-block", temporary_data_leftovers(), [])


def test_instance_reference_geometry() -> None:
    """PICK-3: the reference is part of the *mode*, not a separate switch.

    An instance pick bakes points at the instance origins, so without the source there is no
    visual context at all - that is what `Point Cloud with Reference` says. `Point Cloud` asks
    for the origins alone, and `Realized Instance` shows the real geometry, so both of the
    other two modes keep it (tools/pdt/docs/archive/08-实例支持评估.md §13).
    """
    print("== instance modes: which one keeps the source object visible ==")
    pdt_overlay._editor_state = None
    check(
        "the mapping is exactly PICK-3's",
        {mode: pdt_instances.reference_for_mode(mode) for mode in pdt_constants.INSTANCE_MODES},
        {"POINTS": False, "POINTS_REFERENCE": True, "REALIZED": True},
    )
    check("and a fresh node defaults to the reference mode", pdt_instances.reference_enabled(None), True)

    tree, selection, obj, _curve = build_instance_scene()
    check("a fresh node has no stored mode", pdt_instances.stored_instance_mode(selection), "")

    session = pdt_session.begin_pick(tree, selection, domain="INSTANCE", instance_mode="POINTS_REFERENCE")
    check("the session records it", session["src_reference"], True)
    check("the source object stays visible during the pick", obj.hide_get(), False)
    pdt_session.cancel_pick()
    check("and is still visible afterwards", obj.hide_get(), False)
    check("the source is active again", bpy.context.view_layer.objects.active.name, obj.name)

    # --- the plain point mode hides it, like every other kind's bake ---
    session = pdt_session.begin_pick(tree, selection, domain="INSTANCE", instance_mode="POINTS")
    check("the session records the choice", session["src_reference"], False)
    check("the source object is hidden during the pick", obj.hide_get(), True)
    pdt_session.cancel_pick()
    check("and visible again after cancelling", obj.hide_get(), False)

    # --- the realized mode shows the realized geometry, so the source stays ---
    session = pdt_session.begin_pick(tree, selection, domain="INSTANCE", instance_mode="REALIZED")
    check("a realized pick keeps the reference too", session["src_reference"], True)
    pdt_session.cancel_pick()

    # --- other kinds are unaffected (their bake *is* the geometry) ---
    tree, selection, obj = build_scene("0-1")
    check("a mesh node reports the reference as on", pdt_instances.reference_enabled(selection), True)
    session = pdt_session.begin_pick(tree, selection)
    check("but the mesh session does not use it", session["src_reference"], False)
    check("so the source is hidden as before", obj.hide_get(), True)
    pdt_session.cancel_pick()
    check("and visible again", obj.hide_get(), False)

    # --- there is no reference toggle operator any more: the mode names it ---
    check(
        "the old reference switch is gone",
        hasattr(bpy.types, "PDT_OT_selection_set_reference"),
        False,
    )


def test_element_kind_menu_offers_all_seven() -> None:
    """The pie menu offers the same seven kinds as the node's `Domain` - and no node is read.

    Even on a node that has no `Domain` property at all (an older node in a saved file, or any
    other tree the picker is pointed at) the pick has to be able to address every element kind:
    the pie's choice is what carries it, it is not stored anywhere, and it never rewrites the
    node (L0 明确不做: 不读取被识别节点的 domain 属性).
    """
    print("== the Element Kind menu: seven kinds, chosen per pick ==")
    pdt_overlay._editor_state = None
    tree, selection, _obj = build_mixed_scene(with_point_cloud=True)
    available = pdt_geometry.available_geometry_kinds(tree, selection)

    check("the menu domain list is the node's seven kinds", pdt_constants.DOMAINS, (
        "VERTEX", "EDGE", "FACE", "CONTROL_POINT", "SPLINE", "POINT", "INSTANCE",
    ))
    check(
        "every kind names a component",
        sorted({pdt_constants.DOMAIN_KINDS[d] for d in pdt_constants.DOMAINS}),
        ["CURVE", "INSTANCES", "MESH", "POINTCLOUD"],
    )

    # Each kind steers its own component (the geometry has mesh + curve + point cloud).
    for domain, expected_kind in (
        ("VERTEX", "MESH"),
        ("EDGE", "MESH"),
        ("FACE", "MESH"),
        ("CONTROL_POINT", "CURVE"),
        ("SPLINE", "CURVE"),
        ("POINT", "POINTCLOUD"),
    ):
        check(
            f"choosing {domain} resolves to {expected_kind}",
            pdt_geometry.resolve_geometry_kind(selection, available, None, domain),
            (expected_kind, "domain"),
        )

    # And the choice drives a pick end to end, without touching the node's Domain.
    node_domain_before = selection.domain
    session = pdt_session.begin_pick(tree, selection, domain="POINT")
    check("the chosen kind drives the bake", session["geometry_kind"], "POINTCLOUD")
    check("the session picks on that kind", session["domain"], "POINT")
    check("and the node's own Domain is untouched", selection.domain, node_domain_before)
    pdt_session.cancel_pick()
    # The seven kinds work on a node that has no `Domain` property at all (a group node).
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sub = bpy.data.node_groups.new(name="SevenSub", type="GeometryNodeTree")
    sub.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    sub.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    # A group node's string parameter is its interface's string input; without one there is
    # nothing for a pick to write into.
    sub.interface.new_socket(name="Selection", in_out="INPUT", socket_type="NodeSocketString")
    sub_in = sub.nodes.new("NodeGroupInput")
    sub_out = sub.nodes.new("NodeGroupOutput")
    sub.links.new(sub_in.outputs["Geometry"], sub_out.inputs["Geometry"])
    parent = bpy.data.node_groups.new(name="SevenParent", type="GeometryNodeTree")
    parent.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    parent.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    parent_in = parent.nodes.new("NodeGroupInput")
    group_node = parent.nodes.new("GeometryNodeGroup")
    group_node.node_tree = sub
    parent_out = parent.nodes.new("NodeGroupOutput")
    parent.links.new(parent_in.outputs["Geometry"], group_node.inputs["Geometry"])
    parent.links.new(group_node.outputs["Geometry"], parent_out.inputs["Geometry"])
    mesh = bpy.data.meshes.new("seven_cube")
    mesh.from_pydata([(x, y, z) for x in (0.0, 1.0) for y in (0.0, 1.0) for z in (0.0, 1.0)], [], [])
    mesh.update()
    obj = bpy.data.objects.new("SevenOwner", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.modifiers.new(name="GeometryNodes", type="NODES").node_group = parent
    bpy.context.view_layer.objects.active = obj
    check(
        "the group node has no `Domain` property at all",
        "domain" in group_node.bl_rna.properties.keys(),
        False,
    )
    check(
        "its geometry is reachable",
        sorted(pdt_geometry.available_geometry_kinds(parent, group_node)),
        [("MESH", 8)],
    )
    session = pdt_session.begin_pick(parent, group_node, domain="VERTEX")
    check("a node without the property still picks", session["geometry_kind"], "MESH")
    check("and the session speaks the chosen kind", session["domain"], "VERTEX")
    pdt_session.cancel_pick()

    # L0 `明确不做`: 不读取被识别节点的 domain 属性. There is no reader for it - and no per-node
    # place to remember a choice either (that was the `pdt_pick_target` ID property): the names
    # and the store key may not appear in the package at all. The pass below covers literals,
    # comments and docstrings alike.
    module_source = addon_sources()
    for forbidden in (
        "node_socket_domain",
        "node_property_domain",
        "picked_domain",
        "stored_domain_identifier",
        "store_domain_identifier",
        "stored_geometry_kind",
        "node_geometry_kind",
        "pdt_pick_domain",
        "pdt_pick_kind",
        "pdt_pick_target",
        "TARGET_STORE_KEY",
        "stored_target_identifier",
        "store_target_identifier",
    ):
        check(f"the picker never mentions '{forbidden}'", forbidden in module_source, False)


class _FakeWindowManager:
    """`operator_properties_last()` for the trigger test (see `pick_hit_test`)."""

    def __init__(self, properties):
        self._properties = properties
        self.asked = []

    def operator_properties_last(self, idname):
        self.asked.append(idname)
        return self._properties


class _FakeQuery:
    """Stands in for `bpy.ops.pdt.node_under_cursor` (it needs a real node editor)."""

    def __init__(self, properties=None, raises=None, present=True):
        self.properties = properties
        self.raises = raises
        self.present = present
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.raises is not None:
            raise self.raises
        return {"FINISHED"}


class _FakeOps:
    def __init__(self, query=None):
        self.pdt = type("_Pdt", (), {})()
        if query is not None and query.present:
            self.pdt.node_under_cursor = query
        elif query is not None and not query.present:
            delattr(self.pdt, "node_under_cursor") if hasattr(self.pdt, "node_under_cursor") else None


class _FakeContext:
    def __init__(self, window_manager):
        self.window_manager = window_manager


class _FakeProperties:
    def __init__(self, **values):
        for field in pdt_hit_test.HIT_FIELDS:
            default = False if field in pdt_hit_test.HIT_BOOLEAN_FIELDS else ""
            setattr(self, field, values.get(field, default))


def _hit(**values):
    """A hit dict the way `pick_hit_test` returns it, for the refusal tests.

    Starts from a **good** hit (a string parameter hit on its *row*, on a node with a geometry
    input), so each case only has to override the field it is about.
    """
    result = {
        "state": "ok",
        "found": True,
        "is_string": True,
        "is_input": True,
        "has_geometry_input": True,
        "hit_region": "row",
    }
    for field in pdt_hit_test.HIT_FIELDS:
        result.setdefault(field, "")
    result.update(values)
    return result


def test_trigger_hit_test() -> None:
    """The parameter-level trigger (PICK-1): the query call, and every refusal's prompt."""
    print("== trigger: the editor's socket/row hit test, and the refusals around it ==")

    # The query operator itself has to be installed *and* publish the fields the trigger reads.
    # This is also what catches a wrong `_OT_` prefix in the C++: `bpy.ops.<module>.<name>` is
    # derived from the idname, so `NODE_OT_…` would silently answer `bpy.ops.node.…` and every
    # pick would refuse with "this Blender has no 'pdt.node_under_cursor' operator".
    check("the C++ query operator is registered", hasattr(bpy.ops.pdt, "node_under_cursor"), True)
    published = {
        prop.identifier for prop in bpy.ops.pdt.node_under_cursor.get_rna_type().properties
    }
    check("and publishes every field the trigger reads", set(pdt_hit_test.HIT_FIELDS) <= published, True)
    check(
        "and takes the cursor coordinates",
        {"region_x", "region_y"} <= published,
        True,
    )

    # A hit on a string parameter (its name/value field, or its socket) starts a pick.
    good = _FakeQuery(_FakeProperties(
        found=True,
        node_name="PDT Selection",
        node_idname="GeometryNodePDTSelection",
        socket_identifier="Selection",
        socket_name="Selection",
        socket_idname="NodeSocketString",
        is_string=True,
        is_input=True,
        has_geometry_input=True,
        hit_region="row",
    ))
    window_manager = _FakeWindowManager(good.properties)
    hit = pdt_hit_test.pick_hit_test(_FakeContext(window_manager), 120, 240, ops=_FakeOps(good))
    check("the query ran", hit["state"], "ok")
    check("and reports the socket", (hit["node_name"], hit["socket_identifier"], hit["is_string"]),
          ("PDT Selection", "Selection", True))
    check("with the node's geometry input", hit["has_geometry_input"], True)
    check("and how the cursor hit it", (hit["is_input"], hit["hit_region"]), (True, "row"))
    check("the event's coordinates are passed through", good.calls, [(("EXEC_DEFAULT",),
          {"region_x": 120, "region_y": 240})])
    check("and the result comes from the operator's own properties",
          window_manager.asked, [pdt_constants.NODE_UNDER_CURSOR_IDNAME])
    check("a good hit needs no refusal", pdt_hit_test.hit_refusal_reason(hit), "")

    # Without coordinates (the search-menu entry) the query is asked for its fallback position.
    good.calls.clear()
    pdt_hit_test.pick_hit_test(_FakeContext(window_manager), ops=_FakeOps(good))
    check("no coordinates means no coordinate arguments", good.calls, [(("EXEC_DEFAULT",), {})])

    # A build without the C++ operator has to say so instead of guessing.
    missing = pdt_hit_test.pick_hit_test(_FakeContext(window_manager), ops=_FakeOps(_FakeQuery(present=False)))
    check("a build without the query refuses", missing["state"], "no_operator")
    check("and explains why", "pdt.node_under_cursor" in pdt_hit_test.hit_refusal_reason(missing), True)

    # No node editor under the cursor: Blender's poll raises RuntimeError.
    rejected = _FakeQuery(raises=RuntimeError("Operator bpy.ops.pdt.node_under_cursor.poll() failed"))
    no_editor = pdt_hit_test.pick_hit_test(_FakeContext(window_manager), ops=_FakeOps(rejected))
    check("a failed poll is 'no editor'", no_editor["state"], "no_editor")
    check("with a prompt to hover one", "hover" in pdt_hit_test.hit_refusal_reason(no_editor), True)

    # An older build of the operator that takes no coordinates.
    strict = _FakeQuery(raises=TypeError("unexpected keyword argument 'region_x'"))
    check(
        "a query without coordinates is handled too",
        pdt_hit_test.pick_hit_test(_FakeContext(window_manager), 5, 5, ops=_FakeOps(strict))["state"],
        "no_editor",
    )

    # Nothing under the cursor / the wrong socket / a node without a geometry input.
    check(
        "no parameter under the cursor",
        pdt_hit_test.hit_refusal_reason(_hit(found=False)),
        "no string parameter under the cursor: put the cursor on the parameter's socket, "
        "name or input field, then press the shortcut",
    )
    wrong = pdt_hit_test.hit_refusal_reason(
        _hit(is_string=False, socket_name="Geometry", socket_idname="NodeSocketGeometry")
    )
    check("a non-string parameter names itself", ("Geometry" in wrong and "NodeSocketGeometry" in wrong), True)
    output = pdt_hit_test.hit_refusal_reason(
        _hit(is_input=False, socket_name="Geometry", socket_idname="NodeSocketGeometry")
    )
    check(
        "an output socket is called out as one",
        ("output" in output and "Geometry" in output),
        True,
    )
    geometryless = pdt_hit_test.hit_refusal_reason(_hit(node_name="PDT Selection", has_geometry_input=False))
    check(
        "a node without a geometry input is refused (PICK-1)",
        ("PDT Selection" in geometryless and "no geometry input" in geometryless),
        True,
    )

    # The hit names the node, and the node comes from the tree the editor shows.
    tree, selection, _obj = build_scene("")
    check("the hit resolves to that node", pdt_hit_test.node_from_hit(tree, _hit(node_name=selection.name)), selection)
    check("an unknown name resolves to nothing", pdt_hit_test.node_from_hit(tree, _hit(node_name="nope")), None)
    check("and so does no tree at all", pdt_hit_test.node_from_hit(None, _hit(node_name=selection.name)), None)


def test_pie_menu_entries() -> None:
    """The two pie menus and what a choice does (PICK-1: Domain, then Instance Mode)."""
    print("== pie menus: seven element kinds, then three instance modes ==")
    pdt_overlay._editor_state = None
    tree, selection, _obj = build_mixed_scene(with_point_cloud=True)
    available = pdt_geometry.available_geometry_kinds(tree, selection)

    entries = pdt_menus.domain_pie_entries(selection, tree)
    check("one slice per element kind", [entry["identifier"] for entry in entries], list(pdt_constants.DOMAINS))
    check(
        "labelled like the node's Domain",
        [entry["label"] for entry in entries],
        [pdt_constants.DOMAIN_LABELS[domain] for domain in pdt_constants.DOMAINS],
    )
    present = {entry["identifier"] for entry in entries if entry["present"]}
    check(
        "a slice is marked 'present' exactly when the geometry has that component",
        present,
        {domain for domain in pdt_constants.DOMAINS if pdt_constants.DOMAIN_KINDS[domain] in [k for k, _n in available]},
    )
    check(
        "the labels stay short (the radial layout has to fit them)",
        max(len(entry["label"]) for entry in entries) <= 14,
        True,
    )
    check(
        "and the description of an absent kind lists what the geometry does have",
        all(
            "it has:" in entry["description"]
            for entry in entries
            if not entry["present"]
        ),
        True,
    )
    check(
        "the slices are in `DOMAINS` order, which is the pie's slot order",
        [entry["identifier"] for entry in entries][:3],
        ["VERTEX", "EDGE", "FACE"],
    )

    # The slot plan: one pie item per element kind, in `DOMAINS` order, so a kind the geometry
    # does not have cannot slide the later kinds into its slot. A radial menu counts the items
    # it can display (`LayoutRadial::resolve_impl()`), so a missing kind has to be *represented*
    # (by a SPACE separator: paints nothing, cannot be clicked) rather than dropped.
    slots = pdt_menus.domain_pie_slots(entries)
    check(
        "there is exactly one pie item per element kind (missing ones included)",
        [slot["identifier"] for slot in slots],
        list(pdt_constants.DOMAINS),
    )
    check(
        "each kind is a slice exactly when the geometry has it",
        [slot["kind"] for slot in slots],
        ["slice" if entry["present"] else "placeholder" for entry in entries],
    )
    check(
        "every kind that is there stays in `DOMAINS` order",
        [slot["identifier"] for slot in slots if slot["kind"] == "slice"],
        [domain for domain in pdt_constants.DOMAINS if domain in present],
    )
    check(
        "a placeholder carries no icon and no text of its own",
        all(
            "icon" not in slot and slot["label"] == ""
            for slot in slots
            if slot["kind"] == "placeholder"
        ),
        True,
    )
    check(
        "every slice carries its icon, and only a slice has one",
        [slot.get("icon") for slot in slots if slot["kind"] == "slice"],
        [pdt_constants.DOMAIN_ICONS[slot["identifier"]] for slot in slots if slot["kind"] == "slice"],
    )
    check(
        "a one-kind list still reserves that one slot (no sliding)",
        pdt_menus.domain_pie_slots(
            [{"identifier": "INSTANCE", "label": "Instance", "present": True, "icon": "MOD_INSTANCE"}]
        ),
        [{"kind": "slice", "identifier": "INSTANCE", "label": "Instance", "icon": "MOD_INSTANCE"}],
    )
    check(
        "and an absent one-kind list reserves it as an invisible placeholder",
        pdt_menus.domain_pie_slots(
            [{"identifier": "FACE", "label": "Face", "present": False, "icon": "FACESEL"}]
        ),
        [{"kind": "placeholder", "identifier": "FACE", "label": ""}],
    )

    # And `draw()` must perform that plan instead of inventing its own.
    domain_draw = inspect.getsource(pdt_menus.PDT_MT_selection_domain_pie.draw)
    check("the domain pie draws from the slot plan", "domain_pie_slots(" in domain_draw, True)
    check(
        "a placeholder is a SPACE separator (invisible and unclickable)",
        'pie.separator(type="SPACE")' in domain_draw,
        True,
    )
    check(
        "an absent kind is never drawn as a button any more",
        "RADIOBUT_OFF" not in addon_sources() and 'emboss="NONE"' not in addon_sources(),
        True,
    )

    modes = pdt_menus.instance_mode_pie_entries()
    check("three instance modes, in PICK-3's order", [item["identifier"] for item in modes],
          ["POINTS", "POINTS_REFERENCE", "REALIZED"])
    check(
        "with the names L0 uses",
        [item["label"] for item in modes],
        ["Point Cloud", "Point Cloud with Reference", "Realized Instance"],
    )
    check(
        "the reference is part of the mode, not a separate switch",
        [pdt_instances.reference_for_mode(mode) for mode in pdt_constants.INSTANCE_MODES],
        [False, True, True],
    )

    # Both pies are real radial layouts: the slices go into `layout.menu_pie()`, not into the
    # block layout (which stacks them in one column - the bug the screenshot found).
    for menu in (pdt_menus.PDT_MT_selection_domain_pie, pdt_menus.PDT_MT_selection_instance_mode_pie):
        source = inspect.getsource(menu.draw)
        check(f"'{menu.bl_idname}' builds a radial layout", "menu_pie()" in source, True)
        check(
            f"'{menu.bl_idname}' adds its slices to it",
            ".operator(" in source and "layout.operator(" not in source,
            True,
        )

    # A pie choice is explicit: it is used as given, and never written to the node.
    node_domain_before = selection.domain
    session = pdt_session.begin_pick(tree, selection, domain="CONTROL_POINT")
    check("the chosen element kind is used as given", session["domain"], "CONTROL_POINT")
    check("and it says where it came from", session["domain_source"], "pie")
    check("the baked component follows the kind", session["geometry_kind"], "CURVE")
    check("the node itself is untouched", selection.domain, node_domain_before)
    pdt_session.cancel_pick()
    check("and no kind is stored on the node", "pdt_pick_domain" in selection, False)

    # A kind this geometry does not have is refused with what it does have (PICK-3).
    absent = [
        domain
        for domain in pdt_constants.DOMAINS
        if pdt_constants.DOMAIN_KINDS[domain] not in [kind for kind, _n in available]
    ]
    check("this geometry is missing Instances", absent, ["INSTANCE"])
    message = ""
    try:
        pdt_session.begin_pick(tree, selection, domain="INSTANCE")
    except RuntimeError as ex:
        message = str(ex)
    else:
        pdt_session.cancel_pick()
    check("a kind the geometry lacks is refused", "cannot pick Instance elements" in message, True)
    check("and the refusal lists what it has", "it has:" in message, True)
    check("a refused pick leaves no session", bool(pdt_state.session), False)

    # Unknown identifiers never reach begin_pick's geometry logic.
    check("'INSTANCE' is the only kind that asks a second question (PICK-1)", len(modes), 3)

    # Instance picks: the three modes (PICK-3). What is baked follows the pie's choice alone,
    # whatever the node's own Domain says (L0 明确不做: 不读取被识别节点的 domain 属性).
    tree, selection, _obj, _curve = build_instance_scene()
    for mode, keeps_reference in (("POINTS", False), ("POINTS_REFERENCE", True), ("REALIZED", True)):
        session = pdt_session.begin_pick(tree, selection, domain="INSTANCE", instance_mode=mode)
        check(f"{mode}: the session's mode", session["instance_mode"], mode)
        check(f"{mode}: the source object's visibility follows the mode",
              session["src_reference"], keeps_reference)
        pdt_session.cancel_pick()

    tree, selection, _obj, _curve = build_instance_scene(domain="VERTEX")
    session = pdt_session.begin_pick(tree, selection, domain="INSTANCE", instance_mode="POINTS_REFERENCE")
    check("an instance pick works although the node's Domain says Vertex",
          session["geometry_kind"], "INSTANCES")
    check("and the node's Domain is left alone", selection.domain, "VERTEX")
    pdt_session.cancel_pick()

    # The mode is session-only: nothing is stored on the node (L0 明确不做).
    tree, selection, _obj, _curve = build_instance_scene()
    pdt_session.begin_pick(tree, selection, domain="INSTANCE", instance_mode="REALIZED")
    pdt_session.cancel_pick()
    check("picking writes no mode onto the node", pdt_constants.INSTANCE_MODE_STORE_KEY in selection, False)

    # An older node's stored mode still resolves (two-mode spellings migrate).
    selection[pdt_constants.INSTANCE_MODE_STORE_KEY] = "REALIZED"
    check("legacy 'REALIZED' keeps its meaning", pdt_instances.stored_instance_mode(selection), "REALIZED")
    selection[pdt_constants.INSTANCE_MODE_STORE_KEY] = "POINTS"
    selection[pdt_constants.REFERENCE_STORE_KEY] = True
    check("legacy POINTS + reference becomes the reference mode",
          pdt_instances.stored_instance_mode(selection), "POINTS_REFERENCE")
    check("and reference_enabled follows it", pdt_instances.reference_enabled(selection), True)
    selection[pdt_constants.REFERENCE_STORE_KEY] = False
    check("legacy POINTS without it stays the plain point mode",
          pdt_instances.stored_instance_mode(selection), "POINTS")
    selection[pdt_constants.INSTANCE_MODE_STORE_KEY] = "nonsense"
    check("an unknown stored value is ignored", pdt_instances.stored_instance_mode(selection), "")
    del selection[pdt_constants.INSTANCE_MODE_STORE_KEY]
    del selection[pdt_constants.REFERENCE_STORE_KEY]


def main() -> int:
    test_encode()
    test_pick_apply_roundtrip()
    test_pick_cancel()
    test_cleanup_leftovers()
    test_qualification()
    test_existing_object_selection_does_not_leak()
    test_parameter_preselects_for_continuation()
    test_group_parameter_is_not_preselected()
    test_domain_aware_picking()
    test_long_selection_fallback()
    test_multiple_string_parameters()
    test_modal_event_mapping()
    test_session_guards()
    test_shortcut_management()
    test_keymap_repair()
    test_keymap_repair_runs_once_keymaps_exist()
    test_session_ui_and_guards()
    test_multi_object_selection()
    test_shared_group_input_override()
    test_upstream_modifiers()
    test_trigger_hit_test()
    test_pie_menu_entries()
    test_clear_parameter()
    test_nested_groups()
    test_geometry_source_resolution()
    test_breadcrumb_recovers_group_nodes()
    test_node_editor_pin()
    test_can_pick_call_forms()
    test_ui_icon_names()
    test_non_mesh_source_objects()
    test_mixed_geometry_components()
    test_point_cloud_picking()
    test_curve_spline_domain()
    test_domain_drives_component_choice()
    test_element_kind_menu_offers_all_seven()
    test_instance_picking()
    test_realized_instance_picking()
    test_instance_reference_geometry()
    print(f"\n== summary: {_checks} checks, {len(_failures)} failure(s) ==")
    for label in _failures:
        print(f"  failed: {label}")
    if _failures:
        raise SystemExit(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
