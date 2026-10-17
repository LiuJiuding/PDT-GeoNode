# PDT Blender tooling

> **This file describes the development repository, which is the full one.** The
> published "changes only" repository (`pdt-changes/`, built by `export_changes.ps1`)
> ships a **subset**: the code, the tests, the normative manual (`docs/manual/`) and the
> build/test scripts. Everything below that points at `docs/**` (the L2 design records),
> `<repo>/../qa/` (the probe evidence) or the release scripts (`slice.ps1`,
> `version.ps1`, `publish_github.ps1`, `sync_upstream.ps1`) is development-side only and
> is **not** in that subset.

Build / version / test tooling for this fork of Blender, which only adds C++
geometry nodes on top of upstream. Everything here is **script-relative**: no
machine-specific paths are hardcoded, so the whole toolchain travels with the
repository.

```
<repo>/                       Blender source checkout (git, branch pdt/geo-nodes)
├── source/blender/nodes/geometry/
│   ├── CMakeLists.txt                     ← hook 1/3: custom node sources (PDT block)
│   └── nodes/custom/                      ← all custom nodes live here
│       ├── nodes.json                     manifest: id / label / category / test
│       └── node_geo_pdt_*.cc
├── source/blender/makesrna/intern/rna_nodetree.cc   ← hook 2/3: RNA type (PDT block)
├── scripts/startup/bl_ui/node_add_menu_geometry.py  ← hook 3/3: add-menu (PDT block)
├── scripts/addons_core/pdt_geo_select/    the viewport picker add-on
│   ├── __init__.py                        bl_info + the file map + register()/unregister() only
│   ├── parser.py targets.py               the selection grammar / which string parameter is written
│   ├── hit_test.py                        the preflight: the socket under the cursor, every refusal
│   ├── geometry.py bake.py                the geometry behind the node / the temporary object
│   ├── selection.py instances.py          the temporary selection / the three Instance Modes
│   ├── session.py state.py                begin-finish-cancel the pick / the shared mutable state
│   ├── overlay.py                         the status line, the overlay, the breadcrumb
│   ├── menus.py preferences.py keymap.py  the two pie menus / the reminder / the unbound item
│   ├── operators/                         the trigger and the modal pick (PICK-1), Apply/Cancel/...
│   └── utils/constants.py                 the vocabularies, sizes and operator idnames
├── scripts/addons_core/pdt_mmb_info/      the MMB node-info add-on (Houdini-style node read)
│   ├── __init__.py                        bl_info + the file map + register()/unregister() only
│   ├── preferences.py interface.py        MMB-8's settings / the information block
│   ├── operators/                         the modal operator the user binds a key to
│   ├── session.py hit_test.py             the hold gesture / who is under the cursor
│   ├── geometry.py snapshot.py            which socket carries the stream / reducing it
│   ├── evaluate.py                        our own temporary-tree evaluator (no other add-on)
│   ├── keymap.py                          the one unbound keymap item (MMB-8)
│   └── utils/                             constants, palette, diagnostics log
├── tests/files/modeling/geometry_nodes/pdt/*.blend  functional tests
└── tools/pdt/                            this directory
    ├── pdt_common.ps1        shared path/tool resolution (dot-sourced by the rest)
    ├── build_dev.ps1         fast dev build (geo | blender | install)
    ├── slice.ps1             start | status | finish one slice (branch -> gate -> --no-ff merge + tag)
    ├── sync_addons.ps1       copy scripts/addons_core/pdt_* into the build dir (+ load check)
    ├── addon_load_check.py   `sync_addons.ps1 -Verify` helper: does each add-on load?
    ├── check_compile.ps1     single-file `cl /Zs` syntax gate, ~3 s (no link)
    ├── run_tests.ps1         Python-API probe + menu check + geometry regression
    ├── tests/                the gate's own assertions (versioned): `test_*.py` headless, `gui_*.py` real window
    │   ├── wrangle_engine_selftest.cpp  the Blender-free engine self-test's assertions
    │   ├── build_engine_selftest.ps1    compiles + runs it (~3 s; objects land in `<build>/engine_selftest`)
    ├── hooks/pre-commit      the rule that spec/** is committed on its own (copy into .git/hooks)
    ├── pdt_nodes.py          new / sync / check / list  (maintains the three hooks)
    ├── pdt_spec.py           list / check / sync  (L0 minimal specs vs. manual + tests)
    ├── version.ps1            status / init / manifest / upstream / tag / stamp / verify
    ├── export_changes.ps1     build the "changes only" artifact (`pdt-changes/`, next to the repo)
    ├── publish_github.ps1     pre-flight + push the fork's branch to a GitHub repo
    ├── sync_upstream.ps1      fetch Blender upstream and rebase our commits onto it
    ├── configure_geo_fast.ps1  optional trimmed build dir for cold builds
    ├── probe_nodes.py        generic node-API probe (runs on any Blender)
    ├── templates/            new-node C++ template
    ├── benchmarks/           reproduces the timings in docs/01
    ├── spec/                 L0 minimal specs (hand-written by the user, agent read-only)
    │   ├── README.md         the format and the ownership rule
    │   ├── <topic>.md        one page per feature: goal / contract / invariants / bounds
    │   └── coverage.md       L0 item -> manual section -> test (agent-maintained)
    └── docs/                 findings, measured numbers, workflows
```

