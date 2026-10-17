<#
Export *only our changes* out of the Blender fork into a small, self-contained repo
skeleton - no Blender source tree, no history, no Git-LFS.

    pwsh -File tools\pdt\export_changes.ps1                      # build the folder
    pwsh -File tools\pdt\export_changes.ps1 -GitInit             # ... and `git init` + commit
    pwsh -File tools\pdt\export_changes.ps1 -Out D:\pdt-changes
    pwsh -File tools\pdt\export_changes.ps1 -Patches             # also emit the patch series

Result layout:

    pdt-changes/
      README.md          what this is, the upstream base commit, how to build it
      apply.ps1          copies files\** into a Blender source checkout
      removed.txt        paths our commits delete (normally empty)
      files/<blender paths>   the added/modified files, byte-identical to HEAD
      patches/*.patch    only with -Patches: `git format-patch` series on top of the base

`files/` is the practical route (works without a matching upstream commit).
The patch series is **off by default** on purpose: it replays every commit, so it
carries things this export deliberately leaves out (the deleted scaffold node, the
machine-specific notes, the L2 process records).

What goes out is *code, tests and the normative docs a reader needs to build and
verify the thing* - see `$Exclude` / `$Keep` below for the exact rule.
#>
[CmdletBinding()]
param(
  [string]$Repo = '',
  # Upstream base the fork currently sits on. Moved from v5.2.1 (9e2066ae) to the
  # `blender-v5.2-release` tip e8b7fe17 on 2026-10-15 (the 5.2.3 sync): exporting against
  # the *old* base would list every upstream change in between as if it were ours.
  [string]$Base = 'e8b7fe175f6fc837e8a0db7444c5fe393cd0b5fb',
  [string]$Head = 'HEAD',
  [string]$Out = '',
  [string]$Remote = 'https://github.com/LiuJiuding/PDT-GeoNode.git',

  # Development history stays at home: the L2 records and machine notes describe *this*
  # machine and *this* round of work, and the release/probe tools only mean something next
  # to the local build tree. A reader of the exported repo gets the code, the tests, the
  # normative manual/spec and the build+test toolchain - nothing to sift through.
  # Patterns are repo-relative, forward slashes, matched with PowerShell `-like`
  # (so `*` also crosses `/`: `tools/pdt/docs/**` covers the whole subtree).
  [string[]]$Exclude = @(
    'tools/pdt/docs/**',          # 00-20 过程记录 / archive/ / 机器与会话.md / 05-GPU / 01-编译加速
    'tools/pdt/benchmarks/**',    # reproduces this machine's build timings
    'tools/pdt/inspect_keymap*.py', # local keymap probes
    'tools/pdt/slice.ps1',        # branch/slice/release workflow, this repo only
    'tools/pdt/version.ps1',
    'tools/pdt/publish_github.ps1',
    'tools/pdt/sync_upstream.ps1'
  ),
  # Wins over -Exclude: the normative manual is what `tests/test_manual_examples.py`
  # executes line by line, so it is part of the test contract, not a dev record.
  [string[]]$Keep = @(
    'tools/pdt/docs/manual/**'
  ),
  [switch]$Patches,
  [switch]$GitInit
)

$ErrorActionPreference = 'Stop'
function Say([string]$text) { Write-Host $text }
function Fail([string]$text) { Write-Host "FAIL  $text" -ForegroundColor Red; exit 1 }

# This script lives in <repo>/tools/pdt: the repo is two levels up, and the exported
# artifact sits *next to* the repo (outside it), the way the build records do.
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $Repo) { $Repo = if ($env:PDT_REPO) { $env:PDT_REPO } else { $RepoRoot } }
if (-not $Out) {
  $Out = if ($env:PDT_CHANGES_OUT) { $env:PDT_CHANGES_OUT } else { Join-Path (Split-Path -Parent $RepoRoot) 'pdt-changes' }
}

if (-not (Test-Path (Join-Path $Repo '.git'))) { Fail "not a git working tree: $Repo" }
$Repo = (Resolve-Path $Repo).Path

function GitLines([string[]]$arguments) {
  $result = & git -C $Repo -c core.quotepath=false @arguments
  if ($LASTEXITCODE -ne 0) { Fail "git $($arguments -join ' ') failed" }
  return @($result | Where-Object { $_ -ne '' })
}

$base = @(GitLines @('rev-parse', $Base))[0]
Say "base: $base  ($(GitLines @('log','-1','--format=%h %cd %s','--date=short',$base)))"

# The exported README states which Blender the base commit is. Read it out of that commit
# instead of hard-coding it: the base moves on every upstream sync (it said "5.2.1" for a
# whole round after the 5.2.3 sync moved it), and a wrong version in the hand-off README is
# exactly the kind of stale text this repo keeps having to hunt down.
$versionHeader = @(GitLines @('show', "${base}:source/blender/blenkernel/BKE_blender_version.h"))
function BaseVersionDefine([string]$name) {
  $line = @($versionHeader | Where-Object { $_ -match "^\s*#define\s+$name\s+(.+?)\s*$" })[0]
  if (-not $line) { return '' }
  return ($line -replace "^\s*#define\s+$name\s+", '').Trim()
}
$vNumber = BaseVersionDefine 'BLENDER_VERSION'
$vPatch = BaseVersionDefine 'BLENDER_VERSION_PATCH'
$vCycle = BaseVersionDefine 'BLENDER_VERSION_CYCLE'
$vSuffix = BaseVersionDefine 'BLENDER_VERSION_SUFFIX'
$baseVersion = if ($vNumber -match '^(\d)(\d\d)$') { "$($Matches[1]).$([int]$Matches[2])" } else { $vNumber }
if ($vPatch -ne '') { $baseVersion += ".$vPatch" }
if ($vSuffix -ne '') { $baseVersion += " $vSuffix" }
if ($vCycle -ne '' -and $vCycle -ne 'release') { $baseVersion += " ($vCycle)" }
Say "base version: Blender $baseVersion"

Say "head: $((GitLines @('log','-1','--format=%h %s',$Head)))"

$added = GitLines @('diff', '--name-only', '--diff-filter=AM', $base, $Head)
$removed = GitLines @('diff', '--name-only', '--diff-filter=D', $base, $Head)

# Apply -Exclude / -Keep. `-Keep` is checked first so an excluded subtree can still
# contribute single paths (manual/ under docs/). Reporting is per rule, because "why is
# this file not in the export" is the question this filter is going to be asked.
$changedCount = $added.Count
$droppedBy = [ordered]@{}
$kept = New-Object System.Collections.Generic.List[string]
foreach ($file in $added) {
  $key = $file.Replace('\', '/')
  $isKept = $false
  foreach ($pattern in $Keep) {
    if ($key -like $pattern) { $isKept = $true; break }
  }
  if (-not $isKept) {
    $excludedBy = $null
    foreach ($pattern in $Exclude) {
      if ($key -like $pattern) { $excludedBy = $pattern; break }
    }
    if ($null -eq $excludedBy) { $isKept = $true }
    else {
      if (-not $droppedBy.Contains($excludedBy)) { $droppedBy[$excludedBy] = 0 }
      $droppedBy[$excludedBy]++
    }
  }
  if ($isKept) { $kept.Add($file) }
}
$added = @($kept)
Say ("changed files: {0} added/modified, {1} deleted" -f $changedCount, $removed.Count)
if ($droppedBy.Count -gt 0) {
  $droppedTotal = 0
  foreach ($count in $droppedBy.Values) { $droppedTotal += $count }
  Say ("exported {0} of them, left {1} behind:" -f $added.Count, $droppedTotal)
  foreach ($pattern in $droppedBy.Keys) { Say ("  {0,4}  {1}" -f $droppedBy[$pattern], $pattern) }
}
if ($added.Count -eq 0) { Fail 'nothing to export' }

if (Test-Path $Out) { Remove-Item $Out -Recurse -Force }
$filesDir = Join-Path $Out 'files'
New-Item -ItemType Directory -Path $filesDir -Force | Out-Null

# `git checkout-index` writes the stored blobs byte-for-byte (LF stays LF, UTF-8 names
# intact) and needs no external extractor - `git archive` + `tar` mangles the Chinese
# file names on a GBK console. The LFS filters are disabled so that no `git-lfs
# filter-process` is spawned (impossible under a sandbox without named pipes); the
# resulting pointers are resolved from the local LFS store just below.
$prefix = $filesDir.Replace('\', '/') + '/'
GitLines (@(
  '-c', 'filter.lfs.smudge=', '-c', 'filter.lfs.process=', '-c', 'filter.lfs.required=false',
  'checkout-index', '-f', '--prefix', $prefix, '--'
) + $added) | Out-Null

# Resolve Git-LFS pointers straight out of `.git/lfs/objects` (a plain file copy, so no
# helper process is spawned). Our change set has exactly one such file: the 88 KB test blend.
$lfsRoot = Join-Path $Repo '.git\lfs\objects'
$resolved = 0
$unresolved = @()
$scanned = 0
foreach ($file in @(Get-ChildItem $filesDir -Recurse -File)) {
  $scanned++
  if ($file.Length -gt 1KB) { continue }        # a pointer is ~130 bytes; skip real binaries early
  $firstLines = @(Get-Content $file.FullName -TotalCount 3 -ErrorAction SilentlyContinue)
  if ($firstLines.Count -eq 0) { continue }
  if ($firstLines[0].Trim() -notlike 'version https://git-lfs*') { continue }
  $oid = ($firstLines | Where-Object { $_ -like 'oid sha256:*' } | Select-Object -First 1)
  $oid = ($oid -replace '^oid sha256:', '').Trim()
  if (-not $oid) { $unresolved += $file.FullName.Substring($filesDir.Length + 1); continue }
  $object = Join-Path $lfsRoot (Join-Path $oid.Substring(0, 2) (Join-Path $oid.Substring(2, 2) $oid))
  if (Test-Path $object) {
    Copy-Item $object $file.FullName -Force
    $resolved++
  }
  else {
    $unresolved += $file.FullName.Substring($filesDir.Length + 1)
  }
}
Say "scanned $scanned exported file(s) for LFS pointers"
if ($resolved -gt 0) { Say "resolved $resolved Git-LFS pointer(s) from the local LFS store" }
if ($unresolved.Count -gt 0) {
  Say "WARNING: $($unresolved.Count) file(s) stay LFS pointers (object not in the local store):"
  $unresolved | Select-Object -First 5 | ForEach-Object { Say "         $_" }
}

$exported = @(Get-ChildItem $filesDir -Recurse -File)
$exportBytes = ($exported | Measure-Object -Property Length -Sum).Sum
Say ("exported {0} files, {1:N0} KB" -f $exported.Count, ($exportBytes / 1KB))

# Anything LFS-tracked would come out as a pointer; our change set has none, but check.
$lfsTracked = @($exported | Where-Object {
  (Get-Content $_.FullName -TotalCount 1 -ErrorAction SilentlyContinue) -like 'version https://git-lfs*'
})
if ($lfsTracked.Count -gt 0) {
  Say "WARNING: $($lfsTracked.Count) exported file(s) are Git-LFS pointers, not content:"
  $lfsTracked | Select-Object -First 5 | ForEach-Object { Say "         $($_.FullName)" }
}

$removed | Set-Content -Path (Join-Path $Out 'removed.txt') -Encoding utf8

if ($Patches) {
  $patchDir = Join-Path $Out 'patches'
  New-Item -ItemType Directory -Path $patchDir -Force | Out-Null
  GitLines @('format-patch', '--no-signature', '--no-numbered', "$base..$Head", '-o', $patchDir) | Out-Null
  $patches = @(Get-ChildItem $patchDir -File)
  $patchBytes = ($patches | Measure-Object -Property Length -Sum).Sum
  Say ("patches: {0} files, {1:N0} KB" -f $patches.Count, ($patchBytes / 1KB))
  Say '  WARNING: the series replays every commit, including the paths -Exclude keeps out of files/'
}

$apply = @'
<#
Copy the exported files into a Blender source checkout.

    pwsh -File apply.ps1 -Target C:\src\blender          # copy (overwrites those paths)
    pwsh -File apply.ps1 -Target C:\src\blender -DryRun   # show what would change
    pwsh -File apply.ps1 -Target C:\src\blender -Patches  # git apply patches\*.patch instead

`-Patches` needs a checkout that contains the upstream base commit (see README.md) and
only works when this export was generated with `-Patches` (the default leaves the
patch series out and ships `files/` alone).
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Target,
  [switch]$DryRun,
  [switch]$Patches
)
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot

if (-not (Test-Path (Join-Path $Target 'source\blender\blenkernel\BKE_blender_version.h'))) {
  Write-Host "FAIL  '$Target' does not look like a Blender source checkout" -ForegroundColor Red
  exit 1
}

if ($Patches) {
  if (-not (Test-Path (Join-Path $here 'patches'))) {
    Write-Host 'FAIL  this export has no patches\ (generated without -Patches) - use the copy mode' -ForegroundColor Red
    exit 1
  }
  foreach ($patch in (Get-ChildItem (Join-Path $here 'patches') -Filter '*.patch' | Sort-Object Name)) {
    Write-Host "applying $($patch.Name)"
    & git -C $Target apply --3way $patch.FullName
    if ($LASTEXITCODE -ne 0) { Write-Host "FAIL  $($patch.Name)" -ForegroundColor Red; exit 1 }
  }
  Write-Host 'patches applied'
  exit 0
}

$source = Join-Path $here 'files'
foreach ($file in (Get-ChildItem $source -Recurse -File)) {
  $relative = $file.FullName.Substring($source.Length + 1)
  $destination = Join-Path $Target $relative
  if ($DryRun) { Write-Host "would copy $relative" }
  else {
    $directory = Split-Path -Parent $destination
    if (-not (Test-Path $directory)) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }
    Copy-Item $file.FullName $destination -Force
    Write-Host "copied $relative"
  }
}

$removedFile = Join-Path $here 'removed.txt'
if (Test-Path $removedFile) {
  $removed = @(Get-Content $removedFile | Where-Object { $_ -ne '' })
  if ($removed.Count -gt 0) {
    Write-Host ''
    Write-Host 'these paths were deleted upstream of us - remove them by hand if they still exist:'
    $removed | ForEach-Object { Write-Host "  $_" }
  }
}
Write-Host ''
Write-Host 'now build:'
Write-Host '  . tools\pdt\build_dev.ps1 -Target blender -Safe -KillBlender'
Write-Host '  . tools\pdt\run_tests.ps1 -Gui'
'@
$apply | Set-Content -Path (Join-Path $Out 'apply.ps1') -Encoding utf8

# Single-quoted here-string + token replacement: a double-quoted one would eat every
# backtick (PowerShell's escape character), and the README is full of Markdown code spans
# and fences. The previous version shipped them collapsed to single backticks.
# Two files: README.md (English) + README.zh-CN.md, with a language switch at the top of
# each - GitHub renders README.md on the repository page, so that is where the link lives.
$headLine = @(GitLines @('log', '-1', '--format=%h %s', $Head))[0]
$patchesNote = if ($Patches) { ', `patches/` (the same change as a `git format-patch` series)' } else { '' }
$removedNote = if (@($removed).Count -gt 0) { ', `removed.txt` (paths our commits delete)' } else { '' }
$patchesNoteZh = if ($Patches) { '，另附 `patches/`（同一批改动的 `git format-patch` 序列）' } else { '' }
$removedNoteZh = if (@($removed).Count -gt 0) { '，另附 `removed.txt`（我们删掉的路径）' } else { '' }

$readmeEn = @'
**English** | [简体中文](README.zh-CN.md)

# PDT Geometry Nodes for Blender - changes only

> **This is not a Blender source tree.** It holds *only* the files this project adds to
> or changes in Blender, i.e. two geometry nodes, two add-ons and a handful of upstream
> edits. To get a runnable Blender you copy these files onto a real Blender source
> checkout and build it (step by step below).

Built on Blender {{VERSION}}, upstream base commit `{{BASE}}`, export made from `{{HEAD}}`.

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
> `{{BASE}}` (Blender {{VERSION}}). A ZIP of another 5.2.x applies fine - `apply.ps1` is a
> plain file copy - but it also **overwrites** upstream code in the handful of files we
> touch, so a 5.2.0 tree ends up with 5.2.0-minus-a-few-patches. Prefer cloning the
> release branch and checking out that commit. (We also ship
> `BKE_blender_version.h`, which pins the file subversion to 46, and our
> `versioning_520.cc`; applied onto another 5.2.x the build will simply *report*
> {{VERSION}} - that does not make it the tree these tests were run on.)

## Step by step

```powershell
# 0. clone this repository somewhere OUTSIDE the Blender checkout
git clone <this repo> C:\work\pdt-changes
cd C:\work\pdt-changes

# 1. get Blender's source at the base commit (or a compatible 5.2.x) ...
git clone https://projects.blender.org/blender/blender.git C:\work\blender
git -C C:\work\blender checkout {{BASE}}
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

Everything is byte-identical to the file in our checkout, same path as in Blender.{{PATCHES_NOTE}}{{REMOVED_NOTE}}

## License

Blender is GPL-2.0-or-later. The files under `source/` and the add-ons are covered by that
license; publishing them here satisfies the source-availability requirement for builds you
distribute. `tools/pdt/` scripts and documentation follow the same terms.
'@

$readmeZh = @'
[English](README.md) | **简体中文**

# Blender 的 PDT 几何节点（只含改动）

> **这不是一份 Blender 源码**。这里只有本项目**新增或修改**的文件：两个几何节点、两个
> 插件，以及几处对上游代码的改动。想要能跑的 Blender，得把这些文件拷到一份真实的
> Blender 源码上再编译（下面按步骤写）。

基于 Blender {{VERSION}}，上游基点提交 `{{BASE}}`，导出时本方提交 `{{HEAD}}`。

## 这里有什么

| 东西 | 在 Blender 里的位置 | 干什么用的 |
| --- | --- | --- |
| **PDT Selection**（几何节点） | 节点编辑器 `Shift+A` → **PDT** → PDT Selection | 把你写的**选择字符串**（例如 `0-15 ^3 group:arm`）变成布尔场，喂给 Delete Geometry、Set Position 这类节点 |
| **PDT Wrangle**（几何节点） | `Shift+A` → **PDT** → PDT Wrangle | 在节点里写一小段类 VEX 代码（`@P.y += 1;`），按点 / 面 / 样条逐元素执行 |
| **PDT Geometry Selection**（插件） | 自己绑一个快捷键，光标停在节点的字符串输入框上 | 在 3D 视口里点几何体选元素，自动把索引写回那个输入框，不用手敲数字 |
| **PDT Node Info**（插件） | 自己绑一个快捷键，按住它并把光标放在节点上 | Houdini 那种信息浮层：显示流经这个节点的几何 |
| 几处上游改动 | Outliner 拖放、USD 导入器 | 见下面「文档」里的手册 |

除此之外，你的 Blender 和官方版一样。

## 动手之前先确认

* **Windows 10/11 x64**：`tools/pdt/` 里的辅助脚本是 PowerShell。Linux/macOS 上照样能用，
  只是第 2 步的拷贝要手动做（`cp` + 正常的 CMake 构建）。
* **一份 Blender 源码**（上面那个基点提交），**并且带预编译库**（`lib/windows_x64/` 不能是空的，
  见下一节），外加**能编译 Blender 的环境**（VS2022 + CMake）。也就是说：你得先能自己编 Blender。
  不能的话先照
  [Blender 官方构建文档](https://developer.blender.org/docs/handbook/building_blender/) 做一遍，
  本仓库不负责这一步。
* **磁盘与时间**：光预编译库就有约 **6.5 GB**；从零构建又大又慢（几十分钟到几小时，看机器）。
  之后改一个节点文件，增量编译大约 20–30 秒。

## 准备源码：预编译库（这一步最容易漏）

**下载来的 Blender 源码里没有编译需要的那些库。** 如果你拿的是 ZIP（或 GitHub 的
"Download ZIP"），`lib/windows_x64/` 这个目录**在，但是空的**，CMake 会直接拒绝配置。
这些库放在一个单独的、用 git-lfs 存的仓库里
（`https://projects.blender.org/blender/lib-windows_x64.git`，分支 `blender-v5.2-release`），
官方用一个 update 步骤把它们拉下来：

```powershell
# 在 Blender 源码根目录执行。需要 PATH 里有 git、Python、Git LFS。
make update          # Windows 上用根目录的 make.bat update，它转给同一个脚本
```

这条命令会把 `.gitmodules` 里对应平台的 `lib/<平台>_<架构>` 子模块启用并下载下来。
它**很慢**——Blender 自己的脚本提示"网速慢时可能要一个多小时"。

**如果你的源码是 ZIP，`make update` 用不了**——它驱动的是 git 子模块，而 ZIP 里没有 `.git`。
这时改成把库仓库直接 clone 进那个空目录：

```powershell
git lfs install
git clone -b blender-v5.2-release https://projects.blender.org/blender/lib-windows_x64.git C:\work\blender\lib\windows_x64
```

（我们自己在用的 5.2.3-rc 检出是那个仓库的提交 `60d6e96b9`，正好是该分支的 tip；
要完全对齐就 `git -C C:\work\blender\lib\windows_x64 checkout 60d6e96b9`。）

然后接着走下面的构建步骤。这两条路都是 Blender 官方机制，没有本项目专有的东西。

> **用 ZIP 时特别注意版本。** 我们的文件是相对 `{{BASE}}`（Blender {{VERSION}}）这一版写的。
> 别的 5.2.x 的 ZIP 也能用——`apply.ps1` 就是纯文件拷贝——但它会**覆盖**我们改到的那几个
> 文件里的上游代码，于是 5.2.0 的树会变成"5.2.0 减去几个 5.2.1–5.2.3 的修正"。
> 能用 git 就用 git：克隆 release 分支再 `checkout` 到那个提交。
> 另外我们顺带带了 `BKE_blender_version.h`（把文件子版本钉在 46）和我们的
> `versioning_520.cc`，所以套到别的 5.2.x 上，编译出来的版本号会**自报 {{VERSION}}**——
> 这只是版本字符串变了，那棵树仍然不是我们跑过测试的那一棵。

## 一步一步来（可以照抄）

```powershell
# 0. 把这个仓库克隆到 Blender 源码之外的地方
git clone <本仓库地址> C:\work\pdt-changes
cd C:\work\pdt-changes

# 1. 取到基点提交的 Blender 源码（兼容的 5.2.x 也行）……
git clone https://projects.blender.org/blender/blender.git C:\work\blender
git -C C:\work\blender checkout {{BASE}}
#    ……再加上它的预编译库（官方 update 步骤）。Windows 上：
C:\work\blender\make.bat update
#    （Linux/macOS：源码根目录下 `make update`。ZIP 源码没有 git，这步跑不了——
#      改成手工 clone lib\windows_x64，见上面那一节。）

# 2. 把我们的文件拷进去。先跑预览，它只列出会改哪些文件，不动磁盘
pwsh -File apply.ps1 -Target C:\work\blender -DryRun
pwsh -File apply.ps1 -Target C:\work\blender

# 3. 编译。第一次需要一个「已配置好」的构建目录，两条路选一条：
#    (a) 照 Blender 官方说明配置一个，目录名用 build_windows_x64_vc17_Release，
#        放在源码旁边——脚本默认就找这个名字；
#    (b) 或者让这个脚本配一个精简版（关掉 Cycles/USD，只适合开发，别拿去发布）：
pwsh -File C:\work\blender\tools\pdt\configure_geo_fast.ps1 -Build   # 一次性的长任务
#    走 (b) 的话目录是 C:\work\build_geo_fast，脚本猜不到这个名字，
#    每个 PowerShell 窗口里先告诉它一次（否则会报 project not found）：
$env:PDT_BUILD_DIR = 'C:\work\build_geo_fast'
#    ……以后每次改代码只要：
. C:\work\blender\tools\pdt\build_dev.ps1 -Target install

# 4. 跑测试（无头；加 -Gui 才跑真窗口那几项）
. C:\work\blender\tools\pdt\run_tests.ps1
```

成功的标志：`run_tests.ps1` 最后一行是 `== summary: 0 failure(s) ==`，并且编译出来的
`blender.exe` 在几何节点 `Add` 菜单里有一个 **PDT** 分类。

## 怎么用

1. 启动编译出来的 Blender，给任意物体加一个 Geometry Nodes 修改器。
2. 在节点编辑器里 `Shift+A` → **PDT** → 选一个节点。
3. 两个插件要手动开：`编辑 > 偏好设置 > 插件`，勾上 **PDT Geometry Selection** 与
   **PDT Node Info**，然后**自己去 `偏好设置 > 键位映射` 给它们绑键**——它们故意不抢键，
   所以永远不会和你现有的键位打架（那两条条目默认是未绑定的）。
4. 在**官方** Blender 里打开带这些节点的文件，节点会显示成"未定义节点"——这是正常的，
   它们只存在于用本仓库编出来的版本里。

## 出问题了对症下药

| 现象 | 原因 / 怎么办 |
| --- | --- |
| 配置 CMake 时报 `lib/windows_x64` 里的外部库缺失，或者那个目录干脆是空的 | 源码是 ZIP、没带预编译库。见「准备源码：预编译库」：git 检出的跑 `make update`，ZIP 的手工 clone 库仓库 |
| 拉库时 `git lfs` 报错 | 先装 Git LFS（`git lfs install`）；库仓库里的二进制是 LFS 存的 |
| `apply.ps1` 说目标"not a Blender source checkout" | `-Target` 要指到包含 `source\blender\` 的那一层目录 |
| 编译报某个工程/节点类型不存在 | 构建目录是在拷文件**之前**配置的——用 `-Reconfigure` 重新构建 |
| `run_tests.ps1` 警告 `binary hash ... != HEAD` | `blender.exe` 比源码旧，先重编，否则测试结论不可信 |
| 测试报 `key "test_object" not found` | 命令行里 `.blend` 必须写在 `--python` **前面** |
| `run_tests.ps1 -Gui` 没有 PASS | 那几项要真实桌面会话（无桌面的远程/服务会话跑不了） |
| 在开发仓库里 `git commit` 报 `sh.exe` 之类的错 | 那是开发流程的钩子，本仓库（纯文件快照）不需要 |

## 文档

* `tools/pdt/docs/manual/` —— **规范性**用户手册（中文）：选择字符串的完整语法、Wrangle
  语言、拾取器与 Node Info 的行为。`run_tests.ps1` 会逐行执行手册里的例子，所以它和代码
  永远同步。
* `tools/pdt/spec/` —— 手册对应的手写最小规范（每条都有编号，测试直接引用）。
* `tools/pdt/README.md` —— 工具链自身怎么组织的。

设计过程记录、构建耗时实测、本机环境笔记**有意没有带出来**：它们描述的是开发这台机器。

## `files/` 里都是什么

| 路径 | 内容 |
| --- | --- |
| `source/blender/nodes/geometry/nodes/custom/` | 两个节点、Wrangle 引擎、节点清单 |
| `scripts/addons_core/pdt_geo_select/`、`pdt_mmb_info/` | 两个插件 |
| `tools/pdt/tests/` | `run_tests.ps1` 背后的断言 |
| `tools/pdt/spec/`、`tools/pdt/docs/manual/` | 规范与手册 |
| 三个上游文件 | 几何 CMake 列表、节点 RNA 注册、"Add" 菜单项——只改其中标记好的 `PDT` 区块 |
| `tests/files/usd/...` | 两处 USD 导入器改动的参考文件更新 |

每个文件都与我们工作区里的**逐字节相同**，路径也和 Blender 里一致{{PATCHES_NOTE_ZH}}{{REMOVED_NOTE_ZH}}。

## 许可

Blender 是 GPL-2.0-or-later。`source/` 下的文件与两个插件同样适用该许可；把它们发布出来
即满足分发构建产物时的源码可得性要求。`tools/pdt/` 的脚本与文档同许可。
'@

$tokenMap = [ordered]@{
  '{{BASE}}'            = $base
  '{{VERSION}}'         = $baseVersion
  '{{HEAD}}'            = $headLine
  '{{PATCHES_NOTE}}'    = $patchesNote
  '{{REMOVED_NOTE}}'    = $removedNote
  '{{PATCHES_NOTE_ZH}}' = $patchesNoteZh
  '{{REMOVED_NOTE_ZH}}' = $removedNoteZh
}
foreach ($pair in @(@('README.md', $readmeEn), @('README.zh-CN.md', $readmeZh))) {
  $text = $pair[1]
  foreach ($key in $tokenMap.Keys) { $text = $text.Replace($key, $tokenMap[$key]) }
  $text | Set-Content -Path (Join-Path $Out $pair[0]) -Encoding utf8
}

if ($GitInit) {
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail 'git not found' }
  Push-Location $Out
  try {
    git init --initial-branch=main | Out-Null
    git add -A
    # `files/.gitignore` is one of our own changes (it carries the rules for the pdt
    # test reports), and its first pattern is `.*` - which ignores dotfiles, itself
    # included. Force it in: the committed tree has to match what `apply.ps1` copies.
    if (Test-Path (Join-Path $Out 'files\.gitignore')) { git add -f -- 'files/.gitignore' | Out-Null }
    # Self-check: every file we wrote must be in the index (quotepath off, or the Chinese
    # file names come back escaped and look missing).
    $staged = @(git -c core.quotepath=false diff --cached --name-only)
    $missing = @($exported |
        ForEach-Object { 'files/' + $_.FullName.Substring($filesDir.Length + 1).Replace('\', '/') } |
        Where-Object { $staged -notcontains $_ })
    if ($missing.Count -gt 0) {
      Say "WARNING: $($missing.Count) exported file(s) are not in the commit:"
      $missing | Select-Object -First 5 | ForEach-Object { Say "         $_" }
    }
    git -c user.name='PDT Blender' -c user.email='pdt@localhost' commit -q -m "PDT Geometry Nodes: changes on top of Blender $base"
    Say ''
    Say 'git repository created:'
    Say ("  {0}  ({1} files)" -f $Out, (git ls-files | Measure-Object).Count)
    Say '  add a remote and push when you are ready:'
    Say ("    git remote add origin $Remote")
    Say '    git push -u origin main'
    Say '  the artifact is rebuilt from scratch (one new commit, unrelated to the last'
    Say '  export), so when the remote already holds an older export the push is rejected -'
    Say '  replace it instead (the fetch is what makes the lease checkable):'
    Say '    git fetch origin main'
    Say '    git push --force-with-lease origin main'
  }
  finally { Pop-Location }
}

Say ''
Say "output: $Out"
