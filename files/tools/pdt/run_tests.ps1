# Test the custom geometry nodes of this build.
#
#   . <repo>\tools\pdt\run_tests.ps1                     # Python API + functional tests
#   . <repo>\tools\pdt\run_tests.ps1 -Official           # same probe against the reference build
#   . <repo>\tools\pdt\run_tests.ps1 -SkipFunctional     # API only (fast)
#   . <repo>\tools\pdt\run_tests.ps1 -UpdateBaseline     # re-bake expected geometry
#   . <repo>\tools\pdt\run_tests.ps1 -Gui                # also run the GUI modal test (needs a desktop)
#
# Reference build for -Official: -ReferenceExe or env PDT_REFERENCE_BLENDER.
param(
  [string[]]$NodeIds = @(),
  [switch]$Official,
  [switch]$SkipFunctional,
  [switch]$UpdateBaseline,
  [switch]$Gui,
  [switch]$Verbose,
  [string]$ReferenceExe = ''
)

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\pdt_common.ps1"

$Manifest = Join-Path $PdtRepo 'source\blender\nodes\geometry\nodes\custom\nodes.json'
$Probe = Join-Path $PdtToolsDir 'probe_nodes.py'

$ref = if ($ReferenceExe) { $ReferenceExe } else { $PdtReferenceExe }
$exe = if ($Official) { $ref } else { $PdtBlenderExe }
if (-not $exe) {
  if ($Official) {
    Write-Error 'no reference build given; pass -ReferenceExe <blender.exe> or set PDT_REFERENCE_BLENDER'
  }
  else {
    Write-Error "built blender.exe not found under $PdtBuildDir (build first, or set PDT_BUILD_DIR)"
  }
  exit 2
}
if (-not (Test-Path $Manifest)) { Write-Error "manifest not found: $Manifest"; exit 2 }

# The Python tests import the add-ons from the *installed* copy inside the build directory,
# so a source edit that was never copied would be tested as if it were not there (or worse,
# the previous version would pass). Sync first - it is a file copy, and it also loads each
# add-on once, which turns "a module of the package never made it" into an immediate error
# instead of a mystery test failure. `-Official` compares against the reference build, so it
# must not touch our build directory.
if (-not $Official) {
  & (Join-Path $PdtToolsDir 'sync_addons.ps1') -Quiet
  if ($LASTEXITCODE -ne 0) { Write-Error 'sync_addons.ps1 failed - the add-ons do not load'; exit $LASTEXITCODE }
}

$manifest = Get-Content $Manifest -Raw | ConvertFrom-Json
$nodes = @($manifest.nodes)
if ($NodeIds.Count -gt 0) { $nodes = @($nodes | Where-Object { $NodeIds -contains $_.id }) }

$checkIds = @($nodes | ForEach-Object { [string]$_.id })
$evalIds = @($nodes | Where-Object { $_.eval_node } | ForEach-Object { [string]$_.id })

Write-Host '== Python API probe =='
Write-Host "build:   $exe"
Write-Host "check:   $($checkIds -join ', ')"
Write-Host "evaluate:$($evalIds -join ', ')"

$probeArgs = @('-b', '--factory-startup', '--python', $Probe, '--', '--check')
$probeArgs += $checkIds
if ($evalIds.Count -gt 0) { $probeArgs += @('--eval') + $evalIds }
$probeArgs += @('--list', 'GeometryNode')

$output = & $exe @probeArgs 2>&1
$jsonLine = $output | Select-String -Pattern 'PROBE_JSON:' | Select-Object -First 1
if (-not $jsonLine) {
  Write-Host 'probe produced no result. Raw tail:'
  $output | Select-Object -Last 25 | ForEach-Object { Write-Host "  $_" }
  exit 3
}
# Blender's banner can end up on the same line as the JSON payload, so cut both ends.
$raw = [string]$jsonLine.Line
$raw = $raw.Substring($raw.IndexOf('PROBE_JSON:') + 'PROBE_JSON:'.Length)
$raw = $raw.Substring(0, $raw.LastIndexOf('}') + 1)
$report = $raw | ConvertFrom-Json
$failures = 0

