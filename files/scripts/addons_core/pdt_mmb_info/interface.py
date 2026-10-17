# SPDX-FileCopyrightText: 2025 Blender Foundation
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Drawing the information block: layout, wrapping, fonts and the `blf`/`gpu` calls.

The reference layout is `spec/node_info_ui_v3.png`. Two text sizes only (the domain label and
the attribute group titles are body size, the count is one step above it, MMB-5), one colour
per domain sub-class taken from the palette, one colour for every attribute name and type,
and the separate `added` colour for what the node added. There is no pin, no scrolling and no
cursor follow-up (L0 边界).
"""

from __future__ import annotations

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from .utils.constants import BASE_FONT, MAX_TEXT_WIDTH
from .utils.palette import palette_from_settings


# ---------------------------------------------------------------------------
# number formatting used by the counts table
# ---------------------------------------------------------------------------


def _fmt_number(value) -> str:
    if isinstance(value, float):
        return f"{value:.5g}"
    if isinstance(value, (list, tuple)):
        return " ".join(f"{float(component):.4g}" for component in value)
    return str(value)


# ---------------------------------------------------------------------------
# drawing
# ---------------------------------------------------------------------------

_COLOR_BG = (0.09, 0.09, 0.10, 0.94)


_COLOR_BORDER = (0.35, 0.35, 0.38, 1.0)


#: Only two colours are still fixed: the panel furniture (the failure note and the "… N
#: more" marker are not content the user themes) and the border. Every text colour in the
#: block comes from `palette_from_settings()`.
_COLOR_MUTED = (0.55, 0.56, 0.60, 1.0)


def _text_width(text, font_size):
    blf.size(0, font_size)
    return blf.dimensions(0, str(text))[0]


def _flow_items(cells, max_width, gap):
    """Wrap ``[(text, font, colour)]`` cells into lines that fit `max_width`."""
    lines = []
    current = []
    used = 0.0
    for cell in cells:
        width = _text_width(cell[0], cell[1])
        if current and used + gap + width > max_width:
            lines.append(current)
            current = []
            used = 0.0
        if current:
            used += gap
        current.append(cell)
        used += width
    if current:
        lines.append(current)
    return lines


def _panel_lines(state, body, count_font, max_width, palette):
    """The block as drawn lines: ``([{"cells", "tallest", "height"}], gap)``.

    Top half is always the element counts, and it is a **table of domain sub-classes**: L0
    MMB-5 dropped the component names ("不需要mesh/curve等大类了") and gave every domain its own
    colour, with the number one size above its label ("数量的文字比domain子类文字稍大"). The
    component still decides where the line breaks, so a mesh node's four domains stay on one
    line. Bottom half is the attribute table grouped by domain; a group title repeats the
    colour of its domain from above ("domain子类颜色要和上方信息颜色保持一致") and is drawn at
    the **body** size - the L0 reference `node_info_ui_v3.png` shrank it back to the label
    size. Every attribute name and type shares the one attribute colour ("属性颜色统一"), and
    an attribute the node added is drawn in the separate added colour (MMB-5: "用单独的颜色
    标出（默认 #FFB433，可在首选项里改）"). Nothing else is coloured.
    """
    gap = max(6, int(round(body * 0.8)))
    line_gap = max(2, int(round(body * 0.3)))
    spacer = max(4, int(round(body * 0.5)))
    lines = []

    def add(cells, tallest):
        width = sum(_text_width(text, font) for text, font, _colour in cells)
        width += gap * max(0, len(cells) - 1)
        lines.append({
            "cells": cells,
            "tallest": tallest,
            "height": tallest + line_gap,
            "width": width,
        })

    for component in state["components"]:
        # One line per component: its domains, each in its own colour. A `0` is drawn in the
        # same colour as any other count - the palette says which domain a row is, not
        # whether it has elements (L0 MMB-5 keeps that from the previous pass).
        cells = []
        for entry in component["counts"]:
            colour = palette.get(entry["slot"]) or palette["attribute"]
            if entry["label"]:
                cells.append((entry["label"], body, colour))
            cells.append((_fmt_number(entry["count"]), count_font, colour))
        for wrapped in _flow_items(cells, max_width, gap):
            add(wrapped, count_font)

    if state["attribute_groups"] and state["components"]:
        lines.append({"cells": [], "tallest": spacer, "height": spacer, "width": 0.0})

    for group in state["attribute_groups"]:
        title_colour = palette.get(group.get("slot")) or palette["attribute"]
        add([(group["title"], body, title_colour)], body)
        cells = []
        for item in group["items"]:
            colour = (
                palette["added"] if item["key"] in state["added"] else palette["attribute"]
            )
            # `name(Type)` is one cell, not two: the reference layout writes them with no gap
            # between them (`position(Vec)`), and a cell boundary would insert one.
            cells.append((f"{item['name']}({item['type_label']})", body, colour))
        if group["hidden"]:
            cells.append((f"… {group['hidden']} more", body, _COLOR_MUTED))
        for wrapped in _flow_items(cells, max_width, gap):
            add(wrapped, body)

    if state["reason"]:
        add([(state["reason"], body, _COLOR_MUTED)], body)
    return lines, gap


def font_sizes(scale=1.0):
    """The block's two text sizes at `text_scale = scale`: `(body, count)`.

    MMB-5 fixes the *relation*, not the numbers: the domain labels **and** the attribute
    group titles are the body size, and the count is "稍大" than its label. Measured off the
    L0 reference (`spec/node_info_ui_v3.png`): label cap height 26 px, digit 32 px, i.e.
    x1.23 - so this uses +25%. The earlier reference (`node_info_ui_v2.png`) also drew the
    group titles a size larger (41 px, x1.58); v3 dropped that, so there are two sizes again.
    """
    body = max(6, int(round(BASE_FONT * scale)))
    return body, body + max(1, int(round(body * 0.25)))


def draw_panel(state, position, settings):
    """Draw the information block with its top-left corner at `position`.

    Two text sizes (see `font_sizes`) and the whole block scales with the `Text size`
    preference (MMB-8: "信息块文字整体缩放"). The colours come from the preferences through
    `palette_from_settings()`. Nothing is returned: the block has no buttons, because MMB-7
    dropped the pin.
    """
    if state is None:
        return

    scale = float(getattr(settings, "text_scale", 1.0) or 1.0) if settings else 1.0
    body, count_font = font_sizes(scale)
    palette = palette_from_settings(settings)
    pad = max(5, int(round(body * 0.75)))
    # Wrap to the preferred width, but never wider than the editor: a scaled-up block in a
    # narrow Node Editor must wrap instead of running off the area (the alternative is text
    # clipped by the region with no way to read it).
    region = getattr(bpy.context, "region", None)
    limit = MAX_TEXT_WIDTH * scale
    if region is not None:
        limit = min(limit, max(120.0, region.width - pad * 2 - 8.0))
    max_width = max(120, int(round(limit)))

    lines, gap = _panel_lines(state, body, count_font, max_width, palette)
    width = int(max([line["width"] for line in lines] or [0.0]) + pad * 2)
    height = int(sum(line["height"] for line in lines) + pad * 2)

    x, y = position
    x = max(0.0, x)
    if region is not None:
        x = min(x, max(0.0, region.width - width - 4))
        y = min(y, region.height - 4)

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    try:
        batch = batch_for_shader(
            shader, "TRI_FAN",
            {"pos": [(x, y), (x + width, y), (x + width, y - height), (x, y - height)]},
        )
        shader.bind()
        shader.uniform_float("color", _COLOR_BG)
        batch.draw(shader)
        border = batch_for_shader(
            shader, "LINE_LOOP",
            {"pos": [(x, y), (x + width, y), (x + width, y - height), (x, y - height)]},
        )
        shader.uniform_float("color", _COLOR_BORDER)
        border.draw(shader)

        cursor_y = y - pad
        for line in lines:
            cursor_y -= line["tallest"]
            x_cursor = x + pad
            for text, font, colour in line["cells"]:
                blf.size(0, font)
                blf.color(0, *colour)
                blf.position(0, x_cursor, cursor_y, 0)
                blf.draw(0, text)
                x_cursor += _text_width(text, font) + gap
            cursor_y -= line["height"] - line["tallest"]
    finally:
        gpu.state.blend_set("NONE")
