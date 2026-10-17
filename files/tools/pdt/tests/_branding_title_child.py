"""Helper for `gui_branding_title.py`: capture the OS window titles of this real-window Blender.

    blender.exe --factory-startup <file.blend> --python _branding_title_child.py -- <out.txt>

Never invoked directly by a human (the leading underscore keeps `run_tests.ps1` from picking it
up as a test). The watchdog timer is registered **before** the work, so an exception can never
leave a window hanging - the trap documented in `docs/00-当前状态.md`.

Writes one `TITLE<TAB>...` line per top-level window of this process, then quits.
"""

import ctypes
import os
import sys

import bpy

OUT = sys.argv[-1]


def write(text):
    try:
        with open(OUT, "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        pass


def quit_blender():
    bpy.ops.wm.quit_blender()
    return None  # one-shot timer


bpy.app.timers.register(quit_blender, first_interval=8.0)


def grab():
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        pid = kernel32.GetCurrentProcessId()
        own_pid = ctypes.c_ulong()
        lines = []

        enum_proc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)
        )

        def visit(hwnd, _lparam):
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(own_pid))
            if own_pid.value != pid:
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                lines.append("TITLE\t%s" % buf.value)
            return True

        user32.EnumWindows(enum_proc(visit), 0)
        write("\n".join(lines) + ("\n" if lines else ""))
    except Exception as exc:  # noqa: BLE001
        write("ERROR\t%s\n" % exc)
    return None  # one-shot timer


# Give GHOST a moment to create and title the window, then read it back.
bpy.app.timers.register(grab, first_interval=5.0)

# `--factory-startup` hides the splash, so nothing else is needed; keep the process alive until
# one of the two timers runs.
_ = os.path.dirname(bpy.app.binary_path)
