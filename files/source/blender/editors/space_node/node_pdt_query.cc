/* SPDX-FileCopyrightText: 2026 PDT Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup spnode
 *
 * \brief Read-only query: which node socket is under the cursor.
 *
 * The viewport picker (`scripts/addons_core/pdt_geo_select`) is triggered by the cursor
 * sitting on a node's **string parameter** (L0 `spec/selection_pick.md` PICK-1: socket, name
 * label or value field), and the only implementation of "which socket is that" is
 * `node_find_indicated_socket()` in this module - declared in `node_intern.hh`, a header no
 * other module and no script may include. This operator is that answer in a form an add-on can
 * call: read-only, no UI, no undo step, no report of its own (the caller owns the wording of
 * every refusal).
 *
 * Two hit tests, because the connection dot and the parameter are not the same thing:
 *
 * - `node_find_indicated_socket()` for the **dot** (with its occlusion handling);
 * - `socket_row_under_cursor()` for the **row**, i.e. the socket's name and value field, which
 *   is what a user actually aims at - the dot is only `NODE_SOCKSIZE` across.
 *   `hit_region` says which of the two answered.
 *
 * Two facts decide whether the answers match the screen:
 *
 * - `node_find_indicated_socket()` takes **view space** (`v2d`) coordinates, *not* region
 *   pixels: `node_socket_draw()` stores `socket.runtime->location` as
 *   `node.location * UI_SCALE_FAC` (`node_draw.cc`, `node_to_view()`), and every editor
 *   caller converts a region position first (`node_resize_invoke()`,
 *   `is_event_over_node_or_socket()`). This operator does the same conversion for the
 *   `region_x` / `region_y` it is given.
 * - Coordinates passed by the caller win, because a key event carries the pointer
 *   position *of that event*. Without them the position the event system last saw is used
 *   (`win->runtime->eventstate`, exactly like `space_node.cc` `node_cursor()`), never
 *   `snode.runtime->cursor`: that field is written while drawing (one frame behind) and is
 *   divided by `UI_SCALE_FAC`.
 *
 * A script call does **not** store the operator's properties for the redo panel
 * (`wm_event_system.cc`: `use_last_properties` is false for calls from a script), so the
 * result is stored explicitly with `WM_operator_last_properties_store()`. That is what
 * makes it readable from Python through
 * `bpy.context.window_manager.operator_properties_last("pdt.node_under_cursor")`.
 */

#include <algorithm>
#include <climits>

#include "DNA_node_types.h"
#include "DNA_space_types.h"

#include "BKE_context.hh"
#include "BKE_node_runtime.hh" /* bNodeSocket::owner_node(), bNode::input_sockets() */

#include "BLI_math_vector.hh"

#include "RNA_access.hh"
#include "RNA_define.hh"

#include "UI_view2d.hh"

#include "WM_api.hh"
#include "WM_types.hh"

#include "node_intern.hh" /* own include */

