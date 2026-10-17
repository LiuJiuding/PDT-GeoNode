"""GUI test for the socket-level trigger - the parts a headless run cannot reach.

Run it with a real window and Blender's event-injection feature (there is no way to drive a
modal operator from `-b`):

    build\\...\\bin\\Release\\blender.exe --enable-event-simulate \
        --python tools\\pdt\\tests\\gui_pick_modal.py

**No `--factory-startup`** (L0 `spec/selection_pick.md` 验收): the test has to run in the
user's own configuration, because that is where the coordinates are interesting (UI scale
1.19 on this machine) and where the keymap discipline can actually be observed.

Cases:
  1. `pdt.node_under_cursor` (the C++ query) finds the string socket on a real, drawn node
     editor - and **only** the socket, never the node's middle or its header band (PICK-1).
  2. the trigger: hit test -> first-level pie menu opens -> dismissing it starts no pick
  3. a pie slice starts the session + the modal loop, and Esc / Right-Click cancel it
  4. Enter applies: the parameter holds the picked index, the temporary object is gone
  5. an exception inside the modal ends it while the session stays usable
  6. keymap discipline: one **unbound** addon-level item (PICK-4), user keymaps untouched

Every line goes to stdout *and* to `tests/_gui_pick_modal.txt`: a GUI Blender cannot report a
useful process exit code from a timer, and piped stdout is lost when a watchdog kills the run.
"""

from __future__ import annotations

import sys
import time
import traceback

import bpy

sys.path.insert(0, bpy.utils.resource_path("SYSTEM") + "/scripts/addons_core")
sys.path.insert(0, bpy.utils.resource_path("SYSTEM") + "/scripts/modules")

import pdt_geo_select as pdt  # the package: bl_info + register
from pdt_geo_select.utils import constants as pdt_constants
from pdt_geo_select import hit_test as pdt_hit_test
from pdt_geo_select import menus as pdt_menus
from pdt_geo_select.operators import pick as pdt_pick
from pdt_geo_select import selection as pdt_selection
from pdt_geo_select import state as pdt_state  # noqa: E402

pdt.register_once()

_failures: list[str] = []
_checks = 0


def say(line: str) -> None:
    print(line, flush=True)
    try:
        with open(_log_path(), "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:  # noqa: BLE001 - the log is a convenience, never a requirement
        pass


def _log_path() -> str:
    import os

    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "_gui_pick_modal.txt")


def check(label: str, actual, expected) -> None:
    global _checks
    _checks += 1
    if actual == expected:
        say(f"OK   {label}: {actual}")
    else:
        say(f"FAIL {label}: got {actual}, expected {expected}")
        _failures.append(label)


def quit_soon(delay: float) -> None:
    def _quit():
        bpy.ops.wm.quit_blender()
        return None

    bpy.app.timers.register(_quit, first_interval=delay)


def _c_round(value):
    import math

    return math.floor(value + 0.5) if value >= 0.0 else math.ceil(value - 0.5)


def ui_scale() -> float:
    """`UI_SCALE_FAC`: node space -> view space (the factor the editor draws with)."""
    system = bpy.context.preferences.system
    scale = float(getattr(system, "ui_scale", 0.0) or 0.0)
    if scale > 0.0:
        return scale
    dpi = float(getattr(system, "dpi", 0.0) or 0.0)
    return (dpi / 72.0) if dpi > 0.0 else 1.0


def header_height() -> float:
    """`NODE_DY` - the header band the socket hit test deliberately excludes."""
    system = bpy.context.preferences.system
    pixelsize = int(getattr(system, "pixel_size", None) or 1)
    return float(int(_c_round(18.0 * ui_scale())) + 2 * pixelsize)


