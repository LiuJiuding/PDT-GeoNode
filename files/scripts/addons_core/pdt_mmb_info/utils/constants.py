# SPDX-FileCopyrightText: 2025 Blender Foundation
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Every fixed number and table this add-on uses.

`spec/node_info.md` MMB-8 allows exactly four kinds of user setting (the input-only toggle,
the text-size slider, one colour per domain sub-class, one attribute colour). Everything
else lives here as a constant, so "can the user change this?" has one answer per name: if it
is not in `preferences.py`, it is fixed by the L0.

The colour tables are *defaults* for those four settings, not constants in the strict sense
- the user's picks override them through `utils.palette.palette_from_settings()`.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

GEOMETRY_SOCKET = "NodeSocketGeometry"


#: Node types whose geometry output is a *result* rather than a pass-through of the
#: object's own data; nothing to special-case today, but the name is kept because the
#: "input-only" rule below reads better with it.
TEMP_PREFIX = "PDT_MMB_"


# ---------------------------------------------------------------------------
# fixed values (they used to be preferences)
# ---------------------------------------------------------------------------
# `spec/node_info.md` MMB-8 allows four kinds of user setting - the input-only toggle, the
# text-size slider, one colour per domain sub-class, and the attribute colour - so
# everything else lives here as a constant. The colours themselves are *defaults* for those
# settings, not constants: see `DEFAULT_COLORS` below.

#: Body text size at `text_scale = 1.0`; the preference scales this and every spacing
#: derived from it (MMB-8: "信息块文字整体缩放").
BASE_FONT = 12


#: Widest the flowing text may get before it wraps, at `text_scale = 1.0`. The reference
#: layout (`spec/node_info_ui_v2.png`) puts one component's domains on one line and wraps the
#: attribute items, so the block needs a wrap width rather than a fixed column table.
MAX_TEXT_WIDTH = 420


#: Attributes listed per domain before "… N more" (there is no scrolling: L0 边界).
MAX_ITEMS_PER_GROUP = 24


#: Distance in pixels between the cursor and the block at the moment it appears
#: (the block stays there: L0 边界 "信息块不实时跟随光标").
CURSOR_OFFSET = 18


#: The operator's UI label, i.e. the name the Keymap editor shows for its item and the
#: string the Keymap name search matches. Used for the class and for MMB-8's reminder, so
#: the two can never drift apart.
OPERATOR_LABEL = "PDT Node Info"


#: The modal operator's identifier, as Blender's keymap items and `binding_is_active()`
#: spell it. One constant, because the keymap section is not the only place that needs it.
MODAL_IDNAME = "pdt.node_info_modal"


#: `spec/node_info.md` MMB-8's reminder, verbatim, with its "Operator Name" replaced by the
#: actual operator name. This is the **only** shortcut hint the add-on shows.
REMINDER = f"Assign one shortcut yourself: Preferences>Keymap>{OPERATOR_LABEL}"


#: Print one line per press to the system console. The log file is written regardless
#: (MMB-9), so this is only for a console session and is off by default.
VERBOSE_DIAGNOSTICS = False


# ---------------------------------------------------------------------------
# the palette (defaults for the colour preferences - MMB-8 items 3 and 4)
# ---------------------------------------------------------------------------

#: The domain sub-classes, in the order the counts table lists them. One colour preference
#: per entry, plus the two attribute colours below.
DOMAIN_COLOR_SLOTS = (
    "vertex",
    "edge",
    "face",
    "corner",
    "control_point",
    "spline",
    "point",
    "instances",
    "grid",
)


#: Default colours, keyed by slot: one per domain sub-class, plus the uniform attribute
#: colour and the colour of an attribute the node added. L0 MMB-5 gives every domain its own
#: colour and asks for the *same* colour in both halves ("domain子类颜色要和上方信息颜色保持一致"),
#: which is why a slot - not a component - is what carries it.
#:
#: These are defaults, not constants: all eleven are colour preferences. The hues keep the
#: reference image's associations (Vertex blue, Edge orange, Face cyan, Face Corner green,
#: Control Point violet, Spline red), but this is deliberately **not** a pixel copy of
#: `spec/node_info_ui_v2.png` - the user's call was that the reference's colours are rough
#: and that the default should be a harmonious palette, so saturation and lightness are
#: levelled into a band that stays readable on the dark node editor.
DEFAULT_COLORS = {
    "vertex": (0.524, 0.707, 0.916),          # #86B4EA  blue
    "edge": (0.916, 0.707, 0.524),            # #EAB486  orange
    "face": (0.514, 0.824, 0.886),            # #83D2E2  cyan
    "corner": (0.649, 0.849, 0.631),          # #A5D9A1  green
    "control_point": (0.779, 0.676, 0.924),   # #C7ACEC  violet
    "spline": (0.918, 0.602, 0.628),          # #EA99A0  red
    "point": (0.900, 0.700, 0.827),           # #E6B3D3  pink
    "instances": (0.813, 0.742, 0.707),       # #CFBDB4  warm grey
    "grid": (0.883, 0.826, 0.597),            # #E1D398  yellow
    "attribute": (0.782, 0.789, 0.818),       # #C7C9D1  the plain body colour
    "added": (1.000, 0.707, 0.200),           # #FFB433  "this node added it"
}


#: `(component kind, RNA domain)` -> colour slot. Attribute rows carry Blender's own domain
#: (`POINT` / `EDGE` / `FACE` / `CORNER` / `CURVE` / `SPLINE`), while the counts table knows
#: the same domains; both look their colour up here, so a domain cannot end up with one
#: colour above and another below.
DOMAIN_SLOTS = {
    ("MESH", "POINT"): "vertex",
    ("MESH", "EDGE"): "edge",
    ("MESH", "FACE"): "face",
    ("MESH", "CORNER"): "corner",
    ("CURVES", "POINT"): "control_point",
    ("CURVES", "CURVE"): "spline",
    ("CURVES", "SPLINE"): "spline",
    ("POINTCLOUD", "POINT"): "point",
    ("INSTANCES", "POINT"): "instances",
    ("VOLUME", "GRID"): "grid",
}
