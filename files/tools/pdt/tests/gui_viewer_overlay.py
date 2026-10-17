"""EVO-1..EVO-6: the enhanced viewer overlay, measured as pixels in a real window.

    blender.exe --enable-event-simulate --factory-startup --python tools/pdt/tests/gui_viewer_overlay.py

The drawing cannot be exercised headless, so the acceptance criteria of
`docs/manual/operators/viewer_overlay.md` are counted from screenshots of the 3D viewport
(the same measured-not-looked-at discipline as `gui_mmb_node_info.py`):

  EVO-1  only the object of the active viewer gets lines (a second object with the same
         attribute stays untouched)
  EVO-2  the switch in Overlay > Viewer Node turns the lines on and off
  EVO-3  a Float3 attribute is drawn as yellow direction lines, and is no longer painted as
         colours while the lines are shown
  EVO-4  a Quaternion attribute is drawn as red / green / blue axes
  EVO-5  a Float4x4 attribute's axes follow its decomposed per-axis scale
  EVO-6  above the element budget nothing is drawn (and the frame still renders)

Prints `GUI_TEST_RESULT: PASS|FAIL` for `run_tests.ps1 -Gui`, plus one screenshot per phase
next to this file's `_shots` directory for the visual record.
"""

from __future__ import annotations

import os
import sys
import traceback

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
# Screenshots are test artefacts: keep them next to the running binary (outside the git
# checkout, and writable - the dsh file sandbox denies writes under the system temp dir).
SHOTS = os.environ.get("PDT_VIEWER_OVERLAY_SHOTS") or os.path.join(
    os.path.dirname(bpy.app.binary_path), "viewer_overlay_shots")

CHECKS = 0
FAILURES: list[str] = []
#: Pixels that differ between two renders of the *same* state (viewport anti-aliasing jitters its
#: samples between frames, and the screenshot catches that). Measured in `main()`; every
#: "nothing was drawn" assertion is compared against this floor instead of against zero.
NOISE_FLOOR = 0
# A second grid, far to the right, has the same attribute but no viewer: nothing may be drawn
# there (EVO-1). It also bounds the left/right split used to measure that.
SPLIT_X = 0.5


def nothing_limit():
    """Largest pixel delta still considered "nothing was drawn"."""
    return max(1000, NOISE_FLOOR * 4)


def check(label, actual, expected):
    global CHECKS
    CHECKS += 1
    ok = actual == expected
    print(f"{'OK  ' if ok else 'FAIL'} {label}"
          + ("" if ok else f"   got {actual!r}, want {expected!r}"), flush=True)
    if not ok:
        FAILURES.append(label)


def check_true(label, value):
    check(label, bool(value), True)


def _quit():
    try:
        bpy.ops.wm.quit_blender()
    except Exception:  # noqa: BLE001
        pass
    return None


# --------------------------------------------------------------------------------------
# scene
# --------------------------------------------------------------------------------------

def wipe():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for tree in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(tree)


def store_attribute(tree, geometry_socket, name, data_type, value_socket=None, location=(0, 0)):
    """`Store Named Attribute` node; `value_socket` is linked to its Value input when given."""
    store = tree.nodes.new("GeometryNodeStoreNamedAttribute")
    store.location = location
    store.data_type = data_type
    store.domain = "POINT"
    store.inputs["Name"].default_value = name
    tree.links.new(geometry_socket, store.inputs["Geometry"])
    if value_socket is not None:
        tree.links.new(value_socket, store.inputs["Value"])
    return store


def make_tree(name, data_type, vector_multiplier=3.0):
    """A subdivided cube with a `vel` / `rot` / `mat` point attribute and a viewer node.

    `vector_multiplier` scales the Float3 values: 3.0 gives |v| = 3 (so the two EVO-3 modes are
    distinguishable), 0.0 makes every value degenerate (EVO-7).
    """
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    out = tree.nodes.new("NodeGroupOutput")
    out.location = (900, 0)

    cube = tree.nodes.new("GeometryNodeMeshCube")
    cube.location = (-900, 0)
    subdiv = tree.nodes.new("GeometryNodeSubdivisionSurface")
    subdiv.location = (-700, 0)
    subdiv.inputs["Level"].default_value = 1
    tree.links.new(cube.outputs["Mesh"], subdiv.inputs["Mesh"])

    position = tree.nodes.new("GeometryNodeInputPosition")
    position.location = (-700, -260)

    normal = tree.nodes.new("GeometryNodeInputNormal")
    normal.location = (-700, -440)
    normalize = tree.nodes.new("ShaderNodeVectorMath")
    normalize.operation = "NORMALIZE"
    normalize.location = (-540, -440)
    tree.links.new(normal.outputs["Normal"], normalize.inputs[0])

    if data_type == "FLOAT_VECTOR":
        # |v| = 3 for every point: with Vector Scale 0.4 that makes EVO-3's two modes
        # measurable - Direction draws 0.4 long lines, Vector draws 1.2 long ones.
        scale = tree.nodes.new("ShaderNodeVectorMath")
        scale.operation = "MULTIPLY"
        scale.location = (-430, -440)
        scale.inputs[1].default_value = (vector_multiplier,) * 3
        tree.links.new(normalize.outputs["Vector"], scale.inputs[0])
        store = store_attribute(tree, subdiv.outputs["Mesh"], "vel", "FLOAT_VECTOR",
                                scale.outputs["Vector"], (-380, 0))
    else:
        align = tree.nodes.new("FunctionNodeAlignRotationToVector")
        align.location = (-380, -300)
        align.inputs["Factor"].default_value = 1.0
        tree.links.new(position.outputs["Position"], align.inputs["Vector"])
        store = store_attribute(tree, subdiv.outputs["Mesh"], "rot", "QUATERNION",
                                align.outputs["Rotation"], (-380, 0))

    tree.links.new(store.outputs["Geometry"], out.inputs["Geometry"])

    viewer = tree.nodes.new("GeometryNodeViewer")
    viewer.name = "Viewer"
    viewer.location = (400, -420)
    viewer.viewer_items.new(socket_type="GEOMETRY", name="Geometry")
    viewer.viewer_items.new(socket_type="VECTOR" if data_type == "FLOAT_VECTOR" else "ROTATION",
                            name="value")
    named = tree.nodes.new("GeometryNodeInputNamedAttribute")
    named.location = (0, -460)
    named.data_type = data_type
    named.inputs["Name"].default_value = "vel" if data_type == "FLOAT_VECTOR" else "rot"
    viewer_geometry = next(s for s in viewer.inputs if s.bl_idname == "NodeSocketGeometry")
    viewer_value = next(s for s in viewer.inputs
                        if s.identifier not in {"__extend__", viewer_geometry.identifier})
    tree.links.new(store.outputs["Geometry"], viewer_geometry)
    tree.links.new(named.outputs["Attribute"], viewer_value)
    return tree, viewer, store


