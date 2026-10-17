"""Real-window GUI test for the MMB node-info add-on.

A modal operator cannot be driven from `-b`, so this runs with a real window:

    blender.exe --enable-event-simulate --python tools\\pdt\\tests\\gui_mmb_node_info.py

**No `--factory-startup`**: `spec/node_info.md` 验收 requires the GUI test to run under the
*user's* configuration, because that is the only place the bugs the user actually hits show
up - the wrong coordinate space for a UI scale of 120% was invisible for exactly that
reason. The test builds its own scene and writes no preferences, so it is safe to run
against a real profile.

It checks the things only a window can show:

  1. the add-on **binds no key** (MMB-8: the user assigns it) while still registering the
     one **unbound** item that makes the operator findable in the Keymap editor, so that
     assigning a key by hand is possible at all;
  2. the hit test picks the node under the cursor, in *node* space (`region_to_view` answers
     in view space, which `UI_SCALE_FAC` separates from a node coordinate), and agrees with
     the editor's own hit test (`bpy.ops.node.select(location=...)` is used as the oracle on
     a 9x9 grid, with the old view-space rule kept as a positive control);
  3. the information block actually **draws** - the draw callback is called inside
     a real GPU context, so `gpu.shader.from_builtin` / `blf` failures surface here
     and nowhere else - and that it stays where the trigger put it (L0 边界).

Trap: `bpy.ops.wm.redraw_timer` swallows the active `temp_override`, so every
context-dependent step below runs inside its own `temp_override` block.

Results go to a file as well as stdout (a killed pipe swallows buffered prints),
and an unconditional quit timer is registered before anything else.
"""

from __future__ import annotations

import os
import sys
import traceback

import bpy

# The installed copies, exactly like tools/pdt/tests/test_mmb_node_info.py: the tests must
# exercise what the build ships, not the source tree. The imports are at module level (not
# inside `main()`) because the helpers below run before `main()` does.
sys.path.insert(0, bpy.utils.resource_path("SYSTEM") + "/scripts/addons_core")
sys.path.insert(0, bpy.utils.resource_path("SYSTEM") + "/scripts/modules")

import pdt_geo_select as pdt  # noqa: E402
import pdt_mmb_info as mmb  # noqa: E402
from pdt_mmb_info.utils import constants as mmb_constants  # noqa: E402
from pdt_mmb_info import hit_test as mmb_hit_test  # noqa: E402
from pdt_mmb_info import keymap as mmb_keymap  # noqa: E402
from pdt_mmb_info.operators import node_info as mmb_operators  # noqa: E402
from pdt_mmb_info import preferences as mmb_preferences  # noqa: E402
from pdt_mmb_info import session as mmb_session  # noqa: E402

REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_gui_mmb_node_info.txt")
LOG: list[str] = []
CHECKS = 0
FAILURES: list[str] = []


def log(line=""):
    LOG.append(str(line))
    print(line, flush=True)


def flush_report(verdict="FAIL"):
    log(f"GUI_TEST_RESULT: {verdict}")
    try:
        with open(REPORT, "w", encoding="utf-8") as handle:
            handle.write("\n".join(LOG))
    except OSError:
        pass


def check(label, actual, expected):
    global CHECKS
    CHECKS += 1
    if actual == expected:
        log(f"OK   {label}: {actual}")
    else:
        log(f"FAIL {label}: got {actual!r}, expected {expected!r}")
        FAILURES.append(label)


def check_true(label, condition, detail=""):
    check(label, bool(condition), True)
    if not condition and detail:
        log(f"     ({detail})")


def _quit():
    try:
        bpy.ops.wm.quit_blender()
    except Exception:  # noqa: BLE001
        pass
    return None


def step(mmb, context, event):
    """One modal step, including the cleanup the operator's `modal()` performs.

    `_modal_step` only decides; ending the session is the operator's job, so a test
    that called `_modal_step` alone would leave the session alive and report a
    failure that does not exist.
    """
    action, _ = mmb_session._modal_step(context, event)
    if action in {"FINISH", "CANCEL"}:
        mmb_session._remove_draw_handler()
        mmb_session._clear_session()
    return action


