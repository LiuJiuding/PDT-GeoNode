# Syntax-only compile check for one source file.
#
#   . tools\pdt\check_compile.ps1                       # checks node_geo_pdt_selection.cc
#   . tools\pdt\check_compile.ps1 -Source <path>        # any source inside the geometry target
#   . tools\pdt\check_compile.ps1 -Source <path> -Project <vcxproj>
#                                                       # e.g. bf_draw / bf_rna: the include
#                                                       # dirs and defines come from that project
#
# Why this exists: the geometry target rebuilds in ~10 minutes on this machine, and
# fixing a typo does not need a full link. This runs cl.exe /Zs (parse only, no code
# generation, no object file) with exactly the flags the generated vcxproj uses, so a
# green run means "the real build will not hit a compile error here".
param(
  [string]$Source = '',
  [string]$Project = '',
  [string]$BuildDir = '',
  [string]$RepoDir = '',
  [switch]$Warnings
)

$ErrorActionPreference = 'Stop'

if (-not $BuildDir) {
  # Derived, never hardcoded: pdt_common finds <repo>/../build_windows*_<config>.
  if (Test-Path (Join-Path $PSScriptRoot 'pdt_common.ps1')) {
    . (Join-Path $PSScriptRoot 'pdt_common.ps1')
    $BuildDir = $PdtBuildDir
  }
  if ($env:PDT_BUILD_DIR) { $BuildDir = $env:PDT_BUILD_DIR }
}
if (-not $RepoDir) {
  $RepoDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
  if ($env:PDT_REPO) { $RepoDir = $env:PDT_REPO }
}
if (-not $Source) {
  $Source = Join-Path $RepoDir 'source\blender\nodes\geometry\nodes\custom\node_geo_pdt_selection.cc'
}
if (-not (Test-Path $Source)) { Write-Error "source not found: $Source"; exit 2 }

$project = if ($Project) { $Project } else { Join-Path $BuildDir 'source\blender\nodes\geometry\bf_nodes_geometry.vcxproj' }
if (-not (Test-Path $project)) { Write-Error "project not found: $project"; exit 2 }

[xml]$xml = Get-Content $project
$ns = @{ m = 'http://schemas.microsoft.com/developer/msbuild/2003' }
$cl = Select-Xml -Xml $xml -XPath '//m:ItemDefinitionGroup/m:ClCompile' -Namespace $ns | Select-Object -First 1
if (-not $cl) { Write-Error 'no ClCompile settings in the project'; exit 2 }
$settings = @{}
foreach ($node in $cl.Node.ChildNodes) { $settings[$node.Name] = $node.'#text' }

# The release config, not the debug one: PDT builds bin\Release\blender.exe.
$isDebug = $settings['RuntimeLibrary'] -like 'MultiThreadedDebugDLL'
$runtime = if ($isDebug) { '/MDd' } else { '/MD' }
$opt = if ($settings['Optimization'] -eq 'Disabled') { '/Od' } else { '/O2' }
$debugInfo = if ($settings['DebugInformationFormat']) { '/Zi' } else { '' }

