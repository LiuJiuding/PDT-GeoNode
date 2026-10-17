"""Outliner drag-and-drop parenting: the two drop modes.

    blender.exe -b --factory-startup --python-exit-code 1 --python tools/pdt/tests/test_outliner_parent_drop.py

The behaviour being pinned down (decided 2026-10-13, modifier roles swapped 2026-10-15): dropping
an object onto another object in the Outliner always **keeps the dropped object's world
transform**, and the modifier key only decides *where the offset to the parent is stored*:

* `Shift` (or no modifier where the Outliner allows it) - the default, "keep transform **without**
  inverse": `matrix_parent_inverse` stays identity and the offset is baked into the object's own
  local transform.
* `Shift+Alt` - "keep transform": the offset lives in the parent-inverse matrix, so
  `matrix_parent_inverse` is non-identity.

Both modes were already reachable from `Object > Parent > Set Parent To`, and since this change
the Outliner drop calls the *same* function as the second menu entry
(`OBJECT_OT_parent_no_inverse_set` -> `object::parent_no_inverse_set()`), so the matrix half is
asserted through that operator here and the Outliner wiring is guarded structurally below.

Why the wiring is only structural: `OUTLINER_OT_parent_drop` is a **drop box** operator - it needs
a real prepared drag and a real drop event (`event->custom == EVT_DATA_DRAGDROP`), which no script
can fabricate. The modifier-to-mode decision itself is therefore an eye check
(`qa/outliner_parent/checklist.md`).
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy
from mathutils import Matrix

REPO = Path(__file__).resolve().parents[3]
OUTLINER = REPO / "source" / "blender" / "editors" / "space_outliner"
OBJECT = REPO / "source" / "blender" / "editors" / "object"
ED_OBJECT_HH = REPO / "source" / "blender" / "editors" / "include" / "ED_object.hh"

CHECKS = 0
FAILURES: list[str] = []

IDENTITY = Matrix.Identity(4)
EPS = 1e-5

PAR_LOC = (5.0, -1.0, 2.0)
PAR_ROT = (0.0, 0.0, 0.7)
OLD_PAR_LOC = (-2.0, 4.0, 1.0)
OLD_PAR_ROT = (0.4, 0.0, -0.5)
CHILD_LOC = (1.0, 2.0, 3.0)
CHILD_ROT = (0.3, -0.2, 0.9)
CHILD_SCALE = (1.5, 2.0, 0.5)


def check(label, actual, expected):
    global CHECKS
    CHECKS += 1
    ok = actual == expected
    print(f"{'OK  ' if ok else 'FAIL'} {label}" + ("" if ok else f"   got {actual!r}, want {expected!r}"))
    if not ok:
        FAILURES.append(label)


def check_true(label, condition):
    check(label, bool(condition), True)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def body_of(text: str, signature: str) -> str:
    """The text of one function: from its signature to the closing brace in column 0."""
    start = text.index(signature)
    end = text.index("\n}\n", start)
    return text[start:end]


def max_delta(a: Matrix, b: Matrix) -> float:
    return max(abs(a[r][c] - b[r][c]) for r in range(4) for c in range(4))


def is_identity(m: Matrix) -> bool:
    return max_delta(m, IDENTITY) < EPS


# --------------------------------------------------------------------------- the matrix contract


def empty(name, loc, rot, collection):
    ob = bpy.data.objects.new(name, None)
    ob.empty_display_type = 'PLAIN_AXES'
    ob.location = loc
    ob.rotation_euler = rot
    collection.objects.link(ob)
    return ob


def select(child, parent):
    bpy.ops.object.select_all(action='DESELECT')
    child.select_set(True)
    parent.select_set(True)
    bpy.context.view_layer.objects.active = parent


def build(reparent=False):
    """A parent at PAR_LOC plus a child.

    With `reparent` the child first gets a *previous* parent the ordinary way, so it starts with a
    non-identity parent-inverse - that is the interesting case for re-parenting by drag and drop.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    collection = bpy.context.scene.collection

    par = empty("par", PAR_LOC, PAR_ROT, collection)

    child = bpy.data.objects.new("child", None)
    child.location = CHILD_LOC
    child.rotation_euler = CHILD_ROT
    child.scale = CHILD_SCALE
    collection.objects.link(child)

    if reparent:
        old = empty("old_par", OLD_PAR_LOC, OLD_PAR_ROT, collection)
        select(child, old)
        bpy.ops.object.parent_set(type='OBJECT', keep_transform=True)

    bpy.context.view_layer.update()
    return par, child


def drop_with_inverse(child, parent):
    """What the Outliner does on a `Shift+Alt` drop."""
    select(child, parent)
    bpy.ops.object.parent_set(type='OBJECT', keep_transform=True)
    bpy.context.view_layer.update()


def drop_without_inverse(child, parent, keep_transform=True):
    """What the Outliner does on a plain `Shift` drop (the default mode)."""
    select(child, parent)
    bpy.ops.object.parent_no_inverse_set(keep_transform=keep_transform)
    bpy.context.view_layer.update()


