param(
  [string]$HostPath=(Join-Path $PSScriptRoot '../.cache/rust-target/debug/ming-app-host.exe'),
  [string]$OutputDir=(Join-Path $PSScriptRoot '../artifacts/typography-final'),
  [switch]$Native
)
$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$nativeHost=[IO.Path]::GetFullPath($HostPath)
$evidenceDir=[IO.Path]::GetFullPath($OutputDir)
if (!(Test-Path -LiteralPath $nativeHost -PathType Leaf)) { throw 'Build all features before running acceptance' }
if (Test-Path -LiteralPath $evidenceDir) { throw 'Evidence directory must be fresh; choose a new -OutputDir' }
New-Item -ItemType Directory -Path $evidenceDir | Out-Null
$hostHash=(Get-FileHash -Algorithm SHA256 -LiteralPath $nativeHost).Hash.ToLowerInvariant()
$observations=@()
@{status='running';hostSha256=$hostHash;recordedAt=(Get-Date).ToUniversalTime().ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidenceDir 'acceptance.json')
function Invoke-TextApp([string]$Name,[string[]]$Arguments) {
  $prefix=Join-Path $evidenceDir $Name
  $text=(& $nativeHost @Arguments 2> ($prefix+'.stderr.txt')) -join "`n"
  if ($LASTEXITCODE -ne 0) { throw ('Application failed: '+$prefix+'.stderr.txt') }
  $text | Set-Content -LiteralPath ($prefix+'.json')
  $run=$text | ConvertFrom-Json
  if ($run.appId -ne 'dev.ming.text-demo' -or $run.runtime -ne 'rust-wasmtime' -or $run.exitCode -ne 0 -or $run.framesSubmitted -lt 1) { throw 'Invalid SDK3 outcome' }
  if (@($run.events | Where-Object {$_.code -ge 50 -and $_.code -le 54}).Count -ne 0) { throw 'SDK3 application logged an error' }
  if (@($run.events | Where-Object {$_.code -eq 40 -and $_.value -eq $run.framesSubmitted}).Count -ne 1) { throw 'SDK3 completion evidence missing' }
  if ($null -eq $run.frame.textPrepareMicros -or $run.frame.textPrepareMicros -gt $run.frame.renderMicros) { throw 'Invalid text preparation timing' }
  if ((Get-FileHash -Algorithm SHA256 -LiteralPath $nativeHost).Hash.ToLowerInvariant() -ne $hostHash) { throw 'Host binary changed during acceptance' }
  return $run
}
Push-Location -LiteralPath $taskRoot
try {
  $cases=@(
    @{name='initial';events='[[0,0,0,0]]';frames=1;readback=3344000;width=1100;height=760},
    @{name='repeat';events='[[1,1100,760,0],[7,0,0,0],[0,0,0,0]]';frames=3;readback=10032000;width=1100;height=760},
    @{name='resize';events='[[1,700,500,0],[0,0,0,0]]';frames=2;readback=4744000;width=700;height=500},
    @{name='move';events='[[1,700,500,0],[2,160,0,0],[3,-24,-30,0],[0,0,0,0]]';frames=4;readback=7544000;width=700;height=500}
  )
  foreach ($case in $cases) {
    $eventsPath=Join-Path $evidenceDir ($case.name+'-events.json')
    $case.events | Set-Content -LiteralPath $eventsPath
    $pair=@()
    foreach ($renderer in @('cpu','gpu')) {
      $name=$case.name+'-'+$renderer
      $snapshot=Join-Path $evidenceDir ($name+'.png')
      $run=Invoke-TextApp $name @('run','artifacts/text-demo/app.json','--renderer',$renderer,'--timeout-ms','60000','--fuel','1000000000','--events',$eventsPath,'--snapshot',$snapshot)
      if ($run.windowSystem -ne 'headless' -or $run.framesPresented -ne 0 -or $run.framesSubmitted -ne $case.frames -or $run.frame.width -ne $case.width -or $run.frame.height -ne $case.height) { throw 'Invalid headless execution' }
      if ($renderer -eq 'gpu') {
        if ($run.gpu.deviceType -notin @('DiscreteGpu','IntegratedGpu') -or $run.frame.backend -ne 'wgpu-compute-readback') { throw 'Hardware GPU required' }
        if ($run.gpuStats.imageUploads -ne 0 -or $run.gpuStats.glyphUploads -lt 1 -or $run.gpuStats.glyphResidentBytes -gt 4194304) { throw 'Invalid glyph residency/uploads' }
        if ($run.gpuStats.readbackBytes -ne $case.readback) { throw 'Incorrect headless readback accounting' }
      } elseif ($run.frame.backend -ne 'cpu-reference') { throw 'CPU reference required' }
      $hash=(Get-FileHash -Algorithm SHA256 -LiteralPath $snapshot).Hash.ToLowerInvariant()
      $pair+=$hash
      $observations+=@{case=$case.name;renderer=$renderer;outcome=($name+'.json');snapshot=($name+'.png');pngSha256=$hash;glyphStats=$run.gpuStats;textPrepareMicros=$run.frame.textPrepareMicros;renderMicros=$run.frame.renderMicros;adapter=$run.gpu}
    }
    if ($pair[0] -ne $pair[1]) { throw ('CPU/GPU exact PNG mismatch: '+$case.name) }
  }
  foreach ($renderer in @('cpu','gpu')) {
    $resized=@($observations | Where-Object {$_.case -eq 'resize' -and $_.renderer -eq $renderer})[0]
    $moved=@($observations | Where-Object {$_.case -eq 'move' -and $_.renderer -eq $renderer})[0]
    if ($resized.pngSha256 -eq $moved.pngSha256) { throw 'Scroll/drag did not change same-size output pixels' }
  }
  $initial=@($observations | Where-Object {$_.case -eq 'initial' -and $_.renderer -eq 'gpu'})[0].glyphStats
  $repeat=@($observations | Where-Object {$_.case -eq 'repeat' -and $_.renderer -eq 'gpu'})[0].glyphStats
  if ($initial.glyphUploads -ne $repeat.glyphUploads -or $initial.glyphUploadBytes -ne $repeat.glyphUploadBytes) { throw 'Unchanged redraws reuploaded coverage' }
  $nativeEvidence=@()
  if ($Native) {
    foreach ($snapshotEnabled in @($false,$true)) {
      $name=if ($snapshotEnabled) {'native-snapshot'} else {'native-no-snapshot'}
      $runArgs=@('view','artifacts/text-demo/app.json','--renderer','gpu','--auto-close-ms','12000')
      if ($snapshotEnabled) { $runArgs+=@('--snapshot',(Join-Path $evidenceDir ($name+'.png'))) }
      $run=Invoke-TextApp $name $runArgs
      if ($run.windowSystem -ne 'win32' -or $run.framesPresented -lt 1 -or $run.presentationBackend -ne 'wgpu-surface' -or $run.frame.backend -ne 'wgpu-compute-surface') { throw 'Native GPU Surface required' }
      if ($run.gpu.deviceType -notin @('DiscreteGpu','IntegratedGpu') -or $run.gpuStats.glyphUploads -lt 1 -or $run.gpuStats.imageUploads -ne 0) { throw 'Native hardware glyph rendering required' }
      $expected=if ($snapshotEnabled) {$run.frame.width*$run.frame.height*4} else {0}
      # The presenter uploads eight u32 dimensions/format words, never a pixel frame.
      if ($run.gpuStats.readbackBytes -ne $expected -or $run.gpuStats.presentationUploadBytes -ne (32*$run.framesPresented)) { throw 'Unexpected native pixel readback or presentation-parameter accounting' }
      $nativeEvidence+=@{outcome=($name+'.json');readbackBytes=$run.gpuStats.readbackBytes;presentations=$run.framesPresented;presentationParameterUploadBytes=$run.gpuStats.presentationUploadBytes}
    }
  }
  $summary=@{status='passed';recordedAt=(Get-Date).ToUniversalTime().ToString('o');hostSha256=$hostHash;appSha256=(Get-FileHash -Algorithm SHA256 -LiteralPath 'artifacts/text-demo/main.wasm').Hash.ToLowerInvariant();fontSha256=(Get-FileHash -Algorithm SHA256 -LiteralPath 'assets/fonts/NotoSansSC-Regular.otf').Hash.ToLowerInvariant();parity='Exact CPU/GPU PNG SHA256 equality for all four real Kotlin scenarios; move differs from resize-only pixels at the same 700x500 size';timingScope='Host text preparation and rendering; not compositor frame rate or input latency';observations=$observations;native=$nativeEvidence}
  $summary | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $evidenceDir 'acceptance.json')
  Write-Output ('Verified SDK3 typography: '+(Join-Path $evidenceDir 'acceptance.json'))
} catch {
  @{status='failed';hostSha256=$hostHash;error=$_.Exception.Message;recordedAt=(Get-Date).ToUniversalTime().ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidenceDir 'acceptance.json')
  throw
} finally { Pop-Location }
