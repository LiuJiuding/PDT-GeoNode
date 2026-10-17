# SPDX-FileCopyrightText: 2025 Blender Foundation
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The settings MMB-8 allows, the panel that shows them, and the "Reset Colours" button.

Four kinds of setting and nothing else: the input-only toggle, the whole-block text scale,
one colour per domain sub-class, and the two attribute colours. The panel also prints
MMB-8's one-sentence shortcut reminder and nothing more about the key - assigning it is the
user's job (`keymap.py` explains why, and only makes the operator findable).
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty

from .utils.constants import DEFAULT_COLORS, DOMAIN_COLOR_SLOTS, REMINDER
from .utils.palette import color_pref_name


# ---------------------------------------------------------------------------
# preferences
# ---------------------------------------------------------------------------

class PDTMMBPreferences(bpy.types.AddonPreferences):
    """The settings `spec/node_info.md` MMB-8 allows, and nothing else.

    Four kinds: the input-only toggle, the text-size slider, one colour per domain
    sub-class, and the attribute colours. Everything that used to be a switch here (input
    stream, attribute table, hash, changed-row highlight, row limit, offset, re-read
    interval, pin key, console diagnostics, and the automatic `Alt+MMB` binding) is a fixed
    value at the top of this module. The shortcut in particular is **not** written by the
    add-on any more: MMB-8 says the user assigns it in `Preferences > Keymap`, and the panel
    below only reports whether that has happened.
    """

    bl_idname = __package__ or "pdt_mmb_info"

    show_input_only: BoolProperty(
        name="Show input-only nodes",
        description=(
            "Nodes with a geometry input but no geometry output (field-only nodes) "
            "show nothing by default; enable this to see their input stream"
        ),
        default=False,
    )
    text_scale: FloatProperty(
        name="Layout · Text size",
        description=(
            "Scales the whole information block: its text, the domain labels and numbers, "
            "the attribute headings, and every spacing that separates them (MMB-8)"
        ),
        default=1.0,
        min=0.6,
        max=2.0,
    )

    color_vertex: FloatVectorProperty(
        name="Vertex", description="Colour of the Vertex domain (MESH/POINT)",
        subtype="COLOR", size=3, min=0.0, max=1.0, default=DEFAULT_COLORS["vertex"],
    )
    color_edge: FloatVectorProperty(
        name="Edge", description="Colour of the Edge domain (MESH/EDGE)",
        subtype="COLOR", size=3, min=0.0, max=1.0, default=DEFAULT_COLORS["edge"],
    )
    color_face: FloatVectorProperty(
        name="Face", description="Colour of the Face domain (MESH/FACE)",
        subtype="COLOR", size=3, min=0.0, max=1.0, default=DEFAULT_COLORS["face"],
    )
    color_corner: FloatVectorProperty(
        name="Face Corner", description="Colour of the Face Corner domain (MESH/CORNER)",
        subtype="COLOR", size=3, min=0.0, max=1.0, default=DEFAULT_COLORS["corner"],
    )
    color_control_point: FloatVectorProperty(
        name="Control Point", description="Colour of the Control Point domain (CURVES/POINT)",
        subtype="COLOR", size=3, min=0.0, max=1.0, default=DEFAULT_COLORS["control_point"],
    )
    color_spline: FloatVectorProperty(
        name="Spline", description="Colour of the Spline domain (CURVES/CURVE)",
        subtype="COLOR", size=3, min=0.0, max=1.0, default=DEFAULT_COLORS["spline"],
    )
    color_point: FloatVectorProperty(
        name="Point", description="Colour of the Point domain (POINTCLOUD/POINT)",
        subtype="COLOR", size=3, min=0.0, max=1.0, default=DEFAULT_COLORS["point"],
    )
    color_instances: FloatVectorProperty(
        name="Instances", description="Colour of the Instances component",
        subtype="COLOR", size=3, min=0.0, max=1.0, default=DEFAULT_COLORS["instances"],
    )
    color_grid: FloatVectorProperty(
        name="Grid", description="Colour of the Grid domain (VOLUME)",
        subtype="COLOR", size=3, min=0.0, max=1.0, default=DEFAULT_COLORS["grid"],
    )
    color_attribute: FloatVectorProperty(
        name="Attribute name & type",
        description=(
            "One colour for every attribute name and type in the lower half "
            "(MMB-5: “属性颜色统一”)"
        ),
        subtype="COLOR", size=3, min=0.0, max=1.0, default=DEFAULT_COLORS["attribute"],
    )
    color_added: FloatVectorProperty(
        name="Attribute this node added",
        description=(
            "Attributes present in the output but not in the node's first geometry input "
            "are drawn in this colour (MMB-5)"
        ),
        subtype="COLOR", size=3, min=0.0, max=1.0, default=DEFAULT_COLORS["added"],
    )

    def draw(self, context):
        """The preferences, drawn inside this add-on's entry in the Add-ons list.

        Deliberately a method here and **not** a `bpy.types.Panel`: a standalone panel
        with `bl_space_type = "PREFERENCES"` lands in the wrong place (it drew between the
        global sections instead of under the add-on), while `AddonPreferences.draw` is what
        Blender calls for the entry's own expandable section - which is where every other
        add-on puts its settings, `pdt_light_control` among them.

        MMB-8 fixes the shortcut hint to **one** sentence and says no other hint text is
        needed, so the reminder is printed verbatim (`REMINDER`) and nothing else is said
        about the key. The colours sit behind a `layout.panel()` section (a real collapsible
        header, the same call `node_wrangler` uses for its preference groups) because eleven
        pickers would otherwise bury the two switches.
        """
        layout = self.layout

        box = layout.box()
        box.label(text=REMINDER)

        column = layout.column(heading="Content")
        column.prop(self, "show_input_only")
        column = layout.column(heading="Layout")
        column.prop(self, "text_scale")

        header, body = layout.panel("pdt_mmb_info_colors", default_closed=True)
        header.label(text="Colours")
        if body:
            body.label(text="Per domain sub-class, used by both halves of the block:")
            grid = body.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
            for slot in DOMAIN_COLOR_SLOTS:
                grid.prop(self, color_pref_name(slot))
            body.separator()
            column = body.column(heading="Attributes")
            column.prop(self, "color_attribute")
            column.prop(self, "color_added")
            body.operator("pdt.mmb_reset_colors", icon="LOOP_BACK")


