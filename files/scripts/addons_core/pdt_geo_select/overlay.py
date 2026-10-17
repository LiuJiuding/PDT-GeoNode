# SPDX-FileCopyrightText: 2026 PDT Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The picker's on-screen output: the status line, the small viewport overlay with the live
count, and the node-editor pin / breadcrumb handling that leaves the editor where the user
was (L0 `PICK-6`)."""

from __future__ import annotations

import re
import time
import bpy

from .utils.constants import DOMAIN_LABELS, GEOMETRY_KIND_LABELS, INSTANCE_MODE_LABELS, STATUS_ACTIVE, STATUS_OTHER, STATUS_PAUSED
from .geometry import resolve_breadcrumb_nodes
from .instances import current_session_indices
from .selection import read_selected_indices
from . import state as pick_state

def selected_element_count(context=None) -> int:
    """Live count for the modal overlay/status line.

    Uses the same read path as the picker itself: `mesh.total_*_sel` reports 0 for
    a baked mesh that has no `.select_*` layers yet.
    """
    context = context or bpy.context
    if not pick_state.session:
        return 0
    try:
        if (
            pick_state.session.get("geometry_kind") == "INSTANCES"
            and pick_state.session.get("instance_mode") == "REALIZED"
        ):
            # The parameter holds instance numbers, so the overlay counts those.
            return len(current_session_indices(context))
        return len(read_selected_indices(context, pick_state.session.get("domain", "POINT")))
    except (RuntimeError, AttributeError, ReferenceError):
        return 0


_draw_handle = None


_overlay_text = ""


# When the overlay line was last recounted (see _draw_session_overlay).
_overlay_count_at = 0.0


def _overlay_status_text(context) -> str:
    """The status text for the current session state."""
    from .session import pick_session_state  # deferred: `session` imports this module
    return {
        "ok": STATUS_ACTIVE,
        "paused": STATUS_PAUSED,
        "other_object": STATUS_OTHER,
        "missing": STATUS_PAUSED,
    }.get(pick_session_state(context), STATUS_ACTIVE)


def _refresh_overlay_text(context=None) -> int:
    """Recount and rewrite the overlay line; returns the selected count."""
    global _overlay_text, _overlay_count_at
    context = context or bpy.context
    count = selected_element_count(context)
    domain = DOMAIN_LABELS.get(pick_state.session.get("domain", "POINT"), "Point")
    kind = GEOMETRY_KIND_LABELS.get(pick_state.session.get("geometry_kind", "MESH"), "")
    prefix = f"{kind} / {domain}" if kind else domain
    if pick_state.session.get("geometry_kind") == "INSTANCES":
        mode = INSTANCE_MODE_LABELS.get(pick_state.session.get("instance_mode", "POINTS"), "Points")
        if pick_state.session.get("instance_mode") == "REALIZED":
            picked = GEOMETRY_KIND_LABELS.get(pick_state.session.get("pick_kind", ""), "")
            prefix = f"{kind} ({mode.lower()}: {picked}) / {domain}" if picked else f"{kind} ({mode.lower()}) / {domain}"
        else:
            prefix = f"{kind} ({mode.lower()}) / {domain}"
    _overlay_text = f"PDT Pick [{prefix}]: {count} selected  -  Enter apply / Esc cancel"
    _overlay_count_at = time.monotonic()
    return count


def _draw_session_overlay():
    """Draw the live count in the 3D viewport (never raise into the UI)."""
    global _overlay_text
    if not pick_state.session or not _overlay_text:
        return
    try:
        # With no modal loop nothing refreshes the line, so follow the selection
        # here. Throttled: this runs on every viewport redraw.
        if not pick_state.modal_active and (time.monotonic() - _overlay_count_at) > 0.25:
            _refresh_overlay_text()

        import blf

        font_id = 0
        try:
            blf.size(font_id, 15)
        except TypeError:  # older/newer signature
            blf.size(font_id, 15, 72)
        blf.color(font_id, 1.0, 1.0, 1.0, 1.0)
        blf.position(font_id, 24, 44, 0)
        blf.draw(font_id, _overlay_text)
    except Exception:  # noqa: BLE001 - drawing must never break the viewport
        pass


def _push_session_ui(context) -> None:
    """Reflect the current session state in the status line and overlay."""
    from .session import pick_session_state  # deferred: `session` imports this module
    state = pick_session_state(context)
    if state == "none":
        _pop_session_ui(context)
        return
    count = _refresh_overlay_text(context)
    try:
        context.workspace.status_text_set(f"{_overlay_status_text(context)}   |   {count} selected")
    except Exception:  # noqa: BLE001 - background mode has no workspace UI
        pass
    _add_overlay_handler()


def _pop_session_ui(context) -> None:
    global _overlay_text
    _overlay_text = ""
    try:
        context.workspace.status_text_set(None)
    except Exception:  # noqa: BLE001
        pass
    _remove_overlay_handler()
    _unpin_node_editors()


#: Node editor pinned for the duration of a pick, plus the breadcrumb to put back.
_editor_state: dict | None = None


def _editor_chain(space):
    """The breadcrumb of a node editor as ``[(tree, group_node), ...]``."""
    levels = []
    for item in getattr(space, "path", None) or []:
        tree = getattr(item, "node_tree", None)
        if tree is not None:
            levels.append((tree, None))
    return resolve_breadcrumb_nodes(levels)


def _pin_node_editor(context) -> int:
    """Pin the node editor and remember where in the breadcrumb the user was.

    `begin_pick()` makes the temporary object active and enters edit mode. An
    unpinned node editor re-derives the tree it shows from the *active object*
    (`geometry_node_tree_get_from_context()` returns the active modifier's node
    group) and then calls `ED_node_tree_start()`, which rebuilds the breadcrumb as
    a **single** level: during the pick the editor dropped to the root of the
    tree (or lost its tree entirely), and when the pick ended the user was left
    there instead of in the group they had been editing. `SNODE_PIN` makes
    `snode_set_context()` skip that context lookup, and `_unpin_node_editors()`
    puts both the flag and the breadcrumb back when the session ends.
    """
    global _editor_state
    space = getattr(context, "space_data", None)
    if space is None or getattr(space, "type", None) != "NODE_EDITOR":
        return 0
    if _editor_state is not None and _editor_state["space"] == space:
        return 0
    chain = _editor_chain(space)
    was_pinned = bool(getattr(space, "pin", False))
    pin_set = False
    if not was_pinned:
        try:
            space.pin = True
            pin_set = True
        except Exception:  # noqa: BLE001 - not writable in this context
            pass
    _editor_state = {
        "space": space,
        "was_pinned": was_pinned,
        "pin_set": pin_set,
        "chain": chain,
    }
    return 1


def _unpin_node_editors() -> None:
    """Hand the node editor back to the user: pin flag and breadcrumb."""
    global _editor_state
    state = _editor_state
    if not state:
        return
    _editor_state = None
    space = state["space"]
    if state["pin_set"]:
        try:
            space.pin = False
        except Exception:  # noqa: BLE001 - the window/area is gone
            pass
    try:
        _restore_breadcrumb(space, state["chain"])
    except ReferenceError as ex:
        # The file was replaced (or the tree deleted) while the pick was running, so
        # the remembered breadcrumb points at data-blocks that no longer exist.
        # There is nothing left to restore - do not take the whole session down.
        print(f"PDT picker: the remembered breadcrumb is gone ({ex})")


def _restore_breadcrumb(space, chain) -> bool:
    """Put the node editor back at the level the pick was started from.

    Only when the breadcrumb was *collapsed* by the pick (the current path is a
    prefix of the remembered one). If the user navigated somewhere else on
    purpose, their choice wins. Returns True when a level was pushed back.
    """
    if len(chain) < 2:
        return False
    path = getattr(space, "path", None)
    if path is None or not hasattr(path, "start") or not hasattr(path, "append"):
        return False
    wanted = [tree for tree, _node in chain]
    current = [tree for tree, _node in _editor_chain(space)]
    if current == wanted:
        return False
    if current and not all(a == b for a, b in zip(current, wanted)):
        return False  # a different branch: leave the user's own navigation alone
    try:
        if not current:
            path.start(wanted[0])
        for tree, node in chain[len(current) if current else 1 :]:
            if node is None:
                break
            path.append(tree, node)
    except Exception as exc:  # noqa: BLE001 - path API differences
        print(f"PDT picker: could not restore the node editor breadcrumb ({exc})")
        return False
    return True


def _add_overlay_handler() -> None:
    global _draw_handle
    if _draw_handle is not None:
        return
    try:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_session_overlay, (), "WINDOW", "POST_PIXEL"
        )
    except Exception:  # noqa: BLE001 - no 3D viewport in this session
        _draw_handle = None


def _remove_overlay_handler() -> None:
    global _draw_handle
    if _draw_handle is None:
        return
    try:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, "WINDOW")
    except Exception:  # noqa: BLE001
        pass
    _draw_handle = None


def _overlay_text_reset() -> None:
    global _overlay_text
    _overlay_text = ""
