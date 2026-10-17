**English** | [简体中文](README.zh-CN.md)

# PDT Geometry Nodes for Blender - changes only

> **This is not a Blender source tree.** It holds *only* the files this project adds to
> or changes in Blender, i.e. two geometry nodes, two add-ons and a handful of upstream
> edits. To get a runnable Blender you copy these files onto a real Blender source
> checkout and build it (step by step below).

Built on Blender 5.2.3 LTS (rc), upstream base commit `e8b7fe175f6fc837e8a0db7444c5fe393cd0b5fb`, export made from `aece7da5 docs: brand version = release tag, in the manual and the tag policy`.

## What you get

| What | Where it shows up in Blender | What it does |
| --- | --- | --- |
| **PDT Selection** (geometry node) | `Shift+A` > **PDT** > PDT Selection | Turns a *selection string* you type (say `0-15 ^3 group:arm`) into a boolean field - handy to drive Delete Geometry, Set Position, ... |
| **PDT Wrangle** (geometry node) | `Shift+A` > **PDT** > PDT Wrangle | Write a small VEX-like program in the node (`@P.y += 1;`) and it runs per point / face / spline |
| **PDT Geometry Selection** (add-on) | bind a shortcut, then hover a node's string field | Click geometry in the 3D viewport instead of typing indices; the picked indices are written back into that field |
| **PDT Node Info** (add-on) | bind a shortcut, then hold it over a node | Houdini-style pop-up with the geometry stream flowing through that node |
| small upstream edits | Outliner drag & drop, USD importer | documented in the manual (see *Documentation*) |

The nodes are new; the add-ons add viewport tooling. Everything else in your build is
plain upstream Blender.

## Before you start

* **Windows 10/11 x64** for the helper scripts in `tools/pdt/` (they are PowerShell).
  On Linux/macOS the copy step still works by hand (`cp` + a normal CMake build).
