# SPDX-FileCopyrightText: 2025 Blender Foundation
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The hold gesture: session state, the read at trigger time, and the draw handler.

Holding shows the block, releasing hides it (MMB-7). It does **not** follow the cursor, does
not re-read another node, and a drag while holding neither moves it nor hides it; there is no
pin and no close button. The block is read once, when the trigger opens the session.

The entry logic is a module-level function (`_begin_session`) rather than only an operator
method because a `bpy.types.Operator` subclass cannot be instantiated from Python and
`bpy.ops(...)` does not hand `invoke` an event - that is what lets the real-window test drive
the gesture instead of a mock.
"""

from __future__ import annotations

import bpy

from .hit_test import (
    _nearest_node,
    _node_at,
    _ui_scale,
)
from .interface import draw_panel
from .preferences import prefs
from .snapshot import build_panel_state
from .utils.constants import CURSOR_OFFSET
from .utils.log import _diagnose, _log


# ---------------------------------------------------------------------------
# the session: gesture state, the read at trigger time, and the draw handler
# ---------------------------------------------------------------------------

class _Session:
    """State shared between the modal operator and the draw callback."""

    def __init__(self):
        self.state = None
        self.cursor = (0.0, 0.0)
        self.node_name = ""
        #: The non-modifier key that started the session, when it was a keyboard shortcut
        #: rather than a mouse button. Its release closes the block, like MMB's release.
        self.trigger_key = ""


_SESSION = _Session()


_DRAW_HANDLE = None


def _cursor_node_space(context, space, event):
    """The trigger's pointer in **node space**, or None when it cannot be determined.

    A mouse event carries region-relative coordinates. A *keyboard* event carries only the
    last known *window* position (`eventstate`), usable while it sits inside this region and
    ignored otherwise; the editor's own last cursor (`space.cursor_location`, which
    `ED_node_cursor_location_get()` already keeps in node space) is the next best answer,
    and the region centre the last.
    """
    region = getattr(context, "region", None)
    view2d = getattr(region, "view2d", None)
    if region is None or view2d is None:
        return None
    scale = _ui_scale() or 1.0
    if hasattr(event, "mouse_region_x"):
        local_x, local_y = event.mouse_region_x, event.mouse_region_y
    else:
        window_x = getattr(event, "mouse_x", None)
        window_y = getattr(event, "mouse_y", None)
        inside = (
            window_x is not None
            and window_y is not None
            and region.x <= window_x <= region.x + region.width
            and region.y <= window_y <= region.y + region.height
        )
        if inside:
            local_x, local_y = window_x - region.x, window_y - region.y
        else:
            cursor_location = getattr(space, "cursor_location", None)
            if cursor_location is not None and (cursor_location[0] or cursor_location[1]):
                return float(cursor_location[0]), float(cursor_location[1])
            local_x, local_y = region.width * 0.5, region.height * 0.5
    world_x, world_y = view2d.region_to_view(local_x, local_y)
    # `region_to_view` answers in view space; node coordinates are that divided by
    # `UI_SCALE_FAC` - the same step `node_cursor()` performs in `space_node.cc`.
    return world_x / scale, world_y / scale


def _node_under_cursor(context, event):
    """The node the trigger points at, or None.

    The cursor goes through the editor's own `View2D.region_to_view()` - the conversion
    every Blender node operator uses - and is then brought into node space (see
    `_ui_scale`), which is what makes it comparable with `node.dimensions` instead of
    drifting from the drawing by `UI_SCALE_FAC`. With `event=None` the single selected node
    is the answer, which is what makes the operator testable from a script and gives menus a
    sensible target.
    """
    space = getattr(context, "space_data", None)
    if space is None or getattr(space, "type", "") != "NODE_EDITOR":
        return None
    tree = getattr(space, "edit_tree", None) or getattr(space, "node_tree", None)
    if tree is None:
        return None

    if event is None:
        selected = [node for node in tree.nodes if node.select]
        return selected[0] if len(selected) == 1 else None

    point = _cursor_node_space(context, space, event)
    if point is None:
        return None
    node = _node_at(tree, point[0], point[1])
    if node is None and not hasattr(event, "mouse_region_x"):
        # A keyboard trigger whose pointer position is unknown: nearest to the last known
        # point, which is what a keyboard-only workflow has to work with.
        return _nearest_node(tree, point[0], point[1])
    return node


def _read_node(context, node, force=False):
    """Read one node once per trigger.

    There is no re-read timer any more: the block does not follow the cursor (L0 边界),
    so a node is read when the trigger opens the block and never again while it is up.
    `force` is kept for the entry point, which always passes it.
    """
    _SESSION.node_name = node.name
    try:
        _SESSION.state = build_panel_state(context, node)
        _log(
            "read-ok",
            node=node.name,
            output_rows=len(_SESSION.state.get("output") or []),
            reason=_SESSION.state.get("reason", ""),
            ms=f"{_SESSION.state.get('time_ms', 0.0):.1f}",
        )
    except Exception as error:  # noqa: BLE001 - a read failure must not kill the modal
        _log("read-FAILED", node=node.name, error=f"{type(error).__name__}: {error}")
        import traceback as _traceback

        _log("read-traceback", detail=_traceback.format_exc().replace("\n", " | "))
        _SESSION.state = {
            "node": node.name,
            "node_label": node.bl_idname,
            "stream": "output", "title": "",
            "components": [], "attribute_groups": [], "added": set(),
            "reason": f"could not read this node: {error}",
            "time_ms": 0.0,
        }


def _draw_callback():
    if _SESSION.state is None:
        return
    settings = prefs(bpy.context)
    try:
        draw_panel(_SESSION.state, _SESSION.cursor, settings)
    except Exception as error:  # noqa: BLE001 - a paint failure must be visible, not fatal
        _log("draw-FAILED", error=f"{type(error).__name__}: {error}")
        raise


def _ensure_draw_handler():
    global _DRAW_HANDLE
    if _DRAW_HANDLE is None:
        _DRAW_HANDLE = bpy.types.SpaceNodeEditor.draw_handler_add(
            _draw_callback, (), "WINDOW", "POST_PIXEL"
        )
        _log("draw-handler-added", handle=str(_DRAW_HANDLE))


def _remove_draw_handler():
    global _DRAW_HANDLE
    if _DRAW_HANDLE is not None:
        try:
            bpy.types.SpaceNodeEditor.draw_handler_remove(_DRAW_HANDLE, "WINDOW")
        except Exception:  # noqa: BLE001 - already gone (file reload)
            pass
        _DRAW_HANDLE = None


def _clear_session():
    _SESSION.state = None
    _SESSION.node_name = ""
    _SESSION.trigger_key = ""


def _begin_session(context, event):
    """Start a read at the node under the cursor; False when there is none.

    Module-level so a test can drive the real entry point: a `bpy.types.Operator`
    subclass cannot be instantiated from Python (`bpy_struct.__new__` takes no
    arguments), and `bpy.ops` does not hand `invoke` an event.
    """
    where = "no event"
    if event is not None:
        where = (
            f"type={getattr(event, 'type', '?')} value={getattr(event, 'value', '?')} "
            f"region=({getattr(event, 'mouse_region_x', -1)},{getattr(event, 'mouse_region_y', -1)})"
        )
    _log("keypress", detail=where)
    node = _node_under_cursor(context, event)
    _clear_session()
    if node is None:
        _log("no-node-under-cursor", detail="passing the event through to the view pan")
        _diagnose(
            "MMB press: no node under the cursor -> passing the event through (view pan). "
            "Hover a node's body and try again."
        )
        return False
    if event is not None and getattr(event, "type", "") not in {
        "MIDDLEMOUSE", "LEFTMOUSE", "RIGHTMOUSE", "MOUSEMOVE", "NONE",
    }:
        _SESSION.trigger_key = getattr(event, "type", "")
    _log("node-under-cursor", node=node.name, type=node.bl_idname, ui_scale=f"{_ui_scale():.4f}")
    _diagnose(f"MMB press: node {node.name!r} ({node.bl_idname})")
    if event is not None:
        # The block appears next to the pointer **at the moment of the trigger** and stays
        # there (L0 边界). A keyboard trigger carries the last known pointer position rather
        # than the pointer at that moment, so the region centre stands in for it.
        if hasattr(event, "mouse_region_x"):
            _SESSION.cursor = (
                event.mouse_region_x + CURSOR_OFFSET,
                event.mouse_region_y - CURSOR_OFFSET,
            )
        else:
            region = getattr(context, "region", None)
            _SESSION.cursor = (
                (region.width * 0.5 if region else 0.0) + CURSOR_OFFSET,
                (region.height * 0.5 if region else 0.0) - CURSOR_OFFSET,
            )
    else:
        region = getattr(context, "region", None)
        _SESSION.cursor = (
            region.width * 0.5 if region else 0.0,
            region.height * 0.5 if region else 0.0,
        )
    _read_node(context, node, force=True)
    _ensure_draw_handler()
    state = _SESSION.state or {}
    _log(
        "session-started",
        node=node.name,
        stream=state.get("stream", ""),
        components=len(state.get("components") or []),
        attribute_groups=len(state.get("attribute_groups") or []),
        added=len(state.get("added") or []),
        reason=state.get("reason", ""),
        read_ms=f"{state.get('time_ms', 0.0):.1f}",
        cursor=f"{_SESSION.cursor[0]:.0f},{_SESSION.cursor[1]:.0f}",
    )
    return True


def _modal_step(context, event):
    """One step of the hold gesture: ``(action, need_redraw)``.

    Split out of the operator so a real-window test can drive the gesture without
    manufacturing `bpy.types.Event` objects (they cannot be constructed in Python).
    `action` is None / "RUNNING_MODAL" to keep going, "FINISH" or "CANCEL" to end.

    MMB-7 is short, and so is this: holding shows the block, releasing hides it. There is
    **no pin** and no close button, and a drag inside the hold neither moves the block nor
    hides it - the block keeps the position and the node the trigger gave it. `Esc` stays as
    an escape hatch (a modal that could only be left by releasing the exact key or button
    would be stuck if that release were ever lost); it is an implementation detail, not a
    guarantee.
    """
    event_type = getattr(event, "type", "")
    event_value = getattr(event, "value", "")

    if event_type == "MIDDLEMOUSE" and event_value == "RELEASE":
        return "FINISH", False

    # Keyboard trigger: releasing the key that opened the block closes it, so "hold to
    # read" works with a keyboard shortcut exactly as it does with the mouse button.
    if event_type == _SESSION.trigger_key and event_value == "RELEASE":
        return "FINISH", False

    if event_type == "ESC" and event_value == "PRESS":
        return "CANCEL", False

    return "RUNNING_MODAL", False