Write-Host ("build version : {0}  branch={1}  hash={2}" -f $report.build.version_string, $report.build.build_branch, $report.build.build_hash)

if ($Official) {
  # The reference build must NOT have our nodes - that is what makes the test meaningful.
  $leaked = @($report.checks | Where-Object { $_.ok })
  if ($leaked.Count -gt 0) {
    Write-Host ("FAIL reference build unexpectedly exposes: {0}" -f (($leaked | ForEach-Object { $_.node_type }) -join ', '))
    $failures++
  }
  else {
    Write-Host 'OK   reference build does not expose the custom nodes (expected)'
  }
}
else {
  # Guard against silently testing a stale or foreign binary.
  $head = [string](& git -C $PdtRepo rev-parse --short=12 HEAD 2>$null)
  if ($report.build.build_hash -ne $head) {
    Write-Host ("WARN binary hash {0} != HEAD {1} - rebuild before trusting results" -f $report.build.build_hash, $head)
  }

  foreach ($check in $report.checks) {
    if ($check.ok) {
      Write-Host ("OK   {0}  ({1} in / {2} out)  ui='{3}'" -f $check.node_type, $check.inputs.Count, $check.outputs.Count, $check.ui_name)
      if ($Verbose) {
        $check.inputs | ForEach-Object { Write-Host ("       in  {0} : {1}" -f $_.name, $_.type) }
        $check.outputs | ForEach-Object { Write-Host ("       out {0} : {1}" -f $_.name, $_.type) }
      }
    }
    else {
      Write-Host ("FAIL {0}: {1}" -f $check.node_type, ($check.errors -join '; '))
      $failures++
    }
  }

  foreach ($eval in $report.evals) {
    if ($eval.skipped) { Write-Host ("SKIP {0} (no geometry output)" -f $eval.node_type); continue }
    if ($eval.evaluated -and -not $eval.errors) {
      $via = if ($eval.converted_points) { ' (via Points to Vertices)' } else { '' }
      Write-Host ("OK   {0} evaluated: {1} verts, {2} edges, {3} polys{4}" -f $eval.node_type, $eval.vertices, $eval.edges, $eval.polygons, $via)
    }
    else {
      Write-Host ("FAIL {0} evaluation: {1}" -f $eval.node_type, ($eval.errors -join '; '))
      $failures++
    }
  }

  # The Add menu is Python-side wiring: make sure the PDT category is registered.
  $menuNodes = @($nodes | Where-Object { $_.menu })
  if ($menuNodes.Count -gt 0) {
    $expr = "import bpy; ms=sorted(n for n in dir(bpy.types) if n.startswith('NODE_MT_geometry_node_') and 'PDT' in n.upper()); print('PDT_MENUS:' + ','.join(ms))"
    $menuOut = & $exe -b --factory-startup --python-expr $expr 2>&1
    $menuLine = $menuOut | Select-String -Pattern 'PDT_MENUS:' | Select-Object -First 1
    $menus = if ($menuLine) { ([string]$menuLine.Line).Substring(([string]$menuLine.Line).IndexOf('PDT_MENUS:') + 10).Trim() } else { '' }
    if ($menus) { Write-Host ("OK   add-menu registered: {0}" -f $menus) }
    else { Write-Host 'FAIL no PDT add-menu class is registered (run pdt_nodes.py sync + build -Target install)'; $failures++ }
  }
}

