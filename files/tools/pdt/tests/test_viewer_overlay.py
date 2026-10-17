"""EVO-1..EVO-6: the enhanced viewer overlay's settings (headless half).

    blender.exe -b --factory-startup --python-exit-code 1 --python tools/pdt/tests/test_viewer_overlay.py

What a headless run can check: the overlay properties exist, their defaults and enum items match
`spec/viewer_overlay.md`, they survive a save/load round trip (that is what proves the DNA fields
are really wired) and the pre-existing viewer overlay properties are unchanged.

The drawing itself needs a real viewport and is measured in `tests/gui_viewer_overlay.py`:
there the vector lines, the axes and the element budget are checked as pixels.
"""

from __future__ import annotations

import os
import sys

import bpy

CHECKS = 0
FAILURES: list[str] = []


def scratch_dir():
    """Where the round trip writes its file.

    Not the system temp dir: the dsh file sandbox denies writes there (measured:
    "Cannot open file ...\\Temp\\dsh-*\\overlay.blend@ for writing: Permission denied").
    Next to the running binary is writable and stays outside the git checkout.
    """
    override = os.environ.get("PDT_VIEWER_OVERLAY_SCRATCH")
    if override:
        return override
    return os.path.join(os.path.dirname(bpy.app.binary_path), "viewer_overlay_scratch")


def check(label, actual, expected):
    global CHECKS
    CHECKS += 1
    ok = actual == expected
    print(f"{'OK  ' if ok else 'FAIL'} {label}" + ("" if ok else f"   got {actual!r}, want {expected!r}"))
    if not ok:
        FAILURES.append(label)


def check_close(label, actual, expected, eps=1.0e-6):
    global CHECKS
    CHECKS += 1
    ok = len(actual) == len(expected) and all(abs(a - e) <= eps for a, e in zip(actual, expected))
    print(f"{'OK  ' if ok else 'FAIL'} {label}" + ("" if ok else f"   got {tuple(actual)!r}, want {expected!r}"))
    if not ok:
        FAILURES.append(label)


def view3d_overlays():
    """Background mode has no window, but the startup file's screens are still loaded."""
    return [space.overlay
            for screen in bpy.data.screens
            for area in screen.areas
            for space in area.spaces
            if space.type == "VIEW_3D"]


def main():
    overlays = view3d_overlays()
    check("a viewport overlay is reachable headless (test premise)", len(overlays) > 0, True)
    if not overlays:
        return
    overlay = overlays[0]

    # A stale binary (built before this feature) must fail with a readable line, not a traceback.
    if not hasattr(overlay, "show_viewer_vector"):
        check("the built blender carries the new overlay properties "
              "(run tools/pdt/build_dev.ps1 -Target install)", False, True)
        return

    # EVO-2: one switch in the Overlay > Viewer Node panel, off by default.
    check("EVO-2 show_viewer_vector exists", hasattr(overlay, "show_viewer_vector"), True)
    check("EVO-2 show_viewer_vector defaults to off", bool(overlay.show_viewer_vector), False)

    # EVO-3: two length modes (direction / vector), Vector is the default, scale and colour are
    # user adjustable. These four checks also pin the versioning fix in `versioning_520.cc`:
    # the factory startup file predates the new fields, so a load that skips versioning would
    # read zeros (DIRECTION / 0.0 / black), not the documented defaults.
    mode_items = [item.identifier for item in overlay.bl_rna.properties["viewer_vector_mode"].enum_items]
    check("EVO-3 vector mode items", mode_items, ["DIRECTION", "VECTOR"])
    check("EVO-3 default mode is Vector", overlay.viewer_vector_mode, "VECTOR")
    check_close("EVO-3 default vector scale", (overlay.viewer_vector_scale,), (1.0,))
    check_close("EVO-3 default vector colour is yellow",
                tuple(overlay.viewer_vector_color), (1.0, 0.85, 0.25))

    # EVO-4 / EVO-5: the rotation / matrix axes have their own scale.
    check("EVO-4/5 viewer_axes_scale exists", hasattr(overlay, "viewer_axes_scale"), True)
    check_close("EVO-4/5 default axis scale", (overlay.viewer_axes_scale,), (1.0,))

    # Boundary: the existing viewer overlay behaviour is untouched.
    check("boundary: colour overlay default", bool(overlay.show_viewer_attribute), True)
    check_close("boundary: colour opacity default", (overlay.viewer_attribute_opacity,), (1.0,))
    check("boundary: attribute text default", bool(overlay.show_viewer_text), False)

    # The settings live in DNA, so they have to survive a save/load round trip.
    overlay.show_viewer_vector = True
    overlay.viewer_vector_mode = "DIRECTION"
    overlay.viewer_vector_scale = 2.5
    overlay.viewer_axes_scale = 0.25
    overlay.viewer_vector_color = (0.1, 0.2, 0.3)
    scratch = scratch_dir()
    os.makedirs(scratch, exist_ok=True)
    path = os.path.join(scratch, "viewer_overlay_persist.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)

    overlay = view3d_overlays()[0]
    check("persisted: show_viewer_vector", bool(overlay.show_viewer_vector), True)
    check("persisted: vector mode", overlay.viewer_vector_mode, "DIRECTION")
    check_close("persisted: vector scale", (overlay.viewer_vector_scale,), (2.5,))
    check_close("persisted: axis scale", (overlay.viewer_axes_scale,), (0.25,))
    check_close("persisted: vector colour", tuple(overlay.viewer_vector_color), (0.1, 0.2, 0.3))

    for leftover in (path, path + "1"):
        if os.path.exists(leftover):
            os.remove(leftover)


if __name__ == "__main__":
    try:
        main()
    finally:
        print(f"== test_viewer_overlay: {CHECKS} check(s), {len(FAILURES)} failure(s) ==")
        for failure in FAILURES:
            print(f"  failed: {failure}")
    sys.exit(1 if FAILURES else 0)
