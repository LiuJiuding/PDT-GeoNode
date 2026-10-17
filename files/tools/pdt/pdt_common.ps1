# Shared helpers for the PDT Blender tooling.
#
# Every other script starts with:
#     . "$PSScriptRoot\pdt_common.ps1"
#
# It exposes (all derived from the script location, nothing hardcoded):
#   $PdtRepo         Blender source checkout             (<repo>/tools/pdt/../..)
#   $PdtBuildDir     CMake build directory               (sibling of $PdtRepo)
#   $PdtBlenderExe   built blender.exe inside $PdtBuildDir
#   $PdtRecordsDir   build log / manifests (outside the repo)
#   $PdtReferenceExe official Blender used for comparison (optional)
# and the functions Resolve-PdtMSBuild, Resolve-PdtCMake, Invoke-PdtMSBuild.
#
# Overrides through environment variables:
#   PDT_BUILD_DIR, PDT_RECORDS_DIR, PDT_REFERENCE_BLENDER, PDT_BUILD_TYPE

$PdtToolsDir = $PSScriptRoot
$PdtRepo = Split-Path (Split-Path $PdtToolsDir -Parent) -Parent
$PdtBuildType = if ($env:PDT_BUILD_TYPE) { $env:PDT_BUILD_TYPE } else { 'Release' }

function Get-PdtBuildDir {
  if ($env:PDT_BUILD_DIR) { return $env:PDT_BUILD_DIR }
  $parent = Split-Path $PdtRepo -Parent
  $preferred = Join-Path $parent 'build_windows_x64_vc17_Release'
  if (Test-Path (Join-Path $preferred 'CMakeCache.txt')) { return $preferred }
  $candidate = Get-ChildItem -Path $parent -Directory -Filter 'build_windows*' -ErrorAction SilentlyContinue |
    Where-Object { Test-Path (Join-Path $_.FullName 'CMakeCache.txt') } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  if ($candidate) { return $candidate.FullName }
  return $preferred
}

function Get-PdtBlenderExe([string]$buildDir, [string]$buildType) {
  if (-not (Test-Path $buildDir)) { return $null }
  $direct = Join-Path $buildDir "bin\$buildType\blender.exe"
  if (Test-Path $direct) { return $direct }
  $found = Get-ChildItem -Path (Join-Path $buildDir 'bin') -Filter 'blender.exe' -Recurse -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  if ($found) { return $found.FullName }
  return $null
}

$PdtBuildDir = Get-PdtBuildDir
$PdtBlenderExe = Get-PdtBlenderExe $PdtBuildDir $PdtBuildType
$PdtRecordsDir = if ($env:PDT_RECORDS_DIR) { $env:PDT_RECORDS_DIR } else { Join-Path (Split-Path $PdtRepo -Parent) 'builds' }

function Get-PdtReferenceExe {
  if ($env:PDT_REFERENCE_BLENDER) { return $env:PDT_REFERENCE_BLENDER }
  # Best effort: any official build kept around for comparison ("-Official" tests).
  $patterns = @(
    "$env:ProgramFiles\Blender Foundation\Blender*\blender.exe",
    "$env:LOCALAPPDATA\Programs\Blender Foundation\Blender*\blender.exe"
  )
  foreach ($pattern in $patterns) {
    $hit = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue |
      Sort-Object LastWriteTime -Descending |
      Select-Object -First 1
    if ($hit) { return $hit.FullName }
  }
  return ''
}
$PdtReferenceExe = Get-PdtReferenceExe

function Resolve-PdtCMake {
  $cmd = Get-Command cmake -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  foreach ($guess in @("$env:ProgramFiles\CMake\bin\cmake.exe",
                       "$env:LOCALAPPDATA\Programs\CMake\bin\cmake.exe")) {
    if ($guess -and (Test-Path $guess)) { return $guess }
  }
  return $null
}

function Resolve-PdtMSBuild {
  $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
  if (Test-Path $vswhere) {
    $found = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -find 'MSBuild\**\Bin\MSBuild.exe' 2>$null |
      Select-Object -First 1
    if ($found -and (Test-Path $found)) { return $found }
  }
  $cmd = Get-Command msbuild -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  foreach ($edition in @('Community', 'Professional', 'Enterprise', 'BuildTools')) {
    $guess = "$env:ProgramFiles\Microsoft Visual Studio\2022\$edition\MSBuild\Current\Bin\MSBuild.exe"
    if (Test-Path $guess) { return $guess }
  }
  return $null
}

<#
.SYNOPSIS
  Build one MSBuild project and return timing plus a compact summary.