# --------------------------------------------------- node assertion scripts
if (-not $SkipFunctional) {
  $assertions = @($nodes | Where-Object { $_.assert_script })
  if ($assertions.Count -gt 0) {
    Write-Host ''
    Write-Host '== node assertion scripts =='
    foreach ($node in $assertions) {
      $script = Join-Path $PdtToolsDir "tests\$($node.assert_script)"
      if (-not (Test-Path $script)) { Write-Host ("FAIL {0}: missing {1}" -f $node.id, $script); $failures++; continue }
      $out = & $exe -b --factory-startup --python-exit-code 1 --python $script 2>&1
      $code = $LASTEXITCODE
      if ($code -ne 0) { $failures++ }
      Write-Host ("{0} {1}  ({2})" -f $(if ($code -eq 0) { 'OK  ' } else { 'FAIL' }), $node.assert_script, $node.id)
      $out | Select-String -Pattern '^(OK|FAIL|==)' | ForEach-Object { Write-Host "      $($_.Line)" }
    }
  }

  # Tool-level tests (operator data paths, tooling) not tied to a single node.
  $nodeScripts = @($assertions | ForEach-Object { $_.assert_script })
  $toolScripts = @(Get-ChildItem -Path (Join-Path $PdtToolsDir 'tests') -Filter 'test_*.py' -ErrorAction SilentlyContinue |
      Where-Object { $nodeScripts -notcontains $_.Name } | Sort-Object Name)
  if ($toolScripts.Count -gt 0) {
    Write-Host ''
    Write-Host '== tool tests =='
    foreach ($script in $toolScripts) {
      $out = & $exe -b --factory-startup --python-exit-code 1 --python $script.FullName 2>&1
      $code = $LASTEXITCODE
      if ($code -ne 0) { $failures++ }
      Write-Host ("{0} {1}" -f $(if ($code -eq 0) { 'OK  ' } else { 'FAIL' }), $script.Name)
      $out | Select-String -Pattern '^(OK|FAIL|==)' | ForEach-Object { Write-Host "      $($_.Line)" }
    }
  }
}

# ------------------------------------------------------------ functional tests
if (-not $SkipFunctional) {
  Write-Host ''
  Write-Host '== functional geometry-node tests =='
  $runner = Join-Path $PdtRepo 'tests\python\geo_node_test.py'
  $functional = @($nodes | Where-Object { $_.test })
  if ($functional.Count -eq 0) { Write-Host '(no test blend declared in the manifest)' }
  foreach ($node in $functional) {
    $blend = Join-Path $PdtRepo "tests\files\modeling\geometry_nodes\$($node.test)"
    if (-not (Test-Path $blend)) { Write-Host ("FAIL {0}: missing {1}" -f $node.id, $blend); $failures++; continue }
    if ($UpdateBaseline) { $env:BLENDER_TEST_UPDATE = '1' } else { Remove-Item Env:\BLENDER_TEST_UPDATE -ErrorAction SilentlyContinue }
    # Same order/flags as the generated ctest entries: params, then the .blend, then --python.
    $out = & $exe -b --factory-startup --debug-exit-on-error --python-exit-code 1 $blend --python $runner 2>&1
    $code = $LASTEXITCODE
    $verdict = if ($code -eq 0) { 'OK  ' } else { 'FAIL' }
    if ($code -ne 0) { $failures++ }
    Write-Host ("{0} {1}  ({2})" -f $verdict, $node.test, $node.id)
    if ($code -ne 0 -or $Verbose) {
      $out | Select-String -Pattern 'Results:|Success|Failed|Error|Traceback' | Select-Object -First 10 | ForEach-Object { Write-Host "      $($_.Line)" }
    }
  }
}

