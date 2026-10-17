"""Enable add-ons in a real Blender and report whether they load.

    blender.exe -b --factory-startup --python-exit-code 1 \
        --python tools\\pdt\\addon_load_check.py -- pdt_mmb_info pdt_geo_select

Used by `sync_addons.ps1 -Verify`. It catches what a file copy can break - a module that
never made it into the installed copy, an import cycle, a name that only exists in the
source tree - before a test run reports it as a mysterious logic failure. `default_set=False`
on purpose: enabling for the session must not touch the user's saved preferences.

`addon_utils.enable()` **swallows** a failed import (it prints the traceback and reports
"enabled" anyway), so "enable did not raise" is not the check: a run of a package that never
imported used to be reported as `OK ... version None`. The verdict is therefore taken from
`addon_utils.check()` (is it really enabled?) *and* the module's `bl_info`, so a half-imported
add-on fails here instead of looking like a test regression later.
"""

import sys
import traceback

import addon_utils

names = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if not names:
    print("no add-on given (pass them after `--`)")
    raise SystemExit(2)

failed = []
for name in names:
    try:
        addon_utils.enable(name, default_set=False)
        module = sys.modules.get(name)
        bl_info = getattr(module, "bl_info", {}) or {}
        _loaded_by_default, enabled = addon_utils.check(name)
        if module is None or not bl_info or not enabled:
            failed.append(name)
            print(
                f"FAIL {name}: enabling did not complete (module={module is not None}, "
                f"bl_info={bool(bl_info)}, enabled={bool(enabled)}) - see the traceback above"
            )
            continue
        submodules = sorted(
            key for key in sys.modules
            if key.startswith(name + ".") and not key.endswith("__pycache__")
        )
        print(f"OK   {name}: loaded and registered (version {bl_info.get('version')}, "
              f"{len(submodules)} submodule(s){': ' + ', '.join(submodules) if submodules else ''})")
    except Exception as error:  # noqa: BLE001 - the report is the point
        failed.append(name)
        print(f"FAIL {name}: {type(error).__name__}: {error}")
        traceback.print_exc()

print(f"== addon_load_check: {len(names) - len(failed)}/{len(names)} ok ==")
raise SystemExit(1 if failed else 0)
