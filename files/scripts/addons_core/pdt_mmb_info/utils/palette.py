# SPDX-FileCopyrightText: 2025 Blender Foundation
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The colour palette: one slot per domain sub-class, plus the two attribute colours.

`spec/node_info.md` MMB-5 gives every domain its own colour and asks for the **same** colour
in both halves of the block ("domain子类颜色要和上方信息颜色保持一致"). A *slot* carries it,
not a component, so the counts table and the attribute groups can never disagree.
"""

from __future__ import annotations

from .constants import DEFAULT_COLORS, DOMAIN_SLOTS


def domain_slot(kind, domain):
    """The colour slot of one (component, RNA domain) pair, or `None` if unknown.

    An unknown domain still gets listed - it falls back to the uniform attribute colour
    rather than disappearing, because MMB-5 is about *what is there* and a colour is not a
    reason to drop a row.
    """
    return DOMAIN_SLOTS.get((kind, str(domain)))


def color_pref_name(slot):
    """The preference that overrides one slot: `vertex` -> `color_vertex`."""
    return f"color_{slot}"


def palette_from_settings(settings):
    """`{slot: (r, g, b, 1.0)}`: the defaults with the colour preferences on top.

    A missing or malformed value falls back to the default, so a hand-edited preferences
    file can never leave the block drawing nothing.
    """
    palette = {slot: tuple(rgb) + (1.0,) for slot, rgb in DEFAULT_COLORS.items()}
    if settings is None:
        return palette
    for slot in DEFAULT_COLORS:
        value = getattr(settings, color_pref_name(slot), None)
        if value is not None and len(value) >= 3:
            palette[slot] = (float(value[0]), float(value[1]), float(value[2]), 1.0)
    return palette
