# Fast geometry-nodes development build.
#
#   . <repo>\tools\pdt\build_dev.ps1                       # compile + link blender.exe
#   . <repo>\tools\pdt\build_dev.ps1 -Target geo            # only bf_nodes_geometry (fastest check)
#   . <repo>\tools\pdt\build_dev.ps1 -Target install        # full cycle incl. datafiles/Python scripts
#   . <repo>\tools\pdt\build_dev.ps1 -Reconfigure           # re-run cmake first (after adding files)
#   . <repo>\tools\pdt\build_dev.ps1 -Safe                  # serial mode for restricted sandboxes
#   . <repo>\tools\pdt\build_dev.ps1 -Verify                # build, then run the Python-API node probe
#
# Paths come from tools\pdt\pdt_common.ps1 (script-relative, env-overridable):
#   PDT_BUILD_DIR, PDT_RECORDS_DIR, PDT_BUILD_TYPE
param(
  [ValidateSet('geo', 'blender', 'install')] [string]$Target = 'blender',
  [int]$Jobs = 0,
  [switch]$Reconfigure,
  [switch]$Safe,
  [switch]$Verify,
  [switch]$KillBlender,
  [switch]$Quiet
)

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\pdt_common.ps1"

function Info($msg) { if (-not $Quiet) { Write-Host $msg } }

$targets = @{
  geo     = Join-Path $PdtBuildDir 'source\blender\nodes\geometry\bf_nodes_geometry.vcxproj'
  blender = Join-Path $PdtBuildDir 'source\creator\blender.vcxproj'
  install = Join-Path $PdtBuildDir 'INSTALL.vcxproj'
}
$project = $targets[$Target]
if (-not (Test-Path $project)) {
  Write-Error "project not found: $project`n(is the build directory configured? set PDT_BUILD_DIR)"
  exit 2
}

# ---------------------------------------------------------------- reconfigure
$cmakeStamp = Join-Path $PdtBuildDir '.pdt-cmake-stamp'
$sourceList = Join-Path $PdtRepo 'source\blender\nodes\geometry\CMakeLists.txt'
$needsReconfigure = [bool]$Reconfigure
if (-not $needsReconfigure) {
  if (-not (Test-Path $cmakeStamp)) { $needsReconfigure = $true }
  elseif ((Get-Item $sourceList).LastWriteTime -gt (Get-Item $cmakeStamp).LastWriteTime) { $needsReconfigure = $true }
}
if ($needsReconfigure) {
  $cmake = Resolve-PdtCMake
  if (-not $cmake) { Write-Error 'cmake not found (install CMake or add it to PATH)'; exit 2 }
  Info "cmake: reconfiguring $PdtBuildDir"
  $sw = [Diagnostics.Stopwatch]::StartNew()
  Push-Location $PdtBuildDir
  $cmakeOut = & $cmake . 2>&1
  $cmakeExit = $LASTEXITCODE
  Pop-Location
  $sw.Stop()
  if ($cmakeExit -ne 0) {
    $cmakeOut | Select-String -Pattern 'error|Error|错误' | Select-Object -First 20 | ForEach-Object { Write-Host $_.Line }
    Write-Error "cmake failed (exit $cmakeExit)"
    exit $cmakeExit
  }
  Set-Content -Path $cmakeStamp -Value (Get-Date -Format 's') -Encoding ascii
  Info ("cmake: ok in {0:N1}s" -f $sw.Elapsed.TotalSeconds)
}

# ------------------------------------------------------------- preflight: locks
# A running blender.exe from the build directory makes the link fail with a
# confusing "LNK1104: cannot open file ...blender.exe". Report it clearly.
$running = @()
foreach ($process in (Get-Process -Name blender -ErrorAction SilentlyContinue)) {
  $path = ''
  try { $path = $process.Path } catch { $path = '' }
  if ($path -and $path.StartsWith($PdtBuildDir, [System.StringComparison]::OrdinalIgnoreCase)) {
    $running += $process
  }
}
if ($running.Count -gt 0) {
  $ids = ($running | ForEach-Object { $_.Id }) -join ', '
  if ($KillBlender) {
    Info "killing running blender.exe from this build dir: $ids"
    $running | Stop-Process -Force
    Start-Sleep -Milliseconds 500
  }
  else {
    Write-Error "blender.exe from this build directory is running (PID $ids) and locks the output.`nClose it, or re-run with -KillBlender."
    exit 3
  }
}

# --------------------------------------------------------------------- build
$git = Get-PdtGitState
$dirty = if ($git.dirty -gt 0) { "dirty($($git.dirty))" } else { 'clean' }
Info "build: target=$Target jobs=$(if ($Jobs -eq 0) { if ($Safe) { 1 } else { 'all' } } else { $Jobs }) safe=$([bool]$Safe) git=$($git.short) $dirty"

$result = Invoke-PdtMSBuild -Project $project -Jobs $Jobs -Safe:$Safe
$result.errors | Select-Object -First 25 | ForEach-Object { Write-Host $_ }
Info ("build: exit={0} projects_built={1} generated_sources={2} seconds={3}" -f `
    $result.exit, $result.projects, $result.generated_sources, $result.seconds)

Write-PdtRecord -Line ("{0}`t{1}`t{2}`t{3}`t{4}`t{5}`t{6}`t{7}" -f `
    (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Target, $Jobs, $result.exit, $result.seconds, `
    $result.projects, $git.short, $dirty)

if ($result.exit -ne 0) {
  Write-Error "build failed (exit $($result.exit)); record log: $PdtRecordsDir"
  exit $result.exit
}

if ($Verify) {
  Info 'verify: Python API probe'
  & (Join-Path $PdtToolsDir 'run_tests.ps1') -SkipFunctional
  exit $LASTEXITCODE
}

exit 0
