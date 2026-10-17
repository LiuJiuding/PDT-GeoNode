# SPDX-FileCopyrightText: 2026 PDT Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The two pie menus of the trigger (L0 `PICK-1`): the seven element kinds, then the three
instance modes. Both are radial layouts (`layout.menu_pie()`), and the slice list is built
once - when the pie opens - and travels through `state.pending_pick`."""

from __future__ import annotations

import bpy

from .utils.constants import DOMAINS, DOMAIN_ICONS, DOMAIN_KINDS, DOMAIN_LABELS, DOMAIN_PIE_IDNAME, INSTANCE_MODES, INSTANCE_MODE_ICONS, INSTANCE_MODE_LABELS, INSTANCE_MODE_PIE_IDNAME, _kind_list
from .geometry import available_geometry_kinds
from . import state as pick_state

def domain_pie_entries(node, tree, context=None) -> list[dict]:
    """The first-level pie's slices: one per element kind (PICK-1).

    Always the same seven kinds as the node's own `Domain` property (L0 PDS-9), so a pick can
    be aimed at any element kind even on a node that has no such property. The choice decides
    which component is baked and which numbers are written; it does **not** rewrite the node,
    and the node's own `Domain` is not consulted (L0 `明确不做`).
    The order is `DOMAINS`', which is also the order the node's `Domain` menu lists - and, with
    `layout.menu_pie()`'s slot order, the order the slices fan out in.

    Built **once**, when the pie opens (it takes one temporary evaluation to know which
    components this geometry really has) and then kept in `state.pending_pick` - a pie's `draw`
    runs again for every mouse move, and evaluating the tree in there would crawl. A kind this
    geometry does not have keeps its slot but draws nothing (see `domain_pie_slots()`).
    """
    context = context or bpy.context
    available = available_geometry_kinds(tree, node, context) if tree is not None else []
    components = [kind for kind, _elements in available]

    entries = []
    for domain in DOMAINS:
        kind = DOMAIN_KINDS.get(domain, "")
        present = kind in components
        label = DOMAIN_LABELS.get(domain, domain)
        description = f"Indices address {label.lower()} elements"
        if not present:
            description += f" - not in this geometry (it has: {_kind_list(components)})"
        entries.append(
            {
                "identifier": domain,
                "label": label,
                "description": description,
                "present": present,
                "icon": DOMAIN_ICONS[domain],
            }
        )
    return entries


def domain_pie_slots(entries: list[dict]) -> list[dict]:
    """Which item each element kind puts into the pie, **one item per kind, in `DOMAINS` order**.

    A radial menu hands its slots out by counting the items that are displayable in it
    (`LayoutRadial::resolve_impl()`, `interface_layout.cc`): west, east, south, north, then the
    four diagonals. A kind this geometry does not have must not be *shown* (L0 PICK-1: 当前节点上
    没有的类型不出现), and dropping its item outright would slide every later kind one slot over -
    `Point` would jump into the west slot when there are no vertices. So the missing kinds are
    represented by a `SPACE` separator, which still counts for the slot but draws nothing at all
    (`ButtonType::SeprSpacer` is skipped by the widget code) and cannot be clicked
    (`button_contains_point_px()` refuses anything that is not a button).

    Returned as data (not drawn) so a test can assert the slot plan without a window: each
    element is either `{"kind": "slice", "identifier", "label", "icon"}` or
    `{"kind": "placeholder", "identifier", "label": ""}` - the placeholder deliberately has
    nothing a caller could paint.
    """
    slots = []
    for entry in entries:
        if entry.get("present"):
            slots.append(
                {
                    "kind": "slice",
                    "identifier": entry["identifier"],
                    "label": entry["label"],
                    "icon": entry.get("icon", "NONE"),
                }
            )
        else:
            slots.append(
                {
                    "kind": "placeholder",
                    "identifier": entry["identifier"],
                    "label": "",
                }
            )
    return slots


def instance_mode_pie_entries() -> list[dict]:
    """The second-level pie's slices: PICK-3's three instance modes, in that order."""
    descriptions = {
        "POINTS": (
            "One point per instance at its origin; the source object is hidden. Cheap at any "
            "instance count, but you click the origins, not the instanced geometry"
        ),
        "POINTS_REFERENCE": (
            "The same points at the instance origins, with the source object left visible so "
            "you can tell which origin is which"
        ),
        "REALIZED": (
            "Realize the instances and pick the real geometry; the picked elements are folded "
            "back into instance numbers"
        ),
    }
    return [
        {
            "identifier": mode,
            "label": INSTANCE_MODE_LABELS[mode],
            "description": descriptions[mode],
            "icon": INSTANCE_MODE_ICONS[mode],
        }
        for mode in INSTANCE_MODES
    ]


class PDT_MT_selection_domain_pie(bpy.types.Menu):
    """First-level pie menu: the element kind this pick addresses (PICK-1).

    The slices go into `layout.menu_pie()` - a `LayoutRadial` (`interface_layout.cc`), which is
    what fans them out around the cursor. Adding them to the plain block layout puts every item
    in one column, right on top of each other. Slot order is `radial_dir_order`: west, east,
    south, north, then NW / NE / SW / SE, so `DOMAINS`' own order reads left, right, down, up,
    top-left, top-right, bottom-left - the same order the node's `Domain` menu lists.

    Each slice carries its element kind's own Blender icon (`DOMAIN_ICONS`). A kind this
    geometry does not have keeps its slot but is drawn as a **`SPACE` separator** instead of a
    button: nothing is painted there, so the kind simply does not appear (L0 PICK-1), and the
    kinds that *are* there stay in their own places instead of sliding into the free slot.
    `domain_pie_slots()` decides that plan; `draw()` only performs it.
    """

    bl_idname = DOMAIN_PIE_IDNAME
    bl_label = "Pick Element Kind"

    def draw(self, context):
        pie = self.layout.menu_pie()
        entries = pick_state.pending_pick.get("domain_items") or []
        if not entries:
            pie.label(text="No node to pick on", icon="ERROR")
            return
        for slot in domain_pie_slots(entries):
            if slot["kind"] == "placeholder":
                # Invisible, unclickable, and still one slot - see `domain_pie_slots()`.
                pie.separator(type="SPACE")
                continue
            pie.operator(
                "pdt.selection_pick_domain",
                text=slot["label"],
                icon=slot["icon"] or "NONE",
            ).domain = slot["identifier"]


class PDT_MT_selection_instance_mode_pie(bpy.types.Menu):
    """Second-level pie menu: how instances are picked (PICK-3).

    Three slices, so they land on the west / east / south slots (see the domain pie above for
    the slot order). Each carries its mode's Blender icon; all three are always present.
    """

    bl_idname = INSTANCE_MODE_PIE_IDNAME
    bl_label = "Instance Pick Mode"

    def draw(self, context):
        pie = self.layout.menu_pie()
        for entry in instance_mode_pie_entries():
            pie.operator(
                "pdt.selection_pick_instance_mode",
                text=entry["label"],
                icon=entry["icon"] or "NONE",
            ).mode = entry["identifier"]


#: Every menu class this module registers.
classes = (PDT_MT_selection_domain_pie, PDT_MT_selection_instance_mode_pie)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
