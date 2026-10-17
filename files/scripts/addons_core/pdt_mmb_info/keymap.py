# SPDX-FileCopyrightText: 2025 Blender Foundation
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The one keymap write this add-on makes: a single **unbound** item (MMB-8).

MMB-8 asks for two things that look contradictory - no default shortcut, and a user who
finds the operator in Blender's Keymap editor and assigns a key. The second half needs an
item to exist: that editor's "Search by Name" field only filters the items a keymap already
has, so an operator that appears in no keymap cannot be searched for at all. The item
registered here carries `type="NONE"` and binds nothing; it only makes the operator findable.
The full rationale and the measurements are in the block below.
"""

from __future__ import annotations

import bpy

from .utils.constants import MODAL_IDNAME
from .utils.log import _log


# ---------------------------------------------------------------------------
# the one keymap write: an *unbound* item, so the operator can be found at all
# ---------------------------------------------------------------------------
# `spec/node_info.md` MMB-8: **no default shortcut** - the add-on never decides a key for
# the user. It does have to make the operator *findable* in Blender's own Keymap editor,
# and that editor's "Search by Name" field only filters the items that **already exist**
# (`rna_keymap_ui.draw_filtered`: `filter_text in kmi.idname or filter_text in kmi.name`).
# An operator with no item anywhere cannot be searched for, so on a factory-fresh Blender
# there was no way at all to reach this operator - reported by the user as "start with
# factory settings, enable the add-on, search the Keymap: nothing".
#
# So exactly **one** item is registered, in the **addon** keyconfig, with `type="NONE"`:
# the same unassigned item the Keymap editor's own *Add New* button creates. It carries no
# key (MMB-8 is untouched by that), but it puts the operator into the Node Editor keymap,
# where the user finds it by name and picks a key - "搜到operator并手改快捷键".
#
# Why the addon keyconfig and not the user's: it is merged *on top of* the user/default
# keymaps for dispatch and is **not** stored in `userpref.blend`. Writing into
# `keyconfigs.user` at register time leaves a user keymap holding only our items, which
# shadows the built-in keymap and kills viewport navigation bound there - the accident
# `pdt_geo_select` documents and repairs (`register_addon_keymap` there does the same
# thing, with a real default key; ours is unbound).
#
# What was measured before choosing an unbound item over writing nothing (probe
# `qa/mmb/mmb_keymap_probe.py`, real window, factory startup):
#   * add-on enabled, nothing written -> the Keymap name search for "node info" and for
#     "pdt" both find **0** items (the bug);
#   * one unbound addon item created -> after the event loop merges the keyconfig the same
#     search finds **1** item, `('Node Editor', 'pdt.node_info_modal', 'PDT Node Info',
#     'NONE', 'PRESS')`, and it stays a single item.
#
# The measurements behind the "no default shortcut" half of MMB-8 are kept in
# `docs/09-MMB节点信息.md` §5.5 (an older build bound `Alt+MMB` into the user keymap; it
# worked, and it is exactly what MMB-8 now forbids). What was learned there is still worth
# knowing when *choosing* a key: a plain MMB, and Shift/Ctrl+MMB, never reach an add-on
# operator in the Node Editor, while Alt+MMB does.
# (The identifiers are defined once, near `OPERATOR_LABEL` at the top of the module.)

KEYMAP_NAME = "Node Editor"


KEYMAP_SPACE_TYPE = "NODE_EDITOR"


#: No key: `NONE` is what the Keymap editor itself writes for a freshly added item, and the
#: only event type that cannot be dispatched to.
KEYMAP_ITEM = {"type": "NONE", "value": "PRESS"}


#: The items this module added, so `unregister()` can take them back out.
_addon_keymap_items: list = []


def _keyconfig(name):
    """One of `addon` / `user` / `default`, or None when there is no window manager yet."""
    try:
        keyconfigs = bpy.context.window_manager.keyconfigs
    except Exception:  # noqa: BLE001 - no window manager (early registration)
        return None
    return getattr(keyconfigs, name, None)


def _node_editor_keymaps(keyconfig):
    """Every Node Editor keymap of `keyconfig`, found by name plus space.

    Deliberately not `keyconfig.keymaps.get(KEYMAP_NAME)`: that lookup answered `None` for
    the keymap this module had just created (measured in `qa/mmb/mmb_keymap_probe.py`), while
    iterating the same collection finds it. `name` + `space_type` is also what
    `WM_keymap_list_find()` matches on when the keyconfigs are merged, so this is the same
    key the engine uses.
    """
    if keyconfig is None:
        return []
    return [
        keymap for keymap in keyconfig.keymaps
        if keymap.name == KEYMAP_NAME and keymap.space_type == KEYMAP_SPACE_TYPE
    ]


def _addon_keymap(create=True):
    """The addon keyconfig's Node Editor keymap; created when `create`."""
    keyconfig = _keyconfig("addon")
    if keyconfig is None:
        return None
    found = _node_editor_keymaps(keyconfig)
    if found:
        return found[0]
    if not create:
        return None
    return keyconfig.keymaps.new(
        name=KEYMAP_NAME, space_type=KEYMAP_SPACE_TYPE, region_type="WINDOW"
    )


