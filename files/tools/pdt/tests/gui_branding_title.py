"""BRAND-5: the fork's marker is in the real OS window title.

    blender.exe --python tools\\pdt\\tests\\gui_branding_title.py

`run_tests.ps1 -Gui` runs this (deliberately **without** `--factory-startup`: the marker must
survive the user's own configuration, like the other GUI acceptance tests).

The window title is built by C++ (`WM_window_title_refresh` -> `wm_window_title_text`,
`wm_window.cc`), which also hands it to the window manager - so it can only be observed from a
real window. Two things are measured:

  BRAND-5a  a window that this build opened really carries `- Blender <version> (PDT-GN <release>)`
  BRAND-5b  the same holds with data loaded (the title is rebuilt per file / dirty state)

The child is spawned with a watchdog and writes its result into a file before quitting, so a
stalled GUI can never hang the harness (the trap documented in `docs/00-当前状态.md`).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback

import bpy

#: Read from the version header, like `test_branding.py` - never hard-coded (BRAND-6).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _brand_source import read_brand  # noqa: E402

BRAND, BRAND_FAMILY, BRAND_VERSION = read_brand()
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("PDT_BRANDING_QA") or os.path.join(
    os.path.dirname(bpy.app.binary_path), "branding_qa")

CHECKS = 0
FAILURES: list[str] = []


def check(label, actual, expected):
    global CHECKS
    CHECKS += 1
    if actual == expected:
        print(f"OK   {label}: {actual}", flush=True)
    else:
        print(f"FAIL {label}: got {actual!r}, expected {expected!r}", flush=True)
        FAILURES.append(label)


def check_true(label, condition, detail=""):
    """`detail` is reported for information only - it must never be the thing under test."""
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"OK   {label}: {detail}", flush=True)
    else:
        print(f"FAIL {label}: (detail {detail!r})", flush=True)
        FAILURES.append(label)


def child_titles(blend, out_path):
    """Run one real-window child; return the titles it captured (may be empty)."""
    proc = subprocess.Popen(
        [bpy.app.binary_path, "--factory-startup", blend, "--python", os.path.join(
            HERE, "_branding_title_child.py"), "--", out_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 90
    while time.time() < deadline:
        if os.path.exists(out_path):
            time.sleep(0.5)  # let the writer close the file
            break
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
    try:
        with open(out_path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    return [line.split("\t", 1)[1] for line in lines if line.startswith("TITLE\t")]


def main():
    print("== gui_branding_title:", flush=True)
    if bpy.app.background:
        print("SKIP gui_branding_title: needs a real window (do not run with -b)", flush=True)
        print("GUI_TEST_RESULT: PASS", flush=True)
        return 0

    os.makedirs(OUT, exist_ok=True)
    check_true("BRAND-5a this process names the brand",
               bpy.app.version_string_branded.endswith(f"({BRAND})"),
               bpy.app.version_string_branded)

    # BRAND-5a: a file, straight from factory startup.
    empty = os.path.join(OUT, "empty.blend")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.save_as_mainfile(filepath=empty, compress=False)

    for label, blend in (("BRAND-5a (empty file)", empty),
                         ("BRAND-5b (data loaded)", os.path.join(OUT, "branded_file.blend"))):
        if not os.path.exists(blend):
            print(f"SKIP {label}: {blend} not made yet (run test_branding.py first)", flush=True)
            continue
        out_path = os.path.join(OUT, "title_%s.txt" % os.path.basename(blend))
        if os.path.exists(out_path):
            os.remove(out_path)
        titles = child_titles(blend, out_path)
        # The process owns several top-level windows: `BlenderGLEW`, `NVOGLDC invisible`,
        # `Default IME`, ... Only the real window carries the `" - Blender <version>"` tail.
        real = [t for t in titles if " - Blender " in t]
        check_true(f"{label}: the real window title was captured", len(real) == 1, real)
        check_true(f"{label}: the title carries the brand suffix",
                   bool(real) and real[0].endswith(f" - Blender {bpy.app.version_string_branded}"),
                   real)
        check_true(f"{label}: the marker is a suffix after the version, not a replacement",
                   bool(real) and real[0].endswith(f"({BRAND})") and " - Blender 5." in real[0],
                   real)

    summary = f"{CHECKS} checks, {len(FAILURES)} failure(s)"
    print(f"== gui_branding_title: {summary}", flush=True)
    for label in FAILURES:
        print(f"  failed: {label}", flush=True)
    print("GUI_TEST_RESULT: " + ("FAIL" if FAILURES else "PASS"), flush=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        print("GUI_TEST_RESULT: FAIL", flush=True)
        sys.exit(1)
