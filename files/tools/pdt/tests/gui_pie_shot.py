"""Screenshot probe: open each pie menu in a real window and save a picture of it.

    blender.exe --enable-event-simulate --python tools\\pdt\\tests\\gui_pie_shot.py

Not a test (it asserts almost nothing): it exists so the radial layout can be *looked at*
while tuning it - a pie whose items are all stacked on top of each other is obvious in a
screenshot and invisible to an assertion. It writes `tests/_gui_pie_<name>.png` and quits.
"""

from __future__ import annotations

import os
import sys
import traceback

import bpy

sys.path.insert(0, bpy.utils.resource_path("SYSTEM") + "/scripts/addons_core")
sys.path.insert(0, bpy.utils.resource_path("SYSTEM") + "/scripts/modules")

import pdt_geo_select as pdt  # the package: bl_info + register
from pdt_geo_select.utils import constants as pdt_constants
from pdt_geo_select import menus as pdt_menus
from pdt_geo_select import state as pdt_state  # noqa: E402

pdt.register_once()

_HERE = os.path.dirname(os.path.abspath(__file__))


def shot(name: str) -> None:
    path = os.path.join(_HERE, f"_gui_pie_{name}.png")
    try:
        bpy.ops.screen.screenshot(filepath=path)
        print(f"SHOT {name}: {path}", flush=True)
    except Exception as ex:  # noqa: BLE001 - a probe must not die on the picture
        print(f"SHOT {name} FAILED: {ex}", flush=True)


def quit_soon(delay: float) -> None:
    def _quit():
        bpy.ops.wm.quit_blender()
        return None

    bpy.app.timers.register(_quit, first_interval=delay)


def main() -> None:
    quit_soon(30.0)
    window = bpy.context.window or bpy.context.window_manager.windows[0]
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    tree = bpy.data.node_groups.new(name="PDT Pie Shot", type="GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_in = tree.nodes.new("NodeGroupInput")
    group_in.location = (-400.0, 0.0)
    selection = tree.nodes.new("GeometryNodePDTSelection")
    selection.location = (0.0, 0.0)
    selection.inputs["Selection"].default_value = ""
    group_out = tree.nodes.new("NodeGroupOutput")
    group_out.location = (400.0, 0.0)
    tree.links.new(group_in.outputs["Geometry"], selection.inputs["Geometry"])
    tree.links.new(selection.outputs["Geometry"], group_out.inputs["Geometry"])

    mesh = bpy.data.meshes.new("cube")
    mesh.from_pydata(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0),
         (0.0, 0.0, 1.0), (1.0, 0.0, 1.0), (1.0, 1.0, 1.0), (0.0, 1.0, 1.0)],
        [], [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)],
    )
    mesh.update()
    obj = bpy.data.objects.new("CubeOwner", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.modifiers.new(name="GeometryNodes", type="NODES").node_group = tree
    bpy.context.view_layer.objects.active = obj

    biggest = max(window.screen.areas, key=lambda area: area.width * area.height)
    biggest.type = "NODE_EDITOR"
    space = biggest.spaces.active
    space.node_tree = tree
    tree.nodes.active = selection
    for node in tree.nodes:
        node.select = node is selection
    biggest.tag_redraw()
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)

    cx = biggest.x + biggest.width // 2
    cy = biggest.y + biggest.height // 2

    def settle() -> None:
        """Dismiss the startup splash and park the pointer where the pie should appear.

        A pie spawns at the pointer position the event system last saw, so the probe has to
        put it somewhere explicit - otherwise the picture is of wherever the real mouse is.
        """
        window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=cx, y=cy)
        window.event_simulate(type="LEFTMOUSE", value="PRESS", x=cx, y=cy)
        window.event_simulate(type="LEFTMOUSE", value="RELEASE", x=cx, y=cy)
        biggest.tag_redraw()

    # The pie draws from `_pending_pick`, which the trigger fills. No node editor context is
    # needed for the *slice list*, so it is filled directly.
    pdt_state.pending_pick.clear()
    pdt_state.pending_pick.update(
        {
            "tree": tree,
            "node": selection,
            "socket_identifier": "Selection",
            "domain_items": pdt_menus.domain_pie_entries(selection, tree),
        }
    )

    def open_domain_pie() -> None:
        bpy.ops.wm.call_menu_pie(name=pdt_constants.DOMAIN_PIE_IDNAME)

    def domain_shot() -> None:
        shot("domain")

    def close_pie() -> None:
        window.event_simulate(type="ESC", value="PRESS", x=cx, y=cy)

    def open_instance_pie() -> None:
        bpy.ops.wm.call_menu_pie(name=pdt_constants.INSTANCE_MODE_PIE_IDNAME)

    def instance_shot() -> None:
        shot("instance")

    steps = [
        (0.4, settle),
        (0.8, open_domain_pie),
        (1.2, domain_shot),
        (1.6, close_pie),
        (2.0, open_instance_pie),
        (2.4, instance_shot),
        (2.8, close_pie),
        (3.2, lambda: bpy.ops.wm.quit_blender()),
    ]
    for delay, fn in steps:
        bpy.app.timers.register(fn, first_interval=delay)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        quit_soon(3.0)