.DESCRIPTION
  Invokes MSBuild.exe directly (no vcvars needed: the VS targets resolve the
  toolchain themselves). Windows PowerShell/.NET must be allowed to create the
  named pipes MSBuild uses for parallel nodes and its file tracker; in a
  restricted sandbox pass -Safe, which disables both.
#>
function Invoke-PdtMSBuild {
  param(
    [Parameter(Mandatory = $true)][string]$Project,
    [int]$Jobs = 0,
    [switch]$Safe,
    [string[]]$ExtraArgs = @(),
    [string]$LogFile = ''
  )
  $msbuild = Resolve-PdtMSBuild
  if (-not $msbuild) { throw 'MSBuild.exe not found (install VS 2022 or add msbuild to PATH)' }
  if (-not (Test-Path $Project)) { throw "project not found: $Project" }

  # NOTE: do not name this array `$args` - that is a reserved automatic variable
  # inside functions and the splat would pass the wrong arguments.
  # NOTE: the `+` concatenation must stay in parentheses: inside @() the comma
  # binds tighter than `+`, which would split the switch into extra arguments
  # (MSBuild then reports MSB1008 "only one project can be specified").
  $msbuildArgs = @(
    $Project,
    ('/property:Configuration=' + $PdtBuildType),
    '/p:Platform=x64',
    '/nologo',
    '/verbosity:minimal'
  )
  if ($Safe) {
    if ($Jobs -eq 0) { $Jobs = 1 }
    $msbuildArgs += @('/maxcpucount:1', '/nodeReuse:false', '/p:TrackFileAccess=false', '/p:CL_MPCount=1')
  }
  elseif ($Jobs -gt 0) {
    $msbuildArgs += "/maxcpucount:$Jobs"
  }
  if ($ExtraArgs.Count -gt 0) { $msbuildArgs += $ExtraArgs }

  $sw = [Diagnostics.Stopwatch]::StartNew()
  $output = & $msbuild @msbuildArgs 2>&1
  $exit = $LASTEXITCODE
  $sw.Stop()

  if ($LogFile) { Add-Content -Path $LogFile -Value (($output | Out-String).Trim()) -Encoding utf8 }

  return [ordered]@{
    exit     = $exit
    seconds  = [math]::Round($sw.Elapsed.TotalSeconds, 1)
    output   = @($output)
    errors   = @($output | Select-String -Pattern ': error ' | ForEach-Object { [string]$_.Line })
    # NOTE: this counts "  <name>.cc" lines, which with `/verbosity:minimal` MSBuild only
    # prints for generated sources. It is **not** the number of translation units rebuilt:
    # measured 2026-09-14, a DNA header edit rebuilt 2475 `.obj` files in 289 s while this
    # counter said 1. Trust the wall time and the `.obj` timestamps, not this number.
    generated_sources = @($output | Select-String -Pattern '\.(cc|cxx|cpp|c)\s*$' | Measure-Object).Count
    projects = @($output | Select-String -Pattern '\.vcxproj ->' | Measure-Object).Count
  }
}

function Write-PdtRecord {
  param(
    [string]$Line,
    [string]$RecordsDir = $PdtRecordsDir,
    [string]$File = 'build-log.tsv'
  )
  if (-not (Test-Path $RecordsDir)) { New-Item -ItemType Directory -Path $RecordsDir | Out-Null }
  Add-Content -Path (Join-Path $RecordsDir $File) -Value $Line -Encoding utf8
}

function Resolve-PdtGit {
  # Same idea as Resolve-PdtCMake: do not assume git is on PATH. A constrained shell (or a
  # sandbox where C:\Windows\System32 is missing from PATH) can still drive the tooling.
  $cmd = Get-Command git -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  foreach ($guess in @("$env:ProgramFiles\Git\cmd\git.exe",
                       "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe")) {
    if ($guess -and (Test-Path $guess)) { return $guess }
  }
  return $null
}

function Get-PdtGitState {
  param([string]$Repo = $PdtRepo)
  # NOTE: do not name a helper `Git` - functions win over git.exe in PowerShell
  # and `git` inside it would resolve back to the function and recurse.
  $git = Resolve-PdtGit
  if (-not $git) {
    return [ordered]@{ branch = ''; short = ''; dirty = -1 }
  }
  $run = { param($a) (& $git -C $Repo @a 2>$null) }
  $dirty = @(& $run @('status', '--porcelain'))
  return [ordered]@{
    branch = [string](& $run @('rev-parse', '--abbrev-ref', 'HEAD'))
    short  = [string](& $run @('rev-parse', '--short=12', 'HEAD'))
    dirty  = $dirty.Count
  }
}