* **A Blender source checkout** at the base commit above, **with its precompiled
  libraries** (`lib/windows_x64/` must not be empty - see the next section), and a working
  Visual Studio 2022 + CMake setup - i.e. you can already build Blender itself. If not, do
  [Blender's own build setup](https://developer.blender.org/docs/handbook/building_blender/)
  first; this repository does not replace it.
* **Disk and time**: the precompiled libraries alone are ~6.5 GB, a from-scratch build is
  big and slow (tens of minutes to a few hours depending on the machine). After that,
  changing a node file is a ~20-30 s rebuild.

## Prepare the source tree: the precompiled libraries

**A Blender source download does not contain the libraries the build needs.** If you got
the source as a ZIP (or a GitHub "Download ZIP"), `lib/windows_x64/` exists but is
**empty**, and CMake will refuse to configure. The libraries live in their own
LFS-backed git repository (`https://projects.blender.org/blender/lib-windows_x64.git`,
branch `blender-v5.2-release`) and are fetched by the official update step:

```powershell
# from the Blender source root. Needs git, Python and Git LFS on PATH.
make update          # on Windows the root make.bat forwards "update" to the same script
```

That command enables the matching `lib/<platform>_<arch>` submodule (the URL is in
`.gitmodules`) and downloads it. It is slow - Blender's own script warns it "may take over
an hour" on a slow link.

**If your source is a ZIP, `make update` cannot work** - it drives git submodules and the
ZIP has no `.git`. Clone the library repository into the empty directory instead:

```powershell
git lfs install
git clone -b blender-v5.2-release https://projects.blender.org/blender/lib-windows_x64.git C:\work\blender\lib\windows_x64
```

(Our own 5.2.3-rc checkout uses commit `60d6e96b9` of that repository - the tip of that
branch. `git -C C:\work\blender\lib\windows_x64 checkout 60d6e96b9` pins it exactly.)

Then continue with the build step below. Both routes are the same mechanism Blender
documents; nothing here is project-specific.

> **Version matters more than usual with a ZIP.** Our files are relative to
> `e8b7fe175f6fc837e8a0db7444c5fe393cd0b5fb` (Blender 5.2.3 LTS (rc)). A ZIP of another 5.2.x applies fine - `apply.ps1` is a
> plain file copy - but it also **overwrites** upstream code in the handful of files we
> touch, so a 5.2.0 tree ends up with 5.2.0-minus-a-few-patches. Prefer cloning the
> release branch and checking out that commit. (We also ship
> `BKE_blender_version.h`, which pins the file subversion to 46, and our
> `versioning_520.cc`; applied onto another 5.2.x the build will simply *report*
> 5.2.3 LTS (rc) - that does not make it the tree these tests were run on.)

## Step by step

```powershell
# 0. clone this repository somewhere OUTSIDE the Blender checkout
git clone <this repo> C:\work\pdt-changes
cd C:\work\pdt-changes

# 1. get Blender's source at the base commit (or a compatible 5.2.x) ...
git clone https://projects.blender.org/blender/blender.git C:\work\blender
git -C C:\work\blender checkout e8b7fe175f6fc837e8a0db7444c5fe393cd0b5fb
#    ... plus its precompiled libraries - the official update step. On Windows:
C:\work\blender\make.bat update
#    (Linux/macOS: `make update` from the source root. A ZIP source has no git, so this
#     step cannot run - clone lib\windows_x64 by hand, see the section above.)

# 2. copy our files in - run the preview first, it only lists what would change
pwsh -File apply.ps1 -Target C:\work\blender -DryRun
pwsh -File apply.ps1 -Target C:\work\blender

# 3. build. The first run needs a CONFIGURED build directory. Two ways:
#    (a) follow Blender's own instructions and name it build_windows_x64_vc17_Release,
#        next to the checkout - the scripts find that name by themselves;
#    (b) or let this script configure a trimmed one (Cycles/USD off; dev build only):
pwsh -File C:\work\blender\tools\pdt\configure_geo_fast.ps1 -Build      # long, one time
#    With (b) the directory is C:\work\build_geo_fast, which the scripts do not guess -
#    tell them once per PowerShell window (otherwise: "project not found"):
$env:PDT_BUILD_DIR = 'C:\work\build_geo_fast'
#    ... and then, for every later code change:
. C:\work\blender\tools\pdt\build_dev.ps1 -Target install

# 4. run the tests (headless; add -Gui for the real-window ones)
. C:\work\blender\tools\pdt\run_tests.ps1
```

Expected result: `run_tests.ps1` ends with `== summary: 0 failure(s) ==`, and the built
`blender.exe` has a **PDT** category in the geometry-nodes `Add` menu.

## Using it

1. Start the built Blender, make a Geometry Nodes modifier on any object.
2. `Shift+A` in the node editor -> **PDT** -> pick a node.
3. For the add-ons: `Edit > Preferences > Add-ons`, enable **PDT Geometry Selection** and
   **PDT Node Info**, then **assign them a shortcut yourself** under
   `Preferences > Keymap` (they deliberately grab no key - so they never fight your
   keymap; the two entries are listed unbound).
4. In *official* Blender those nodes appear as undefined nodes - that is expected, they
   only exist in a build made from this repository.

## If something goes wrong

| Symptom | What it means |
| --- | --- |
| Configure stops with "the required external libraries in `lib/windows_x64` are missing" (or that folder is simply empty) | the source came from a ZIP - it has no precompiled libraries. See *Prepare the source tree*: `make update` in a git checkout, or clone the library repository by hand |
| `git lfs` errors while fetching the libraries | install Git LFS first (`git lfs install`); the library repository keeps its binaries in LFS |
| `apply.ps1` says the target "does not look like a Blender source checkout" | point `-Target` at the folder that contains `source\blender\` |
| Build says a project/node type is missing | the build directory was configured before the files were copied - rebuild with `-Reconfigure` |
| `run_tests.ps1` warns `binary hash ... != HEAD` | the `blender.exe` is older than the sources; rebuild before trusting the test result |
| `key "test_object" not found` in a test | the `.blend` argument has to come **before** `--python` on the command line |
| `run_tests.ps1 -Gui` fails with no PASS line | those tests need a real desktop session (no RDP-less service session) |
| A hook/`sh.exe` error on `git commit` in the development repo | that is the development workflow, not needed here - this repository is a plain file dump |

## Documentation

* `tools/pdt/docs/manual/` - the **normative** manual (Chinese): syntax of the selection
  string, the Wrangle language, the picker and node-info behaviour. `run_tests.ps1`
  executes its examples, so it is always in sync with the code.
* `tools/pdt/spec/` - the hand-written minimal specs those manuals implement.
* `tools/pdt/README.md` - how the tooling is put together.

The design records, measured build timings and machine notes are deliberately **not**
shipped: they describe the machine this was developed on.

## What is in `files/`

| Path | Content |
| --- | --- |
| `source/blender/nodes/geometry/nodes/custom/` | the two nodes, the Wrangle engine, the node manifest |
| `scripts/addons_core/pdt_geo_select/`, `pdt_mmb_info/` | the two add-ons |
| `tools/pdt/tests/` | the assertions behind `run_tests.ps1` |
| `tools/pdt/spec/`, `tools/pdt/docs/manual/` | the specs and the manual |
| three upstream files | geometry CMake list, node RNA registration, "Add" menu entry - only the marked `PDT` blocks |
| `tests/files/usd/...` | updated reference files for two documented USD importer changes |

Everything is byte-identical to the file in our checkout, same path as in Blender.

## License

Blender is GPL-2.0-or-later. The files under `source/` and the add-ons are covered by that
license; publishing them here satisfies the source-availability requirement for builds you
distribute. `tools/pdt/` scripts and documentation follow the same terms.
