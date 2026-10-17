# SPDX-FileCopyrightText: 2026 PDT Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The add-on's only keymap write: **one unbound item** in the addon keyconfig (L0 `PICK-4`:
no default shortcut - the user assigns one), plus the repair that cleans up the
user-keyconfig damage older versions could cause (never a key the user bound)."""

from __future__ import annotations

import bpy

from .utils.constants import PICKER_IDNAMES, PICKER_MODAL_IDNAME, PICK_KEYMAP_ITEM, PICK_KEYMAP_NAME

_addon_keymap_items: list = []


#: Items earlier versions registered in the *user* keyconfig and no longer want.
LEGACY_FALLBACK_IDNAMES = ("pdt.selection_apply", "pdt.selection_cancel")


LEGACY_FALLBACK_KEYS = (
    ("RET", False, False, False),
    ("ESC", False, False, False),
    ("RIGHTMOUSE", False, False, False),
)


def _keyconfig(name: str):
    try:
        keyconfigs = bpy.context.window_manager.keyconfigs
    except Exception:  # noqa: BLE001 - no window manager yet
        return None
    return getattr(keyconfigs, name, None)


def _addon_keymap(name: str, space_type: str, create: bool = True):
    """A keymap of the *addon* keyconfig (merged, never shadowing).

    Found by iterating `name` + `space_type` rather than `keymaps.get(name)`: `.get()` answered
    `None` for a keymap this add-on had just created (measured for the MMB add-on,
    `pdt_mmb_info/keymap.py`), while the same lookup by iteration finds it. `name` +
    `space_type` is also what `WM_keymap_list_find()` matches on when keyconfigs merge.
    """
    keyconfig = _keyconfig("addon")
    if keyconfig is None:
        return None
    found = [
        keymap
        for keymap in keyconfig.keymaps
        if keymap.name == name and keymap.space_type == space_type
    ]
    if found:
        return found[0]
    if not create:
        return None
    return keyconfig.keymaps.new(name=name, space_type=space_type, region_type="WINDOW")


def register_addon_keymap() -> int:
    """Put the one **unbound** item in place; returns how many items were added.

    PICK-4: the add-on never decides a key for the user. What it does have to do is make the
    operator *findable* in Blender's own Keymap editor, whose "Search by Name" field only
    filters the items a keymap **already** has (`rna_keymap_ui.draw_filtered`) - an operator
    that appears in no keymap cannot be searched for, let alone bound. So exactly one item is
    created, in the **addon** keyconfig (merged on top of the user/default keymaps, never
    stored in `userpref.blend`), carrying `type="NONE"` - the unassigned placeholder the
    Keymap editor's own *Add New* button writes.
    """
    keymap = _addon_keymap(PICK_KEYMAP_NAME, "NODE_EDITOR")
    if keymap is None:
        return 0
    for item in keymap.keymap_items:
        if item.idname == PICKER_MODAL_IDNAME:
            # Already there (register can run again after a reload); remember it, so
            # `unregister()` still takes it back out.
            if not any(existing is item for _keymap, existing in _addon_keymap_items):
                _addon_keymap_items.append((keymap, item))
            return 0
    item = keymap.keymap_items.new(PICKER_MODAL_IDNAME, **PICK_KEYMAP_ITEM)
    _addon_keymap_items.append((keymap, item))
    return 1


def unregister_addon_keymap() -> None:
    """Remove the item(s) this module added; never touch a key the user bound themselves."""
    for keymap, item in list(_addon_keymap_items):
        try:
            keymap.keymap_items.remove(item)
        except Exception:  # noqa: BLE001 - already gone
            pass
    _addon_keymap_items.clear()


def _keymaps_named(keyconfig, name: str):
    return [keymap for keymap in keyconfig.keymaps if keymap.name == name]


def item_is_bound(item) -> bool:
    """True when a keymap item actually carries a key (PICK-4: ours never does)."""
    return str(getattr(item, "type", "NONE")) != "NONE" and bool(getattr(item, "active", True))


def picker_shortcut_items():
    """Picker keymap items that **carry a key**, user-level first.

    The unbound item this add-on registers is deliberately not reported as "the shortcut":
    it binds nothing, so counting it would claim a key works when none is assigned.
    """
    found = []
    for keyconfig_name in ("user", "addon"):
        keyconfig = _keyconfig(keyconfig_name)
        if keyconfig is None:
            continue
        for keymap in _keymaps_named(keyconfig, PICK_KEYMAP_NAME):
            found.extend(
                item
                for item in keymap.keymap_items
                if item.idname in PICKER_IDNAMES and item_is_bound(item)
            )
    return found


def describe_shortcut(item) -> str:
    """'Ctrl+Shift+P' for a keymap item; 'not assigned' when there is none."""
    if item is None:
        return "not assigned"
    parts = [name for name, on in (("Ctrl", item.ctrl), ("Shift", item.shift), ("Alt", item.alt)) if on]
    parts.append(str(item.type).replace("_", " ").title())
    return "+".join(parts)


def picker_shortcut_label() -> str:
    items = picker_shortcut_items()
    return describe_shortcut(items[0] if items else None)


def repair_keymaps() -> list[str]:
    """Undo the user-keyconfig damage older versions of this add-on could cause.

    Only touches keymaps that contain nothing but our own items (which can only
    be a keymap this add-on created by accident), plus the legacy Enter/Esc/RMB
    fallback items it used to add. Returns the names of the repaired keymaps.

    Two things this has to get right, and the first version got both wrong:

    * **When**: at `register()` time the *user keyconfig is not loaded yet*.
      Measured on this build: 0 keymaps inside `register()`, 108 by the time a
      file has been loaded. So the repair used to iterate an empty list, return
      `[]` and print nothing - a silent no-op on every startup, while the
      damaged keymap stayed in `userpref.blend` forever. It therefore also runs
      from `_on_load_post` (which fires right after the keymaps appear) and from
      a one-shot startup timer.
    * **How**: `keyconfig.keymaps.remove()` only drops the *runtime* keymap. The
      damaged keymap is also stored in `U.user_keymaps`, and a stored keymap
      without `KEYMAP_DIFF` makes Blender override the built-in one entirely
      ("For compatibility with old user preferences with non-diff keymaps we
      override the original entirely", `wm_keymap_patch_update`), so the damage
      came back on the next keyconfig rebuild. `KeyMap.restore_to_default()`
      deletes the stored entry and rebuilds the keymap from the built-in one.
    """
    keyconfig = _keyconfig("user")
    if keyconfig is None:
        return []
    # Collect first: restoring a keymap rebuilds the keyconfig, which invalidates
    # the pointers of the list we are iterating.
    damaged: list[tuple[str, str]] = []
    for keymap in keyconfig.keymaps:
        if _is_damaged_keymap(keymap):
            damaged.append((keymap.name, keymap.space_type))
            continue
        # Mixed keymap (real user items in it): only drop our legacy fallbacks.
        for item in list(keymap.keymap_items):
            signature = (item.type, bool(item.ctrl), bool(item.shift), bool(item.alt))
            if item.idname in LEGACY_FALLBACK_IDNAMES and signature in LEGACY_FALLBACK_KEYS:
                idname, key = item.idname, item.type
                keymap.keymap_items.remove(item)
                print(f"PDT picker: removed legacy fallback keymap item '{idname}' ({key})")

    removed = []
    for name, space_type in damaged:
        keymap = _find_user_keymap(keyconfig, name, space_type)
        if keymap is None:
            continue
        try:
            keymap.restore_to_default()
        except Exception as exc:  # noqa: BLE001 - no such API / no window manager
            print(f"PDT picker: restoring '{name}' failed ({exc})")
        # `restore_to_default()` only helps for a keymap that was *persisted* in
        # `U.user_keymaps`: it deletes that stored entry and rebuilds the keymap
        # from the built-in one. A keymap that only ever existed in the runtime
        # keyconfig has no stored entry, so the call does nothing and the shadow
        # is still there - drop it instead.
        keymap = _find_user_keymap(keyconfig, name, space_type)
        if keymap is not None and _is_damaged_keymap(keymap):
            keyconfig.keymaps.remove(keymap)
        removed.append(name)
    if removed:
        print(
            "PDT picker: repaired user keymap(s) that this add-on had created by accident "
            f"({', '.join(removed)}); Blender's built-in bindings are active again"
        )
    return removed


def _is_damaged_keymap(keymap) -> bool:
    """True for a keymap holding nothing but our own items (the accident shape)."""
    items = list(keymap.keymap_items)
    return bool(items) and all(item.idname.startswith("pdt.") for item in items)


def _find_user_keymap(keyconfig, name: str, space_type: str):
    return next(
        (km for km in keyconfig.keymaps if km.name == name and km.space_type == space_type),
        None,
    )


def _repair_keymaps_when_ready() -> None:
    """One-shot startup timer: run the repair once the key configuration exists.

    `load_post` already covers the normal startup, but a timer also covers a
    startup where no file is loaded at all.
    """
    repair_keymaps()
    return None  # returning None unregisters the timer


def register() -> int:
    """Clean up older versions' damage, then put the one unbound item in place (PICK-4)."""
    repair_keymaps()
    added = register_addon_keymap()
    try:
        if not bpy.app.timers.is_registered(_repair_keymaps_when_ready):
            bpy.app.timers.register(_repair_keymaps_when_ready, first_interval=0.0)
    except Exception as exc:  # noqa: BLE001 - timers need a window manager
        print(f"PDT picker: startup keymap repair timer not scheduled ({exc})")
    return added


def unregister() -> None:
    unregister_addon_keymap()
    try:
        if bpy.app.timers.is_registered(_repair_keymaps_when_ready):
            bpy.app.timers.unregister(_repair_keymaps_when_ready)
    except Exception:  # noqa: BLE001 - no window manager any more
        pass
