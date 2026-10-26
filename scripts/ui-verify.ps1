param(
  [string]$HostPath=(Join-Path $PSScriptRoot '../.cache/rust-target/debug/ming-app-host.exe'),
  [string]$OutputDir=(Join-Path $PSScriptRoot '../artifacts/ui-aa'),
  [switch]$Native
)
$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$nativeHost=[IO.Path]::GetFullPath($HostPath)
$evidenceDir=[IO.Path]::GetFullPath($OutputDir)
if (!(Test-Path -LiteralPath $nativeHost -PathType Leaf)) { throw 'Build all features first' }
if (Test-Path -LiteralPath $evidenceDir) { throw 'Evidence directory must be fresh; choose a new -OutputDir' }
New-Item -ItemType Directory -Path $evidenceDir | Out-Null
$hostHash=(Get-FileHash -Algorithm SHA256 -LiteralPath $nativeHost).Hash.ToLowerInvariant()
$observations=@()
$boundInputs=@{$nativeHost=$hostHash}
$buildInfoPath=Join-Path $taskRoot 'artifacts/ui-demo/build-info.json'
$buildInfoHash=$null
function Assert-InputsUnchanged {
  foreach ($item in $boundInputs.GetEnumerator()) {
    if (!(Test-Path -LiteralPath $item.Key -PathType Leaf) -or (Get-FileHash -Algorithm SHA256 -LiteralPath $item.Key).Hash.ToLowerInvariant() -ne $item.Value) { throw ('Input changed during acceptance: '+$item.Key) }
  }
  if ($null -eq $buildInfoHash -and (Test-Path -LiteralPath $buildInfoPath)) { throw 'Build metadata appeared during acceptance' }
}
@{status='running';hostSha256=$hostHash} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidenceDir 'acceptance.json')
function Invoke-UiApp([string]$Name,[string[]]$Arguments) {
  Assert-InputsUnchanged
  $prefix=Join-Path $evidenceDir $Name
  $text=(& $nativeHost @Arguments 2> ($prefix+'.stderr.txt')) -join "`n"
  if ($LASTEXITCODE -ne 0) { throw ('Application failed: '+$prefix+'.stderr.txt') }
  $text | Set-Content -LiteralPath ($prefix+'.json')
  $run=$text | ConvertFrom-Json
  if ($run.appId -ne 'dev.ming.ui-demo' -or $run.runtime -ne 'rust-wasmtime' -or $run.exitCode -ne 0 -or $run.framesSubmitted -lt 1) { throw 'Invalid SDK4 outcome' }
  if (@($run.events).Count -ne 1 -or $run.events[0].code -ne 40 -or $run.events[0].value -ne $run.framesSubmitted) { throw 'SDK4 completion/error evidence differs' }
  if ($run.bulkBytesRead -ne 0 -or $run.inputCounts[0] -lt 1) { throw 'Unexpected file I/O or missing close event' }
  if ($null -eq $run.frame.textPrepareMicros -or $run.frame.textPrepareMicros -gt $run.frame.renderMicros) { throw 'Invalid text preparation timing' }
  Assert-InputsUnchanged
  return $run
}
function Assert-PngSize([string]$Path,[int]$Width,[int]$Height) {
  $bytes=[IO.File]::ReadAllBytes($Path)
  if ($bytes.Length -lt 33 -or $bytes[0] -ne 137 -or [Text.Encoding]::ASCII.GetString($bytes,1,3) -ne 'PNG' -or [Text.Encoding]::ASCII.GetString($bytes,12,4) -ne 'IHDR') { throw 'Invalid PNG header' }
  [long]$w=0;[long]$h=0
  for ($i=0;$i -lt 4;$i++) { $w=$w*256+$bytes[16+$i];$h=$h*256+$bytes[20+$i] }
  if ($w -ne $Width -or $h -ne $Height) { throw 'PNG dimensions differ from accepted frame' }
}
Push-Location -LiteralPath $taskRoot
try {
  $appPath=Join-Path $taskRoot 'artifacts/ui-demo/main.wasm'
  $manifestPath=Join-Path $taskRoot 'artifacts/ui-demo/app.json'
  $fontPath=Join-Path $taskRoot 'assets/fonts/NotoSansSC-Regular.otf'
  foreach ($path in @($appPath,$manifestPath,$fontPath,$PSCommandPath)) { $boundInputs[$path]=(Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() }
  $appHash=$boundInputs[$appPath];$manifestHash=$boundInputs[$manifestPath];$fontHash=$boundInputs[$fontPath]
  if (Test-Path -LiteralPath $buildInfoPath) {
    $buildInfoHash=(Get-FileHash -Algorithm SHA256 -LiteralPath $buildInfoPath).Hash.ToLowerInvariant()
    $boundInputs[$buildInfoPath]=$buildInfoHash
    $buildInfo=Get-Content -Raw -LiteralPath $buildInfoPath | ConvertFrom-Json
    if ($buildInfo.packagedSha256 -ne $appHash) { throw 'Packaged Wasm differs from build metadata' }
  }
  $manifest=Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
  if ($manifest.schemaVersion -ne 1 -or $manifest.sdkVersion -ne 4 -or $manifest.id -ne 'dev.ming.ui-demo' -or $manifest.entry -ne 'main.wasm') { throw 'SDK4 manifest required' }
  Assert-InputsUnchanged
  $cases=@(
    @{name='initial';events='[[0,0,0,0]]';frames=1;w=1100;h=760},
    @{name='toggle';events='[[8,0,0,0],[4,960,40,0],[0,0,0,0]]';frames=2;w=1100;h=760},
    @{name='repeat';events='[[4,960,40,0],[4,960,40,0],[0,0,0,0]]';frames=3;w=1100;h=760},
    @{name='small';events='[[1,480,520,0],[4,300,40,0],[0,0,0,0]]';frames=3;w=480;h=520},
    @{name='maximum';events='[[1,1600,1200,0],[0,0,0,0]]';frames=2;w=1600;h=1200},
    @{name='wide';events='[[1,1920,1080,0],[0,0,0,0]]';frames=2;w=1920;h=1080},
    @{name='minimum';events='[[1,320,240,0],[0,0,0,0]]';frames=2;w=320;h=240},
    @{name='square';events='[[1,2048,2048,0],[0,0,0,0]]';frames=2;w=2048;h=2048},
    @{name='rapid';events='[[1,1920,1080,0],[1,320,240,0],[1,2048,2048,0],[0,0,0,0]]';frames=4;w=2048;h=2048;readback=28722816}
  )
  foreach ($case in $cases) {
    $eventsPath=Join-Path $evidenceDir ($case.name+'-events.json');$case.events | Set-Content -LiteralPath $eventsPath
    $pair=@()
    foreach ($renderer in @('cpu','gpu')) {
      $name=$case.name+'-'+$renderer;$snapshot=Join-Path $evidenceDir ($name+'.png')
      $run=Invoke-UiApp $name @('run','artifacts/ui-demo/app.json','--renderer',$renderer,'--timeout-ms','60000','--fuel','1000000000','--events',$eventsPath,'--snapshot',$snapshot)
      if ($run.windowSystem -ne 'headless' -or $run.framesPresented -ne 0 -or $run.framesSubmitted -ne $case.frames -or $run.frame.width -ne $case.w -or $run.frame.height -ne $case.h) { throw ('Invalid headless state: '+$name) }
      if ($run.frame.width*$run.frame.height*$run.frame.commands -gt 64000000) { throw 'Sample exceeded command-candidate budget' }
      if ($renderer -eq 'gpu') {
        if ($run.gpu.deviceType -notin @('DiscreteGpu','IntegratedGpu') -or $run.frame.backend -ne 'wgpu-compute-readback') { throw 'Hardware GPU required' }
        if ($run.gpuStats.imageUploads -ne 0 -or $run.gpuStats.glyphUploads -lt 1 -or $run.gpuStats.glyphResidentBytes -gt 4194304) { throw 'Invalid glyph/image work' }
        $expected=if ($case.ContainsKey('readback')) {$case.readback} else {(1100*760+($case.frames-1)*$case.w*$case.h)*4}
        if ($run.gpuStats.readbackBytes -ne $expected) { throw ('Headless readback mismatch: '+$name) }
      } elseif ($run.frame.backend -ne 'cpu-reference') { throw 'CPU reference required' }
      Assert-PngSize $snapshot $run.frame.width $run.frame.height
      $hash=(Get-FileHash -Algorithm SHA256 -LiteralPath $snapshot).Hash.ToLowerInvariant();$pair+=$hash
      $observations+=@{case=$case.name;renderer=$renderer;outcome=($name+'.json');snapshot=($name+'.png');pngSha256=$hash;stats=$run.gpuStats;frame=$run.frame}
    }
    if ($pair[0] -ne $pair[1]) { throw ('CPU/GPU exact PNG mismatch: '+$case.name) }
  }
  $initial=@($observations | Where-Object {$_.case -eq 'initial' -and $_.renderer -eq 'gpu'})[0]
  $toggle=@($observations | Where-Object {$_.case -eq 'toggle' -and $_.renderer -eq 'gpu'})[0]
  $repeat=@($observations | Where-Object {$_.case -eq 'repeat' -and $_.renderer -eq 'gpu'})[0]
  if ($initial.pngSha256 -eq $toggle.pngSha256 -or $initial.pngSha256 -ne $repeat.pngSha256) { throw 'Layer toggle/reversal did not change/restore actual pixels' }
  if ($toggle.stats.glyphUploadBytes -ne $repeat.stats.glyphUploadBytes) { throw 'Restoring warmed text caused extra coverage uploads' }
  $nativeEvidence=@()
  if ($Native) {
    foreach ($withSnapshot in @($false,$true)) {
      $name=if ($withSnapshot) {'native-snapshot'} else {'native-no-snapshot'}
      $runArgs=@('view','artifacts/ui-demo/app.json','--renderer','gpu','--auto-close-ms','12000')
      if ($withSnapshot) { $runArgs+=@('--snapshot',(Join-Path $evidenceDir ($name+'.png'))) }
      $run=Invoke-UiApp $name $runArgs
      if ($run.windowSystem -ne 'win32' -or $run.framesPresented -lt 1 -or $run.presentationBackend -ne 'wgpu-surface' -or $run.frame.backend -ne 'wgpu-compute-surface' -or $run.gpu.deviceType -notin @('DiscreteGpu','IntegratedGpu')) { throw 'Native hardware Surface required' }
      $expected=if ($withSnapshot) {$run.frame.width*$run.frame.height*4} else {0}
      if ($run.gpuStats.readbackBytes -ne $expected -or $run.gpuStats.presentationUploadBytes -ne 32*$run.framesPresented) { throw 'Native readback/parameter accounting differs' }
      if ($withSnapshot) { Assert-PngSize (Join-Path $evidenceDir ($name+'.png')) $run.frame.width $run.frame.height }
      $nativeEvidence+=@{outcome=($name+'.json');readbackBytes=$run.gpuStats.readbackBytes;presentations=$run.framesPresented;presentationParameterUploadBytes=$run.gpuStats.presentationUploadBytes}
    }
  }
  Assert-InputsUnchanged
  $summary=@{status='passed';recordedAt=(Get-Date).ToUniversalTime().ToString('o');hostSha256=$hostHash;appSha256=$appHash;manifestSha256=$manifestHash;buildInfoSha256=$buildInfoHash;fontSha256=$fontHash;harnessSha256=$boundInputs[$PSCommandPath];parity='Exact PNG equality for nine real Kotlin CPU/GPU cases';timingScope='Host preparation and rendering, including headless readback; not desktop FPS or input latency';observations=$observations;native=$nativeEvidence}
  $summary | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $evidenceDir 'acceptance.json')
  Write-Output ('Verified SDK4 UI: '+(Join-Path $evidenceDir 'acceptance.json'))
} catch {
  @{status='failed';hostSha256=$hostHash;error=$_.Exception.Message;recordedAt=(Get-Date).ToUniversalTime().ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidenceDir 'acceptance.json')
  throw
} finally { Pop-Location }
