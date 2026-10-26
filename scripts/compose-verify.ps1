param(
  [string]$HostPath=(Join-Path $PSScriptRoot '../.cache/rust-target/release/ming-app-host.exe'),
  [string]$OutputDir=(Join-Path $PSScriptRoot '../artifacts/ui-compose'),
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
$buildInfoPath=Join-Path $taskRoot 'artifacts/compose-demo/build-info.json'
$buildInfoHash=$null
$buildInfo=$null
function Assert-InputsUnchanged {
  foreach ($item in $boundInputs.GetEnumerator()) {
    if (!(Test-Path -LiteralPath $item.Key -PathType Leaf) -or (Get-FileHash -Algorithm SHA256 -LiteralPath $item.Key).Hash.ToLowerInvariant() -ne $item.Value) { throw ('Input changed during acceptance: '+$item.Key) }
  }
  if ($null -eq $buildInfoHash -and (Test-Path -LiteralPath $buildInfoPath)) { throw 'Build metadata appeared during acceptance' }
}
@{status='running';hostSha256=$hostHash} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidenceDir 'acceptance.json')
function Invoke-ComposeApp([string]$Name,[string[]]$Arguments) {
  Assert-InputsUnchanged
  $prefix=Join-Path $evidenceDir $Name
  $text=(& $nativeHost @Arguments 2> ($prefix+'.stderr.txt')) -join "`n"
  if ($LASTEXITCODE -ne 0) { throw ('Application failed: '+$prefix+'.stderr.txt') }
  $text | Set-Content -LiteralPath ($prefix+'.json')
  $run=$text | ConvertFrom-Json
  if ($null -eq $run.frame -or $run.frame.sequence -ne $run.framesSubmitted) { throw 'Frame sequence differs from accepted submission count' }
  if ($run.PSObject.Properties.Name -notcontains 'presentedFrame') { throw 'Presentation identity missing' }
  if ($run.windowSystem -eq 'headless') {
    if ($null -ne $run.presentedFrame) { throw 'Headless execution claimed a presented frame' }
  } else {
    $presented=$run.presentedFrame
    if ($null -eq $presented -or $presented.sequence -ne $run.frame.sequence -or $presented.width -ne $run.frame.width -or $presented.height -ne $run.frame.height) { throw 'Latest submitted frame was not accepted by native presentation' }
  }
  if ($run.appId -ne 'dev.ming.compose-demo' -or $run.runtime -ne 'rust-wasmtime' -or $run.exitCode -ne 0 -or $run.framesSubmitted -lt 1) { throw 'Invalid SDK4 outcome' }
  if (@($run.events).Count -ne 1 -or $run.events[0].code -ne 40 -or $run.events[0].value -ne $run.framesSubmitted) { throw 'SDK4 completion/error evidence differs' }
  if ($run.bulkBytesRead -ne 0 -or $run.inputCounts[0] -lt 1) { throw 'Unexpected file I/O or missing close event' }
  if ($run.frame.width -ge 320 -and $run.frame.height -ge 240 -and ($null -eq $run.frame.textPrepareMicros -or $run.frame.textPrepareMicros -gt $run.frame.renderMicros)) { throw 'Invalid text preparation timing' }
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
  $appPath=Join-Path $taskRoot 'artifacts/compose-demo/main.wasm'
  $manifestPath=Join-Path $taskRoot 'artifacts/compose-demo/app.json'
  $fontPath=Join-Path $taskRoot 'assets/fonts/NotoSansSC-Regular.otf'
  foreach ($path in @($appPath,$manifestPath,$fontPath,$PSCommandPath)) { $boundInputs[$path]=(Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() }
  $appHash=$boundInputs[$appPath];$manifestHash=$boundInputs[$manifestPath];$fontHash=$boundInputs[$fontPath]
  if (!(Test-Path -LiteralPath $buildInfoPath -PathType Leaf)) { throw 'Kotlin build metadata required' }
  $buildInfoHash=(Get-FileHash -Algorithm SHA256 -LiteralPath $buildInfoPath).Hash.ToLowerInvariant()
  $boundInputs[$buildInfoPath]=$buildInfoHash
  $buildInfo=Get-Content -Raw -LiteralPath $buildInfoPath | ConvertFrom-Json
  if ($buildInfo.rawPath -isnot [string] -or [string]::IsNullOrWhiteSpace($buildInfo.rawPath) -or $buildInfo.rawSha256 -isnot [string] -or $buildInfo.rawSha256 -notmatch '^[0-9a-f]{64}$') { throw 'Compiler output path and SHA256 required in build metadata' }
  if ($buildInfo.packagedSha256 -ne $appHash) { throw 'Packaged Wasm differs from build metadata' }
  $rawPath=[IO.Path]::GetFullPath($buildInfo.rawPath)
  if (!$rawPath.StartsWith($taskRoot+[IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Compiler output path escapes the project' }
  $rawHash=(Get-FileHash -Algorithm SHA256 -LiteralPath $rawPath).Hash.ToLowerInvariant()
  if ($rawHash -ne $buildInfo.rawSha256) { throw 'Compiler output differs from build metadata' }
  $boundInputs[$rawPath]=$rawHash
  $manifest=Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
  if ($manifest.schemaVersion -ne 1 -or $manifest.sdkVersion -ne 4 -or $manifest.id -ne 'dev.ming.compose-demo' -or $manifest.entry -ne 'main.wasm') { throw 'SDK4 manifest required' }
  $sourceHashes=@{}
  $sourceFiles=@()
  foreach ($relative in @('Cargo.toml','Cargo.lock','rust-toolchain.toml','crates/ming-app-host/Cargo.toml','settings.gradle.kts','build.gradle.kts','scripts/build-kotlin.mjs','scripts/wasm-sdk-adapter.mjs')) { $sourceFiles+=Get-Item -LiteralPath (Join-Path $taskRoot $relative) }
  $sourceFiles+=Get-ChildItem -LiteralPath (Join-Path $taskRoot 'crates/ming-app-host') -Recurse -File | Where-Object { $_.Extension -in @('.rs','.wgsl') -or $_.FullName.Contains([IO.Path]::DirectorySeparatorChar+'fixtures'+[IO.Path]::DirectorySeparatorChar) }
  $sourceFiles+=Get-ChildItem -LiteralPath (Join-Path $taskRoot 'ming-sdk/src') -Recurse -File
  $sourceFiles+=Get-ChildItem -LiteralPath (Join-Path $taskRoot 'samples/compose-demo/src') -Recurse -File
  foreach ($relative in @('samples/compose-demo/build.gradle.kts','samples/compose-demo/app.json','samples/compose-demo/event-contract.json')) { $sourceFiles+=Get-Item -LiteralPath (Join-Path $taskRoot $relative) }
  $sourceFiles+=Get-ChildItem -LiteralPath (Join-Path $taskRoot 'ming-compose/src') -Recurse -File
  $sourceFiles+=Get-ChildItem -LiteralPath (Join-Path $taskRoot 'ming-compose/tests') -Recurse -File
  $portRoot=Join-Path $taskRoot 'probes/compose-wasi/port'
  $sourceFiles+=Get-ChildItem -LiteralPath (Join-Path $portRoot 'upstream') -Recurse -File
  foreach ($module in @('annotation','collection','platform','runtime-annotation','runtime')) {
    $source=Join-Path $portRoot ($module+'/src')
    if (Test-Path -LiteralPath $source) { $sourceFiles+=Get-ChildItem -LiteralPath $source -Recurse -File }
    $sourceFiles+=Get-Item -LiteralPath (Join-Path $portRoot ($module+'/build.gradle.kts'))
  }
  foreach ($relative in @('ming-compose/build.gradle.kts','probes/compose-wasi/port/settings.gradle.kts','probes/compose-wasi/port/build.gradle.kts','probes/compose-wasi/port/gradle.properties','scripts/build-compose-contract.mjs')) { $sourceFiles+=Get-Item -LiteralPath (Join-Path $taskRoot $relative) }
  foreach ($file in $sourceFiles) {
    $hash=(Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()
    $boundInputs[$file.FullName]=$hash
    $sourceHashes[$file.FullName.Substring($taskRoot.Length+1).Replace([string][IO.Path]::DirectorySeparatorChar,'/')]=$hash
  }
  Assert-InputsUnchanged
  $contractPath=Join-Path $taskRoot 'samples/compose-demo/event-contract.json'
  $boundInputs[$contractPath]=(Get-FileHash -Algorithm SHA256 -LiteralPath $contractPath).Hash.ToLowerInvariant()
  $contract=Get-Content -Raw -LiteralPath $contractPath | ConvertFrom-Json
  if ($contract.schemaVersion -ne 1 -or $contract.initialViewport.width -ne 1100 -or $contract.initialViewport.height -ne 760 -or $contract.completionEvent.code -ne 40) { throw 'Unexpected shadow event contract' }
  $cases=@()
  foreach ($property in $contract.cases.PSObject.Properties) {
    $w=1100;$h=760;[long]$readback=1100*760*4
    foreach ($event in $property.Value.events) {
      if ($event[0] -eq 1) { $w=$event[1];$h=$event[2];$readback+=[long]$w*$h*4 }
      elseif ($event[0] -eq 4) { $readback+=[long]$w*$h*4 }
    }
    $cases+=@{name=$property.Name;events=(ConvertTo-Json -InputObject $property.Value.events -Compress -Depth 5);frames=$property.Value.frames;w=$w;h=$h;readback=$readback;reuse=$true}
  }
  $cases+=@(
    @{name='narrow';events='[[1,64,900,0],[0,0,0,0]]';frames=2;w=64;h=900;reuse=$false},
    @{name='tiny';events='[[1,1,1,0],[0,0,0,0]]';frames=2;w=1;h=1;reuse=$false},
    @{name='minimum';events='[[1,320,240,0],[0,0,0,0]]';frames=2;w=320;h=240;reuse=$false}
  )
  foreach ($case in $cases) {
    $eventsPath=Join-Path $evidenceDir ($case.name+'-events.json');$case.events | Set-Content -LiteralPath $eventsPath
    $pair=@()
    foreach ($renderer in @('cpu','gpu')) {
      $name=$case.name+'-'+$renderer;$snapshot=Join-Path $evidenceDir ($name+'.png')
      $run=Invoke-ComposeApp $name @('run','artifacts/compose-demo/app.json','--renderer',$renderer,'--timeout-ms','60000','--fuel','1000000000','--events',$eventsPath,'--snapshot',$snapshot)
      if ($run.windowSystem -ne 'headless' -or $run.framesPresented -ne 0 -or $run.framesSubmitted -ne $case.frames -or $run.frame.width -ne $case.w -or $run.frame.height -ne $case.h) { throw ('Invalid headless state: '+$name) }
      if ($run.frame.width*$run.frame.height*$run.frame.commands -gt 64000000) { throw 'Sample exceeded command-candidate budget' }
      if ($renderer -eq 'gpu') {
        if ($run.gpu.deviceType -notin @('DiscreteGpu','IntegratedGpu') -or $run.frame.backend -ne 'wgpu-compute-readback') { throw 'Hardware GPU required' }
        if ($run.gpuStats.imageUploads -ne 0 -or $run.gpuStats.glyphUploads -lt 1 -or $run.gpuStats.glyphResidentBytes -gt 4194304) { throw 'Invalid glyph/image work' }
        $stats=$run.gpuStats
        if ($stats.shadowCacheMisses -lt 1 -or $stats.shadowEntries -lt 1 -or $stats.shadowEntries -gt 8 -or $stats.shadowResidentBytes -gt 2097152 -or $stats.shadowGenerationWork -lt 1 -or $stats.shadowParameterUploadBytes -ne 32*$stats.shadowCacheMisses) { throw 'Invalid shadow cache/parameter accounting' }
        if ($case.reuse -and ($stats.shadowCacheMisses -ne 1 -or $stats.shadowCacheEvictions -ne 0 -or $stats.shadowCacheHits -ne ($case.frames-1))) { throw ('Move/color/large resize failed to reuse canonical shadow masks: '+$name) }

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
  $increment=@($observations | Where-Object {$_.case -eq 'increment' -and $_.renderer -eq 'gpu'})[0]
  $hide=@($observations | Where-Object {$_.case -eq 'hide' -and $_.renderer -eq 'gpu'})[0]
  $restore=@($observations | Where-Object {$_.case -eq 'restore' -and $_.renderer -eq 'gpu'})[0]
  if ($initial.pngSha256 -eq $increment.pngSha256 -or $initial.pngSha256 -eq $hide.pngSha256 -or $initial.pngSha256 -ne $restore.pngSha256) { throw 'Compose state/node removal/reversal did not change/restore actual pixels' }
  if ($initial.stats.shadowParameterUploadBytes -ne $restore.stats.shadowParameterUploadBytes -or $hide.stats.glyphUploadBytes -ne $restore.stats.glyphUploadBytes) { throw 'Restoring warmed resources caused extra uploads' }
  $nativeEvidence=@()
  if ($Native) {
    foreach ($withSnapshot in @($false,$true)) {
      $name=if ($withSnapshot) {'native-snapshot'} else {'native-no-snapshot'}
      $runArgs=@('view','artifacts/compose-demo/app.json','--renderer','gpu','--auto-close-ms','12000')
      if ($withSnapshot) { $runArgs+=@('--snapshot',(Join-Path $evidenceDir ($name+'.png'))) }
      $run=Invoke-ComposeApp $name $runArgs
      if ($run.windowSystem -ne 'win32' -or $run.framesPresented -lt 1 -or $run.presentationBackend -ne 'wgpu-surface' -or $run.frame.backend -ne 'wgpu-compute-surface' -or $run.gpu.deviceType -notin @('DiscreteGpu','IntegratedGpu')) { throw 'Native hardware Surface required' }
      $expected=if ($withSnapshot) {$run.frame.width*$run.frame.height*4} else {0}
      if ($run.gpuStats.readbackBytes -ne $expected -or $run.gpuStats.presentationUploadBytes -ne 32*$run.framesPresented) { throw 'Native readback/parameter accounting differs' }
      if ($withSnapshot) { Assert-PngSize (Join-Path $evidenceDir ($name+'.png')) $run.frame.width $run.frame.height }
      $nativeEvidence+=@{outcome=($name+'.json');readbackBytes=$run.gpuStats.readbackBytes;presentations=$run.framesPresented;presentationParameterUploadBytes=$run.gpuStats.presentationUploadBytes}
    }
  }
  Assert-InputsUnchanged
  $summary=@{status='passed';recordedAt=(Get-Date).ToUniversalTime().ToString('o');hostSha256=$hostHash;appSha256=$appHash;manifestSha256=$manifestHash;buildInfoSha256=$buildInfoHash;fontSha256=$fontHash;harnessSha256=$boundInputs[$PSCommandPath];sourceSha256=$sourceHashes;parity='Exact PNG equality for eight real Kotlin/Compose CPU/GPU cases';timingScope='Host preparation and rendering, including headless readback; not desktop FPS or input latency';observations=$observations;native=$nativeEvidence}
  $summary | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $evidenceDir 'acceptance.json')
  Write-Output ('Verified Compose Ming components: '+(Join-Path $evidenceDir 'acceptance.json'))
} catch {
  @{status='failed';hostSha256=$hostHash;error=$_.Exception.Message;recordedAt=(Get-Date).ToUniversalTime().ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidenceDir 'acceptance.json')
  throw
} finally { Pop-Location }