def make_object(name, tree, location):
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new("GeometryNodes", "NODES")
    modifier.node_group = tree
    obj.location = location
    return obj


def build_scene():
    """Returns (viewer_object, plain_object, tree, viewer, extra_nodes)."""
    wipe()
    os.makedirs(SHOTS, exist_ok=True)
    tree, viewer, store = make_tree("Viewer Overlay Test", "FLOAT_VECTOR")
    # Far enough apart that the viewer object's lines (0.4..1.2 long) can never reach the right
    # half of the screen, where the plain object sits (measured before: at +/-1.6 the line tips
    # crossed the screen midline by 148 px and broke the EVO-1 check).
    obj = make_object("Viewer Object", tree, (-3.0, 0.0, 0.0))
    plain = make_object("Plain Object", tree, (3.0, 0.0, 0.0))
    # The plain object shares the tree, so it renders the same attribute - but it is not the
    # viewer's object, so EVO-1 says nothing may be drawn on it.
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj, plain, tree, viewer, store


def build_matrix_scene(scale):
    """A second tree whose Float4x4 attribute has the given uniform scale."""
    wipe()
    tree = bpy.data.node_groups.new("Viewer Overlay Matrix", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    out = tree.nodes.new("NodeGroupOutput")
    out.location = (900, 0)
    cube = tree.nodes.new("GeometryNodeMeshCube")
    cube.location = (-900, 0)
    subdiv = tree.nodes.new("GeometryNodeSubdivisionSurface")
    subdiv.location = (-700, 0)
    subdiv.inputs["Level"].default_value = 1
    tree.links.new(cube.outputs["Mesh"], subdiv.inputs["Mesh"])
    combine = tree.nodes.new("FunctionNodeCombineTransform")
    combine.location = (-380, -300)
    combine.inputs["Scale"].default_value = scale
    store = store_attribute(tree, subdiv.outputs["Mesh"], "mat", "FLOAT4X4",
                            combine.outputs["Transform"], (-380, 0))
    tree.links.new(store.outputs["Geometry"], out.inputs["Geometry"])
    viewer = tree.nodes.new("GeometryNodeViewer")
    viewer.name = "Viewer"
    viewer.viewer_items.new(socket_type="GEOMETRY", name="Geometry")
    viewer.viewer_items.new(socket_type="MATRIX", name="value")
    named = tree.nodes.new("GeometryNodeInputNamedAttribute")
    named.data_type = "FLOAT4X4"
    named.inputs["Name"].default_value = "mat"
    viewer_geometry = next(s for s in viewer.inputs if s.bl_idname == "NodeSocketGeometry")
    viewer_value = next(s for s in viewer.inputs
                        if s.identifier not in {"__extend__", viewer_geometry.identifier})
    tree.links.new(store.outputs["Geometry"], viewer_geometry)
    tree.links.new(named.outputs["Attribute"], viewer_value)
    obj = make_object("Viewer Object", tree, (0.0, 0.0, 0.0))
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj, tree, viewer


def build_instance_matrix_scene(rotation_z_deg=0.0, object_rotation_z_deg=0.0, rotation_only=False):
    """One point instancing a small cube, viewing the Instance domain's `instance_transform`.

    The shape of the scene the bug was reported with (`test_matrix.blend`, 2026-09-16):
    `Instance on Points` -> `instance_transform` -> `Capture Attribute` -> Viewer (Matrix).
    A single point keeps the measurement unambiguous (one tripod, at the world origin); the
    reported file instances a cube, which produces the same tripods eight times.

    `rotation_only` views the same instance transform as its `Quaternion` part instead (EVO-4),
    which goes through `append_quaternion_axes` - the same direction conversion.
    """
    import math

    wipe()
    tree = bpy.data.node_groups.new("Viewer Overlay Instance Matrix", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    out = tree.nodes.new("NodeGroupOutput")
    out.location = (900, 0)

    line = tree.nodes.new("GeometryNodeMeshLine")
    line.location = (-900, 0)
    line.inputs["Count"].default_value = 1

    cube = tree.nodes.new("GeometryNodeMeshCube")
    cube.location = (-900, -300)
    cube.inputs["Size"].default_value = (0.3, 0.3, 0.3)

    instance = tree.nodes.new("GeometryNodeInstanceOnPoints")
    instance.location = (-600, 0)
    instance.inputs["Rotation"].default_value = (0.0, 0.0, math.radians(rotation_z_deg))
    tree.links.new(line.outputs["Mesh"], instance.inputs["Points"])
    tree.links.new(cube.outputs["Mesh"], instance.inputs["Instance"])

    named = tree.nodes.new("GeometryNodeInputNamedAttribute")
    named.location = (-600, -420)
    named.data_type = "FLOAT4X4"
    named.inputs["Name"].default_value = "instance_transform"

    item_type = "ROTATION" if rotation_only else "MATRIX"
    value_socket = named.outputs["Attribute"]
    if rotation_only:
        separate = tree.nodes.new("FunctionNodeSeparateTransform")
        separate.location = (-450, -420)
        tree.links.new(named.outputs["Attribute"], separate.inputs["Transform"])
        value_socket = separate.outputs["Rotation"]

    capture = tree.nodes.new("GeometryNodeCaptureAttribute")
    capture.location = (-300, 0)
    capture.capture_items.new(item_type, "value")
    capture.domain = "INSTANCE"
    tree.links.new(instance.outputs["Instances"], capture.inputs["Geometry"])
    tree.links.new(value_socket, capture.inputs["value"])
    tree.links.new(capture.outputs["Geometry"], out.inputs["Geometry"])

    viewer = tree.nodes.new("GeometryNodeViewer")
    viewer.name = "Viewer"
    viewer.location = (300, -500)
    # The viewer decides which domain `.viewer` is captured on; the reported file has INSTANCE
    # (the default POINT would capture nothing here - the geometry set holds only instances).
    viewer.domain = "INSTANCE"
    viewer.viewer_items.new(socket_type="GEOMETRY", name="Geometry")
    viewer.viewer_items.new(socket_type=item_type, name="value")
    viewer_geometry = next(s for s in viewer.inputs if s.bl_idname == "NodeSocketGeometry")
    viewer_value = next(s for s in viewer.inputs
                        if s.identifier not in {"__extend__", viewer_geometry.identifier})
    tree.links.new(capture.outputs["Geometry"], viewer_geometry)
    tree.links.new(capture.outputs["Attribute"], viewer_value)

    obj = make_object("Viewer Object", tree, (0.0, 0.0, 0.0))
    obj.rotation_euler = (0.0, 0.0, math.radians(object_rotation_z_deg))
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj, tree, viewer


def build_budget_scene(elements):
    """A grid with more points than the overlay's element budget."""
    wipe()
    tree = bpy.data.node_groups.new("Viewer Overlay Budget", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    out = tree.nodes.new("NodeGroupOutput")
    grid = tree.nodes.new("GeometryNodeMeshGrid")
    side = int(elements ** 0.5) + 1
    grid.inputs["Size X"].default_value = 4.0
    grid.inputs["Size Y"].default_value = 4.0
    grid.inputs["Vertices X"].default_value = side
    grid.inputs["Vertices Y"].default_value = side
    position = tree.nodes.new("GeometryNodeInputPosition")
    normalize = tree.nodes.new("ShaderNodeVectorMath")
    normalize.operation = "NORMALIZE"
    tree.links.new(position.outputs["Position"], normalize.inputs[0])
    store = store_attribute(tree, grid.outputs["Mesh"], "vel", "FLOAT_VECTOR",
                            normalize.outputs["Vector"], (-380, 0))
    tree.links.new(store.outputs["Geometry"], out.inputs["Geometry"])
    viewer = tree.nodes.new("GeometryNodeViewer")
    viewer.name = "Viewer"
    viewer.viewer_items.new(socket_type="GEOMETRY", name="Geometry")
    viewer.viewer_items.new(socket_type="VECTOR", name="value")
    named = tree.nodes.new("GeometryNodeInputNamedAttribute")
    named.data_type = "FLOAT_VECTOR"
    named.inputs["Name"].default_value = "vel"
    viewer_geometry = next(s for s in viewer.inputs if s.bl_idname == "NodeSocketGeometry")
    viewer_value = next(s for s in viewer.inputs
                        if s.identifier not in {"__extend__", viewer_geometry.identifier})
    tree.links.new(store.outputs["Geometry"], viewer_geometry)
    tree.links.new(named.outputs["Attribute"], viewer_value)
    obj = make_object("Viewer Object", tree, (0.0, 0.0, 0.0))
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj, tree, viewer


# --------------------------------------------------------------------------------------
# real-window plumbing
# --------------------------------------------------------------------------------------

def activate_viewer(window, tree, viewer):
    """Activate through the real operator, not by writing flags by hand (EVO-1's premise)."""
    areas = [area for area in window.screen.areas if area.type == "NODE_EDITOR"]
    if not areas:
        candidates = [a for a in window.screen.areas if a.type in {"OUTLINER", "DOPESHEET_EDITOR",
                                                                   "PROPERTIES"}]
        if not candidates:
            return False
        candidates[0].type = "NODE_EDITOR"
        areas = [candidates[0]]
    area = areas[0]
    space = area.spaces.active
    space.tree_type = "GeometryNodeTree"
    space.node_tree = tree
    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
    for node in tree.nodes:
        node.select = node == viewer
    tree.nodes.active = viewer
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.node.view_all()
        result = bpy.ops.node.activate_viewer()
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
    return "FINISHED" in repr(result)


def set_prop(owner, name, value):
    """Set an RNA property if this build has it (the test must not die on a renamed toggle)."""
    if hasattr(owner, name):
        setattr(owner, name, value)
        return True
    print(f"  (skipped: {type(owner).__name__}.{name} does not exist)", flush=True)
    return False


def setup_view3d(window):
    areas = [area for area in window.screen.areas if area.type == "VIEW_3D"]
    if not areas:
        return None, None
    area = max(areas, key=lambda a: a.width * a.height)
    space = area.spaces.active
    overlay = space.overlay
    space.shading.type = "SOLID"
    set_prop(space, "show_viewer", True)
    set_prop(space, "show_gizmo_navigate", False)
    set_prop(space, "show_gizmo", False)
    overlay.show_overlays = True
    overlay.show_viewer_attribute = True
    set_prop(overlay, "show_viewer_vector", True)
    overlay.viewer_attribute_opacity = 1.0
    overlay.show_viewer_text = False
    # Keep the measurement clean: the floor grid and its coloured axes would be counted.
    set_prop(overlay, "show_floor", False)
    set_prop(overlay, "show_axis_x", False)
    set_prop(overlay, "show_axis_y", False)
    set_prop(overlay, "show_cursor", False)
    set_prop(overlay, "show_object_origins", False)
    space.region_3d.view_perspective = "PERSP"
    # Baseline the noise: with viewport AA off, two renders of the same state are identical.
    set_prop(bpy.context.scene.display, "viewport_aa", "OFF")
    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.view3d.view_selected()
        # The selection outline is orange - it would be counted as "coloured" pixels, so the
        # measurement happens with nothing selected (measured: 1514 px of outline before this).
        bpy.ops.object.select_all(action="DESELECT")
    space.region_3d.view_distance *= 1.15
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
    return area, region


def shot(area, region, window, name):
    path = os.path.join(SHOTS, f"{name}.png")
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=path)
    return path


def image_rgb(path):
    """RGB of a screenshot in displayed (sRGB) values."""
    import numpy as np

    image = bpy.data.images.load(path, check_existing=False)
    image.colorspace_settings.name = "Non-Color"
    image.reload()
    width, height = image.size
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    rgb = pixels.reshape(height, width, 4)[:, :, :3].copy()
    bpy.data.images.remove(image)
    # `Image.pixels` starts at the bottom-left; blank the top-right corner where the
    # navigation gizmo lives (it is drawn with the same axis colours).
    rgb[height - int(height * 0.15):, int(width * 0.85):] = 0.0
    return rgb


def coloured_mask(rgb):
    """Boolean mask of pixels with a visible hue."""
    return (rgb.max(axis=2) - rgb.min(axis=2)) > 0.25


def coloured_pixels(rgb):
    """Pixels with a visible hue. Used for the "colour overlay is / is not there" checks."""
    return int(coloured_mask(rgb).sum())


def draw_mask(rgb_length_zero, rgb_drawn, threshold=0.02):
    """Pixels that changed when the overlay was given a non-zero length.

    This is how the drawn geometry is measured: rendering the *same* frame with length 0 and with
    the test length isolates exactly the lines / axes, and is independent of the view transform,
    the theme and whatever else the viewport draws. Measured before this change: the linear
    yellow `(1.0, 0.85, 0.25)` reaches the screenshot as `(1.0, 0.9, 0.5)` (AgX desaturates), so
    a hardcoded colour threshold was measuring the view transform instead of the feature.
    """
    import numpy as np

    return np.abs(rgb_drawn - rgb_length_zero).max(axis=2) > threshold


def mask_stats(mask, rgb):
    """How much was drawn, where, and with which dominant channel."""
    height, width = mask.shape
    selected = rgb[mask]
    split = int(width * SPLIT_X)

    def dominant(channel):
        if selected.shape[0] == 0:
            return 0
        others = [c for c in range(3) if c != channel]
        return int(((selected[:, channel] - selected[:, others].max(axis=1)) > 0.05).sum())

    return {
        "drawn": int(mask.sum()),
        "drawn_left": int(mask[:, :split].sum()),
        "drawn_right": int(mask[:, split:].sum()),
        "red": dominant(0),
        "green": dominant(1),
        "blue": dominant(2),
    }


def render(area, region, window, name):
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=3)
    rgb = image_rgb(shot(area, region, window, name))
    print(f"  [{name}] coloured_px={coloured_pixels(rgb)}", flush=True)
    return rgb


def view_top_ortho(window, area, region, distance=2.0):
    """Look straight down at the origin: a Z rotation is then a plain screen rotation."""
    space = area.spaces.active
    rv3d = space.region_3d
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.view3d.view_axis(type="TOP")
        bpy.ops.object.select_all(action="DESELECT")
    rv3d.view_perspective = "ORTHO"
    rv3d.view_location = (0.0, 0.0, 0.0)
    rv3d.view_distance = distance
    # The orthographic view has its own grid (`show_ortho_grid`), and a grid line drawn *over* an
    # axis (both through the origin) washes its colour out: measured at rotation 0 the X axis came
    # out grey instead of red where it coincided with the grid, which left only 109 px to measure
    # instead of the full 729 px. `setup_view3d` only turns off the perspective floor grid.
    set_prop(space.overlay, "show_ortho_grid", False)
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
    return rv3d


def screen_angle_deg(rv3d, world_dir):
    """Where a world direction points on screen, in `image_rgb`'s convention (+y is up).

    The expectation is derived from the actual view matrix instead of being hardcoded, so the
    check keeps its meaning if the view is not exactly axis aligned.
    """
    import math

    from mathutils import Vector

    camera_to_world = rv3d.view_matrix.inverted()
    right = Vector((camera_to_world[0][0], camera_to_world[1][0], camera_to_world[2][0]))
    up = Vector((camera_to_world[0][1], camera_to_world[1][1], camera_to_world[2][1]))
    direction = Vector(world_dir)
    return math.degrees(math.atan2(direction.dot(up), direction.dot(right))) % 180.0


def axis_mask(mask, rgb, channel):
    """`mask` restricted to the pixels whose `channel` dominates (how an axis colour is told
    apart: the theme's X/Y/Z colours survive the AgX view transform as "this channel is
    brighter", not as a fixed RGB value)."""
    import numpy as np

    others = [c for c in range(3) if c != channel]
    dominant = (rgb[:, :, channel] - rgb[:, :, others].max(axis=2)) > 0.05
    return mask & dominant


def axis_angle_deg(mask, rgb, channel):
    """Screen angle (mod 180) of the drawn pixels whose `channel` dominates, by PCA.

    A tripod axis is a straight segment, so the principal axis of its pixels is its direction -
    which needs no knowledge of where the tripod origin is, and stays valid when the far end is
    clipped by the viewport.
    """
    import math

    import numpy as np

    selected = axis_mask(mask, rgb, channel)
    ys, xs = np.nonzero(selected)
    if xs.size < 20:
        return None, int(selected.sum())
    points = np.stack([xs - xs.mean(), ys - ys.mean()], axis=1).astype(np.float64)
    covariance = points.T @ points / points.shape[0]
    _values, vectors = np.linalg.eigh(covariance)
    principal = vectors[:, -1]
    return math.degrees(math.atan2(principal[1], principal[0])) % 180.0, int(selected.sum())


def axis_length_px(mask, rgb, channel, origin_px):
    """Distance from the tripod origin to the farthest drawn pixel of that axis."""
    import numpy as np

    selected = axis_mask(mask, rgb, channel)
    ys, xs = np.nonzero(selected)
    if xs.size == 0:
        return 0.0
    return float(np.hypot(xs - origin_px[0], ys - origin_px[1]).max())



def angle_gap(first, second):
    """Smallest difference between two screen angles, both taken mod 180 (an axis has no sign)."""
    difference = abs((first - second) % 180.0)
    return min(difference, 180.0 - difference)


# --------------------------------------------------------------------------------------
# phases
# --------------------------------------------------------------------------------------

def phase_scope_and_vectors(window, area, region, obj, plain, tree, viewer):
    if not activate_viewer(window, tree, viewer):
        check_true("EVO-1 viewer node could be activated through the operator", False)
        return
    check_true("EVO-1 viewer node activated through the operator", True)

    overlay = area.spaces.active.overlay
    overlay.viewer_vector_scale = 0.4

    # Today's behaviour (colour overlay), for the "colours come back" comparison.
    overlay.show_viewer_vector = False
    colours = render(area, region, window, "evo3_colour_overlay")

    # EVO-3 mode 1: every line has unit length (times the user scale).
    overlay.show_viewer_vector = True
    overlay.viewer_vector_mode = "DIRECTION"
    overlay.viewer_vector_scale = 0.0
    direction_empty = render(area, region, window, "evo3_direction_zero")
    overlay.viewer_vector_scale = 0.4
    direction = render(area, region, window, "evo3_direction_mode")
    direction_stats = mask_stats(draw_mask(direction_empty, direction), direction)

    # EVO-3 mode 2 (the default): the length follows |v| - 3x the unit length in this scene.
    overlay.viewer_vector_mode = "VECTOR"
    overlay.viewer_vector_scale = 0.0
    vector_empty = render(area, region, window, "evo3_vector_zero")
    overlay.viewer_vector_scale = 0.4
    vector = render(area, region, window, "evo3_vector_mode")
    vector_stats = mask_stats(draw_mask(vector_empty, vector), vector)
    print(f"  [evo3] direction={direction_stats} vector={vector_stats}", flush=True)

    check_true("EVO-3 Float3 draws direction lines", direction_stats["drawn"] > 300)
    check_true("EVO-3 Vector mode draws lines", vector_stats["drawn"] > 300)
    check_true("EVO-3 Vector mode lines follow the magnitude (longer than Direction mode)",
               vector_stats["drawn"] > direction_stats["drawn"] * 1.8)
    # EVO-1: only the viewer's object is drawn (the plain object sits in the right half). The
    # right half may still show the noise floor, so it is compared against that and against the
    # left half's much larger count.
    check_true("EVO-1 lines are drawn on the viewer's object", vector_stats["drawn_left"] > 300)
    check_true("EVO-1 nothing is drawn on the other object",
               vector_stats["drawn_right"] < nothing_limit()
               and vector_stats["drawn_left"] > vector_stats["drawn_right"] * 5)
    # EVO-3: while the lines are shown the attribute is no longer painted as surface colours.
    # The lines themselves are coloured pixels, so they are subtracted with the diff mask; what
    # is left must be back at the "nothing drawn" level.
    line_mask = draw_mask(vector_empty, vector)
    baseline = coloured_pixels(vector_empty)
    colours_only = coloured_pixels(colours)
    surface_after = int((coloured_mask(vector) & ~line_mask).sum())
    print(f"  [evo3] coloured: baseline={baseline} colour-overlay={colours_only} "
          f"surface-after={surface_after} lines={int(line_mask.sum())}", flush=True)
    check_true("EVO-3 colours are suppressed while lines are drawn",
               surface_after < baseline + (colours_only - baseline) * 0.5)
    check_true("EVO-3 the colour overlay was really there before (test premise)",
               colours_only - baseline > 2000)

    # EVO-2: with the switch off the length parameter must do nothing at all, and the viewport
    # must look like it did before this feature existed.
    overlay.show_viewer_vector = False
    overlay.viewer_vector_scale = 0.0
    off_zero = render(area, region, window, "evo2_switch_off_zero")
    overlay.viewer_vector_scale = 1.0
    off_full = render(area, region, window, "evo2_switch_off")
    check_true("EVO-2 the switch turns the lines off",
               int(draw_mask(off_zero, off_full).sum()) < nothing_limit())
    check_true("EVO-2 the colour overlay is back when the switch is off",
               abs(coloured_pixels(off_full) - coloured_pixels(colours)) < coloured_pixels(colours) * 0.2)


def phase_style(window, area, region, obj, plain, tree, viewer):
    """EVO-7: guides are not depth-occluded; degenerate values and origin dots draw nothing.

    The occlusion claim needs an occluder, so an opaque cube is placed between the camera and the
    viewer's object: if the guides were depth tested they would vanish behind it. A control render
    (colour overlay, cube hidden vs shown) proves the cube really hides the object, so a surviving
    line count is evidence and not an artefact of a cube that missed.
    """
    rv3d = area.spaces.active.region_3d
    overlay = area.spaces.active.overlay
    camera = rv3d.view_matrix.inverted().translation
    occluder = make_occluder(obj.location.lerp(camera, 0.45), size=4.0)

    overlay.show_viewer_vector = False
    occluder.hide_viewport = True
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
    visible = render(area, region, window, "evo7_object_visible")
    occluder.hide_viewport = False
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
    hidden = render(area, region, window, "evo7_object_occluded")
    print(f"  [evo7] colour overlay: object visible={coloured_pixels(visible)} px, "
          f"behind the occluder={coloured_pixels(hidden)} px", flush=True)
    check_true("EVO-7 test premise: the occluder really hides the viewer's object",
               coloured_pixels(hidden) < coloured_pixels(visible) * 0.4)

    overlay.show_viewer_vector = True
    overlay.viewer_vector_scale = 0.0
    empty = render(area, region, window, "evo7_occluded_zero")
    overlay.viewer_vector_scale = 0.4
    through = render(area, region, window, "evo7_through_occluder")
    stats = mask_stats(draw_mask(empty, through), through)
    print(f"  [evo7] guides drawn through the occluder: {stats}", flush=True)
    check_true("EVO-7 guides are not depth-occluded (drawn through the occluder)",
               stats["drawn"] > 300)

    # EVO-7: a degenerate value draws nothing. Every vector is exactly zero here, and a non-zero
    # user scale must not turn that into a zero length segment - nor into an origin dot, since a
    # dot would not depend on the length either.
    _obj, tree, viewer = build_vector_scene(vector_multiplier=0.0)
    if not activate_viewer(window, tree, viewer):
        check_true("EVO-7 could activate the degenerate scene's viewer", False)
        return
    area, region = setup_view3d(window) or (area, region)
    overlay = area.spaces.active.overlay
    overlay.show_viewer_vector = True
    overlay.viewer_vector_mode = "VECTOR"
    overlay.viewer_vector_scale = 0.0
    zero_scale = render(area, region, window, "evo7_degenerate_zero")
    # The jitter depends on the scene (how many edges are on screen), so this claim gets its own
    # floor: without it the check passed by 4 px once (1080 vs a 1084 limit from another scene).
    zero_scale_again = render(area, region, window, "evo7_degenerate_zero_b")
    limit = max(1000, int(draw_mask(zero_scale, zero_scale_again).sum()) * 3)
    overlay.viewer_vector_scale = 0.4
    degenerate = render(area, region, window, "evo7_degenerate")
    degenerate_drawn = int(draw_mask(zero_scale, degenerate).sum())
    print(f"  [evo7] |v| = 0 with Vector Scale 0.4: delta {degenerate_drawn} px "
          f"(this scene's limit {limit})", flush=True)
    check_true("EVO-7 degenerate values draw nothing (and no origin dots)",
               degenerate_drawn < limit)


def phase_rotation(window, area, region):
    _obj, tree, viewer = build_quaternion_scene()
    if not activate_viewer(window, tree, viewer):
        check_true("EVO-4 could activate the rotation viewer", False)
        return
    area, region = setup_view3d(window) or (area, region)
    overlay = area.spaces.active.overlay
    overlay.show_viewer_vector = True

    overlay.viewer_axes_scale = 0.0
    empty = render(area, region, window, "evo4_axes_zero")
    overlay.viewer_axes_scale = 1.0
    axes = render(area, region, window, "evo4_quaternion_axes")
    stats = mask_stats(draw_mask(empty, axes), axes)
    print(f"  [evo4] {stats}", flush=True)

    check_true("EVO-4 rotation draws axes", stats["drawn"] > 500)
    # EVO-4: the three axes use the theme axis colours. In the displayed sRGB values (after the
    # AgX view transform) they measure as X (1.0, 0.5, 0.6), Y (0.8, 0.9, 0.0), Z (0.4, 0.8, 1.0),
    # so what is checked is which channel dominates, not an absolute colour.
    check_true("EVO-4 rotation draws the X axis (red-dominant)", stats["red"] > 150)
    check_true("EVO-4 rotation draws the Y axis (green-dominant)", stats["green"] > 150)
    check_true("EVO-4 rotation draws the Z axis (blue-dominant)", stats["blue"] > 150)
    # EVO-4 says the three axes are equally long; in a perspective projection that is a bound.
    lengths = sorted((stats["red"], stats["green"], stats["blue"]))
    check_true("EVO-4 the three axes are comparable in length (ratio < 3)",
               lengths[0] > 0 and lengths[2] < lengths[0] * 3.0)

    # EVO-2 with a type that has no colour overlay at all: off means nothing.
    overlay.show_viewer_vector = False
    overlay.viewer_axes_scale = 0.0
    off_zero = render(area, region, window, "evo2_rotation_off_zero")
    overlay.viewer_axes_scale = 1.0
    off_full = render(area, region, window, "evo2_rotation_off")
    check_true("EVO-2 rotation axes disappear when switched off",
               int(draw_mask(off_zero, off_full).sum()) < nothing_limit())
    check_true("EVO-2 rotation draws nothing at all when switched off",
               int(draw_mask(empty, off_full).sum()) < nothing_limit())


def build_vector_scene(vector_multiplier=3.0):
    """A single Float3 object; `vector_multiplier=0` makes every value degenerate (EVO-7)."""
    wipe()
    tree, viewer, _store = make_tree("Viewer Overlay Vectors", "FLOAT_VECTOR",
                                     vector_multiplier=vector_multiplier)
    obj = make_object("Viewer Object", tree, (0.0, 0.0, 0.0))
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj, tree, viewer


def make_occluder(location, size):
    """An opaque cube to put between the camera and the viewer's object (EVO-7)."""
    import bmesh

    mesh = bpy.data.meshes.new("Occluder")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Occluder", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    return obj


def build_quaternion_scene():
    wipe()
    tree, viewer, _store = make_tree("Viewer Overlay Rotation", "QUATERNION")
    obj = make_object("Viewer Object", tree, (0.0, 0.0, 0.0))
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj, tree, viewer


def phase_instance_orientation(window, area, region):
    """EVO-4 / EVO-5 in the Instance domain: the tripod shows the instance's own orientation.

    Reported bug (2026-09-16, `test_matrix.blend`): a preview instance's object matrix is
    `base_object * instance_transform`, and the viewed value (`instance_transform`) *is* that
    transform - so the axes came out rotated twice, i.e. with `Instance on Points`' Rotation Z the
    tripod turned twice as fast as the instances.

    The measurement is the screen angle of the drawn X and Y axes (PCA of the red / green
    dominant pixels, see `axis_angle_deg`) against two expectations taken from the view matrix:
    the instance's real orientation, and the doubled one.
    """
    import math

    from bpy_extras import view3d_utils
    from mathutils import Matrix, Vector

    def rotate_z(deg):
        return Matrix.Rotation(math.radians(deg), 3, "Z") @ Vector((1.0, 0.0, 0.0))

    # (label, Instance-on-Points Rotation Z, object Rotation Z): the third one proves that the
    # base object's own rotation is still applied - the fix must not mean "ignore the object".
    cases = (("axis_aligned", 0.0, 0.0),
             ("rotated_instance", 35.0, 0.0),
             ("rotated_object", 25.0, 20.0))
    # EVO-5 (Matrix) and EVO-4 (Quaternion) share the direction conversion, so both are measured.
    for rotation_only in (False, True):
        value_kind = "quaternion" if rotation_only else "matrix"
        for label, instance_z, object_z in cases:
            label = f"{label}_{value_kind}"
            _obj, tree, viewer = build_instance_matrix_scene(instance_z, object_z, rotation_only)
            if not activate_viewer(window, tree, viewer):
                check_true(f"EVO-4/5 could activate the instance viewer ({label})", False)
                return
            area, region = setup_view3d(window) or (area, region)
            rv3d = view_top_ortho(window, area, region)
            overlay = area.spaces.active.overlay
            overlay.show_viewer_vector = True
            overlay.viewer_vector_mode = "VECTOR"
            overlay.viewer_axes_scale = 0.0
            empty = render(area, region, window, f"evo5_instance_{label}_zero")
            overlay.viewer_axes_scale = 1.0
            axes = render(area, region, window, f"evo5_instance_{label}")
            mask = draw_mask(empty, axes)

            # The tripod origin is the instance origin (the world origin here); the projection
            # turns the drawn pixels back into world units, so the length claim does not depend on
            # the zoom.
            origin_px = view3d_utils.location_3d_to_region_2d(region, rv3d, Vector((0.0, 0.0, 0.0)))
            tip_px = view3d_utils.location_3d_to_region_2d(
                region, rv3d, rotate_z(object_z + instance_z))
            px_per_unit = (Vector(tip_px) - Vector(origin_px)).length
            length_world = axis_length_px(mask, axes, 0, origin_px) / px_per_unit

            x_angle, x_px = axis_angle_deg(mask, axes, 0)
            y_angle, y_px = axis_angle_deg(mask, axes, 1)
            want_x = screen_angle_deg(rv3d, rotate_z(object_z + instance_z))
            want_y = screen_angle_deg(rv3d, rotate_z(object_z + instance_z + 90.0))
            bug_x = screen_angle_deg(rv3d, rotate_z(object_z + instance_z * 2.0))
            print(f"  [evo5 {label}] X drawn at {x_angle} deg (want {want_x:.1f}, twice would be "
                  f"{bug_x:.1f}), {x_px} px; Y drawn at {y_angle} deg (want {want_y:.1f}), "
                  f"{y_px} px; X length {length_world:.3f} world units "
                  f"(the value's own scale is 1)", flush=True)

            check_true(f"EVO-4/5 instance axes are drawn ({label})", x_px > 300 and y_px > 300)
            check_true(f"EVO-4/5 instance X axis follows the instance orientation ({label})",
                       x_angle is not None and angle_gap(x_angle, want_x) < 6.0)
            check_true(f"EVO-4/5 instance Y axis follows the instance orientation ({label})",
                       y_angle is not None and angle_gap(y_angle, want_y) < 6.0)
            check_true(f"EVO-4/5 instance axis length follows the value's scale ({label})",
                       0.8 < length_world < 1.2)
            if instance_z:
                # The reported bug: the axes turned twice as fast as the instances.
                check_true(f"EVO-4/5 instance X axis is not the doubled orientation ({label})",
                           x_angle is not None and angle_gap(x_angle, bug_x) > 15.0)
                check_true(f"EVO-4/5 instance Y axis is not the doubled orientation ({label})",
                           y_angle is not None and angle_gap(y_angle, bug_x + 90.0) > 15.0)


def phase_matrix_scale(window, area, region):
    """EVO-5: the axes follow the decomposed per-axis scale."""
    drawn = {}
    for label, scale in (("zero", (0.0, 0.0, 0.0)),
                         ("small", (0.3, 0.3, 0.3)),
                         ("large", (1.2, 1.2, 1.2))):
        _obj, tree, viewer = build_matrix_scene(scale)
        if not activate_viewer(window, tree, viewer):
            check_true(f"EVO-5 could activate the matrix viewer ({label})", False)
            return
        area, region = setup_view3d(window) or (area, region)
        area.spaces.active.overlay.show_viewer_vector = True
        drawn[label] = render(area, region, window, f"evo5_matrix_{label}")
    small = mask_stats(draw_mask(drawn["zero"], drawn["small"]), drawn["small"])
    large = mask_stats(draw_mask(drawn["zero"], drawn["large"]), drawn["large"])
    print(f"  [evo5] scale 0.3 -> {small['drawn']} px, scale 1.2 -> {large['drawn']} px", flush=True)
    check_true("EVO-5 matrix axes are drawn", small["drawn"] > 500)
    check_true("EVO-5 a larger decomposed scale draws longer axes",
               large["drawn"] > small["drawn"] * 1.3)


def phase_budget(window, area, region):
    """EVO-6: above the element budget nothing is drawn, and the viewport still renders."""
    import time

    # Control first: the same scene below the budget does draw lines, so a zero result above the
    # budget means the gate fired - not that the scene was broken.
    _obj, tree, viewer = build_budget_scene(1600)
    if not activate_viewer(window, tree, viewer):
        check_true("EVO-6 could activate the viewer for the control scene", False)
        return
    area, region = setup_view3d(window) or (area, region)
    overlay = area.spaces.active.overlay
    overlay.show_viewer_vector = True
    overlay.viewer_vector_scale = 0.0
    control_empty = render(area, region, window, "evo6_control_zero")
    overlay.viewer_vector_scale = 0.4
    control = render(area, region, window, "evo6_control")
    control_stats = mask_stats(draw_mask(control_empty, control), control)
    check_true("EVO-6 the control scene below the budget draws lines", control_stats["drawn"] > 500)

    _obj, tree, viewer = build_budget_scene(1004001)
    if not activate_viewer(window, tree, viewer):
        check_true("EVO-6 could activate the viewer for the budget scene", False)
        return
    area, region = setup_view3d(window) or (area, region)
    overlay = area.spaces.active.overlay
    overlay.show_viewer_vector = True
    started = time.time()
    overlay.viewer_vector_scale = 0.0
    empty = render(area, region, window, "evo6_budget_zero")
    # This scene needs its own noise floor: a 1M vertex grid jitters more between frames than the
    # small test scenes, and the "nothing drawn" claim is measured against that.
    empty_again = render(area, region, window, "evo6_budget_zero_b")
    local_noise = int(draw_mask(empty, empty_again).sum())
    overlay.viewer_vector_scale = 0.4
    drawn = render(area, region, window, "evo6_budget")
    seconds = time.time() - started
    budget_drawn = int(draw_mask(empty, drawn).sum())
    limit = max(1000, local_noise * 3)
    print(f"  [evo6] 1004001 elements: frame drawn in {seconds:.2f}s, delta {budget_drawn} px, "
          f"local noise {local_noise} px (limit {limit})", flush=True)
    check_true("EVO-6 above the budget nothing is drawn", budget_drawn < limit)
    check_true("EVO-6 the frame still rendered (viewport did not freeze)", seconds < 60.0)


def harness_smoke(window, area, region, tree, viewer):
    """Everything that does not need the new properties: the viewer node built from RNA, the
    activation operator, the screenshot and the pixel counter. Run against a stale binary this
    is what tells "the probe is broken" apart from "the feature is not built in yet"."""
    check_true("harness: the viewer node has a geometry and a value item",
               len(viewer.viewer_items) == 2)
    check_true("harness: the viewer node was activated through the operator",
               activate_viewer(window, tree, viewer))
    rgb = render(area, region, window, "harness_smoke")
    check_true("harness: the screenshot has a usable viewport", rgb.shape[1] > 200)
    check_true("harness: the pixel counter sees the colour overlay", coloured_pixels(rgb) > 500)


def main():
    window = bpy.context.window or bpy.context.window_manager.windows[0]
    os.makedirs(SHOTS, exist_ok=True)

    obj, plain, tree, viewer, _store = build_scene()
    area, region = setup_view3d(window)
    if area is None:
        check_true("a 3D viewport area exists", False)
        return
    if not hasattr(area.spaces.active.overlay, "show_viewer_vector"):
        harness_smoke(window, area, region, tree, viewer)
        check_true("the built blender carries the new overlay properties "
                   "(run tools\\pdt\\build_dev.ps1 -Target install)", False)
        return

    global NOISE_FLOOR
    noise_a = render(area, region, window, "noise_a")
    noise_b = render(area, region, window, "noise_b")
    NOISE_FLOOR = int(draw_mask(noise_a, noise_b).sum())
    print(f"  [noise] two renders of the same state differ by {NOISE_FLOOR} px "
          f"(limit for 'nothing drawn': {nothing_limit()})", flush=True)

    phase_scope_and_vectors(window, area, region, obj, plain, tree, viewer)
    phase_style(window, area, region, obj, plain, tree, viewer)
    phase_rotation(window, area, region)
    phase_matrix_scale(window, area, region)
    phase_instance_orientation(window, area, region)
    phase_budget(window, area, region)


if __name__ == "__main__":
    bpy.app.timers.register(_quit, first_interval=300.0)
    try:
        main()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        FAILURES.append("exception during the test")
    finally:
        print(f"== gui_viewer_overlay: {CHECKS} check(s), {len(FAILURES)} failure(s) ==", flush=True)
        for failure in FAILURES:
            print(f"  failed: {failure}", flush=True)
        print(f"GUI_TEST_RESULT: {'PASS' if not FAILURES else 'FAIL'}", flush=True)
        bpy.app.timers.register(_quit, first_interval=2.0)
