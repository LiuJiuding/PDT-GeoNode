# SPDX-FileCopyrightText: 2025 Blender Foundation
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Diagnostics: the always-on log file (MMB-9) and the optional console line.

MMB-9 requires four failure modes - the key never arrived, no node under the cursor, the
geometry read raised, the block painted wrong - to be distinguishable in a log that is
written unconditionally, not behind a preference. `_diagnose()` is the console extra for a
session with a terminal; it is a fixed constant now, not a setting.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

import bpy

from .constants import VERBOSE_DIAGNOSTICS


# ---------------------------------------------------------------------------
# always-on file log
# ---------------------------------------------------------------------------

#: Where the diagnostic log goes. `PDT_MMB_LOG` overrides it; the default is the OS temp
#: directory, which is writable everywhere and needs no knowledge of the source checkout.
LOG_PATH = os.environ.get("PDT_MMB_LOG") or os.path.join(tempfile.gettempdir(), "pdt_mmb.log")


_log_started = False


def _log(event, **fields):
    """Append one line to `LOG_PATH`, unconditionally.

    Deliberately *not* behind the `verbose` preference and not only on stderr. When the
    block does not appear, the question is which of four steps failed - the key never
    reached the operator, the operator found no node, the read raised, or the block was
    built but not painted - and every one of those looks identical from outside. A file
    is also the only channel that survives a window launched from a desktop icon, where
    there is no console to read.

    The file is truncated once per Blender start (first write of the process), so it
    cannot grow without bound, and every line is flushed so a crash still leaves
    evidence.
    """
    global _log_started
    try:
        parts = [event]
        for key, value in fields.items():
            parts.append(f"{key}={value!r}" if isinstance(value, str) else f"{key}={value}")
        with open(LOG_PATH, "w" if not _log_started else "a", encoding="utf-8") as handle:
            if not _log_started:
                _log_started = True
                handle.write(
                    f"=== PDT node info, session start {time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"blender {bpy.app.version_string} ===\n"
                )
            handle.write(" ".join(parts) + "\n")
    except Exception:  # noqa: BLE001 - diagnostics must never break the feature
        pass


def _diagnose(message):
    """One stderr line per trigger, when `VERBOSE_DIAGNOSTICS` is on.

    This exists because "the block does not appear" has three very different causes
    that look identical from the outside: the key binding never reached the operator,
    the operator ran but no node was under the cursor, or the block was built but not
    drawn. One line per press tells them apart from the system console, with no
    debugger and no rebuild. The log file (`MMB-9`) is written either way; the console
    line is a fixed constant now, not a preference (MMB-8 allows only two settings).
    """
    if not VERBOSE_DIAGNOSTICS:
        return
    try:
        print(f"PDT node info: {message}", file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001 - diagnostics must never break the operator
        pass