def matrix_contract():
    # -------- the default mode: keep the world transform, leave the inverse identity.
    par, child = build()
    before = child.matrix_world.copy()
    drop_without_inverse(child, par)
    check_true("Shift (default): the object ends up parented", child.parent is par)
    check_true("Shift (default): parent-inverse is identity (no inverse correction)",
               is_identity(child.matrix_parent_inverse))
    check_true("Shift (default): the world transform is kept",
               max_delta(child.matrix_world, before) < EPS)
    check_true("Shift (default): the offset went into the local transform instead",
               max(abs(v) for v in child.location) > EPS)

    # -------- the Alt mode: world kept, but through the inverse matrix.
    par, child = build()
    before = child.matrix_world.copy()
    drop_with_inverse(child, par)
    check_true("Shift+Alt: the world transform is kept",
               max_delta(child.matrix_world, before) < EPS)
    check_true("Shift+Alt: parent-inverse is non-identity (offset stored there)",
               not is_identity(child.matrix_parent_inverse))
    check_true("Shift+Alt: parent-inverse carries the offset in its translation",
               max(abs(v) for v in child.matrix_parent_inverse.translation) > EPS)

    # -------- negative control: `keep_transform=False` really does move the object, so the
    # identity inverse above comes from the `keep_transform` branch, not from an ignored flag.
    par, child = build()
    before = child.matrix_world.copy()
    drop_without_inverse(child, par, keep_transform=False)
    check_true("Without-Inverse (no keep transform): parent-inverse is identity too",
               is_identity(child.matrix_parent_inverse))
    check_true("Without-Inverse (no keep transform): the object moves to the parent's origin",
               max_delta(child.matrix_world, before) > 1.0)
    check_true("Without-Inverse (no keep transform): the local location is cleared",
               max(abs(v) for v in child.location) < EPS)

    # -------- re-parenting an object that already has a parent: both modes keep the world, and
    # they differ exactly in where the offset is stored - the whole point of the change.
    par, child = build(reparent=True)
    before = child.matrix_world.copy()
    check_true("re-parent setup: the child starts with a non-identity inverse",
               not is_identity(child.matrix_parent_inverse))
    drop_without_inverse(child, par)
    check_true("re-parent, without inverse: the world transform is still kept",
               max_delta(child.matrix_world, before) < EPS)
    check_true("re-parent, without inverse: the inverse was cleared",
               is_identity(child.matrix_parent_inverse))

    # -------- a parent loop is refused (the helper's documented `false` return).
    par, child = build()
    drop_without_inverse(child, par)
    par_parent_before = par.parent
    select(par, child)  # `child` would become `par`'s parent -> loop
    try:
        bpy.ops.object.parent_no_inverse_set(keep_transform=True)
        loop_reported = False
    except RuntimeError as exc:
        # A reported error makes `bpy.ops` raise; the message is the operator's own.
        loop_reported = "Loop in parents" in str(exc)
    check_true("a parent loop is reported, not applied", loop_reported)
    check_true("a parent loop is refused and the object is left untouched",
               par.parent is par_parent_before)
    check_true("a parent loop is refused, the child keeps its parent", child.parent is par)


# --------------------------------------------------------------------------- source wiring guards


def wiring_guards():
    dragdrop = read(OUTLINER / "outliner_dragdrop.cc")
    relations = read(OBJECT / "object_relations.cc")
    ed_object_hh = read(ED_OBJECT_HH)

    set_parents = body_of(dragdrop, "static void parent_drop_set_parents(")
    invoke = body_of(dragdrop, "static wmOperatorStatus parent_drop_invoke(")
    parent_drop_ot = body_of(dragdrop, "void OUTLINER_OT_parent_drop(")
    helper = body_of(relations, "bool parent_no_inverse_set(Object *ob, Object *par,")
    noinv_exec = body_of(relations, "static wmOperatorStatus parent_noinv_set_exec(")

    check_true("the drop takes the mode as a parameter (not the old keep_transform flag)",
               "const bool keep_inverse" in set_parents and "keep_transform" not in set_parents)
    check_true("the inverse mode keeps the transform through the inverse matrix",
               "parent_type, false, true, nullptr)" in set_parents)
    check_true("the default (no Alt) goes through the without-inverse API",
               "object::parent_no_inverse_set(object, parent, true)" in set_parents)
    check_true("the world matrix read by the without-inverse path is evaluated first",
               "CTX_data_ensure_evaluated_depsgraph(C)" in set_parents)
    check_true("Alt now selects the inverse mode, so without-inverse is the default",
               "/*keep_inverse=*/(event->modifier & KM_ALT)" in invoke)
    check_true("and the old inverted mapping is gone (Alt is no longer 'without inverse')",
               "!(event->modifier & KM_ALT)" not in invoke)
    check_true("the operator hint mentions the Alt modifier", "hold Alt" in parent_drop_ot)
    check_true("the with-inverse drop still reports loop errors",
               'BKE_report(reports, RPT_ERROR, "Loop in parents")' in set_parents)

    check_true("the without-inverse API is declared for other callers",
               "bool parent_no_inverse_set(Object *ob, Object *par, bool keep_transform);"
               in ed_object_hh)
    check_true("the API refuses a parent loop before changing anything",
               helper.index("BKE_object_parent_loop_check(par, ob)")
               < helper.index("ob->parent = par;"))
    check_true("the API keeps the world transform by re-basing the local transform",
               "BKE_object_apply_parent_inverse(ob)" in helper)
    check_true("the API's other branch clears the inverse and the local location",
               "unit_m4(ob->parentinv)" in helper and "zero_v3(ob->loc)" in helper)
    check_true("the without-inverse *menu* operator now calls the same API (one implementation)",
               "parent_no_inverse_set(ob, par, keep_transform)" in noinv_exec and
               "ob->parent = par;" not in noinv_exec)


def main():
    matrix_contract()
    wiring_guards()
    print(f"== test_outliner_parent_drop: {CHECKS} checks, {len(FAILURES)} failure(s) ==")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