# ------------------------------------------------------------------- GUI test
# The modal picker's key handling cannot be driven in background mode; Blender's
# event simulation plus a real window can. Needs a desktop session, so opt-in.
if ($Gui) {
  Write-Host ''
  Write-Host '== GUI modal test (real window + event injection) =='
  $guiScript = Join-Path $PdtToolsDir 'tests\gui_pick_modal.py'
  if ($Official) {
    Write-Host 'SKIP the GUI test exercises our add-on, not the reference build'
  }
  elseif (-not (Test-Path $guiScript)) {
    Write-Host "FAIL missing $guiScript"
    $failures++
  }
  else {
    # Deliberately **without** -FactoryStartup: `spec/selection_pick.md` 验收 requires the
    # user's own configuration, and that is where the socket-level hit test is interesting
    # (UI scale != 1 changes the coordinate spaces it has to agree on).
    $guiOut = & $exe --enable-event-simulate --python $guiScript 2>&1
    $verdict = $guiOut | Select-String -Pattern 'GUI_TEST_RESULT:' | Select-Object -First 1
    $guiOut | Select-String -Pattern '^(FAIL|  failed)' | ForEach-Object { Write-Host "      $($_.Line)" }
    if ($verdict -and ([string]$verdict.Line) -match 'PASS') {
      Write-Host ("OK   gui_pick_modal.py  ({0})" -f ([string]$verdict.Line).Trim())
    }
    else {
      Write-Host 'FAIL gui_pick_modal.py (no PASS verdict; is a desktop session available?)'
      $guiOut | Select-Object -Last 12 | ForEach-Object { Write-Host "      $_" }
      $failures++
    }
  }

  # The MMB node-info modal has the same constraint (a keymap press and a draw
  # callback cannot be exercised headless), so it gets its own real-window run - and
  # deliberately **without** -FactoryStartup: `spec/node_info.md` 验收 requires the user's
  # own configuration, because that is where the bugs the user hits live (the UI-scale
  # coordinate bug was invisible under the factory profile).
  $mmbGui = Join-Path $PdtToolsDir 'tests\gui_mmb_node_info.py'
  if ($Official) {
    Write-Host 'SKIP the MMB GUI test exercises our add-on, not the reference build'
  }
  elseif (-not (Test-Path $mmbGui)) {
    Write-Host "FAIL missing $mmbGui"
    $failures++
  }
  else {
    $mmbOut = & $exe --enable-event-simulate --python $mmbGui 2>&1
    $mmbVerdict = $mmbOut | Select-String -Pattern 'GUI_TEST_RESULT:' | Select-Object -First 1
    $mmbSummary = $mmbOut | Select-String -Pattern '^== gui_mmb_node_info:' | Select-Object -First 1
    $mmbOut | Select-String -Pattern '^(FAIL|  failed)' | ForEach-Object { Write-Host "      $($_.Line)" }
    if ($mmbVerdict -and ([string]$mmbVerdict.Line) -match 'PASS') {
      Write-Host ("OK   gui_mmb_node_info.py  ({0})" -f ([string]$mmbSummary.Line).Trim())
    }
    else {
      Write-Host 'FAIL gui_mmb_node_info.py (no PASS verdict; is a desktop session available?)'
      $mmbOut | Select-Object -Last 12 | ForEach-Object { Write-Host "      $_" }
      $failures++
    }
  }

  # EVO-1..EVO-6 (the enhanced viewer overlay) are drawn by the C++ overlay engine, so their
  # acceptance criteria are counted from screenshots of a real window. See
  # docs/manual/operators/viewer_overlay.md.
  $overlayGui = Join-Path $PdtToolsDir 'tests\gui_viewer_overlay.py'
  if ($Official) {
    Write-Host 'SKIP the viewer overlay GUI test exercises our overlay, not the reference build'
  }
  elseif (-not (Test-Path $overlayGui)) {
    Write-Host "FAIL missing $overlayGui"
    $failures++
  }
  else {
    $overlayOut = & $exe --enable-event-simulate --factory-startup --python $overlayGui 2>&1
    $overlayVerdict = $overlayOut | Select-String -Pattern 'GUI_TEST_RESULT:' | Select-Object -First 1
    $overlaySummary = $overlayOut | Select-String -Pattern '^== gui_viewer_overlay:' | Select-Object -First 1
    $overlayOut | Select-String -Pattern '^(FAIL|  failed)' | ForEach-Object { Write-Host "      $($_.Line)" }
    if ($overlayVerdict -and ([string]$overlayVerdict.Line) -match 'PASS') {
      Write-Host ("OK   gui_viewer_overlay.py  ({0})" -f ([string]$overlaySummary.Line).Trim())
    }
    else {
      Write-Host 'FAIL gui_viewer_overlay.py (no PASS verdict; is a desktop session available?)'
      $overlayOut | Select-Object -Last 15 | ForEach-Object { Write-Host "      $_" }
      $failures++
    }
  }

  # BRAND-5: the build brand is appended to the *window title*, which is built by C++ and handed
  # to the window manager (GHOST), so it can only be read back from a real window. Deliberately
  # without `--factory-startup` on the harness side, like the other GUI acceptance runs. See
  # docs/manual/operators/build_branding.md.
  $brandGui = Join-Path $PdtToolsDir 'tests\gui_branding_title.py'
  if ($Official) {
    Write-Host 'SKIP the branding GUI test is about this fork, not the reference build'
  }
  elseif (-not (Test-Path $brandGui)) {
    Write-Host "FAIL missing $brandGui"
    $failures++
  }
  else {
    $brandOut = & $exe --python $brandGui 2>&1
    $brandVerdict = $brandOut | Select-String -Pattern 'GUI_TEST_RESULT:' | Select-Object -First 1
    $brandSummary = $brandOut | Select-String -Pattern '^== gui_branding_title:' | Select-Object -First 1
    $brandOut | Select-String -Pattern '^(FAIL|  failed)' | ForEach-Object { Write-Host "      $($_.Line)" }
    if ($brandVerdict -and ([string]$brandVerdict.Line) -match 'PASS') {
      Write-Host ("OK   gui_branding_title.py  ({0})" -f ([string]$brandSummary.Line).Trim())
    }
    else {
      Write-Host 'FAIL gui_branding_title.py (no PASS verdict; is a desktop session available?)'
      $brandOut | Select-Object -Last 15 | ForEach-Object { Write-Host "      $_" }
      $failures++
    }
  }
}

