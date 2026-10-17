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
