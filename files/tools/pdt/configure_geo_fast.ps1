# Create (optionally build) a trimmed second build directory for geometry-node work.
#
# The normal build directory is already fast for incremental node work. This
# directory only pays off for COLD builds: it disables the modules that dominate
# a from-scratch build and that geometry nodes never need.
#
#   . <repo>\tools\pdt\configure_geo_fast.ps1              # configure only
#   . <repo>\tools\pdt\configure_geo_fast.ps1 -Build       # configure + full build (long!)
#
# It is deliberately a SEPARATE directory: the resulting binaries are not
# feature-equivalent to the release build, so never ship them.
param(
  [switch]$Build,
  [int]$Jobs = 0,
  [string]$BuildDir = '',
  [switch]$Force
)

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\pdt_common.ps1"

if (-not $BuildDir) { $BuildDir = Join-Path (Split-Path $PdtRepo -Parent) 'build_geo_fast' }
if (-not $Jobs) { $Jobs = 20 }

$cmake = Resolve-PdtCMake
if (-not $cmake) { Write-Error 'cmake not found (install CMake or add it to PATH)'; exit 2 }

if ((Test-Path (Join-Path $BuildDir 'CMakeCache.txt')) -and -not $Force) {
  Write-Host "build dir already configured: $BuildDir (use -Force to reconfigure)"
}
else {
  # Big-ticket modules that geometry nodes do not need.
  $opts = @(
    '-DCMAKE_BUILD_TYPE=Release'
    '-DWITH_CYCLES=OFF'            # largest single win for a cold build
    '-DWITH_USD=OFF'
    '-DWITH_HYDRA=OFF'
    '-DWITH_ALEMBIC=OFF'
    '-DWITH_MATERIALX=OFF'
    '-DWITH_FREESTYLE=OFF'
    '-DWITH_OPENIMAGEDENOISE=OFF'
    '-DWITH_LIBMV=OFF'
    '-DWITH_POTRACE=OFF'
    '-DWITH_DRACO=OFF'
    '-DWITH_LLVM=OFF'
    '-DWITH_XR_OPENXR=OFF'
    '-DWITH_PYTHON=ON'             # required: tests and probes use the Python API
    '-DWITH_PYTHON_INSTALL=ON'
  )
  Write-Host "configuring $BuildDir"
  Write-Host ("options: " + ($opts -join ' '))
  & $cmake -S $PdtRepo -B $BuildDir -G 'Visual Studio 17 2022' -A x64 @opts
  if ($LASTEXITCODE -ne 0) {
    Write-Error "cmake configure failed (exit $LASTEXITCODE). Remove the offending -DWITH_* option and retry."
    exit $LASTEXITCODE
  }
  Write-Host "configured: $BuildDir"
}

if ($Build) {
  Write-Host "building root INSTALL in $BuildDir with $Jobs jobs (this is the long part)"
  $result = Invoke-PdtMSBuild -Project (Join-Path $BuildDir 'INSTALL.vcxproj') -Jobs $Jobs
  Write-Host ("build exit={0} elapsed={1:N1}s" -f $result.exit, $result.seconds)
  $exe = Get-PdtBlenderExe $BuildDir $PdtBuildType
  Write-Host "binary: $exe (feature-trimmed, iteration only)"
}
