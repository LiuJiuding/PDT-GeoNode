# SPDX-FileCopyrightText: 2025 Blender Foundation
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Which node is under the cursor - the same answer the editor itself gives (MMB-2/3/4).

The trigger fires on exactly the region the editor means by "this node": the drawn node
rectangle (`bNode::runtime->draw_bounds`, what `node.dimensions` measures and what Blender's
own `node_under_mouse_get()` hit-tests). Two details decide whether that matches the screen:
`View2D.region_to_view()` answers in *view* space, so the point must be divided by
`UI_SCALE_FAC` before it can be compared with a node coordinate (missing that put the block
on the wrong node for 22 of 32 probe points on a 120% display), and a collapsed node is
centred on its location rather than hanging below it. Frames are skipped (MMB-4).
"""

from __future__ import annotations

import math

import bpy


# ---------------------------------------------------------------------------
# node space, node boxes, and the hit test
# ---------------------------------------------------------------------------


def _c_round(value):
    """`roundf()` - half away from zero, unlike Python's banker's rounding."""
    return math.floor(value + 0.5) if value >= 0.0 else math.ceil(value - 0.5)


def _ui_scale():
    """`UI_SCALE_FAC`: the factor between *node space* and the editor's *view space*.

    `node.location_absolute` is in node space (raw data coordinates), while
    `View2D.region_to_view()` and `node.dimensions` are in view space: the editor draws a
    node at `location * UI_SCALE_FAC` (`node_draw.cc`, `node_to_view()`), and it divides its
    own cursor by the same factor before using it as a node coordinate (`space_node.cc`,
    `node_cursor()` - "snode->runtime->cursor is in placing new nodes space").

    Measured on a 120% Windows display (`UI_SCALE_FAC = 1.194`): mixing the two spaces put
    the hit box in the wrong place for 22 of 32 probe points, which is the "middle mouse
    misses the node I am pointing at" report. `preferences.system.ui_scale` *is*
    `U.scale_factor` (`rna_userdef.cc`); `dpi / 72` is the same number and stays as the
    fallback for builds that do not expose it.
    """
    try:
        system = bpy.context.preferences.system
    except Exception:  # noqa: BLE001
        return 1.0
    scale = float(getattr(system, "ui_scale", 0.0) or 0.0)
    if scale > 0.0:
        return scale
    dpi = float(getattr(system, "dpi", 0.0) or 0.0)
    return (dpi / 72.0) if dpi > 0.0 else 1.0


def _node_dy():
    """`NODE_DY` = `U.widget_unit`, the height of a node's header (title) band.

    `U.widget_unit = round(18 * UI_SCALE_FAC) + 2 * pixelsize` (`wm_window.cc`). Neither
    `widget_unit` nor `pixelsize` is exposed under those names, but the inputs are
    (`preferences.system.pixel_size`, `preferences.system.ui_scale`). Only the collapsed
    box needs it.
    """
    try:
        system = bpy.context.preferences.system
    except Exception:  # noqa: BLE001
        return 20.0
    pixelsize = getattr(system, "pixel_size", None)
    pixelsize = int(pixelsize) if pixelsize else 1
    return float(int(_c_round(18.0 * _ui_scale())) + 2 * pixelsize)


def node_box(location, dimensions, *, collapsed=False, ui_scale=None, node_dy=None):
    """The drawn rectangle of a node in **node space**: `(left, top, width, height)`.

    This is `bNode::runtime->draw_bounds` expressed in node coordinates - exactly what the
    editor itself hit-tests in `node_under_mouse_get()` (`node_select.cc`) and what
    `node.dimensions` measures. `location` is `node.location_absolute`: the raw coordinate,
    *not* `node.location`, which is relative to the parent frame.

    A collapsed node (`node.hide` is `NODE_COLLAPSED`, the H key) is not drawn from its top
    edge: Blender centres it on the location and shifts it up by half a header
    (`node_update_collapsed`), so the plain "location is the top edge" rule would sit half a
    node too high - which is why collapsed nodes used to be skipped altogether.
    """
    scale = _ui_scale() if ui_scale is None else float(ui_scale)
    if scale <= 0.0:
        scale = 1.0
    left = _c_round(location[0] * scale) / scale
    top = _c_round(location[1] * scale) / scale
    width = float(dimensions[0]) / scale
    height = float(dimensions[1]) / scale
    if collapsed:
        dy = _node_dy() if node_dy is None else float(node_dy)
        top = float(location[1]) + (float(dimensions[1]) * 0.5 - dy * 0.5) / scale
    return float(left), float(top), width, height


def _node_extent(node):
    """`node_box()` for a node of the edited tree, in node space.

    `node.dimensions` is the size the editor actually drew (it follows the socket count, the
    labels, the panel state), so it beats `node.height`, which only holds true data for some
    node types. A node the editor has not drawn yet reports `(0, 0)`; `_node_at()` skips
    those instead of guessing a size from `width`/`bl_height_default`.
    """
    location = getattr(node, "location_absolute", None)
    if location is None:
        location = node.location
        parent = node.parent
        while parent is not None:
            location = location + parent.location
            parent = parent.parent
    return node_box(location, node.dimensions, collapsed=bool(getattr(node, "hide", False)))


def _is_drawn(node):
    """False while the editor has not drawn this node yet (`draw_bounds` is still empty)."""
    dimensions = node.dimensions
    return dimensions[0] > 0.0 and dimensions[1] > 0.0


def _is_frame(node):
    return getattr(node, "bl_idname", "") == "NodeFrame"


def _draw_order(node, index):
    """A Python stand-in for the editor's `ui_order` (`tree_draw_order_update()`).

    Blender sorts by selection state (an unselected node is in the background, a selected
    one - or one with a selected parent - in front), then by depth (*"if A is an ancestor, it
    is always behind B"*), then by the persistent `node.ui_order` that RNA does not expose.
    Selection and depth are the parts the hit test can observe; `tree.nodes` order stands in
    for `ui_order`. Ascending sort is "back to front", so `_node_at()` walks it reversed.
    """
    depth = 0
    selected = bool(getattr(node, "select", False))
    parent = getattr(node, "parent", None)
    while parent is not None:
        depth += 1
        selected = selected or bool(getattr(parent, "select", False))
        parent = getattr(parent, "parent", None)
    return (1 if selected else 0, depth, index)


def _nearest_node(tree, world_x, world_y):
    """The node whose centre is closest to a node-space point.

    A last resort only: a keyboard trigger whose pointer is unknown (the key was pressed
    while the pointer sat outside this editor), where "the node you are looking at" is
    approximated by "the node nearest the point Blender last saw".
    """
    best = None
    best_distance = None
    for node in tree.nodes:
        if _is_frame(node) or not _is_drawn(node):
            continue
        left, top, width, height = _node_extent(node)
        distance = (
            (left + width * 0.5 - world_x) ** 2 + (top - height * 0.5 - world_y) ** 2
        )
        if best_distance is None or distance < best_distance:
            best, best_distance = node, distance
    return best


def _node_at(tree, world_x, world_y):
    """The topmost drawn node containing a **node-space** point, or None.

    The rule the editor itself uses for clicking and hovering (`node_under_mouse_get()`):
    the node drawn last wins. Frames are skipped - the editor only hits their border and
    they carry no geometry, so letting one swallow a pick inside it can only hide the node
    the user meant.
    """
    entries = [
        (_draw_order(node, index), node)
        for index, node in enumerate(tree.nodes)
        if not _is_frame(node)
    ]
    entries.sort(key=lambda entry: entry[0])
    for _order, node in reversed(entries):
        if not _is_drawn(node):
            continue
        left, top, width, height = _node_extent(node)
        if left <= world_x <= left + width and top - height <= world_y <= top:
            return node
    return None