def register_addon_keymap() -> int:
    """Add the single **unbound** item once; returns how many items were added.

    Idempotent: `register()` can run again (add-on reload), and a second item would show up
    twice in the Keymap editor.
    """
    keymap = _addon_keymap()
    if keymap is None:
        return 0
    for item in keymap.keymap_items:
        if item.idname == MODAL_IDNAME:
            # Already there (register() ran twice, or the module was reloaded): remember it so
            # `unregister()` still takes it back out, without listing the same item twice.
            if not any(existing is item for _km, existing in _addon_keymap_items):
                _addon_keymap_items.append((keymap, item))
            return 0
    item = keymap.keymap_items.new(MODAL_IDNAME, **KEYMAP_ITEM)
    _addon_keymap_items.append((keymap, item))
    return 1


def unregister_addon_keymap() -> None:
    """Remove the item(s) this module added, leaving the addon keymap itself in place."""
    for keymap, item in list(_addon_keymap_items):
        try:
            keymap.keymap_items.remove(item)
        except Exception:  # noqa: BLE001 - already gone (file reload / keymap rebuilt)
            pass
    _addon_keymap_items.clear()


def item_is_bound(item) -> bool:
    """True when a keymap item actually carries a key.

    `NONE` is the unassigned placeholder this add-on registers (and what the Keymap editor
    writes for a fresh item), so it must not be reported as "the shortcut works".
    """
    return str(getattr(item, "type", "NONE")) != "NONE" and bool(getattr(item, "active", True))


def keymap_item_is_bound(keymap) -> bool:
    """True when `keymap` holds a bound item for this operator."""
    return any(
        item.idname == MODAL_IDNAME and item_is_bound(item) for item in keymap.keymap_items
    )


def binding_is_active(context=None):
    """True when the user has **assigned a key** to `pdt.node_info_modal`.

    Reported in the startup log so a user can see whether the key they picked is the one
    Blender actually dispatches. **The add-on never assigns a key** (MMB-8): the item it
    registers carries `type="NONE"`, which this deliberately does not count - otherwise the
    status would claim "the key works" while nothing is bound.

    Event dispatch reads the **user** keymap (with the addon keyconfig merged on top), so
    those two are what is checked.
    """
    context = context or bpy.context
    for keyconfig_name in ("user", "addon"):
        keyconfig = _keyconfig(keyconfig_name)
        for keymap in _node_editor_keymaps(keyconfig):
            if keymap_item_is_bound(keymap):
                return True
    return False


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def register() -> int:
    """Register the unbound item once; returns how many items were added.

    The add-on itself computes `binding_is_active()` for its startup log line - this only
    puts the item in place.
    """
    added = register_addon_keymap()
    _log("keymap-items-added", count=added, keymap=KEYMAP_NAME, item=MODAL_IDNAME)
    return added


def unregister() -> None:
    unregister_addon_keymap()

