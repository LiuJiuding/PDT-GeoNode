# Sync the PDT add-ons from the source tree into the build directory.
#
#   . <repo>\tools\pdt\sync_addons.ps1                 # every pdt_* add-on
#   . <repo>\tools\pdt\sync_addons.ps1 -Addon pdt_mmb_info
#   . <repo>\tools\pdt\sync_addons.ps1 -Clean          # also drop target files the source lost
#   . <repo>\tools\pdt\sync_addons.ps1 -Verify         # + load each one in the built Blender
#
# Why this exists: both add-ons are pure Python, so changing them needs no compile - copying
# `scripts/addons_core/<addon>/` into `<build>\bin\<type>\<version>\scripts\addons_core\` is
# exactly what `build_dev.ps1 -Target install` does for them (that is where the tests import
# from). Coping by hand with a single `Copy-Item __init__.py` was fine while the add-on was
# one file; `pdt_mmb_info` is a **package** now (preferences/interface/operators/...), and a
# new module that never reaches the build would make the tests silently run old code.
#
# Paths come from tools\pdt\pdt_common.ps1 (PDT_BUILD_DIR, PDT_BUILD_TYPE, PDT_REPO).
param(
  [string[]]$Addon = @(),
  [switch]$Clean,
  [switch]$NoVerify,
  [switch]$Quiet
)

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\pdt_common.ps1"

function Info($msg) { if (-not $Quiet) { Write-Host $msg } }

# --------------------------------------------------------------- locate both sides
$sourceRoot = Join-Path $PdtRepo 'scripts\addons_core'
if (-not (Test-Path $sourceRoot)) {
  Write-Error "source add-ons not found: $sourceRoot (set PDT_REPO)"
  exit 2
}

# The installed copy lives under a version directory (`...\bin\Release\5.2\scripts\addons_core`),
# whose name follows the Blender version - so it is found, not hardcoded.
$binRoot = Join-Path $PdtBuildDir "bin\$PdtBuildType"
$versionDir = $null
if (Test-Path $binRoot) {
  $versionDir = Get-ChildItem -Path $binRoot -Directory -ErrorAction SilentlyContinue |
    Where-Object { Test-Path (Join-Path $_.FullName 'scripts\addons_core') } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
}
if (-not $versionDir) {
  Write-Error "no built copy of scripts\addons_core under $binRoot - build once (-Target install) or set PDT_BUILD_DIR"
  exit 2
}
$targetRoot = Join-Path $versionDir.FullName 'scripts\addons_core'

if ($Addon.Count -eq 0) {
  $Addon = @(Get-ChildItem -Path $sourceRoot -Directory -Filter 'pdt_*' |
    Sort-Object Name | ForEach-Object { $_.Name })
}
if ($Addon.Count -eq 0) {
  Write-Error "no pdt_* add-on found in $sourceRoot"
  exit 2
}

Info "sync: $($Addon -join ', ')"
Info "  from $sourceRoot"
Info "    to $targetRoot"

# ------------------------------------------------------------------------- copy
$failed = 0
$copiedTotal = 0
foreach ($name in $Addon) {
  $src = Join-Path $sourceRoot $name
  $dst = Join-Path $targetRoot $name
  if (-not (Test-Path $src)) { Write-Error "add-on not in the source tree: $src"; $failed++; continue }

  # A stale __pycache__ is the classic "I changed it but Blender runs the old code" trap:
  # clear every one of them in the target (a package has one per sub-package).
  Get-ChildItem -Path $dst -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
  if (-not (Test-Path $dst)) { New-Item -ItemType Directory -Path $dst | Out-Null }

  Copy-Item -Path (Join-Path $src '*') -Destination $dst -Recurse -Force
  $copied = @(Get-ChildItem -Path $src -Recurse -File -Filter '*.py')

  if ($Clean) {
    $targetFiles = @(Get-ChildItem -Path $dst -Recurse -File -Filter '*.py')
    foreach ($file in $targetFiles) {
      $relative = $file.FullName.Substring($dst.Length).TrimStart('\')
      if (-not (Test-Path (Join-Path $src $relative))) {
        Remove-Item -Force $file.FullName
        Info "  removed (no longer in the source): $name\$relative"
      }
    }
  }

  $copiedTotal += $copied.Count
  Info ("  {0}: {1} file(s) -> {2}" -f $name, $copied.Count, $dst)
  if (-not $Quiet) {
    foreach ($file in $copied) {
      $relative = $file.FullName.Substring($src.Length).TrimStart('\')
      Write-Host ("      {0}" -f $relative)
    }
  }
}

if ($failed -gt 0) { exit 1 }

# ----------------------------------------------------------------------- verify
# Loading the add-on is the only check that catches a *missing module* in the copy: the
# import graph of a package fails all at once, and the tests would otherwise import the
# installed copy and report something that looks like a logic bug.
if (-not $NoVerify) {
  if (-not $PdtBlenderExe) {
    Info 'verify: skipped (no built blender.exe found)'
  }
  else {
    $check = Join-Path $PdtToolsDir 'addon_load_check.py'
    $output = & $PdtBlenderExe -b --factory-startup --python-exit-code 1 --python $check -- @Addon 2>&1
    $exit = $LASTEXITCODE
    $output | Select-String -Pattern '^OK |^FAIL |^== |Traceback|Error' | ForEach-Object { Write-Host $_.Line }
    if ($exit -ne 0) {
      Write-Error "verify failed (exit $exit) - the copied add-on does not load"
      exit $exit
    }
    Info 'verify: every add-on loaded'
  }
}

Info ("sync: ok ({0} python file(s), {1} add-on(s))" -f $copiedTotal, $Addon.Count)
Info 'sync: a Blender that is already running still holds the old modules - use'
Info '      "Reload Scripts" (F3) there, or toggle the add-on off/on, or restart it.'
exit 0