def build_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    tree = bpy.data.node_groups.new(name="PDT GUI Test", type="GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_in = tree.nodes.new("NodeGroupInput")
    group_in.location = (-300.0, 0.0)
    selection = tree.nodes.new("GeometryNodePDTSelection")
    selection.location = (0.0, 0.0)
    # Start from an empty parameter: the node's own default is `*` (everything), and a pick
    # would then pre-select the whole temporary mesh - which would hide what this test
    # measures (that a pick starts from nothing when the parameter is empty).
    selection.inputs["Selection"].default_value = ""
    group_out = tree.nodes.new("NodeGroupOutput")
    # Explicit, separate positions: the sweep below scans a band around the pick node, and
    # auto-placed nodes would land on top of each other (their sockets would be hit too).
    group_out.location = (300.0, 0.0)
    tree.links.new(group_in.outputs["Geometry"], selection.inputs["Geometry"])
    tree.links.new(selection.outputs["Geometry"], group_out.inputs["Geometry"])
    mesh = bpy.data.meshes.new("cube")
    corners = [
        (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0), (1.0, 0.0, 1.0), (1.0, 1.0, 1.0), (0.0, 1.0, 1.0),
    ]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    mesh.from_pydata(corners, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("CubeOwner", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new(name="GeometryNodes", type="NODES")
    modifier.node_group = tree
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return tree, selection, obj


def main() -> None:
    open(_log_path(), "w", encoding="utf-8").close()
    quit_soon(60.0)
    window = bpy.context.window or bpy.context.window_manager.windows[0]
    tree, selection, obj = build_scene()

    # ---------------------------------------------------------------- keymap discipline
    # Regression guard for the worst bug this add-on had: writing keymap items into the *user*
    # keyconfig replaced a built-in keymap (viewport navigation and click-select died). Only a
    # GUI run can see the stored keymaps. PICK-4 adds the other half: the item this add-on
    # registers must be **unbound** (the user assigns the key).
    user_keyconfig = bpy.context.window_manager.keyconfigs.user
    default_keyconfig = bpy.context.window_manager.keyconfigs.default
    addon_keyconfig = bpy.context.window_manager.keyconfigs.addon

    def item_count(keyconfig, name):
        return sum(len(keymap.keymap_items) for keymap in keyconfig.keymaps if keymap.name == name)

    def addon_picker_items():
        return [
            item
            for keymap in addon_keyconfig.keymaps
            if keymap.name == pdt_constants.PICK_KEYMAP_NAME
            for item in keymap.keymap_items
            if item.idname == pdt_constants.PICKER_MODAL_IDNAME
        ]

    def user_pdt_items():
        """The pdt items in the user's own keymaps, as plain data (pointers go stale).

        A user who has assigned the shortcut *does* have an item here - PICK-4 wants exactly
        that. What must not happen is the add-on writing one, so the snapshot is compared with
        the same snapshot at the end of the run.
        """
        return sorted(
            (item.idname, str(item.type), bool(item.ctrl), bool(item.shift), bool(item.alt))
            for keymap in user_keyconfig.keymaps
            if keymap.name in ("3D View", "Node Editor")
            for item in keymap.keymap_items
            if item.idname.startswith("pdt.")
        )

    user_items_before = user_pdt_items()
    say(f"     [keymap] the user's own pdt items at start: {user_items_before}")

    for keymap_name in ("3D View", "Node Editor"):
        check(
            f"user '{keymap_name}' still holds Blender's items",
            item_count(user_keyconfig, keymap_name) >= item_count(default_keyconfig, keymap_name),
            True,
        )
    check("the picker item exists in the addon keyconfig", len(addon_picker_items()), 1)
    check("and it binds no key (PICK-4)", [item.type for item in addon_picker_items()], ["NONE"])

    # ------------------------------------------------------------------ the node editor
    biggest = max(window.screen.areas, key=lambda area: area.width * area.height)
    cx, cy = biggest.x + biggest.width // 2, biggest.y + biggest.height // 2

    def as_node_editor() -> None:
        biggest.type = "NODE_EDITOR"
        space = biggest.spaces.active
        space.node_tree = tree
        for node in tree.nodes:
            node.select = node is selection
        tree.nodes.active = selection
        biggest.tag_redraw()
        # The socket hit test reads runtime locations that are written while drawing, so the
        # editor really has to have drawn the tree before the sweep below means anything.
        try:
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
        except Exception:  # noqa: BLE001 - headless-ish runs have no window to swap
            pass

    def as_viewport() -> None:
        biggest.type = "VIEW_3D"
        biggest.tag_redraw()

    def node_region():
        return next(region for region in biggest.regions if region.type == "WINDOW")

    def override():
        return bpy.context.temp_override(
            window=window, area=biggest, region=node_region(), space_data=biggest.spaces.active
        )

    def press(kind: str, tag: str, dx: int = 0) -> None:
        window.event_simulate(type=kind, value="PRESS", x=cx + dx, y=cy)
        say(f"     [{tag}] {kind}")

    def release(kind: str, tag: str, dx: int = 0) -> None:
        window.event_simulate(type=kind, value="RELEASE", x=cx + dx, y=cy)

    def select_one(tag: str) -> None:
        """A left click in the viewport, i.e. what the user does while picking."""
        press("LEFTMOUSE", tag, -60)
        release("LEFTMOUSE", tag, -60)

    # ------------------------------------------- part 1: the real hit tests (dot and row)
    def sweep_hit_test() -> None:
        """Ask the C++ query around the drawn node: the parameter's row, and nothing else.

        The box is computed in **view space** (what the editor's own hit test speaks:
        `node.location * UI_SCALE_FAC`, with `node.dimensions` already in view space), then
        converted to region pixels with `View2D.view_to_region()` - the same pair of spaces the
        editor itself uses (`node_resize_invoke()`).

        Two things are checked here, and they are the point of the row hit test:
        a point **on the connection dot** answers `hit_region == "socket"`, and a point on the
        parameter's **name or its input field** - well to the right of the dot - answers
        `hit_region == "row"`. The node's header, its empty right-hand area and open space must
        answer nothing at all.
        """
        as_node_editor()
        region = node_region()
        v2d = region.view2d
        scale = ui_scale()
        left = _c_round(selection.location_absolute[0] * scale)
        top = _c_round(selection.location_absolute[1] * scale)
        width, height = float(selection.dimensions[0]), float(selection.dimensions[1])
        if width <= 0.0 or height <= 0.0:
            check("the node is drawn (dimensions known)", (width, height), ("> 0", "> 0"))
            return
        say(
            f"     [sweep] node view box=({left:.0f},{top:.0f}) size=({width:.0f},{height:.0f}) "
            f"scale={scale:.3f} header={header_height():.0f}"
        )

        def hit_at(view_x: int, view_y: int) -> dict:
            region_x, region_y = v2d.view_to_region(view_x, view_y, clip=False)
            with override():
                return pdt_hit_test.pick_hit_test(bpy.context, region_x, region_y)

        hits: dict[str, int] = {}
        regions: dict[str, set] = {}
        rows: dict[str, set] = {}
        strings: set = set()
        nodes: set = set()
        row_hits_that_are_not_inputs = 0
        header_hits = 0
        far_hits = 0
        first_hit = None
        for view_x in range(int(left) - 30, int(left + width) + 10, 6):
            for view_y in range(int(top - height), int(top) + 6, 4):
                hit = hit_at(view_x, view_y)
                if not hit.get("found"):
                    continue
                identifier = hit["socket_identifier"]
                hits[identifier] = hits.get(identifier, 0) + 1
                regions.setdefault(identifier, set()).add(hit["hit_region"])
                rows.setdefault(identifier, set()).add(int(view_y))
                nodes.add(hit["node_name"])
                if hit["is_string"]:
                    strings.add(identifier)
                if hit["hit_region"] == "row" and not hit["is_input"]:
                    row_hits_that_are_not_inputs += 1
                if first_hit is None:
                    first_hit = (hit, view_x, view_y)
                if view_y > top - header_height():
                    header_hits += 1
        # Well outside the node.
        for dx, dy in ((200, 200), (-200, 120), (300, -80)):
            if hit_at(int(left) + dx, int(top) + dy).get("found"):
                far_hits += 1

        # A targeted probe of the **input field**: the same row as the socket, but far to the
        # right of the dot - that is where a user's cursor actually is when they hover the
        # parameter. The dot itself answers as "socket", the field as "row".
        selection_row_y = None
        for view_y in rows.get("Selection", set()):
            selection_row_y = int(view_y)
            break
        field_hit = None
        dot_hit = None
        if selection_row_y is not None:
            field_hit = hit_at(int(left + width * 0.6), selection_row_y)
            for view_x in range(int(left) - 20, int(left) + 20, 2):
                candidate = hit_at(view_x, selection_row_y)
                if candidate.get("found") and candidate.get("hit_region") == "socket":
                    dot_hit = candidate
                    break

        say(f"     [sweep] hits by socket: {hits}")
        say(f"     [sweep] how they were hit: { {k: sorted(v) for k, v in regions.items()} }")
        say(f"     [sweep] field probe: {field_hit}")
        check("the string parameter is found", "Selection" in hits, True)
        check("and so is the geometry input next to it", "Geometry" in hits, True)
        check("never another node", sorted(nodes), [selection.name])
        check("only the Selection socket is a string parameter", sorted(strings), ["Selection"])
        check(
            "each socket has its own row band",
            bool(rows.get("Selection")) and not (rows.get("Selection") & rows.get("Geometry", set())),
            True,
        )
        check(
            "the connection dot answers as 'socket'",
            dot_hit is not None and dot_hit["hit_region"] == "socket",
            True,
        )
        check(
            "the name/input field answers as 'row' (PICK-1)",
            field_hit is not None
            and field_hit.get("found")
            and field_hit.get("socket_identifier") == "Selection"
            and field_hit.get("hit_region") == "row",
            True,
        )
        check("the header band is never a hit", header_hits, 0)
        check("empty space is never a hit", far_hits, 0)
        check(
            "a *row* hit is always an input socket (never an output's row)",
            row_hits_that_are_not_inputs,
            0,
        )
        if first_hit is not None:
            hit, _view_x, _view_y = first_hit
            check("the hit names the node under the cursor", hit["node_name"], selection.name)
            check("and reports its geometry input", hit["has_geometry_input"], True)
        # Every socket that answers is one of that node's own sockets (`Selection` is also an
        # *output* name on this node - the boolean field - hence the `is_input` accounting).
        check(
            "every hit is one of the node's own sockets",
            all(identifier in {socket.identifier for socket in selection.inputs}
                | {socket.identifier for socket in selection.outputs}
                for identifier in hits),
            True,
        )

    # ---------------------------------------------------- part 2: the trigger and its pies
    def trigger_opens_the_pie() -> None:
        as_node_editor()
        pdt_hit_test.pick_hit_test = lambda context=None, region_x=None, region_y=None, ops=None: {
            "state": "ok",
            "found": True,
            "node_name": selection.name,
            "node_idname": selection.bl_idname,
            "socket_identifier": "Selection",
            "socket_name": "Selection",
            "socket_idname": "NodeSocketString",
            "is_string": True,
            "is_input": True,
            "has_geometry_input": True,
            "hit_region": "row",
        }
        with override():
            result = bpy.ops.pdt.selection_pick_modal()
        say(f"     [trigger] operator returned {result}")
        # `wm.call_menu_pie` reports "INTERFACE" once it has created the pop-up ("Handled but
        # not executed (popup menus)", `rna_enum_operator_return_items`), and "CANCELLED" when
        # the menu name is unknown - so this is the pie really opening, not just our code
        # running. The pop-up lives in the *screen's* region list, which RNA does not expose.
        check("the trigger opened the pie menu", result, {"INTERFACE"})
        check("the pending pick remembers the node", pdt_state.pending_pick.get("node"), selection)
        check("and the socket the cursor was on", pdt_state.pending_pick.get("socket_identifier"), "Selection")
        check(
            "and the seven element kinds for the pie",
            [entry["identifier"] for entry in pdt_state.pending_pick.get("domain_items", [])],
            list(pdt_constants.DOMAINS),
        )
        check("no operator took the modal stack yet", len(window.modal_operators), 0)
        check("no pick session started yet", bool(pdt_state.session), False)
        for name in (pdt_constants.DOMAIN_PIE_IDNAME, pdt_constants.INSTANCE_MODE_PIE_IDNAME):
            with override():
                opened = bpy.ops.wm.call_menu_pie(name=name)
            say(f"     [pie] {name} -> {opened}")
            check(f"'{name}' opens as a pie menu", opened, {"INTERFACE"})
            press("ESC", f"pie:{name}")
            release("ESC", f"pie:{name}")
        # Both menus are drawn as radial layouts; the slices are visible in
        # `qa|tests/_gui_pie_*.png` (gui_pie_shot.py), which is how the "all items in one
        # column" bug was found.
        check(
            "both pies build a radial layout (menu_pie)",
            all("menu_pie()" in __import__("inspect").getsource(menu.draw)
                for menu in (pdt_menus.PDT_MT_selection_domain_pie, pdt_menus.PDT_MT_selection_instance_mode_pie)),
            True,
        )

    def dismiss_the_pie() -> None:
        press("ESC", "trigger")
        release("ESC", "trigger")

    def after_dismiss() -> None:
        check("dismissing the pie starts nothing", bool(pdt_state.session), False)
        check("and no modal is left", len(window.modal_operators), 0)

    # ------------------------------------------- part 3/4: a slice starts the pick session
    def pending_for(node, socket_identifier="Selection"):
        pdt_state.pending_pick.clear()
        pdt_state.pending_pick.update(
            {
                "tree": tree,
                "node": node,
                "socket_identifier": socket_identifier,
                "domain_items": pdt_menus.domain_pie_entries(node, tree),
            }
        )

    def start_pick(domain: str, tag: str, operator: str = "selection_pick_domain", **kwargs) -> None:
        as_node_editor()
        pending_for(selection)
        with override():
            result = getattr(bpy.ops.pdt, operator)(domain=domain, **kwargs)
        say(f"     [{tag}] {operator} returned {result}")

    def session_open(label: str) -> None:
        check(f"{label}: session open", bool(pdt_state.session), True)
        check(f"{label}: in edit mode", bpy.context.mode, "EDIT_MESH")
        check(f"{label}: one modal", len(window.modal_operators), 1)
        check(f"{label}: the session's element kind", pdt_state.session.get("domain"), "VERTEX")
        # `begin_pick` rebuilds the selection from the parameter (which is empty here), so the
        # pick starts with nothing selected no matter what the object's own state was.
        check(f"{label}: starts with nothing selected", selected_now(), [])

    def cancelled(label: str) -> None:
        check(f"{label}: session closed", bool(pdt_state.session), False)
        check(f"{label}: left edit mode", bpy.context.mode, "OBJECT")
        check(f"{label}: modal ended", len(window.modal_operators), 0)
        check(
            f"{label}: temporary object removed",
            [o for o in bpy.data.objects if o.name.startswith("PDT_PICK")],
            [],
        )
        check(f"{label}: original object active", bpy.context.view_layer.objects.active.name, "CubeOwner")
        check(f"{label}: original object visible", obj.hide_get(), False)

    def report_failures() -> None:
        for label in _failures:
            say(f"  failed: {label}")

    def cancel_via_operator() -> None:
        """`pdt.selection_cancel` - how a paused pick is dropped without the modal (F3)."""
        with override():
            result = bpy.ops.pdt.selection_cancel()
        say(f"     [cancel] operator returned {result}")

    def quit_via_operator() -> None:
        bpy.ops.wm.quit_blender()

    def instance_mode_without_a_pick() -> None:
        pdt_state.pending_pick.clear()
        as_node_editor()
        # An operator that reports an ERROR raises in Python (`RuntimeError`), it does not just
        # return CANCELLED - so both outcomes mean "refused, nothing started".
        try:
            with override():
                result = bpy.ops.pdt.selection_pick_instance_mode(mode="REALIZED")
            refused = result == {"CANCELLED"}
        except RuntimeError as ex:
            say(f"     [instance-mode] refused with {ex}")
            refused = True
        check("a slice without a pending pick is refused", refused, True)
        check("and starts no session", bool(pdt_state.session), False)
        check("and leaves no modal behind", len(window.modal_operators), 0)

    def selected_now() -> list:
        return pdt_selection.read_selected_indices(None, "VERTEX")

    def probe_selection_state(tag: str) -> None:
        temp = bpy.data.objects.get(pdt_state.session.get("temp_object", ""))
        object_mode_selection = (
            [index for index, vertex in enumerate(temp.data.vertices) if vertex.select]
            if temp is not None
            else None
        )
        say(
            f"     [probe:{tag}] mode={bpy.context.mode} "
            f"active={getattr(getattr(bpy.context.view_layer, 'objects', None), 'active', None)} "
            f"select_mode={tuple(bpy.context.tool_settings.mesh_select_mode)} "
            f"edit={selected_now()} object={object_mode_selection} "
            f"preselected={pdt_state.session.get('preselected')}"
        )

    def select_programmatically(indices) -> None:
        """What the user does by hand: change the edit-mode selection of the temporary mesh."""
        temp = bpy.data.objects[pdt_state.session["temp_object"]]
        applied = pdt_selection._set_geometry_selection(temp.data, "MESH", "VERTEX", set(indices))
        say(f"     [select] {applied} element(s), now {selected_now()}")

    # ------------------------------------------------------------------ part 5: bad modal
    _broken: dict = {}

    def break_modal() -> None:
        _broken["original"] = pdt_pick.PDT_OT_selection_pick_domain._modal_impl

        def _raise(self, context, event):
            raise RuntimeError("gui test: deliberate failure")

        pdt_pick.PDT_OT_selection_pick_domain._modal_impl = _raise

    def repair_modal() -> None:
        original = _broken.get("original")
        if original is not None:
            pdt_pick.PDT_OT_selection_pick_domain._modal_impl = original

    steps = [
        # 1) the real query against a real node editor
        (0.6, lambda: sweep_hit_test()),
        # 2) the trigger: hit test -> pie -> dismissing it starts nothing
        (1.4, lambda: trigger_opens_the_pie()),
        (1.8, lambda: dismiss_the_pie()),
        (2.2, lambda: after_dismiss()),
        # 3) a pie slice + Esc
        (2.5, lambda: start_pick("VERTEX", "esc")),
        (2.8, lambda: session_open("esc")),
        (2.9, lambda: probe_selection_state("esc")),
        (3.0, lambda: select_one("esc")),
        (3.1, lambda: check("esc: a viewport click does not end the modal", len(window.modal_operators), 1)),
        (3.3, lambda: press("ESC", "esc")),
        (3.7, lambda: cancelled("esc")),
        # 4) a pie slice + Right-Click
        (4.0, lambda: start_pick("VERTEX", "rmb")),
        (4.3, lambda: select_one("rmb")),
        (4.6, lambda: press("RIGHTMOUSE", "rmb")),
        (4.8, lambda: release("RIGHTMOUSE", "rmb")),
        (5.2, lambda: cancelled("rmb")),
        # 5) a pie slice + Enter applies what is selected
        (5.5, lambda: start_pick("VERTEX", "enter")),
        (5.7, lambda: select_programmatically({3})),
        (6.1, lambda: press("RET", "enter")),
        (6.5, lambda: check("enter: session closed", bool(pdt_state.session), False)),
        (6.6, lambda: check("enter: modal ended", len(window.modal_operators), 0)),
        (6.7, lambda: check("enter: parameter written", selection.inputs["Selection"].default_value, "3")),
        # 6) the instance-mode slice needs a pending pick
        (7.0, lambda: instance_mode_without_a_pick()),
        # 7) an error inside the modal ends it, the session survives
        (7.3, lambda: start_pick("VERTEX", "broken")),
        (7.6, lambda: break_modal()),
        (7.8, lambda: press("ESC", "broken")),
        (8.3, lambda: check("broken: modal ended", len(window.modal_operators), 0)),
        (8.5, lambda: check("broken: session kept", bool(pdt_state.session), True)),
        (8.7, lambda: repair_modal()),
        (8.9, lambda: cancel_via_operator()),
        (9.2, lambda: check("broken: cancelled afterwards", bool(pdt_state.session), False)),
        (9.4, lambda: check(
            "broken: no leftovers",
            [o for o in bpy.data.objects if o.name.startswith("PDT_PICK")],
            [],
        )),
        # 8) keymap discipline after the event loop merged the keyconfigs
        (9.8, lambda: check(
            "the picker item is still the only unbound pdt item",
            [item.type for item in addon_picker_items()],
            ["NONE"],
        )),
        (9.9, lambda: check(
            "the add-on wrote nothing into the user keyconfig",
            user_pdt_items(),
            user_items_before,
        )),
        (10.0, lambda: check(
            "user keymaps intact after the merge",
            all(
                item_count(user_keyconfig, name) >= item_count(default_keyconfig, name)
                for name in ("3D View", "Node Editor")
            ),
            True,
        )),
        (10.3, lambda: say(f"GUI_TEST_RESULT: {'FAIL' if _failures else 'PASS'} "
                           f"({_checks} checks, {len(_failures)} failure(s))")),
        (10.6, lambda: report_failures()),
        (11.0, lambda: quit_via_operator()),
    ]
    started = time.monotonic()

    def guarded(index: int, fn):
        """Run one step, and turn an exception into a *failure* instead of losing the step.

        `bpy.app.timers` callbacks that raise only print a traceback and are dropped, so an
        aborted step used to look like a shorter (still green) run - which is exactly how a
        wrongly patched hit test hid six checks for a whole round.
        """

        def run():
            try:
                fn()
            except Exception as ex:  # noqa: BLE001 - record it, keep the run going
                traceback.print_exc()
                check(f"step #{index} ran without raising", f"{type(ex).__name__}: {ex}", None)
            return None

        return run

    for index, (delay, fn) in enumerate(steps):
        bpy.app.timers.register(guarded(index, fn), first_interval=delay)
    say(f"     [setup] {len(steps)} steps queued at t={started:.2f}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        try:
            with open(_log_path(), "a", encoding="utf-8") as handle:
                handle.write(traceback.format_exc())
        except Exception:  # noqa: BLE001
            pass
        quit_soon(3.0)

