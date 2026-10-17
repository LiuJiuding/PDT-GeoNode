# SPDX-FileCopyrightText: 2026 PDT Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The trigger and the pick itself: hit test -> first-level pie (-> second level for
`Instance`) -> the session's modal loop, where Enter applies and Esc / Right-Click cancels.
The *slice* operators own the modal loop, because Blender is still running the slice the
user released on when the session begins."""

from __future__ import annotations

import bpy

from ..utils.constants import DEFAULT_INSTANCE_MODE, DOMAINS, DOMAIN_PIE_IDNAME, INSTANCE_MODES, INSTANCE_MODE_PIE_IDNAME
from ..hit_test import hit_refusal_reason, node_from_hit, pick_geometry_hint
from ..menus import domain_pie_entries
from ..session import begin_pick, cancel_pick, pick_event_action, pick_session_state, try_finish_pick
from ..overlay import _pop_session_ui, _push_session_ui
from .. import state as pick_state
from .. import hit_test

class _PickModalLoop:
    """The modal half of the picker, shared by every operator that can start a pick.

    Pressing the user's shortcut opens a pie; the slice that is released on starts the session.
    That slice's operator is therefore the one that has to own the modal loop (it is the
    operator Blender is still running when the session begins), which is why these methods are
    assigned to each of them instead of living in one class.
    """

    def modal(self, context, event):
        # An unexpected error must neither leave a silently dead modal (which would swallow
        # every later event, including Esc) nor throw the session away: end the modal, keep the
        # session, and let the user finish it with Apply Selection / Cancel Selection.
        try:
            return self._modal_impl(context, event)
        except Exception as ex:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            _pop_session_ui(context)
            self.report(
                {"ERROR"},
                f"PDT pick stopped on an error ({ex}); the session is still open - "
                "use Apply Selection / Cancel Selection (F3) to end it",
            )
            return {"CANCELLED"}

    def _modal_impl(self, context, event):
        state = pick_session_state(context)
        action = pick_event_action(event.type, event.value)
        if event.value in {"PRESS", "CLICK"}:
            # Console breadcrumb: makes a "nothing happened" report diagnosable.
            print(f"PDT pick modal: key={event.type} value={event.value} state={state} action={action}")

        if state == "none":
            # The session was finished behind our back (an operator, an undo, a script). End
            # the modal instead of swallowing events forever.
            _pop_session_ui(context)
            print("PDT pick modal: session gone, ending the modal")
            return {"CANCELLED"}

        if action == "cancel":
            _pop_session_ui(context)
            cancel_pick(context)
            self.report({"INFO"}, "Selection cancelled")
            return {"CANCELLED"}
        if action == "apply":
            ok, message = try_finish_pick(context)
            if not ok:
                print(f"PDT pick modal: apply refused ({state}): {message}")
                self.report({"ERROR"}, message)
                _push_session_ui(context)
                return {"RUNNING_MODAL"}
            _pop_session_ui(context)
            self.report({"INFO"}, message)
            return {"FINISHED"}

        # Everything else drives the normal edit-mode tools; keep the status line in sync with
        # what the session is doing.
        _push_session_ui(context)
        return {"PASS_THROUGH"}

    def cancel(self, context):
        # Blender force-cancelled the modal (file load, area closed, ...).
        _pop_session_ui(context)
        if pick_state.session:
            cancel_pick(context)


def _start_pick(self, context, domain: str, instance_mode: str = "") -> set:
    """Start the session (and its modal loop) for the choice a pie just made.

    Every refusal comes from `begin_pick()` - it owns the wording, and it is the only place
    that knows the geometry (PICK-3: a kind this geometry does not carry, a mixed or nested
    instance stream, a node without a `Domain`, ...).
    """
    tree = pick_state.pending_pick.get("tree")
    node = pick_state.pending_pick.get("node")
    if tree is None or node is None:
        self.report({"ERROR"}, "the pick lost the node it was started on; press the shortcut again")
        return {"CANCELLED"}
    try:
        begin_pick(
            tree,
            node,
            context,
            domain=domain,
            instance_mode=instance_mode or None,
            target_identifier=pick_state.pending_pick.get("socket_identifier", ""),
        )
    except RuntimeError as ex:
        self.report({"ERROR"}, str(ex))
        return {"CANCELLED"}

    _push_session_ui(context)
    pick_state.modal_active = True
    context.window_manager.modal_handler_add(self)
    self.report({"INFO"}, "Enter applies the selection, Esc cancels")
    return {"RUNNING_MODAL"}


def pick_from_cursor(self, context, event=None) -> set:
    """The picker's trigger (PICK-1): hit test, then the first-level pie menu.

    `event` is the key event that fired the shortcut, so the hit test uses the pointer position
    *of that event* (`event.mouse_region_x/y`) instead of the editor's one-frame-old cached
    cursor. Without an event (the search menu, `F3`) the query falls back to the position the
    event system last saw - the pointer still has to be on the socket, which is the point of a
    socket-level trigger.

    Everything that is not a string parameter socket on a node with a geometry input is refused
    here, with a prompt and without opening a menu.
    """
    space = getattr(context, "space_data", None)
    tree = getattr(space, "edit_tree", None) if space is not None else None
    if tree is None:
        self.report({"ERROR"}, "open a node editor on a node tree first")
        return {"CANCELLED"}

    region_x = getattr(event, "mouse_region_x", None) if event is not None else None
    region_y = getattr(event, "mouse_region_y", None) if event is not None else None
    hit = hit_test.pick_hit_test(context, region_x, region_y)
    reason = hit_refusal_reason(hit)
    if reason:
        self.report({"ERROR"}, reason)
        return {"CANCELLED"}

    node = node_from_hit(tree, hit)
    if node is None:
        self.report({"ERROR"}, f"'{hit.get('node_name')}' is not in the tree this editor shows")
        return {"CANCELLED"}

    pick_state.pending_pick.clear()
    pick_state.pending_pick.update(
        {
            "tree": tree,
            "node": node,
            "socket_identifier": hit.get("socket_identifier", ""),
            "domain_items": domain_pie_entries(node, tree, context),
        }
    )
    hint = pick_geometry_hint(tree, node, context)
    if hint:
        self.report({"INFO"}, hint)
    # Opens the radial menu; the slice the user releases on runs `pdt.selection_pick_domain`.
    return bpy.ops.wm.call_menu_pie(name=DOMAIN_PIE_IDNAME)


class PDT_OT_selection_pick_domain(bpy.types.Operator):
    """A slice of the first-level pie menu: the element kind this pick addresses."""

    bl_idname = "pdt.selection_pick_domain"
    bl_label = "Pick Element Kind"
    bl_description = (
        "Which element kind this pick addresses (the same seven as the node's Domain). "
        "It decides which component is baked into the temporary object and which numbers are "
        "written back; it does not change the node. Kinds the geometry does not have are marked "
        "in the list and refused when the pick starts"
    )
    bl_options = {"REGISTER", "UNDO"}

    #: A plain string rather than an enum: the slice list is built **once** when the pie opens
    #: (it takes one temporary evaluation) and lives in `state.pending_pick`, so nothing has to be
    #: re-evaluated - or made context-dependent - while the menu is drawn.
    domain: bpy.props.StringProperty(name="Domain")

    def execute(self, context):
        if self.domain not in DOMAINS:
            self.report({"ERROR"}, f"unknown element kind '{self.domain}'")
            return {"CANCELLED"}
        if self.domain == "INSTANCE":
            # PICK-1: `Domain = Instance` asks the second-level question before anything is
            # baked. The slice the user releases on then starts the pick itself.
            return bpy.ops.wm.call_menu_pie(name=INSTANCE_MODE_PIE_IDNAME)
        return _start_pick(self, context, self.domain)

    modal = _PickModalLoop.modal
    _modal_impl = _PickModalLoop._modal_impl
    cancel = _PickModalLoop.cancel


class PDT_OT_selection_pick_instance_mode(bpy.types.Operator):
    """A slice of the second-level pie menu: how instances are picked (PICK-3)."""

    bl_idname = "pdt.selection_pick_instance_mode"
    bl_label = "Instance Pick Mode"
    bl_description = (
        "'Point Cloud' bakes one point per instance at its origin; 'Point Cloud with Reference' "
        "is that same bake with the source object left visible; 'Realized Instance' realizes the "
        "instances and lets the real geometry be picked, folding the selection back into "
        "instance numbers"
    )
    bl_options = {"REGISTER", "UNDO"}

    mode: bpy.props.StringProperty(name="Mode", default=DEFAULT_INSTANCE_MODE)

    def execute(self, context):
        if self.mode not in INSTANCE_MODES:
            self.report({"ERROR"}, f"unknown instance pick mode '{self.mode}'")
            return {"CANCELLED"}
        return _start_pick(self, context, "INSTANCE", self.mode)

    modal = _PickModalLoop.modal
    _modal_impl = _PickModalLoop._modal_impl
    cancel = _PickModalLoop.cancel


class PDT_OT_selection_pick_modal(bpy.types.Operator):
    bl_idname = "pdt.selection_pick_modal"
    bl_label = "Pick Selection (Viewport)"
    bl_description = (
        "Pick geometry elements in the viewport: put the cursor on a node's string parameter "
        "socket, then choose the element kind in the pie menu; Enter applies, Esc or "
        "Right-Click cancels"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        space = getattr(context, "space_data", None)
        return space is not None and space.type == "NODE_EDITOR"

    def invoke(self, context, event):
        return pick_from_cursor(self, context, event)

    def execute(self, context):
        # The event-less entry (the search menu): the pointer still has to be on the socket,
        # which the query reads from the position the event system last saw.
        return pick_from_cursor(self, context, None)

    modal = _PickModalLoop.modal
    _modal_impl = _PickModalLoop._modal_impl
    cancel = _PickModalLoop.cancel


class PDT_OT_selection_pick(bpy.types.Operator):
    """Same picker as `pdt.selection_pick_modal`, under its original id.

    This used to be a two step operator that only opened the session. A session
    without a modal loop cannot hear Enter/Esc/Right-Click at all - a keymap item
    the user had assigned to it silently shadowed the modal picker, and the pick
    then ran with Esc and Right-Click doing nothing (Right-Click opened the
    edit-mode context menu instead). Both ids now run the same trigger, so every
    entry point behaves identically.

    The methods are *copied* rather than inherited: registering a subclass of an
    already registered operator leaves the base type's RNA poll broken in Blender
    5.2 (`bpy.ops.pdt.selection_pick_modal.poll()` returns False although the
    Python poll returns True), which makes the operator unusable.
    """

    bl_idname = "pdt.selection_pick"
    bl_label = "Pick Selection From Viewport"
    bl_description = (
        "Bake the geometry at the node under the cursor into a temporary object, enter the "
        "matching edit mode, then Enter applies the selection and Esc / Right-Click cancels it"
    )
    bl_options = {"REGISTER", "UNDO"}

    poll = PDT_OT_selection_pick_modal.poll
    invoke = PDT_OT_selection_pick_modal.invoke
    execute = PDT_OT_selection_pick_modal.execute
    modal = _PickModalLoop.modal
    cancel = _PickModalLoop.cancel
    _modal_impl = _PickModalLoop._modal_impl


#: The classes this module contributes to `operators.classes`.
classes = (
    PDT_OT_selection_pick_domain,
    PDT_OT_selection_pick_instance_mode,
    PDT_OT_selection_pick_modal,
    PDT_OT_selection_pick,
)