Next to the repository (outside git), three sibling directories matter:

| Sibling | What it is | Where it is documented |
| --- | --- | --- |
| `<repo>/../qa/` | development probes and their evidence, one directory per feature | `qa/README.md` |
| `<repo>/../qa/build_drop/` | **the build drop folder**: how a sandboxed shell gets a *parallel* build (about 30 s instead of 17 min) | `qa/build_drop/README.md`, `docs/01-编译加速.md` §4 |
| `<repo>/../builds/` | build records (`build-log.tsv`) and backups | `docs/02-版本管理.md` |

The workspace root's own `README.md` is the **tool map** ("what do I want to do → which command"),
and the root `README.md` → this file → `docs/00-当前状态.md` is the reading order for a new session.

### Where a new file goes: `tests/` or `qa/`?

| The file is… | Goes to | Why |
| --- | --- | --- |
| an **assertion** that must stay green — it goes red when the behaviour regresses | **`tools/pdt/tests/`** (versioned, run by `run_tests.ps1`) | the gate has to be able to run it forever; `test_*` is headless, `gui_*` needs a real window, and a new one has to be added to the gate |
| a **one-off measurement, screenshot or log** — evidence for a decision | **`<repo>/../qa/<feature>/`** (outside git) | its *conclusion* belongs in `docs/**`; the script itself may go afterwards, and `_`-prefixed files mean "one-off" — see `qa/README.md` |

Two leaks this rule exists to stop (both found and cleaned on 2026-10-09): a **stale copy** of the engine
self-test lived in `qa/wrangle/` next to the real one in `tests/` (same file name, different content —
the worst kind of duplicate), and the GUI tests write their screenshots and logs into `tests/_gui_*`,
which `.gitignore` already excludes (`/tools/pdt/tests/_gui_*.png|.txt`); by the qa convention those are
deleted after the run, not kept.

### Screenshotting an editor area (GUI evidence)