$includeArgs = @()
foreach ($dir in ($settings['AdditionalIncludeDirectories'] -split ';')) {
  $dir = $dir.Trim()
  if ($dir -and $dir -notlike '*%(*)') { $includeArgs += "/I`"$dir`"" }
}
$defines = @()
foreach ($def in ($settings['PreprocessorDefinitions'] -split ';')) {
  $def = $def.Trim()
  if ($def -and $def -notlike '*%(*)') { $defines += "/D$def" }
}
$externalArgs = @()
# No /experimental:external here on purpose: with it, cl.exe treats the /I directories
# as external and then does *not* find our own headers (measured: BKE_attribute.hh not
# found while the path was on the command line). Warnings are off anyway.

# Activate the MSVC environment (this only sets env vars, no vcvarsall chaining needed).
$vswhere = 'C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe'
$vsRoot = if (Test-Path $vswhere) { & $vswhere -latest -property installationPath } else { '' }
if (-not $vsRoot) { Write-Error 'Visual Studio not found (vswhere)'; exit 2 }
$vcvars = Join-Path $vsRoot 'VC\Auxiliary\Build\vcvars64.bat'
if (-not (Test-Path $vcvars)) { Write-Error "vcvars64.bat not found: $vcvars"; exit 2 }
$comspec = if ($env:ComSpec) { $env:ComSpec } else { 'C:\Windows\System32\cmd.exe' }
$envLines = & $comspec /c "`"$vcvars`" >nul 2>&1 && set"
if (-not ($envLines | Where-Object { $_ -match '^(INCLUDE|LIB)=' })) {
  Write-Error 'vcvars64 produced no INCLUDE/LIB (a VS developer shell may already be active)'
  exit 2
}
foreach ($line in $envLines) {
  if ($line -match '^([^=]+)=(.*)$') { Set-Item -Path "Env:$($matches[1])" -Value $matches[2] }
}
# This Visual Studio install has no Windows SDK component, so vcvars64 only sets the
# MSVC paths (measured: WindowsSDKVersion= empty, no crtdbg.h). The SDK is installed
# separately; add it explicitly instead of depending on the developer prompt.
$sdkRoot = 'C:\Program Files (x86)\Windows Kits\10'
$ucrtVersion = (Get-ChildItem (Join-Path $sdkRoot 'Include') -Directory -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | Select-Object -First 1).Name
if ($ucrtVersion) {
  $env:INCLUDE = (@((Join-Path $sdkRoot "Include\$ucrtVersion\ucrt"),
                    (Join-Path $sdkRoot "Include\$ucrtVersion\um"),
                    (Join-Path $sdkRoot "Include\$ucrtVersion\shared"),
                    (Join-Path $sdkRoot "Include\$ucrtVersion\winrt"),
                    $env:INCLUDE) | Where-Object { $_ }) -join ';'
  $env:LIB = (@((Join-Path $sdkRoot "Lib\$ucrtVersion\ucrt\x64"),
                (Join-Path $sdkRoot "Lib\$ucrtVersion\um\x64"),
                $env:LIB) | Where-Object { $_ }) -join ';'
}
else {
  Write-Error "no Windows SDK include directory under $sdkRoot"
  exit 2
}

$clArgs = @('/nologo', '/Zs', '/c', '/std:c++20', '/EHsc', '/bigobj', '/J', '/utf-8',
            '/Zc:__cplusplus', '/permissive-', '/W0') + `
          ($(if ($Warnings) { @() } else { @('/w') })) + `
          @($runtime, $opt) + $(if ($debugInfo) { @($debugInfo) } else { @() }) + `
          $defines + $includeArgs + @(
            "/I`"$RepoDir\lib\windows_x64\pthreads\include`"",
            "/I`"$RepoDir\lib\windows_x64\tbb\include`"",
            "/I`"$RepoDir\lib\windows_x64\openVDB\include`"",
            "/I`"$RepoDir\lib\windows_x64\OpenImageIO\include`"",
            "/I`"$RepoDir\lib\windows_x64\fmt\include`"",
            "/I`"$RepoDir\lib\windows_x64\vulkan\include`"") + `
          # `/Zi` otherwise drops a 5 MB vc140.pdb into the *current directory* on
          # every check (measured: it kept reappearing in the repo root). Point the
          # program database at the temp directory instead.
          @("/Fd`"$(Join-Path ([IO.Path]::GetTempPath()) 'pdt_check_compile.pdb')`"") + `
          @($Source)

# A response file keeps the ~4 KB of include paths out of PowerShell's argument
# marshalling, which silently mangles quotes (measured: "BKE_attribute.hh not found"
# while the path was visibly on the command line).
$rspPath = Join-Path ([IO.Path]::GetTempPath()) 'pdt_check_compile.rsp'
Set-Content -Path $rspPath -Value $clArgs -Encoding ascii

Write-Host "check: $Source"
$sw = [Diagnostics.Stopwatch]::StartNew()
$output = & cl.exe "@$rspPath" 2>&1
$code = $LASTEXITCODE
$sw.Stop()
$output | Where-Object { $_ -match 'error|Error' } | Select-Object -First 40 | ForEach-Object { Write-Host $_ }
Write-Host ("check: exit={0} seconds={1:N1}" -f $code, $sw.Elapsed.TotalSeconds)
exit $code