def build_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    mesh = bpy.data.meshes.new("cube")
    mesh.from_pydata(
        [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
         (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)], [],
        [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)],
    )
    mesh.update()
    obj = bpy.data.objects.new("CubeOwner", mesh)
    bpy.context.scene.collection.objects.link(obj)

    tree = bpy.data.node_groups.new("MMB GUI", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_in = tree.nodes.new("NodeGroupInput")
    group_in.location = (-500, 0)
    subdiv = tree.nodes.new("GeometryNodeSubdivisionSurface")
    subdiv.name = "Subdivide"
    subdiv.location = (0, 0)
    tree.links.new(group_in.outputs["Geometry"], subdiv.inputs["Mesh"])
    group_out = tree.nodes.new("NodeGroupOutput")
    group_out.location = (300, 0)
    tree.links.new(subdiv.outputs["Mesh"], group_out.inputs["Geometry"])

    modifier = obj.modifiers.new("GeometryNodes", "NODES")
    modifier.node_group = tree
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return tree, subdiv, obj


def find_node_editor(window):
    for area in window.screen.areas:
        if area.type != "NODE_EDITOR":
            continue
        for region in area.regions:
            if region.type == "WINDOW":
                return area, region
    return None, None


class _Event:
    """A stand-in for `bpy.types.Event`, which cannot be constructed in Python."""

    def __init__(self, type, value="PRESS", region_xy=(0, 0)):
        self.type = type
        self.value = value
        self.mouse_x = region_xy[0]
        self.mouse_y = region_xy[1]
        self.mouse_region_x = region_xy[0]
        self.mouse_region_y = region_xy[1]


def main():
    global CHECKS

    pdt.register_once()
    # This test runs under the *user's* configuration (L0 验收), where the add-on is
    # normally already enabled - Blender registered these classes itself at startup. So the
    # test brings the state it needs instead of assuming a clean profile: unregister (a
    # no-op when it is not registered) and register again. That is also what a "Reload
    # Scripts" does, so it exercises a real path.
    mmb.unregister()
    mmb.register()

    window = bpy.context.window or bpy.context.window_manager.windows[0]
    tree, subdiv, obj = build_scene()
    settings = mmb_preferences.prefs(bpy.context)
    check(
        "the two preferences L0 allows are reachable",
        (settings.show_input_only, settings.text_scale) if settings else None,
        (False, 1.0),
    )

    # ------------------------------------------------------------- keymap
    # MMB-8: the add-on assigns **no** key - the user picks one in Blender's Keymap editor.
    # It does register ONE **unbound** item (`type="NONE"`), because that editor's name
    # search only filters the items a keymap already has (`rna_keymap_ui.draw_filtered`):
    # with no item anywhere the operator could not be searched for at all, which is the bug
    # the user hit on a factory-fresh profile. So the checks here are: *our* item exists and
    # carries no key, the name search finds it, and registering binds nothing.
    #
    # A **user** binding is expected and welcome (that is the whole point of MMB-8) - this
    # test runs under the user's own configuration, where the key they assigned lives on the
    # merged item (`user_modified=True`, measured with `qa/mmb/mmb_binding_probe.py`). What must
    # never happen is the add-on binding one itself, which is what the round-trip below pins.
    keyconfigs = bpy.context.window_manager.keyconfigs

    def our_items(keyconfig, bound_only=False):
        return [
            item for km in keyconfig.keymaps if km.name == mmb_keymap.KEYMAP_NAME
            for item in km.keymap_items
            if item.idname == mmb_operators.PDT_OT_node_info_modal.bl_idname
            and (mmb_keymap.item_is_bound(item) if bound_only else True)
        ]

    def user_bindings():
        """`(key, origin)` of every *bound* item in the user's own keymap for our operator.

        The user's binding is data, not a check: it exists on their profile (measured with
        `qa/mmb/mmb_binding_probe.py`: `Alt+MMB`, `user_modified=True`), and unregistering the
        add-on must leave it exactly as it was.
        """
        return [
            (str(item.type), item.value, bool(item.ctrl), bool(item.shift), bool(item.alt),
             item.active, bool(item.is_user_modified))
            for item in our_items(keyconfigs.user, bound_only=True)
        ]

    def name_search_hits(text):
        """What the Keymap panel's "Search by Name" field lists.

        The predicate is `rna_keymap_ui.draw_filtered(filter_type='NAME')` verbatim:
        `filter_text in kmi.idname.lower() or filter_text in kmi.name.lower()`, over the
        keymaps of `wm.keyconfigs.user` - the table the Keymap editor draws.
        """
        text = text.lower()
        return [
            (km.name, kmi.idname) for km in keyconfigs.user.keymaps
            for kmi in km.keymap_items
            if text in (kmi.idname or "").lower() or text in (kmi.name or "").lower()
        ]

    check_true(
        "the operator is registered and searchable (no INTERNAL flag)",
        hasattr(bpy.types, "PDT_OT_node_info_modal")
        and "INTERNAL" not in bpy.types.PDT_OT_node_info_modal.bl_options,
        f"options={sorted(getattr(bpy.types.PDT_OT_node_info_modal, 'bl_options', []))}",
    )
    addon_our_items = our_items(keyconfigs.addon)
    check("the addon keyconfig holds one item for our operator", len(addon_our_items), 1)
    check_true(
        "that item carries no key (MMB-8: the add-on assigns none)",
        all(mmb_keymap.item_is_bound(item) is False for item in addon_our_items),
        f"{[(item.type, item.value, item.alt) for item in addon_our_items]}",
    )
    check_true(
        "the add-on never writes a key into the *default* keymap",
        our_items(keyconfigs.default) == [],
        f"{our_items(keyconfigs.default)}",
    )
    check_true(
        "there is no operator that would bind a key for the user",
        not hasattr(bpy.types, "PDT_OT_node_info_bind_default"),
        "a bind operator is back - MMB-8 says the user owns the keymap",
    )
    check_true(
        "the shortcut status is reported, not written",
        callable(getattr(mmb_keymap, "binding_is_active", None)),
    )
    # The bug itself: the name the reminder tells the user to type must be findable in the
    # table the Keymap editor draws. The addon item is merged into the user keyconfig by
    # Blender's own keyconfig update, which runs on the event loop - hence the redraw first.
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
    hits = name_search_hits(mmb_constants.OPERATOR_LABEL)
    check_true(
        f"the Keymap name search finds {mmb_constants.OPERATOR_LABEL!r} in the user keyconfig",
        any(idname == mmb_operators.PDT_OT_node_info_modal.bl_idname for _name, idname in hits),
        f"hits={hits[:5]}",
    )

    default_kc = keyconfigs.default
    user_kc = keyconfigs.user

    def total_items(keyconfig):
        return sum(len(km.keymap_items) for km in keyconfig.keymaps)

    def snapshot_keymaps():
        return {
            (kc.name if kc is not None else "?", km.name): len(km.keymap_items)
            for kc in (keyconfigs.addon, default_kc, user_kc) if kc is not None
            for km in kc.keymaps
        }

    def our_count_snapshot():
        return {
            (kc.name if kc is not None else "?", km.name): len(our_items(kc))
            for kc in (keyconfigs.addon, default_kc, user_kc) if kc is not None
            for km in kc.keymaps if km.name == mmb_keymap.KEYMAP_NAME
        }

    before = snapshot_keymaps()
    before_ours = our_count_snapshot()
    before_bound = our_items(user_kc, bound_only=True) if user_kc is not None else []
    mmb.unregister()
    mmb.register()
    after = snapshot_keymaps()
    # Compared here rather than through `check()`: the snapshot is every keymap of every
    # keyconfig (hundreds of entries), and printing it on success buried the report.
    CHECKS += 1
    if after == before:
        log(f"OK   registering leaves every keymap at its previous size: {len(after)} keymaps")
    else:
        changed = {
            key: (before.get(key), after.get(key))
            for key in set(before) | set(after) if before.get(key) != after.get(key)
        }
        label = "registering adds exactly one item per keymap that changes"
        log(f"FAIL {label}: {changed}")
        FAILURES.append(label)
    check("that item is ours, in the Node Editor keymap", our_count_snapshot(), before_ours)
    check(
        "registering binds no key of our operator (even if the user has one)",
        len(our_items(user_kc, bound_only=True)) if user_kc is not None else 0,
        len(before_bound),
    )
    if user_kc is not None and default_kc is not None:
        check_true(
            "the user 'Node Editor' keymap still holds Blender's items",
            sum(len(km.keymap_items) for km in user_kc.keymaps if km.name == "Node Editor")
            >= sum(len(km.keymap_items) for km in default_kc.keymaps if km.name == "Node Editor"),
            f"user={total_items(user_kc)} default={total_items(default_kc)}",
        )
        check_true(
            "the user 'Node Editor' keymap is not an all-ours shell",
            not (
                sum(len(km.keymap_items) for km in user_kc.keymaps if km.name == "Node Editor") > 0
                and len(our_items(user_kc))
                == sum(len(km.keymap_items) for km in user_kc.keymaps if km.name == "Node Editor")
            ),
            f"user Node Editor items={total_items(user_kc)}",
        )

    # ------------------------------------------------------- node editor setup
    area, region = find_node_editor(window)
    if area is None:
        area = window.screen.areas[0]
        area.type = "NODE_EDITOR"
        region = next(r for r in area.regions if r.type == "WINDOW")
    if area is None or region is None:
        check_true("a real Node Editor area is available", False)
        return

    def override():
        return bpy.context.temp_override(window=window, area=area, region=region)

    with override():
        area.spaces.active.node_tree = tree
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
    with override():
        space = area.spaces.active
        check_true(
            "the editor shows the test tree",
            (space.edit_tree or space.node_tree) == tree,
            f"{getattr(space, 'node_tree', None)}",
        )

    # ---------------------------------------------------------- hit test
    # Put the node exactly under the middle of the node editor region, then ask the
    # add-on's own hit test - it must use the editor's `region_to_view` conversion, and it
    # must bring the result into *node* space (see the grid check below).
    scale = mmb_hit_test._ui_scale()
    log(
        f"     (UI_SCALE_FAC={scale:.6f} dpi={bpy.context.preferences.system.dpi} "
        f"pixel_size={bpy.context.preferences.system.pixel_size})"
    )
    region_x = region.x + region.width // 2
    region_y = region.y + region.height // 2
    local_x, local_y = region_x - region.x, region_y - region.y
    with override():
        world_x, world_y = region.view2d.region_to_view(local_x, local_y)
        # `region_to_view` answers in *view* space, while a node location is a node-space
        # coordinate: writing one into the other only lines up when UI_SCALE_FAC is 1. That
        # is how this test kept passing on a scaled display while the operator kept missing.
        subdiv.location = (world_x / scale - subdiv.width * 0.5, world_y / scale + 20.0)
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)

    with override():
        for node in tree.nodes:
            node.select = node.name == "Subdivide"
        left, top, width, height = mmb_hit_test._node_extent(subdiv)
        log(
            f"     (region=({region.x},{region.y},{region.width}x{region.height}) "
            f"cursor_region=({local_x},{local_y}) cursor_view=({world_x:.1f},{world_y:.1f}) "
            f"node_box=({left:.1f},{top:.1f} {width:.1f}x{height:.1f})"
        )
        pointer = _Event("MOUSEMOVE", "NOTHING", (local_x, local_y))
        hovered = mmb_session._node_under_cursor(bpy.context, pointer)
        check("the node under the cursor is found", getattr(hovered, "name", None), "Subdivide")

        # Negative control: a point far to the left of every node finds nothing.
        far = _Event("MOUSEMOVE", "NOTHING", (2, local_y))
        check("empty space finds no node", mmb_session._node_under_cursor(bpy.context, far), None)

    # ------------------------------------------- the editor is the hit-test oracle
    # `bpy.ops.node.select(location=...)` runs the editor's *own* hit test
    # (`node_under_mouse_get()`), so it is the authority on "is the cursor on this node".
    # A grid of points over the node and its margin must get the same answer from the add-on.
    # Selection is cleared before each oracle call - that is how the freshly selected node
    # becomes the answer - and restored afterwards; global undo is switched off so a hundred
    # probes cannot fill the undo stack.
    def ours(local_xy):
        node = mmb_session._node_under_cursor(bpy.context, _Event("MOUSEMOVE", "NOTHING", local_xy))
        return getattr(node, "name", None)

    def oracle(local_xy):
        for node in tree.nodes:
            node.select = False
        bpy.ops.node.select(location=(int(local_xy[0]), int(local_xy[1])))
        hit = next((node for node in tree.nodes if node.select), None)
        return getattr(hit, "name", None)

    def old_rule(local_xy):
        """The previous behaviour: a node-space box tested against a view-space cursor."""
        world_x, world_y = region.view2d.region_to_view(local_xy[0], local_xy[1])
        for node in reversed(list(tree.nodes)):
            if node.hide:
                continue
            left, top, width, height = mmb_hit_test._node_extent(node)
            if left <= world_x <= left + width and top - height <= world_y <= top:
                return node.name
        return None

    with override():
        select_backup = {node.name: node.select for node in tree.nodes}
        left, top, width, height = mmb_hit_test._node_extent(subdiv)
        check_true(
            "the box is the drawn rectangle in node space",
            abs(left - subdiv.location_absolute[0]) < 1.0
            and abs(top - subdiv.location_absolute[1]) < 1.0
            and abs(width - subdiv.dimensions[0] / scale) < 0.01
            and abs(height - subdiv.dimensions[1] / scale) < 0.01,
            f"box=({left:.2f},{top:.2f},{width:.2f},{height:.2f}) "
            f"location={tuple(subdiv.location_absolute)} dimensions={tuple(subdiv.dimensions)}",
        )
        x0, y0 = region.view2d.view_to_region(left * scale, top * scale, clip=False)
        x1, y1 = region.view2d.view_to_region(
            (left + width) * scale, (top - height) * scale, clip=False
        )
        margin = 12
        steps = 9
        mismatches: list[tuple] = []
        old_only_misses: list[tuple] = []
        undo = bpy.context.preferences.edit.use_global_undo
        bpy.context.preferences.edit.use_global_undo = False
        try:
            for row in range(steps):
                for column in range(steps):
                    px = int(x0 - margin + (x1 - x0 + 2 * margin) * column / (steps - 1))
                    py = int(y0 - margin + (y1 - y0 + 2 * margin) * row / (steps - 1))
                    expected = oracle((px, py))
                    got = ours((px, py))
                    if expected != got:
                        mismatches.append((px, py, expected, got))
                    if expected == "Subdivide" and old_rule((px, py)) != "Subdivide":
                        old_only_misses.append((px, py))
        finally:
            bpy.context.preferences.edit.use_global_undo = undo
            for node in tree.nodes:
                node.select = select_backup.get(node.name, False)
        log(
            f"     (grid {steps}x{steps} over the node + {margin}px margin, "
            f"rect=({x0},{y0})-({x1},{y1}), mismatches={len(mismatches)}, "
            f"old rule missed {len(old_only_misses)} of the editor's own hits)"
        )
        check("the add-on matches the editor's hit test on every grid point", mismatches[:4], [])
        # Positive control: with the bug put back (a view-space cursor compared against a
        # node-space box) the same grid must miss points the editor counts as on the node.
        # Without this control the grid could pass for the wrong reason on a 100% display.
        if abs(scale - 1.0) > 0.01:
            check_true(
                "positive control: the old view-space rule misses the editor's own hits",
                len(old_only_misses) >= 5,
                f"only {len(old_only_misses)} of the grid points exposed the old bug",
            )
        else:
            log("     (SKIP positive control: UI_SCALE_FAC is 1.0 on this display)")

    # ------------------------------------------------------- collapsed node
    # `node.hide` is NODE_COLLAPSED (the H key): the node keeps its title band and is drawn
    # *centred* on its location. The add-on used to skip collapsed nodes outright, so the one
    # thing such a node still shows - its title - could not be read at all.
    group_out = tree.nodes["Group Output"]
    with override():
        group_out.hide = True
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    with override():
        box = mmb_hit_test._node_extent(group_out)
        collapsed_centre = region.view2d.view_to_region(
            (box[0] + box[2] * 0.5) * scale, (box[1] - box[3] * 0.5) * scale, clip=False
        )
        check("a collapsed node is found by its title", ours(collapsed_centre), "Group Output")
        check("the editor agrees about the collapsed node", oracle(collapsed_centre), "Group Output")
        group_out.hide = False

    # ------------------------------------------------------------ frame nodes
    # A frame carries no geometry and the editor itself only hits its border, so picking the
    # frame instead of whatever sits inside it can only hide the node the user meant. The
    # add-on deliberately returns nothing: this records the difference rather than hiding it.
    frame = tree.nodes.new("NodeFrame")
    frame.name = "FrameProbe"
    frame.label = "probe"
    frame.width = 300.0
    frame.height = 200.0
    with override():
        # Keep the frame inside the visible area (a frame's bounds are only computed for a
        # childless frame from its own width/height, but the probe has to be on screen for
        # `view_to_region` to answer with sensible region coordinates).
        centre_view_x, centre_view_y = region.view2d.region_to_view(
            region.width * 0.5, region.height * 0.5
        )
        frame.location = (centre_view_x / scale - 150.0, centre_view_y / scale)
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    with override():
        box = mmb_hit_test._node_extent(frame)
        frame_border = region.view2d.view_to_region(box[0] * scale + 4.0, (box[1] - 10.0) * scale, clip=False)
        check("the editor hits the frame's border", oracle(frame_border), "FrameProbe")
        check("the add-on skips frames (they carry no geometry)", ours(frame_border), None)
        tree.nodes.remove(frame)

    # ------------------------------------------------- the block really draws
    # `bpy.ops` does not hand an event to `invoke`, so the entry point it calls is
    # driven directly with the same pointer event the hit test used.
    with override():
        started = mmb_session._begin_session(
            bpy.context, _Event("MIDDLEMOUSE", "PRESS", (local_x, local_y))
        )
        check("invoke over a node starts the modal", started, True)
        check_true(
            "the information block was built",
            mmb_session._SESSION.state is not None and mmb_session._SESSION.state["components"],
            f"state={mmb_session._SESSION.state}",
        )
        if mmb_session._SESSION.state is not None:
            check("the block names the hovered node", mmb_session._SESSION.state["node"], "Subdivide")
            check("the block comes from the output", mmb_session._SESSION.state["stream"], "output")
            mesh = next(
                (c for c in mmb_session._SESSION.state["components"] if c["label"] == "Mesh"), None
            )
            check(
                "the block reports the subdivided mesh",
                {entry["label"]: entry["count"] for entry in mesh["counts"]}.get("Vertex")
                if mesh else None,
                26,
            )
            check_true(
                "the attribute table is grouped by domain",
                any(g["title"] == "Vertex Attributes" for g in mmb_session._SESSION.state["attribute_groups"]),
                f"groups={[g['title'] for g in mmb_session._SESSION.state['attribute_groups']]}",
            )
        check_true("a draw handler is registered", mmb_session._DRAW_HANDLE is not None)

        # L0 边界 + MMB-7: the block stays where the trigger put it, keeps the node it was
        # opened on, and a drag does not hide it either ("按住拖动时不移动，也不用让信息块
        # 消失") - so even a long drag leaves the block exactly as it was.
        place = mmb_session._SESSION.cursor
        node_before = mmb_session._SESSION.node_name
        mmb_session._modal_step(bpy.context, _Event("MOUSEMOVE", "NOTHING", (local_x + 80, local_y + 60)))
        check("a drag does not move the block", mmb_session._SESSION.cursor, place)
        check("a drag does not re-read another node", mmb_session._SESSION.node_name, node_before)
        check_true("a drag does not hide the block", mmb_session._SESSION.state is not None)

    # This is the point of the GUI test: the draw callback runs for real, inside
    # Blender's own draw loop, and must not raise.
    error = None
    try:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=3)
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    check("drawing the block in the draw loop raises nothing", error, None)

    with override():
        check_true(
            "the block survived the redraws",
            mmb_session._SESSION.state is not None,
            "the session was torn down by a draw",
        )
        # Call the callback directly too, so a GPU/blf failure is reported as this
        # test's failure instead of vanishing into Blender's draw loop. The second and
        # third positions cover the clamping at the region edge and a scaled block.
        for label, cursor, scale in (
            ("at the cursor", (local_x, local_y), 1.0),
            ("away from the cursor", (region.width * 0.3, region.height * 0.6), 1.0),
            ("scaled up", (region.width - 8, region.height - 8), 1.8),
        ):
            mmb_session._SESSION.cursor = cursor
            saved = mmb_preferences.prefs(bpy.context).text_scale
            mmb_preferences.prefs(bpy.context).text_scale = scale
            error = None
            try:
                mmb_session._draw_callback()
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            finally:
                mmb_preferences.prefs(bpy.context).text_scale = saved
            check(f"draw_panel runs {label} in a real GPU context", error, None)

    # ------------------------------------------------------- modal lifecycle
    # `_modal_step` is what the operator's `modal()` runs; driving it directly is the
    # only way to test the gesture, because `bpy.types.Event` cannot be built in
    # Python and simulated events do not reach keymaps.
    cursor_xy = (local_x, local_y)
    with override():
        mmb_session._begin_session(bpy.context, _Event("MIDDLEMOUSE", "PRESS", cursor_xy))
        mmb_session._remove_draw_handler()  # no event loop runs in a script
        check_true("invoke over a node starts the modal", mmb_session._SESSION.state is not None)

        action = step(mmb, bpy.context, _Event("MOUSEMOVE", "NOTHING", cursor_xy))
        check("a mouse move keeps the modal alive", action, "RUNNING_MODAL")
        check_true("the block survives a mouse move", mmb_session._SESSION.state is not None)

        # MMB-7: no pin. The pin key must do nothing at all, and a stray click must not
        # close anything either.
        action = step(mmb, bpy.context, _Event("P", "PRESS", cursor_xy))
        check("the old pin key does nothing", action, "RUNNING_MODAL")
        check_true("the block is still shown after that key", mmb_session._SESSION.state is not None)
        action = step(mmb, bpy.context, _Event("LEFTMOUSE", "PRESS", (3, 3)))
        check("a stray click does nothing", action, "RUNNING_MODAL")
        check_true("the block is still shown after a click", mmb_session._SESSION.state is not None)

        action = step(mmb, bpy.context, _Event("MIDDLEMOUSE", "RELEASE", cursor_xy))
        check("MMB release finishes the modal", action, "FINISH")
        check("finishing clears the session", mmb_session._SESSION.state, None)
        check("finishing removes the draw handler", mmb_session._DRAW_HANDLE, None)

        # Esc stays as an escape hatch even though MMB-7 does not ask for it.
        mmb_session._begin_session(bpy.context, _Event("MIDDLEMOUSE", "PRESS", cursor_xy))
        mmb_session._remove_draw_handler()  # no event loop runs in a script
        check_true("a fresh hold shows the block", mmb_session._SESSION.state is not None)
        action = step(mmb, bpy.context, _Event("ESC", "PRESS", cursor_xy))
        check("Esc cancels the modal", action, "CANCEL")
        check("cancelling clears the session", mmb_session._SESSION.state, None)
        check("cancelling removes the draw handler", mmb_session._DRAW_HANDLE, None)

    # ------------------------------------------------------------- cleanup
    leftovers = [o.name for o in bpy.data.objects if o.name.startswith(mmb_constants.TEMP_PREFIX)]
    check("no temporary probe objects are left behind", leftovers, [])
    leftover_groups = [g.name for g in bpy.data.node_groups if g.name.startswith("PDT_PICK_group_")]
    check("no temporary node groups are left behind", leftover_groups, [])
    check(
        "the original tree is untouched",
        [n.name for n in tree.nodes],
        ["Group Input", "Subdivide", "Group Output"],
    )

    user_bindings_before = user_bindings()
    mmb.unregister()
    check(
        "unregister removes the keymap item",
        [
            item.idname for km in bpy.context.window_manager.keyconfigs.addon.keymaps
            if km.name == mmb_keymap.KEYMAP_NAME for item in km.keymap_items
            if item.idname == mmb_operators.PDT_OT_node_info_modal.bl_idname
        ],
        [],
    )
    # The user's own binding lives on the merged item in *their* keymap; removing our addon
    # entry must not touch it (MMB-8: the key belongs to the user). Compared against the
    # snapshot taken before the unregister above.
    check(
        "unregister leaves the user's own binding alone",
        user_bindings(),
        user_bindings_before,
    )


if __name__ == "__main__":
    bpy.app.timers.register(_quit, first_interval=120.0)
    verdict = "FAIL"
    try:
        main()
        verdict = "PASS" if not FAILURES else "FAIL"
    except Exception:  # noqa: BLE001
        log(traceback.format_exc())
        verdict = "FAIL"
    finally:
        flush_report(verdict)
        log(f"== gui_mmb_node_info: {CHECKS} checks, {len(FAILURES)} failure(s) ==")
        for name in FAILURES:
            log(f"  failed: {name}")
        bpy.app.timers.register(_quit, first_interval=1.0)
