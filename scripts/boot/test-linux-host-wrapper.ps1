#requires -Version 7.3
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/linux-host-qualification.ps1"

$expected = @{
  Image = $script:LinuxHostRustImage
  Config = @{ User = '65534:65534' }
  Mounts = @(
    @{ Type = 'bind'; Source = 'C:\repo'; Destination = '/repo'; RW = $false },
    @{ Type = 'volume'; Name = 'unique-volume'; Destination = '/prep'; RW = $true }
  )
  HostConfig = @{
    NetworkMode = 'none'; ReadonlyRootfs = $true; Privileged = $false
    NanoCpus = 2000000000; Memory = 4294967296; PidsLimit = 256
    CapAdd = @(); CapDrop = @('ALL'); SecurityOpt = @('no-new-privileges')
    Tmpfs = @{'/tmp'='rw,nosuid,nodev,size=128m,mode=1777'}
    Mounts = @(
      @{ Type = 'bind'; Source = 'C:\repo'; Target = '/repo'; ReadOnly = $true },
      @{ Type = 'volume'; Source = 'unique-volume'; Target = '/prep'; ReadOnly = $false }
    )
  }
}
Assert-LinuxHostContainer $expected 'C:\repo' 'unique-volume'
$count = 1
$candidate = $expected | ConvertTo-Json -Depth 8 | ConvertFrom-Json -AsHashtable
$candidate.HostConfig.CapAdd = $null
$candidate.HostConfig.Mounts[1].Remove('ReadOnly')
Assert-LinuxHostContainer $candidate 'C:\repo' 'unique-volume'
$count++
foreach ($change in @(
  @('Image', 'wrong'), @('User', '0'), @('NetworkMode', 'bridge'),
  @('ReadonlyRootfs', $false), @('Privileged', $true), @('NanoCpus', 3000000000),
  @('Memory', 8589934592), @('PidsLimit', 512), @('CapAdd', @('SYS_ADMIN')),
  @('CapDrop', @()), @('SecurityOpt', @()), @('Mounts', @())
)) {
  $candidate = $expected | ConvertTo-Json -Depth 8 | ConvertFrom-Json -AsHashtable
  $key, $value = $change
  if ($key -eq 'Image') { $candidate.Image = $value }
  elseif ($key -eq 'User') { $candidate.Config.User = $value }
  else { $candidate.HostConfig[$key] = $value }
  $rejected = $false
  try { Assert-LinuxHostContainer $candidate 'C:\repo' 'unique-volume' }
  catch { $rejected = $true }
  if (!$rejected) { throw "Wrapper accepted changed $key" }
  $count++
}
$candidate = $expected | ConvertTo-Json -Depth 8 | ConvertFrom-Json -AsHashtable
$candidate.HostConfig.Mounts[0].ReadOnly = $false
$rejected = $false
try { Assert-LinuxHostContainer $candidate 'C:\repo' 'unique-volume' } catch { $rejected = $true }
if (!$rejected) { throw 'Wrapper accepted writable source' }
$count++
foreach ($change in @('Binds', 'VolumesFrom', 'extra-effective', 'wrong-effective-volume', 'writable-effective-source', 'extra-tmpfs')) {
  $candidate = $expected | ConvertTo-Json -Depth 8 | ConvertFrom-Json -AsHashtable
  switch ($change) {
    Binds { $candidate.HostConfig.Binds = @('C:\sensitive:/host:rw') }
    VolumesFrom { $candidate.HostConfig.VolumesFrom = @('other-container') }
    extra-effective { $candidate.Mounts += @{Type='bind';Source='C:\sensitive';Destination='/host';RW=$true} }
    wrong-effective-volume { $candidate.Mounts[1].Name = 'other-volume' }
    writable-effective-source { $candidate.Mounts[0].RW = $true }
    extra-tmpfs { $candidate.HostConfig.Tmpfs['/repo/override']='rw,size=1m' }
  }
  $rejected = $false
  try { Assert-LinuxHostContainer $candidate 'C:\repo' 'unique-volume' } catch { $rejected = $true }
  if (!$rejected) { throw "Wrapper accepted changed $change" }
  $count++
}
$initializer = $expected | ConvertTo-Json -Depth 8 | ConvertFrom-Json -AsHashtable
$initializer.Config.User='0:0'
$initializer.HostConfig.CapAdd=@('CHOWN')
$initializer.HostConfig.Tmpfs=$null
$initializer.HostConfig.Mounts=@($initializer.HostConfig.Mounts[1])
$initializer.Mounts=@($initializer.Mounts[1])
Assert-LinuxHostInitializer $initializer 'unique-volume'
$count++
$canonicalInitializer = $initializer | ConvertTo-Json -Depth 8 | ConvertFrom-Json -AsHashtable
$canonicalInitializer.HostConfig.CapAdd = @('CAP_CHOWN')
Assert-LinuxHostInitializer $canonicalInitializer 'unique-volume'
$count++
foreach ($caps in @(@('CHOWN', 'CAP_CHOWN'), @('cap_chown'), @())) {
  $candidate = $initializer | ConvertTo-Json -Depth 8 | ConvertFrom-Json -AsHashtable
  $candidate.HostConfig.CapAdd = $caps
  $rejected = $false
  try { Assert-LinuxHostInitializer $candidate 'unique-volume' } catch { $rejected = $true }
  if (!$rejected) { throw 'initializer accepted non-exact capability set' }
  $count++
}
foreach ($field in @('Memory','CapAdd','Mounts')) {
  $candidate = $initializer | ConvertTo-Json -Depth 8 | ConvertFrom-Json -AsHashtable
  switch ($field) {
    Memory {$candidate.HostConfig.Memory=0}
    CapAdd {$candidate.HostConfig.CapAdd=@('SYS_ADMIN')}
    Mounts {$candidate.Mounts[0].Name='other-volume'}
  }
  $rejected=$false
  try {Assert-LinuxHostInitializer $candidate 'unique-volume'}catch{$rejected=$true}
  if(!$rejected){throw "initializer accepted $field change"}
  $count++
}
$nativeRoot = Join-Path ([IO.Path]::GetTempPath()) ('mingos-wrapper-controls-' + [guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $nativeRoot)
$script:LinuxHostEvidence = $nativeRoot
$powershell = (Get-Process -Id $PID).Path
try {
  $raw = Invoke-LinuxHostDocker @('-NoProfile', '-Command', '[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); [Console]::Out.Write("你好"); [Console]::Error.Write("错误")') 'native-success' 10 $powershell
  if ($raw -cne '你好' -or [IO.File]::ReadAllText("$nativeRoot/native-success.stderr.log") -cne '错误') { throw 'raw output capture changed bytes' }
  $count++
  foreach ($case in @('start-error', 'nonzero', 'timeout')) {
    $failed = $false
    try {
      switch ($case) {
        start-error { [void](Invoke-LinuxHostDocker @() $case 1 'C:\no-such-mingos-docker.exe') }
        nonzero { [void](Invoke-LinuxHostDocker @('-NoProfile', '-Command', 'exit 7') $case 10 $powershell) }
        timeout { [void](Invoke-LinuxHostDocker @('-NoProfile', '-Command', 'Start-Sleep -Seconds 30') $case 1 $powershell) }
      }
    } catch { $failed = $true }
    if (!$failed) { throw "native $case did not fail" }
    $result = Get-Content -Raw "$nativeRoot/$case.result.json" | ConvertFrom-Json -AsHashtable
    if ($case -eq 'start-error' -and (!$result.error -or $null -ne $result.exitCode)) { throw 'start failure result missing' }
    if ($case -eq 'nonzero' -and $result.exitCode -ne 7) { throw 'nonzero exit not preserved' }
    if ($case -eq 'timeout' -and !$result.timedOut) { throw 'timeout result missing' }
    $count++
  }
} finally { Remove-Item -LiteralPath $nativeRoot -Recurse -Force }

$originalDocker = (Get-Item Function:Invoke-LinuxHostDocker).ScriptBlock
$fakeRoot = Join-Path $Repo ('artifacts/wrapper-fake-control-' + [guid]::NewGuid().ToString('N'))
function Invoke-LinuxHostDocker([string[]]$Arguments, [string]$Name, [int]$TimeoutSeconds = 180) {
  $g = Get-Content -Raw (Join-Path $script:LinuxHostEvidence 'generation.json') | ConvertFrom-Json -AsHashtable
  switch -Wildcard ($Name) {
    image-inspect { return '[]' }
    create-volume { return $g.volume }
    volume-inspect { return (@(@{Labels=@{'org.mingos.generation'=$g.generation;'org.mingos.nonce'=$g.nonce}}) | ConvertTo-Json -Depth 5) }
    create-initializer { return ('a' * 64) }
    initializer-before {
      $fake=$initializer | ConvertTo-Json -Depth 8 | ConvertFrom-Json -AsHashtable
      $fake.Id='a'*64
      $fake.Config.Labels=@{'org.mingos.generation'=$g.generation;'org.mingos.nonce'=$g.nonce}
      $fake.HostConfig.Mounts[0].Source=$g.volume
      $fake.Mounts[0].Name=$g.volume
      return (@($fake) | ConvertTo-Json -Depth 8)
    }
    initialize-volume { throw 'ORIGINAL_START_FAILURE' }
    cleanup-inspect-* {
      if ($TimeoutSeconds -gt 15) { throw 'unbounded cleanup inspect' }
      return (@(@{Id=('a'*64);Image=$script:LinuxHostRustImage;Config=@{Labels=@{'org.mingos.generation'=$g.generation;'org.mingos.nonce'=$g.nonce}};State=@{Running=$true}}) | ConvertTo-Json -Depth 8)
    }
    cleanup-kill-* { throw 'CLEANUP_KILL_FAILURE' }
    default { throw "unexpected fake command $Name" }
  }
}
try {
  $failure = $null
  try { Invoke-LinuxHostQualification 'source-check' $fakeRoot $Repo } catch { $failure = $_ }
  if ($null -eq $failure -or $failure.Exception.Message -notlike '*ORIGINAL_START_FAILURE*') { throw 'original failure was masked' }
  $result = Get-Content -Raw (Join-Path $fakeRoot 'result.json') | ConvertFrom-Json -AsHashtable
  if ($result.status -ne 'failed' -or $result.cleanupErrors.Count -ne 1 -or $result.cleanupErrors[0] -notlike '*CLEANUP_KILL_FAILURE*') { throw 'overall failed result lost cleanup failure' }
  if ($result.ownedContainers.Count -ne 1) { throw 'initializer not registered before start' }
  $count++
  $retried = $false
  try { Invoke-LinuxHostQualification 'source-check' $fakeRoot $Repo; $retried=$true } catch {}
  if ($retried) { throw 'failed generation was reused' }
  $count++
} finally {
  Set-Item Function:Invoke-LinuxHostDocker $originalDocker
  if (Test-Path -LiteralPath $fakeRoot) { Remove-Item -LiteralPath $fakeRoot -Recurse -Force }
}
Write-Output "Passed $count wrapper contract controls"