# ------------------------------------------------- engine self-test (standalone, no Blender)
# The Wrangle engine (`pdt_wrangle_vm.hh`) includes no Blender header, so its checks compile
# with cl.exe alone in seconds; that is the fast loop used while changing the language, and the
# other half of that node's acceptance list. A machine without a C++ toolchain reports SKIP,
# not a failure: that is a missing tool, not a code defect.
$engineTest = Join-Path $PdtToolsDir 'tests\build_engine_selftest.ps1'
if (Test-Path $engineTest) {
  Write-Host ''
  Write-Host '== engine self-test (standalone) =='
  $engineOut = & $engineTest 2>&1
  $engineCode = $LASTEXITCODE
  if ($engineCode -eq 3) {
    Write-Host 'SKIP no MSVC + Windows SDK toolchain on this machine'
  }
  elseif ($engineCode -ne 0) {
    $failures++
    $engineOut | Select-Object -Last 12 | ForEach-Object { Write-Host "      $_" }
    Write-Host 'FAIL engine self-test'
  }
  else {
    Write-Host 'OK   engine self-test'
    $engineOut | Select-String -Pattern '^==' | ForEach-Object { Write-Host "      $($_.Line)" }
  }
}

# ------------------------------------------------------- L0 spec coverage (advisory)
# `tools/pdt/spec/` holds the user's hand-written minimal specs; this only reports whether
# the detail layer (`docs/manual/`) and the tests have kept up with them. It never fails the
# run: the user edits L0 on their own schedule, and a fresh decision is not a broken build.
$specTool = Join-Path $PdtToolsDir 'pdt_spec.py'
if ((Test-Path $specTool) -and (Get-Command python -ErrorAction SilentlyContinue)) {
  Write-Host ''
  Write-Host '== L0 spec coverage (advisory) =='
  & python $specTool check 2>&1 | ForEach-Object { Write-Host "     $_" }
}

Write-Host ''
Write-Host ("== summary: {0} failure(s) ==" -f $failures)
exit ($(if ($failures -gt 0) { 1 } else { 0 }))