_PREF_FALLBACK = None


def prefs(context=None):
    """The add-on preferences, or a live fallback when the add-on is not "enabled".

    `bpy.context.preferences.addons` only lists add-ons registered through
    `addon_utils`; a test (or a `--python-expr` harness) that calls `register()`
    directly gets none. Rather than making every caller handle None, one
    AddonPreferences entry is created on demand and kept - it is the same RNA
    type, so reading and writing it behaves identically to the real one.
    """
    global _PREF_FALLBACK
    context = context or bpy.context
    addons = context.preferences.addons
    addon = addons.get(__package__ or "pdt_mmb_info")
    if addon is not None:
        return addon.preferences
    if _PREF_FALLBACK is None:
        try:
            created = addons.new()
            created.module = __package__ or "pdt_mmb_info"
            _PREF_FALLBACK = created.preferences
        except Exception as error:  # noqa: BLE001
            print(f"PDT node info: no preferences available ({error})")
            return None
    return _PREF_FALLBACK


class PDT_OT_mmb_reset_colors(bpy.types.Operator):
    """Put every domain and attribute colour back to the default palette."""

    bl_idname = "pdt.mmb_reset_colors"
    bl_label = "Reset Colours"
    bl_description = (
        "Restore the default colours for every domain sub-class and for the attribute "
        "names (the stored preferences are overwritten; Save Preferences keeps them)"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        settings = prefs(context)
        if settings is None:
            self.report({"ERROR"}, "no preferences available")
            return {"CANCELLED"}
        restored = 0
        for slot, rgb in DEFAULT_COLORS.items():
            name = color_pref_name(slot)
            try:
                setattr(settings, name, rgb)
            except Exception as error:  # noqa: BLE001 - report, never raise into the UI
                self.report({"WARNING"}, f"could not reset {name}: {error}")
                continue
            restored += 1
        self.report({"INFO"}, f"colours reset to the default palette ({restored})")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

classes = (
    PDTMMBPreferences,
    PDT_OT_mmb_reset_colors,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:  # noqa: BLE001 - already gone (add-on reload, double unregister)
            pass

