param(
  [ValidateRange(1,20)][int]$Samples=5,
  [string]$HostPath=(Join-Path $PSScriptRoot '../.cache/rust-target/debug/ming-app-host.exe'),
  [string]$OutputDir=(Join-Path $PSScriptRoot '../artifacts/gpu')
)
$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$nativeHost=[IO.Path]::GetFullPath($HostPath)
$evidenceDir=[IO.Path]::GetFullPath($OutputDir)
if (!(Test-Path -LiteralPath $nativeHost -PathType Leaf)) { throw 'Build first: scripts/rust.ps1 build --locked --features gpu' }
New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null
$eventsPath=Join-Path $evidenceDir 'events.json'
'[[1,1100,760,0],[2,200,0,0],[3,32,-17,0],[0,0,0,0]]' | Set-Content -LiteralPath $eventsPath
$observations=@()
Push-Location -LiteralPath $taskRoot
try {
  for ($i=1; $i -le $Samples; $i++) {
    foreach ($renderer in @('cpu','gpu')) {
      $prefix=Join-Path $evidenceDir ($renderer+'-'+$i)
      $runArgs=@('run','artifacts/image-viewer/app.json','--grant','1=samples/image-viewer/data/coastal.png','--renderer',$renderer,'--timeout-ms','60000','--fuel','1000000000','--events',$eventsPath,'--snapshot',($prefix+'.png'))
      $text=(& $nativeHost @runArgs 2> ($prefix+'.stderr.txt')) -join "`n"
      if ($LASTEXITCODE -ne 0) { throw ('Application failed: '+$prefix+'.stderr.txt') }
      $text | Set-Content -LiteralPath ($prefix+'.json')
      $run=$text | ConvertFrom-Json
      if ($run.exitCode -ne 0 -or $run.framesSubmitted -ne 3 -or $run.windowSystem -ne 'headless') { throw 'Unexpected application outcome' }
      $expectedBackend=if ($renderer -eq 'gpu') {'wgpu-compute-readback'} else {'cpu-reference'}
      if ($run.frame.backend -ne $expectedBackend) { throw 'Unexpected render backend' }
      if ($renderer -eq 'gpu' -and $run.gpu.deviceType -notin @('DiscreteGpu','IntegratedGpu')) { throw 'Hardware adapter required' }
      $hash=(Get-FileHash -Algorithm SHA256 -LiteralPath ($prefix+'.png')).Hash.ToLowerInvariant()
      $observations+=@{sample=$i;renderer=$renderer;renderMicros=$run.frame.renderMicros;gpuTiming=$run.frame.gpuTiming;durationMs=$run.durationMs;pngSha256=$hash;adapter=$run.gpu}
    }
    if ($observations[-1].pngSha256 -ne $observations[-2].pngSha256) { throw 'CPU/GPU image parity failed' }
  }
  $summary=@{
    recordedAt=(Get-Date).ToUniversalTime().ToString('o')
    configuration='Cargo dev; ming-app-host opt-level=1; 1100x760; 15 commands; final frame of 3'
    timingScope='renderMicros includes packing, upload, GPU wait and readback for GPU; excludes device initialization, app startup and window presentation. No GPU timestamps or input latency.'
    parity='exact PNG SHA256 equality for every CPU/GPU pair'
    hostSha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $nativeHost).Hash.ToLowerInvariant()
    appSha256=(Get-FileHash -Algorithm SHA256 -LiteralPath 'artifacts/image-viewer/main.wasm').Hash.ToLowerInvariant()
    shaderSha256=(Get-FileHash -Algorithm SHA256 -LiteralPath 'crates/ming-app-host/src/graphics/scene.wgsl').Hash.ToLowerInvariant()
    observations=$observations
  }
  $summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $evidenceDir 'comparison.json')
  foreach ($renderer in @('cpu','gpu')) {
    $times=@($observations | Where-Object {$_.renderer -eq $renderer} | ForEach-Object {$_.renderMicros} | Sort-Object)
    $middle=[int][Math]::Floor($times.Count/2)
    $median=if ($times.Count%2) {$times[$middle]} else {($times[$middle-1]+$times[$middle])/2}
    Write-Output ($renderer+': '+$times.Count+' samples; min/median/max microseconds '+$times[0]+'/'+$median+'/'+$times[-1])
  }
  Write-Output ('Evidence: '+(Join-Path $evidenceDir 'comparison.json'))
} finally { Pop-Location }
