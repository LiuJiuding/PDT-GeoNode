# SPDX-FileCopyrightText: 2026 PDT Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The mutable state the modules share.

Three things live here, and only because more than one module needs them:

* `session` - the running pick (`begin_pick()` fills it, `cancel_pick()` empties it). **Never
  rebind it**: `clear()` + `update()` instead, because the other modules hold a reference to
  this very dict (`selection.py`, `instances.py`, `overlay.py`, the operators).
* `pending_pick` - what the trigger found while the pie menu is collecting a choice.
* `modal_active` - True while the pick's modal loop owns the events; the overlay uses it to
  decide whether it has to recount the selection itself (`overlay.py`).
"""

from __future__ import annotations

#: The running pick session; see the module docstring for the no-rebinding rule.
session: dict = {}

#: The node and socket a pie menu is collecting a choice for (see `operators/pick.py`).
pending_pick: dict = {}

#: True while the pick's modal loop is running.
modal_active = False
