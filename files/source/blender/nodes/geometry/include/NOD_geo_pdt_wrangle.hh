/* SPDX-FileCopyrightText: 2026 PDT Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

#include "DNA_node_types.h"

#include "NOD_socket_items.hh"

namespace blender::nodes {

/**
 * PDT fork: the **channel list** of the `PDT Wrangle` node
 * (`tools/pdt/spec/pdt_wrangle.md`, `WR-35`…`WR-40`).
 *
 * A channel is a named value socket the user adds to the node (a per-node spare parameter, in
 * Houdini terms); the code reads it with `chf` / `chi` / `chv`. This is the same shape as
 * #BakeItemsAccessor: one socket per item, the item carries only the name and the socket type,
 * and the shared `socket_items::` helpers provide the add/remove/move operators, the list panel,
 * the unique naming and the blend read/write.
 */
struct PDTWrangleItemsAccessor : public socket_items::SocketItemsAccessorDefaults {
  using ItemT = NodeGeometryPDTWrangleItem;
  static StructRNA **item_srna;
  static int node_type;
  static constexpr StringRefNull node_idname = "GeometryNodePDTWrangle";
  static constexpr bool has_type = true;
  static constexpr bool has_name = true;
  struct operator_idnames {
    static constexpr StringRefNull add_item = "NODE_OT_pdt_wrangle_channel_add";
    static constexpr StringRefNull remove_item = "NODE_OT_pdt_wrangle_channel_remove";
    static constexpr StringRefNull move_item = "NODE_OT_pdt_wrangle_channel_move";
  };
  struct ui_idnames {
    static constexpr StringRefNull list = "NODE_UL_pdt_wrangle_channels";
  };
  struct rna_names {
    static constexpr StringRefNull items = "channels";
    static constexpr StringRefNull active_index = "active_index";
  };

  static socket_items::SocketItemsRef<NodeGeometryPDTWrangleItem> get_items_from_node(bNode &node)
  {
    auto *storage = static_cast<NodeGeometryPDTWrangle *>(node.storage);
    return {&storage->items, &storage->items_num, &storage->active_index};
  }

  static void copy_item(const NodeGeometryPDTWrangleItem &src,
                        NodeGeometryPDTWrangleItem &dst)
  {
    dst = src;
    dst.name = BLI_strdup_null(dst.name);
  }

  static void destruct_item(NodeGeometryPDTWrangleItem *item)
  {
    MEM_SAFE_DELETE(item->name);
  }

  static void blend_write_item(BlendWriter *writer, const ItemT &item);
  static void blend_read_data_item(BlendDataReader *reader, ItemT &item);

  static eNodeSocketDatatype get_socket_type(const NodeGeometryPDTWrangleItem &item)
  {
    return item.socket_type;
  }

  static char **get_name(NodeGeometryPDTWrangleItem &item)
  {
    return &item.name;
  }

  /** `WR-35`: a channel is a float, an int, a vector, a rotation or a matrix - the five value
   * types the language has (`WR-36` reads them with `chf` / `chi` / `chv` / `chq` / `chm`).
   * Blender's other socket types (boolean, colour, string) stay out: the language has no value
   * for them, so offering them would only create a channel the code cannot read. */
  static bool supports_socket_type(const eNodeSocketDatatype socket_type, const int /*ntree_type*/)
  {
    return socket_type == SOCK_FLOAT || socket_type == SOCK_INT || socket_type == SOCK_VECTOR ||
           socket_type == SOCK_ROTATION || socket_type == SOCK_MATRIX;
  }

  static void init_with_socket_type_and_name(bNode &node,
                                             NodeGeometryPDTWrangleItem &item,
                                             const eNodeSocketDatatype socket_type,
                                             const char *name)
  {
    auto *storage = static_cast<NodeGeometryPDTWrangle *>(node.storage);
    item.socket_type = socket_type;
    item.identifier = storage->next_identifier++;
    socket_items::set_item_name_and_make_unique<PDTWrangleItemsAccessor>(node, item, name);
  }

  static std::string socket_identifier_for_item(const NodeGeometryPDTWrangleItem &item)
  {
    return "Channel_" + std::to_string(item.identifier);
  }
};

}  // namespace blender::nodes