Use **the agent plugin's `dsh_blender_screenshot.py`** — it lives in the `dsh-blender` plugin
(`%USERPROFILE%\.dsh\profiles\web\node_modules\dsh-blender\scripts\`), so every project on this machine
gets the same implementation, and it is what `blender_live_snapshot` uses for live sessions.

```powershell
# one editor area (the scene comes from a file that defines setup()), or the whole window without --area
& $exe --factory-startup --enable-event-simulate `
    --python "$env:USERPROFILE\.dsh\profiles\web\node_modules\dsh-blender\scripts\dsh_blender_screenshot.py" -- `
    --setup qa\wrangle\code_gutter_shot.py --out qa\wrangle\code_gutter_shot.png --area NODE_EDITOR
```

**Do not call `bpy.ops.screen.screenshot()` / `screenshot_area()` yourself.** Measured on 2026-10-10:
they read the window's *front buffer* and returned a 1x1 PNG in about 8 of 10 runs (2 successes), and
they happily photograph whatever is painted on top. The plugin module captured 9 of 9, first attempt,
in the same session. It is also where the evidence for the wrangle line-number gutter comes from.

Three traps it absorbs; all three cost real time to find:

| Trap | What happens | What it does |
| --- | --- | --- |
| the front buffer | `screenshot*()` writes a 1x1 PNG, silently | captures with Win32 `PrintWindow`; this also works when the window is *behind* another one |
| a popup in the way | the splash screen (shown on every no-file startup) or an open menu lands in the picture | switches `preferences.view.show_splash` off and queues an `Esc`; **the capture must happen from a timer**, because a queued event is only handled once the window manager pumps its queue (measured: a capture in the same synchronous script came back byte-identical to one taken with the popup still up) |
| the two flips | `PrintWindow`'s DIB is bottom-up *and* `Image.pixels` is bottom-up; get them wrong in opposite directions and the picture is upside down | flips once while cropping, once while writing, then checks the file |

Those traps are written down here rather than only in the plugin because this is where a session in
*this* repository will look.

## Path resolution

| Value | Default | Override |
| --- | --- | --- |
| repo root | `tools/pdt/../..` | `PDT_REPO` (python) |
| build dir | sibling `build_windows_x64_vc17_Release` containing `CMakeCache.txt`; else newest `build_windows*` | `PDT_BUILD_DIR` |
| built binary | `<build>/bin/<type>/blender.exe` | `PDT_BUILD_TYPE` |
| records (build log, manifests) | sibling `builds/` of the repo, i.e. **outside** git | `PDT_RECORDS_DIR` |
| reference build (`-Official`) | newest `%ProgramFiles%\Blender Foundation\Blender*`, else `%LOCALAPPDATA%\Programs\Blender Foundation\Blender*` | `PDT_REFERENCE_BLENDER` |
| MSBuild / CMake | `vswhere` → `PATH` → known VS 2022 layout / `cmake` on `PATH` → known layout | — |

No `vcvarsall.bat` call is needed: MSBuild's own VS targets resolve the toolchain.

## Quick reference

```powershell
. <repo>\tools\pdt\check_compile.ps1                   # ~3 s: syntax/type gate for ONE source
. <repo>\tools\pdt\build_dev.ps1 -Target geo           # compile bf_nodes_geometry
. <repo>\tools\pdt\build_dev.ps1 -Target blender       # compile + link blender.exe
. <repo>\tools\pdt\build_dev.ps1 -Target install       # + datafiles/Python scripts
. <repo>\tools\pdt\sync_addons.ps1                     # only the pdt_* add-ons (no compile)
. <repo>\tools\pdt\run_tests.ps1                       # API + menu + geometry regression
. <repo>\tools\pdt\run_tests.ps1 -Gui                  # + the real-window modal picker test
. <repo>\tools\pdt\run_tests.ps1 -Official             # must NOT see the custom nodes
python tools\pdt\pdt_nodes.py new GeometryNodePDTX "PDT X"
python tools\pdt\pdt_nodes.py sync
python tools\pdt\pdt_nodes.py check
python tools\pdt\pdt_spec.py check                     # L0 spec -> manual -> tests (advisory)
. <repo>\tools\pdt\version.ps1 -Action status
```

### Sharing the changes (changes-only artifact)

```powershell
# pdt-changes/  = files/ (byte-identical copies of our added/modified files)
#                 + apply.ps1 + README.md   (sits NEXT TO the repo, outside it)
pwsh -File tools\pdt\export_changes.ps1 -GitInit     # rebuild + git init + one commit
cd ..\pdt-changes; git remote add origin <url>; git push -u origin main
```

The export is **code + tests + the normative docs** (`docs/manual/`, `spec/`) plus the
build/test toolchain. `-Exclude` / `-Keep` (see the script header) hold back the L2 design
records (`docs/**`), the machine notes, `benchmarks/` and the release scripts; pass
`-Patches` to also emit the `git format-patch` series (off by default: it replays every
commit, including the paths the filter leaves out).

Override the defaults with `-Repo` / `-Out` (or `PDT_REPO` / `PDT_CHANGES_OUT`); the
artifact is regenerated from scratch on every run, so its own history is always a
single commit. It is **not** part of the fork: clone it, run `apply.ps1` on a matching
Blender checkout, build.

### How long a change costs (measured)

| What you changed | Loop |
| --- | --- |
| nothing | seconds (MSBuild no-op) |
| one custom-node `.cc` (in `bf_nodes_geometry`'s unity build) | **~11 minutes** — four measurements: 653.5 / 649.9 / 656.2 / 657.4 s (2026-09-11) |
| `interface_handlers.cc` (an editor translation unit), **serial inside a sandbox** | **1032.7 s** (2026-10-09), i.e. `build_dev.ps1 -Target install -Safe` |
| the same change **through the drop folder** (parallel, outside the sandbox) | **~30 s** (measured `wall=29.3s` for a full `install`) |
| only Python (an add-on) | seconds — `sync_addons.ps1` copies the add-on into the build's `bin\<type>\<version>\scripts\addons_core\` and loads it once, **no build at all** (`run_tests.ps1` syncs by itself before testing) |

`check_compile.ps1` (~3 s) catches syntax, type and header errors but **no logic error**:
it is worth running before every long build, and it is not a substitute for the tests.
A change that is syntactically perfect but semantically wrong (the P13 regression where a
function signature changed and a call site did not) passes it every time.

### Building from a sandboxed shell

Two settings of MSBuild need **named pipes**, which a sandboxed shell blocks: the parallel
worker nodes and the file tracker. The symptoms are easy to misread, so start here:

| Symptom | What it is |
| --- | --- |
| `exit=1` **with no output at all**, within a second or two | `/maxcpucount` > 1: the worker nodes could not be spawned |
| `MSB6006: "CL.exe" exited with code 2` in `buildinfoobj.vcxproj` | serial, but the file tracker is still on |

So there are exactly two ways to build here:

```powershell
# 1) the fast one: hand the build to the scheduled task, which runs OUTSIDE the sandbox (~30 s)
pwsh -NoProfile -File <workspace>\qa\build_drop\drop_build.ps1 -Target install -Jobs 16

# 2) the slow one: serial inside the sandbox (`-Safe` = /maxcpucount:1 + TrackFileAccess=false)
. <repo>\tools\pdt\build_dev.ps1 -Target install -Safe
```

Close the Blender GUI first either way: a running `blender.exe` from the build directory locks the
output and the build refuses with `exit=3` (do not reach for `-KillBlender`).

One more sandbox fact for the git side: the `pre-commit` hook is **installed and does enforce** the
`spec/**`-on-its-own rule. It was re-verified on 2026-10-15 from *outside* the sandbox (a custom
`run.cmd` through the drop folder: `git hook run pre-commit` with one spec file and one other file
staged prints its rejection and exits 1). What varies per session is whether the agent's sandbox can
start cygwin `sh.exe` at all - when it cannot (`couldn't create signal pipe, Win32 error 5`) the hook
never runs and **`git commit` fails with HEAD unchanged**. Measured 2026-10-09 (fails), 2026-10-13
(works: commit returns 0 and the hook rejects mixed staging), 2026-10-15 (fails again). So: try a
normal commit first; if it fails, commit with `--no-verify` and keep the `spec/**` rule by hand
(each commit's `git show --stat` must be either only `tools/pdt/spec/**` or none) - that is bypassing
a hook that cannot start, not bypassing the rule. Exit codes of `git switch` / `merge` / `tag` lie
the same way (git-lfs owns the `post-*` hooks): check `git log` / `git tag` instead of the code.
And run the hook through git, never by hand - a bare `sh.exe` has no `grep` on PATH and the script
then passes silently; use `git hook run pre-commit`.

## Documentation

* `docs/00-当前状态.md` — **read this first in a new session**: current facts, how to verify, what is left.
* `docs/01-编译加速.md` — measured build numbers, why `/maxcpucount` hardly matters, what to do instead.
* `docs/02-版本管理.md` — branch/tag/manifest policy and the upstream rebase workflow.
* `docs/03-节点开发与测试.md` — the three hooks, the two crash/link traps, Python-API reachability, the three test layers.
* `docs/05-GPU构建.md` — Cycles GPU kernels (CUDA `sm_120`, OptiX): the exact CMake options, measured kernel build
  times, the two probes under `qa/`, and why OptiX currently needs a newer driver than this machine has.
* `docs/机器与会话.md` — machine/session facts for real-window GUI tests: display topology, permission
  limits, the probes under `qa/` (not project knowledge; read only when you need a real window or elevation).
* `docs/archive/README.md` — index of the archived process documents below (read one only if you touch that feature).
* `docs/archive/04-视口选择.md` — the P1–P13 development log of `PDT Selection`, including every trap that was hit.
* `docs/archive/05-P13交接.md` — the P13 problem statement (historical; the blocker it describes is fixed).
* `docs/archive/06-P13实施计划.md` — the P13 plan, the full defect triage, and the execution record.
* `docs/archive/07-曲线支持评估.md` — can `PDT Selection` support curves? Measured answer (the node already
  does; the picker does not), the Blender 5.2 curve-API traps, and the plan (A/B) with test plan.
* `docs/archive/08-实例支持评估.md` — can it support instances? Measured answer for both picker designs
  (instances→points, and realize-with-instance-index), the instance-API traps, the C++ `Instance`
  domain plan, and the freeze budget (peak 5.5 GB for a 4.8M-vertex realize).

## User manual

`manual/` is the **normative specification** of what the nodes and operators do — the
Blender-manual-style reference for users, and the top-level design document for this fork.
Behaviour, socket tooltips, panel labels and tests are all expected to agree with it; when
they disagree, the manual is what the implementation is changed to match.

* `manual/index.md` — start here, and the list of guarantees that must hold.
* `manual/nodes/pdt_selection.md` — the `PDT Selection` node.
* `manual/operators/selection_pick.md` — the viewport picker (`Ctrl+Shift+P`).
* `manual/reference/selection_syntax.md` — the normative grammar and semantics of the selection string.

