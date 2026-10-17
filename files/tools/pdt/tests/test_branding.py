"""BRAND-1..4: the fork's identity on the binary, in files, and across builds.

    blender.exe -b --factory-startup --python-exit-code 1 --python tools\\pdt\\tests\\test_branding.py

Run by `run_tests.ps1` like every other `test_*.py`, and it re-runs *itself* through this binary
and through the official reference build as subprocesses, because "which build wrote this file"
cannot be answered from inside one process:

  BRAND-1  the fork names itself: `bpy.app.brand_id` and the branded version string, while the
           plain version string stays upstream-shaped (it goes into OBJ/PLY/USD headers).
  BRAND-2  a file saved by this build carries the brand in its `FileGlobal.build_hash`, and the
           build reads it back (`bpy.app.file_build_hash`) on load - with no warning for its own
           files, and the custom node still in the tree.
  BRAND-3  the official build sees the same marker in the same file through its existing API
           (`bpy.app.build_hash`), without knowing anything about us.
  BRAND-4  opening a file whose recorded build is *not of this family* is reported through the
           native report channel; a file from another *version* of this fork, and a file that
           names no build at all, stay silent.

`gui_branding_title.py` (real window) covers the last piece, the OS window title.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import traceback

import bpy

#: The brand is read from the version header (BRAND-6: the brand version is the release
#: tag), never hard-coded here - see `_brand_source.py`.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _brand_source import MAX_BRAND_BYTES, BrandSourceError, read_brand  # noqa: E402

try:
    BRAND, BRAND_FAMILY, BRAND_VERSION = read_brand()
except BrandSourceError as ex:  # pragma: no cover - configuration error, not a test result
    print(f"FAIL BRAND-1 cannot read the brand from the version header: {ex}", flush=True)
    print("== test_branding: 0 checks, 1 failure(s)", flush=True)
    raise SystemExit(1) from ex

#: A name of a *different* family and a different length than the brand, so patching the
#: fixture cannot be silently masked by leftover bytes of the brand.
FOREIGN = "otherbuild-2.0"

#: What the official build stamps into a file it writes: `git rev-parse --short=12 HEAD` of the
#: upstream release branch (a hex hash), i.e. *not* a brand. Asserting the shape keeps this test
#: from silently passing if a future official build ever writes something else.
OFFICIAL_HASH_RE = r"^[0-9a-f]{8,}$"

#: Written next to the running binary (outside the checkout): the dsh file sandbox denies writes
#: to the system temp dir, and test artefacts do not belong in git.
OUT_DIR = os.environ.get("PDT_BRANDING_QA") or os.path.join(
    os.path.dirname(bpy.app.binary_path), "branding_qa")

CHECKS = 0
FAILURES: list[str] = []
SKIPS: list[str] = []


def check(label, actual, expected):
    global CHECKS
    CHECKS += 1
    if actual == expected:
        print(f"OK   {label}: {actual}", flush=True)
    else:
        print(f"FAIL {label}: got {actual!r}, expected {expected!r}", flush=True)
        FAILURES.append(label)


def check_true(label, condition, detail=True):
    """`detail` is shown for information only - it must never be the thing under test."""
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"OK   {label}: {detail}", flush=True)
    else:
        print(f"FAIL {label}: (detail {detail!r})", flush=True)
        FAILURES.append(label)


def skip(label, why):
    print(f"SKIP {label}: {why}", flush=True)
    SKIPS.append(label)


def run_blender(exe, args, timeout=180):
    """Run a Blender process and return (exit code, stdout+stderr)."""
    proc = subprocess.run([exe, *args], capture_output=True, text=True, errors="replace",
                          timeout=timeout)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def field(text, name):
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(name + " "):
            return line[len(name) + 1 :].strip()
    return None


def field_present(text, name):
    return field(text, name) is not None


def name_of(printed):
    """`"b'PDT-GN v1'"` / `"'PDT-GN v1'"` -> the bytes/str body, for comparing names."""
    if printed is None:
        return None
    body = printed.strip()
    if body.startswith("b"):
        body = body[1:]
    return body.strip("'\"")


# --------------------------------------------------------------------------- probe side
# The same file is re-entered as a child process with a mode argument. Everything below runs
# inside that child; `main()` never runs there.

def make_blend(path):
    """Save a .blend containing one custom node, through this binary.

    The tree needs a real user (a modifier on an object): an unreferenced node group is not
    written out, and then the round-trip assertions would measure nothing.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mesh = bpy.data.meshes.new("m")
    obj = bpy.data.objects.new("Cube", mesh)
    bpy.context.scene.collection.objects.link(obj)
    tree = bpy.data.node_groups.new("PDTTree", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    node = tree.nodes.new("GeometryNodePDTSelection")
    next(s for s in node.inputs if s.identifier == "Selection").default_value = "0..9"
    node.domain = "FACE"
    group_out = tree.nodes.new("NodeGroupOutput")
    tree.links.new(node.outputs["Geometry"], group_out.inputs["Geometry"])
    modifier = obj.modifiers.new("pdt", "NODES")
    modifier.node_group = tree
    bpy.ops.wm.save_as_mainfile(filepath=path, compress=False)
    return tree


def patch_build_marker(path, marker):
    """Rewrite the 16-byte `FileGlobal.build_hash` field in a saved .blend.

    Used to build the fixtures for BRAND-4: there is no way to *save* a file naming another build
    from this build (that is what BRAND-2 asserts), so the field is patched directly. It is a
    fixed 16 bytes, so a different-length name also clears whatever the brand left behind.

    Offset: `FileGlobal` starts 16 bytes after the `GLOB` marker (`code`, `int64 len`,
    `uint64 old`, `int SDNAnr`, `int nr`), then `subvstr[4]`, `short subversion`,
    `short minversion`, `short minsubversion`, `_pad[6]`, then the three pointers, `_pad1`,
    `fileflags`, `globalf`, `uint64 build_commit_timestamp`, `char build_hash[16]`.
    """
    data = bytearray(open(path, "rb").read())
    at = data.find(BRAND.encode())
    if at == -1:
        return False
    data[at : at + 16] = marker.encode().ljust(16, b"\0")
    open(path, "wb").write(bytes(data))
    return bytes(open(path, "rb").read())[at : at + 16] == marker.encode().ljust(16, b"\0")


def probe(path, save_as=None):
    bpy.ops.wm.open_mainfile(filepath=path)
    if save_as:
        bpy.ops.wm.save_as_mainfile(filepath=save_as, compress=False)
    wm = bpy.context.window_manager
    reports = [(r.type, r.message) for r in wm.reports] if wm else []
    # `getattr` everywhere: this same file also runs inside the *official* build for BRAND-3,
    # where `brand_id` / `file_build_hash` / `version_string_branded` do not exist at all.
    print("FILE_BUILD_HASH", repr(getattr(bpy.app, "file_build_hash", None)), flush=True)
    print("BINARY_BRAND_ID", repr(getattr(bpy.app, "brand_id", None)), flush=True)
    print("VERSION_STRING_BRANDED", repr(getattr(bpy.app, "version_string_branded", None)),
          flush=True)
    print("HAS_FILE_BUILD_HASH", hasattr(bpy.app, "file_build_hash"), flush=True)
    print("VERSION_FILE", tuple(bpy.app.version_file), flush=True)
    for kind, message in reports:
        print("WM_REPORT", kind, message.replace("\n", " "), flush=True)
    trees = [
        (t.name, [(n.bl_idname, getattr(n, "domain", None)) for n in t.nodes])
        for t in bpy.data.node_groups
    ]
    print("TREES", trees, flush=True)


if "--" in sys.argv:
    _args = sys.argv[sys.argv.index("--") + 1 :]
    if _args and _args[0] in {"check", "check-foreign", "check-save"}:
        probe(_args[1], _args[2] if _args[0] == "check-save" else None)
        sys.exit(0)


# ---------------------------------------------------------------------------- test side

def main():
    global CHECKS
    print("== test_branding:", flush=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    exe = bpy.app.binary_path
    common = ["-b", "--factory-startup", "--python-exit-code", "1", "--python", __file__, "--"]

    # ------------------------------------------------------------------ BRAND-1
    check("BRAND-1 bpy.app.brand_id", bpy.app.brand_id, BRAND)
    check("BRAND-1 bpy.app.version_string_branded", bpy.app.version_string_branded,
          f"{bpy.app.version_string} ({BRAND})")
    check_true("BRAND-1 the branded string ends with the brand",
               bpy.app.version_string_branded.endswith(f"({BRAND})"),
               bpy.app.version_string_branded)
    check_true("BRAND-1 the plain string is not branded", BRAND not in bpy.app.version_string,
               bpy.app.version_string)
    # The marker goes into `FileGlobal.build_hash`, which is `char[16]`: a longer brand is
    # silently truncated on save and would stop matching its own files.
    check_true("BRAND-1 the brand is ASCII and fits build_hash[16]",
               BRAND.isascii() and len(BRAND) <= MAX_BRAND_BYTES,
               f"{BRAND!r} = {len(BRAND)} bytes, limit {MAX_BRAND_BYTES}")
    check_true("BRAND-1 the brand is the family plus the version",
               BRAND == f"{BRAND_FAMILY} {BRAND_VERSION}", BRAND)

    # ------------------------------------------------------- BRAND-2 / BRAND-3
    own = os.path.join(OUT_DIR, "branded_file.blend")
    if os.path.exists(own):
        os.remove(own)
    make_blend(own)  # in-process: saving itself must keep working

    code, text = run_blender(exe, [*common, "check", own])
    check("BRAND-2 child exit code", code, 0)
    check_true("BRAND-2 the probe printed its fields", field_present(text, "FILE_BUILD_HASH"),
               text.strip().splitlines()[-3:])
    check("BRAND-2 a saved file records the brand", name_of(field(text, "FILE_BUILD_HASH")), BRAND)
    check_true("BRAND-2 our own file raises no foreign-build warning",
               "different Blender build" not in text,
               [line for line in text.splitlines() if line.startswith("WM_REPORT")])
    check_true("BRAND-2 the custom node survived the round trip",
               "PDTSelection" in (field(text, "TREES") or ""), field(text, "TREES"))

    official = os.environ.get("PDT_REFERENCE_BLENDER") or ""
    if not official or not os.path.exists(official):
        skip("BRAND-3", "no reference build (set PDT_REFERENCE_BLENDER)")
    else:
        # The one upstream-facing claim that is testable: `build_hash` lives in the same
        # `FileGlobal` block as everything else official Blender reads and writes about a file,
        # and that block survives an official build re-saving the file. So let the official build
        # open our file and save it again, then read the result back here.
        roundtrip = os.path.join(OUT_DIR, "official_roundtrip.blend")
        if os.path.exists(roundtrip):
            os.remove(roundtrip)
        code, text = run_blender(official, [*common, "check-save", own, roundtrip])
        check("BRAND-3 official child exit code", code, 0)
        check_true("BRAND-3 the official build really re-saved the file", os.path.exists(roundtrip),
                   roundtrip)
        check("BRAND-3 official build has no brand_id of its own",
              field(text, "BINARY_BRAND_ID"), "None")
        check("BRAND-3 official build has no file_build_hash API",
              field(text, "HAS_FILE_BUILD_HASH"), "False")
        check("BRAND-3 official build's version string is not branded",
              field(text, "VERSION_STRING_BRANDED"), "None")
        if os.path.exists(roundtrip):
            code, back = run_blender(exe, [*common, "check", roundtrip])
            check("BRAND-3 round-trip child exit code", code, 0)
            # Measured, and the honest version of the claim: an official build **re-writing** the
            # file stamps its own build identity into it - the brand belongs to whichever build
            # wrote the file last, not to the project. What does survive is the rest of the
            # `FileGlobal` block (and the DNA data), so the tree is still there, but a custom node
            # that only this fork can type degrades to `NodeUndefined`: the tree loads, the node's
            # storage does not.
            check_true("BRAND-3 after an official build re-saves it, the file names *that* build",
                       bool(re.match(OFFICIAL_HASH_RE, name_of(field(back, "FILE_BUILD_HASH")) or "")),
                       name_of(field(back, "FILE_BUILD_HASH")))
            check_true("BRAND-3 the tree survives, with the node degraded to NodeUndefined",
                       "NodeUndefined" in (field(back, "TREES") or ""), field(back, "TREES"))

    # ------------------------------------------------------------------ BRAND-4
    # The fixture is a file naming a build that is not ours. There is no way to *save* one from
    # this build - that is exactly what BRAND-2 asserts - so the fixed 16-byte field is patched.
    # (A "newer file" fixture is impossible by construction: the loader refuses to open a file
    # whose sub-version is ahead of the binary, measured as "Missing DNA block", which is also why
    # the C++ rule does not compare versions - see the comment on `wm_file_brand_warn_foreign`.)
    foreign = os.path.join(OUT_DIR, "foreign_build_file.blend")
    if os.path.exists(foreign):
        os.remove(foreign)
    make_blend(foreign)
    if not patch_build_marker(foreign, FOREIGN):
        FAILURES.append("BRAND-4 fixture: GLOB block / build_hash field not found")
        print(f"FAIL BRAND-4 could not patch the marker in {foreign}", flush=True)

    if not FAILURES:
        code, text = run_blender(exe, [*common, "check-foreign", foreign])
        check("BRAND-4 child exit code", code, 0)
        check("BRAND-4 the file's build name is readable", name_of(field(text, "FILE_BUILD_HASH")),
              FOREIGN)
        reports = [line for line in text.splitlines() if line.startswith("WM_REPORT")]
        check_true("BRAND-4 a foreign build is reported", bool(reports), reports)
        check_true("BRAND-4 the report names the other build",
                   any(FOREIGN in line for line in reports), reports)
        check_true("BRAND-4 the report goes through the native warning channel",
                   any("WARNING" in line for line in reports), reports)

    # The other direction of the same rule (BRAND-4 says "family", not "exact build"): a
    # file saved by an *earlier release of this fork* must not be reported. Without this,
    # every user upgrading the fork would get a false "different build" warning on their own
    # files - the reason the check compares the family prefix instead of the whole brand.
    other_version = f"{BRAND_FAMILY} v9.9.9"
    upgraded = os.path.join(OUT_DIR, "other_version_file.blend")
    if os.path.exists(upgraded):
        os.remove(upgraded)
    make_blend(upgraded)
    if patch_build_marker(upgraded, other_version):
        code, text = run_blender(exe, [*common, "check-foreign", upgraded])
        check("BRAND-4 other-version child exit code", code, 0)
        check("BRAND-4 the file's build name is readable", name_of(field(text, "FILE_BUILD_HASH")),
              other_version)
        check_true("BRAND-4 another version of this fork raises no warning",
                   not [line for line in text.splitlines() if line.startswith("WM_REPORT")],
                   [line for line in text.splitlines() if line.startswith("WM_REPORT")])
    else:
        FAILURES.append("BRAND-4 fixture: could not patch the marker to another version")
        print(f"FAIL BRAND-4 could not patch the marker in {upgraded}", flush=True)

    # Negative control for the same rule: a file that names *no* build (pre-branding files, which
    # is what everything in the workspace was until this change) must stay silent. Without this,
    # "warn on anything that is not us" could silently degrade into "warn on everything".
    legacy = os.path.join(OUT_DIR, "unbranded_file.blend")
    if os.path.exists(legacy):
        os.remove(legacy)
    make_blend(legacy)
    if patch_build_marker(legacy, ""):
        code, text = run_blender(exe, [*common, "check-foreign", legacy])
        check("BRAND-4 unborned file child exit code", code, 0)
        check_true("BRAND-4 a file naming no build raises no warning",
                   not [line for line in text.splitlines() if line.startswith("WM_REPORT")],
                   [line for line in text.splitlines() if line.startswith("WM_REPORT")])

    print(f"== test_branding: {CHECKS} checks, {len(FAILURES)} failure(s), {len(SKIPS)} skip(s)",
          flush=True)
    for label in FAILURES:
        print(f"  failed: {label}", flush=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        print(f"== test_branding: {CHECKS} checks, 1 failure(s)", flush=True)
        sys.exit(1)
