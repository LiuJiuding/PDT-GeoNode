<#
.SYNOPSIS
  Compile and run the standalone Wrangle engine self-test (no Blender, ~10 s).

.DESCRIPTION
  The engine (`pdt_wrangle_vm.hh`) is header-only and includes no Blender header at all, so its
  checks compile with `cl.exe` alone. That is the fast loop used while changing the language:
  seconds instead of a 26 s incremental build, and no blender.exe needed.

  The toolchain is found here rather than through `vcvars64`: in this sandbox `vcvars64` cannot
  probe the Windows SDK from the registry, so INCLUDE/LIB are assembled by hand from the newest
  MSVC and Windows Kit directories. Object files and the executable go to the *build* directory
  (outside the repo) so a run never leaves anything to commit.

  Exit codes: 0 pass, 1 checks failed, 2 compile failed, 3 no toolchain (callers may skip).

.EXAMPLE
  & tools\pdt\tests\build_engine_selftest.ps1
#>
param(
  [switch]$SkipRun,
  [switch]$ShowToolchain,
  [string]$OutDir = ''
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\pdt_common.ps1"   # $PdtRepo, $PdtBuildDir

function Find-PdtVsRoot {
  $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
  if (Test-Path $vswhere) {
    $found = & $vswhere -latest -products * -property installationPath 2>$null |
      Select-Object -First 1
    if ($found -and (Test-Path $found)) { return $found }
  }
  foreach ($edition in @('Community', 'Professional', 'Enterprise', 'BuildTools')) {
    $guess = Join-Path $env:ProgramFiles "Microsoft Visual Studio\2022\$edition"
    if (Test-Path $guess) { return $guess }
  }
  return ''
}

function Get-PdtNewestDir([string]$Parent, [string]$Filter) {
  if (-not (Test-Path $Parent)) { return '' }
  $hit = Get-ChildItem -Path $Parent -Directory -Filter $Filter -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | Select-Object -First 1
  if ($hit) { return $hit.FullName }
  return ''
}

$vsRoot = Find-PdtVsRoot
$vcTools = if ($vsRoot) { Get-PdtNewestDir (Join-Path $vsRoot 'VC\Tools\MSVC') '14.*' } else { '' }
$sdkRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10'
# NOTE: Get-PdtNewestDir returns a *full path* - do not prepend the parent again. Include and
# Lib are resolved separately, so a Kit whose two halves are different versions still works.
$sdkInclude = Get-PdtNewestDir (Join-Path $sdkRoot 'Include') '10.*'
$sdkLib = Get-PdtNewestDir (Join-Path $sdkRoot 'Lib') '10.*'

if (-not $vcTools -or -not $sdkInclude -or -not $sdkLib) {
  Write-Host 'SKIP engine self-test: no MSVC + Windows SDK toolchain found'
  exit 3
}
$cl = Join-Path $vcTools 'bin\Hostx64\x64\cl.exe'
if (-not (Test-Path $cl)) {
  Write-Host "SKIP engine self-test: cl.exe is not under $vcTools"
  exit 3
}

$outDir = if ($OutDir) { $OutDir } else { Join-Path $PdtBuildDir 'engine_selftest' }
New-Item -ItemType Directory -Path $outDir -Force | Out-Null
$env:INCLUDE = "$vcTools\include;$sdkInclude\ucrt;$sdkInclude\shared;$sdkInclude\um"
$env:LIB = "$vcTools\lib\x64;$sdkLib\ucrt\x64;$sdkLib\um\x64"

if ($ShowToolchain) {
  Write-Host "cl     : $cl"
  Write-Host "INCLUDE: $env:INCLUDE"
  Write-Host "LIB    : $env:LIB"
  Write-Host "out    : $outDir"
  Write-Host ("ucrt header present: {0}" -f (Test-Path (Join-Path $sdkInclude 'ucrt\crtdbg.h')))
  exit 0
}

$source = Join-Path $PSScriptRoot 'wrangle_engine_selftest.cpp'
$engineDir = Join-Path $PdtRepo 'source\blender\nodes\geometry\nodes\custom'
$exe = Join-Path $outDir 'wrangle_engine_selftest.exe'

$sw = [Diagnostics.Stopwatch]::StartNew()
# `/Fo` takes an explicit object *path* here: a directory with a trailing backslash would end
# the argument as `\"`, which escapes the closing quote and mangles every following switch.
# Keeping the .obj out of the source tree matters: a test run must leave nothing to commit.
$obj = Join-Path $outDir 'wrangle_engine_selftest.obj'
$compile = & $cl /nologo /O2 /std:c++17 /EHsc "/I$engineDir" $source "/Fo$obj" "/Fe$exe" 2>&1
$compileExit = $LASTEXITCODE
$sw.Stop()

if ($compileExit -ne 0) {
  $compile | Select-Object -Last 12 | ForEach-Object { Write-Host "      $_" }
  Write-Host "FAIL engine self-test: compile exit=$compileExit"
  exit 2
}
Write-Host ("     compiled in {0:N1} s" -f $sw.Elapsed.TotalSeconds)
if ($SkipRun) { exit 0 }

& $exe
exit $LASTEXITCODE