namespace blender {

namespace ed::space_node {

/* -------------------------------------------------------------------- */
/** \name Socket under cursor
 * \{ */

/** Input coordinate that means "no position given, use the event system's own". */
constexpr int no_region_coord = -1;

static bool node_has_geometry_input(const bNode &node)
{
  for (const bNodeSocket *socket : node.input_sockets()) {
    if (socket->type == SOCK_GEOMETRY) {
      return true;
    }
  }
  return false;
}

/**
 * The socket **row** (its name and its value field) a point is in, or nullptr.
 *
 * The connection dot is only `NODE_SOCKSIZE` across, which is fiddly to aim at - and it is not
 * what a user means by "the string parameter": the parameter is the row, i.e. its name and its
 * value field. This pass finds that row, using the geometry the drawer itself writes while
 * laying a node out:
 *
 * - `socket.runtime->location.y` is the row's vertical centre (`node_draw.cc`,
 *   `node_update_basis_socket()`: `location = float2(locx, locy - NODE_DYS)`), and
 * - one row is `NODE_DY` tall (`locy` never advances by less, and a multi-input socket's row is
 *   taller: `node_socket_calculate_height()`).
 *
 * Only **inputs** are considered (a string parameter is always an input), only rows the editor
 * really draws as stand-alone sockets (`is_icon_visible()`, so a socket inside a collapsed
 * panel is skipped - its `location` is its panel's header, which would otherwise swallow the
 * header's own row), and only sockets with a real data type: virtual `SOCK_CUSTOM` sockets are
 * panel headers (`__extend__`), not parameters.
 *
 * Nodes are walked front to back, so a node in front of another answers first - the same
 * "which node is on top" rule `node_under_mouse_get()` uses.
 */
static bNodeSocket *socket_row_under_cursor(SpaceNode &snode, const float2 &cursor)
{
  if (snode.edittree == nullptr) {
    return nullptr;
  }
  bNodeTree &tree = *snode.edittree;
  tree.ensure_topology_cache();

  for (bNode *node : tree_draw_order_calc_nodes_reversed(tree)) {
    if (node->is_frame() || (node->flag & NODE_COLLAPSED)) {
      /* Frames only hit their border, and a collapsed node draws a compact socket column
       * rather than rows - the dot hit test above covers those. */
      continue;
    }
    const rctf &bounds = node->runtime->draw_bounds;
    if (cursor.y > bounds.ymax - NODE_DY) {
      /* The header band is the node's own title, not a socket row. */
      continue;
    }
    if (cursor.x < bounds.xmin - NODE_SOCKSIZE || cursor.x > bounds.xmax) {
      continue;
    }
    for (bNodeSocket *socket : node->input_sockets()) {
      if (socket->type == SOCK_CUSTOM || !socket->is_icon_visible()) {
        continue;
      }
      const float half_row =
          std::max(float(NODE_DY), node_socket_calculate_height(*socket)) * 0.5f;
      const float center = socket->runtime->location.y;
      if (cursor.y >= center - half_row && cursor.y <= center + half_row) {
        return socket;
      }
    }
  }
  return nullptr;
}

/**
 * Where the cursor is, in the space both hit tests speak: **view space** (`v2d`), which is what
 * `node_find_indicated_socket()` compares against (`node_socket_draw()` stores
 * `socket.runtime->location` as `node.location * UI_SCALE_FAC`).
 *
 * `region_x` / `region_y` are region pixels (what a key event reports as
 * `event->mouse_region_x/y`); when they are not given, the last known pointer position of this
 * window is converted the same way (`node_cursor()`'s source, never `snode.runtime->cursor`,
 * which is written while drawing and therefore a frame behind).
 */
static bool cursor_in_view_space(const bContext &C,
                                 const SpaceNode &snode,
                                 const ARegion &region,
                                 const wmOperator &op,
                                 float2 &r_cursor)
{
  const int region_x = RNA_int_get(op.ptr, "region_x");
  const int region_y = RNA_int_get(op.ptr, "region_y");

  if (region_x >= 0 && region_y >= 0) {
    ui::view2d_region_to_view(&region.v2d, float(region_x), float(region_y), &r_cursor.x, &r_cursor.y);
    return true;
  }

  const wmWindow *win = CTX_wm_window(&C);
  const wmEvent *event = (win != nullptr) ? win->runtime->eventstate : nullptr;
  if (event == nullptr) {
    return false;
  }
  ui::view2d_region_to_view(&region.v2d,
                            float(event->xy[0] - region.winrct.xmin),
                            float(event->xy[1] - region.winrct.ymin),
                            &r_cursor.x,
                            &r_cursor.y);
  return true;
}

/**
 * The socket the cursor indicates: its dot if the cursor is on one (the editor's own answer,
 * occlusion included), else the row it is in. `r_hit_region` says which of the two answered,
 * so a caller can tell "on the dot" from "on the name / value field".
 */
static bNodeSocket *indicated_socket(SpaceNode &snode,
                                     ARegion &region,
                                     const float2 &cursor,
                                     const char **r_hit_region)
{
  /* Both directions here: an output socket under the cursor must answer (and be reported as a
   * non-string socket) instead of letting an input socket of the same row win by default. */
  if (bNodeSocket *socket = node_find_indicated_socket(snode, region, cursor, SOCK_IN | SOCK_OUT))
  {
    *r_hit_region = "socket";
    return socket;
  }
  if (bNodeSocket *socket = socket_row_under_cursor(snode, cursor)) {
    *r_hit_region = "row";
    return socket;
  }
  return nullptr;
}

/** Publish "nothing was hit" - every result field is a property the caller reads. */
static void store_result_nothing(wmOperator &op)
{
  RNA_boolean_set(op.ptr, "found", false);
  RNA_string_set(op.ptr, "node_name", "");
  RNA_string_set(op.ptr, "node_idname", "");
  RNA_string_set(op.ptr, "socket_identifier", "");
  RNA_string_set(op.ptr, "socket_name", "");
  RNA_string_set(op.ptr, "socket_idname", "");
  RNA_boolean_set(op.ptr, "is_string", false);
  RNA_boolean_set(op.ptr, "is_input", false);
  RNA_boolean_set(op.ptr, "has_geometry_input", false);
  RNA_string_set(op.ptr, "hit_region", "");
}

static wmOperatorStatus node_under_cursor_exec(bContext *C, wmOperator *op)
{
  store_result_nothing(*op);

  SpaceNode *snode = CTX_wm_space_node(C);
  ARegion *region = CTX_wm_region(C);
  float2 cursor;
  const char *hit_region = "";
  bNodeSocket *socket = nullptr;
  if (snode != nullptr && snode->edittree != nullptr && region != nullptr &&
      cursor_in_view_space(*C, *snode, *region, *op, cursor))
  {
    socket = indicated_socket(*snode, *region, cursor, &hit_region);
  }

  if (socket != nullptr) {
    const bNode &node = socket->owner_node();
    RNA_boolean_set(op->ptr, "found", true);
    RNA_string_set(op->ptr, "node_name", node.name);
    RNA_string_set(op->ptr, "node_idname", node.idname);
    RNA_string_set(op->ptr, "socket_identifier", socket->identifier);
    RNA_string_set(op->ptr, "socket_name", socket->name);
    RNA_string_set(op->ptr, "socket_idname", socket->idname);
    RNA_boolean_set(op->ptr, "is_string", socket->type == SOCK_STRING);
    RNA_boolean_set(op->ptr, "is_input", socket->in_out == SOCK_IN);
    RNA_boolean_set(op->ptr, "has_geometry_input", node_has_geometry_input(node));
    RNA_string_set(op->ptr, "hit_region", hit_region);
  }

  /* The result has to outlive this call (see the file comment). */
  WM_operator_last_properties_store(op);
  return OPERATOR_FINISHED;
}

static bool node_under_cursor_poll(bContext *C)
{
  const SpaceNode *snode = CTX_wm_space_node(C);
  const ARegion *region = CTX_wm_region(C);
  return snode != nullptr && snode->edittree != nullptr && region != nullptr &&
         region->regiontype == RGN_TYPE_WINDOW;
}

void NODE_OT_node_under_cursor(wmOperatorType *ot)
{
  ot->name = "Socket Under Cursor (query)";
  ot->description =
      "Report which node input socket is under the cursor, and whether that node has a "
      "geometry input (read-only query used by the PDT selection picker)";
  /* Scripts reach an operator through `bpy.ops.<prefix>.<name>`, where the prefix is what
   * stands before `_OT_` - so this one is `bpy.ops.pdt.node_under_cursor`, next to the
   * add-on's own operators. (`NODE_OT_…` would have made it `bpy.ops.node.node_under_cursor`,
   * which is the convention for an editor operator; the picker's query deliberately sits in
   * the PDT namespace with the rest of the picker.) */
  ot->idname = "PDT_OT_node_under_cursor";

  ot->exec = node_under_cursor_exec;
  ot->poll = node_under_cursor_poll;

  /* Optional: region-relative pixel coordinates (a key event's `mouse_region_x/y`).
   * Negative means "use the pointer position the event system last saw". */
  RNA_def_int(ot->srna,
              "region_x",
              no_region_coord,
              no_region_coord,
              INT_MAX,
              "Region X",
              "Cursor X in region pixels; negative uses the event system's position",
              no_region_coord,
              INT_MAX);
  RNA_def_int(ot->srna,
              "region_y",
              no_region_coord,
              no_region_coord,
              INT_MAX,
              "Region Y",
              "Cursor Y in region pixels; negative uses the event system's position",
              no_region_coord,
              INT_MAX);

  /* Results. Deliberately editable properties: `RNA_string_set()` refuses to write a
   * property without `PROP_EDITABLE`. A string property takes no `""` default (RNA logs an
   * error for that), so the default is null and an unset result reads as "". */
  RNA_def_boolean(ot->srna, "found", false, "Found", "A node socket is under the cursor");
  RNA_def_string(ot->srna, "node_name", nullptr, 64, "Node", "Name of the socket's node");
  RNA_def_string(
      ot->srna, "node_idname", nullptr, 64, "Node Type", "Type idname of that node");
  RNA_def_string(ot->srna,
                 "socket_identifier",
                 nullptr,
                 64,
                 "Socket Identifier",
                 "Identifier of the socket (what a caller writes into)");
  RNA_def_string(ot->srna, "socket_name", nullptr, 64, "Socket Name", "Display name of the socket");
  RNA_def_string(ot->srna,
                 "socket_idname",
                 nullptr,
                 64,
                 "Socket Type",
                 "Socket data type idname, for example NodeSocketString");
  RNA_def_boolean(ot->srna,
                  "is_string",
                  false,
                  "Is String",
                  "The socket is a string parameter (its type is NodeSocketString)");
  RNA_def_boolean(ot->srna,
                  "is_input",
                  false,
                  "Is Input",
                  "The socket is an input socket (a string parameter always is)");
  RNA_def_boolean(ot->srna,
                  "has_geometry_input",
                  false,
                  "Has Geometry Input",
                  "The socket's node has a geometry input socket");
  RNA_def_string(ot->srna,
                 "hit_region",
                 nullptr,
                 16,
                 "Hit Region",
                 "'socket' when the cursor was on the connection dot, 'row' when it was on the "
                 "socket's name or value field");
}

/** \} */

}  // namespace ed::space_node

}  // namespace blender
