# PDT custom geometry nodes

All self-added geometry nodes live in this directory. Nothing outside the marked
`PDT CUSTOM` blocks in

* `source/blender/nodes/geometry/CMakeLists.txt`
* `scripts/startup/bl_ui/node_add_menu_geometry.py`

is modified, which keeps upstream rebases conflict-free.

## Files

| File | Purpose |
| --- | --- |
| `nodes.json` | manifest: one entry per custom node (id, label, category, source, flags). Managed by `tools/pdt_nodes.py`. |
| `node_geo_pdt_*.cc` | one node per file, generated from `tools/templates/node_geo_custom.cc.in` |
| `pdt_wrangle_vm.hh` | the Wrangle engine (lexer/parser/compiler/VM/math library). **Header only and free of Blender headers on purpose**, so it can be built and self-tested standalone in seconds by `tools/pdt/tests/build_engine_selftest.ps1`; a header needs no entry in `nodes.json` or the CMake `SRC` list. |

## Rules for a new node

1. Create it with the tool, never by hand:
   `python tools\pdt_nodes.py new GeometryNodePDTMyThing "PDT My Thing"`,
   then `python tools\pdt_nodes.py sync`.
2. One node per file, one distinct namespace per file
   (`blender::nodes::node_geo_pdt_<name>_cc`). `bf_nodes_geometry` is built in
   *unity* mode, so colliding internal names across files break the build.
3. Register through `NOD_REGISTER_NODE(node_register)` — `intern/discover_nodes.py`
   picks it up automatically, there is no central list to edit.
4. Expose every option as a **socket** (`decl::Int/Float/Bool/Vector/String`) or a
   **menu socket** (`decl::Menu(...).static_items(...)`), never as a new DNA
   property. Sockets are automatically visible in the Python API, need no
   `.blend` versioning, and keep the diff outside `DNA_*.h`.
5. Mark nodes that can be evaluated standalone (geometry in → geometry out) with
   `"eval_node": true` in `nodes.json` so `tools/run_tests.ps1` exercises them.
6. Add a functional test blend under
   `tests/files/modeling/geometry_nodes/pdt/` and reference it as
   `"test": "pdt/<file>.blend"` in the manifest. (No node currently declares one:
   the scaffold node that did was removed on 2026-10-16.)

## Reference implementation

There is no standalone example node any more: the `PDT Grid Points` scaffold that
used to show declare / exec / register was deleted on 2026-10-16. Read
`tools/pdt/templates/node_geo_custom.cc.in` (the shape every new node starts from)
and `node_geo_pdt_selection.cc` (the smallest real node) instead.
