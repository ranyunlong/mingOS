#requires -Version 7.3
$ErrorActionPreference = 'Stop'
$public = Join-Path $PSScriptRoot 'new-linux-host-build.ps1'
if (!(Test-Path -LiteralPath $public)) { throw 'public cold generation coordinator required' }
$script:UnexpectedDocker = 0
function docker.exe { $script:UnexpectedDocker++; throw 'Docker must not execute in controls' }
. $public
if ($script:UnexpectedDocker) { throw 'dot-source performed Docker side effects' }
$count = 1
function Rejected([scriptblock]$Action, [string]$Reason) {
  $rejected = $false
  try { & $Action } catch { $rejected = $true }
  if (!$rejected) { throw "accepted $Reason" }
  $script:count++
}
$cid = 'a' * 64
$generation = 'persistent-update-20261107-linux-0123456789abcdef'
$nonce = 'b' * 32
$mounts = @(
  @{type='bind';source='C:\checkout';target='/checkout';readonly=$true},
  @{type='volume';source='fresh-source';target='/source';readonly=$false},
  @{type='volume';source='packages';target='/packages';readonly=$true;subpath='packages'})
$inspection = @{
  Id=$cid;Image=$script:LinuxHostRustImage
  Config=@{User='65534:65534';Labels=@{'org.mingos.generation'=$generation;'org.mingos.nonce'=$nonce}}
  HostConfig=@{NetworkMode='none';ReadonlyRootfs=$true;Privileged=$false;NanoCpus=2000000000;Memory=4294967296;PidsLimit=256
    CapAdd=@();CapDrop=@('ALL');SecurityOpt=@('no-new-privileges');Binds=@();VolumesFrom=@()
    Tmpfs=@{'/tmp'='rw,nosuid,nodev,size=512m,mode=1777'}
    Mounts=@(@{Type='bind';Source='C:\checkout';Target='/checkout';ReadOnly=$true},
      @{Type='volume';Source='fresh-source';Target='/source';ReadOnly=$false},
      @{Type='volume';Source='packages';Target='/packages';ReadOnly=$true;VolumeOptions=@{Subpath='packages'}})}
  Mounts=@(@{Type='bind';Source='/run/desktop/mnt/host/c/checkout';Destination='/checkout';RW=$false},
    @{Type='volume';Name='fresh-source';Destination='/source';RW=$true},
    @{Type='volume';Name='packages';Destination='/packages';RW=$false})
}
Assert-LinuxHostBuildInspection $inspection $cid $generation $nonce $mounts '65534:65534' $false
$count++
foreach ($change in @('image','id','labels','effective-source','effective-rw','requested-source','subpath','extra-effective','extra-requested','capabilities','tmpfs')) {
  $candidate = $inspection | ConvertTo-Json -Depth 15 | ConvertFrom-Json -AsHashtable
  switch ($change) {
    image { $candidate.Image='sha256:wrong' }
    id { $candidate.Id='c'*64 }
    labels { $candidate.Config.Labels['org.mingos.nonce']='wrong' }
    effective-source { $candidate.Mounts[0].Source='/run/desktop/mnt/host/c/other' }
    effective-rw { $candidate.Mounts[0].RW=$true }
    requested-source { $candidate.HostConfig.Mounts[0].Source='C:\other' }
    subpath { $candidate.HostConfig.Mounts[2].VolumeOptions.Subpath='other' }
    extra-effective { $candidate.Mounts += @{Type='bind';Source='C:\secret';Destination='/secret';RW=$true} }
    extra-requested { $candidate.HostConfig.Mounts += @{Type='volume';Source='other';Target='/other'} }
    capabilities { $candidate.HostConfig.CapAdd=@('SYS_ADMIN') }
    tmpfs { $candidate.HostConfig.Tmpfs['/extra']='rw' }
  }
  Rejected { Assert-LinuxHostBuildInspection $candidate $cid $generation $nonce $mounts '65534:65534' $false } $change
}
Rejected { Assert-LinuxHostBuildName 'cargo,name' } 'comma volume name'
Rejected { Assert-LinuxHostBuildName '../cargo' } 'traversing volume name'
Rejected { Resolve-LinuxHostBuildPath 'C:\repo,other' Directory } 'comma bind path'
Rejected { Resolve-LinuxHostBuildPath 'relative\repo' Directory } 'relative bind path'
Rejected { Resolve-LinuxHostBuildPath 'C:\repo\..\other' Directory } 'traversing bind path'
$initializer = $inspection | ConvertTo-Json -Depth 15 | ConvertFrom-Json -AsHashtable
$initializer.Config.User='0:0'
$initializer.HostConfig.CapAdd=@('CAP_CHOWN')
$initializer.HostConfig.Tmpfs=$null
Assert-LinuxHostBuildInspection $initializer $cid $generation $nonce $mounts '0:0' $true
$count++
$initializer.HostConfig.CapAdd=@('CHOWN','CAP_CHOWN')
Rejected { Assert-LinuxHostBuildInspection $initializer $cid $generation $nonce $mounts '0:0' $true } 'duplicate initializer capability'
if ($script:UnexpectedDocker) { throw 'validator performed Docker side effects' }
$originalDocker = (Get-Item Function:Invoke-LinuxHostDocker).ScriptBlock
$script:FakeBuildCalls = [Collections.Generic.List[object]]::new()
$script:FakeBuildInspection = $inspection
function Invoke-LinuxHostDocker([string[]]$Arguments, [string]$Name, [int]$TimeoutSeconds=180, [string]$Executable='') {
  $script:FakeBuildCalls.Add(@{name=$Name;argv=$Arguments;timeout=$TimeoutSeconds})
  switch -Wildcard ($Name) {
    *-create { return ('a' * 64) }
    *-inspect-* { return ConvertTo-Json -InputObject @($script:FakeBuildInspection) -Depth 20 }
    *-run { return '' }
    default { throw "unexpected Docker boundary call: $Name" }
  }
}
try {
  $script:FakeBuildInspection.State=@{Running=$false;ExitCode=0;OOMKilled=$false}
  $context = @{generation=$generation;nonce=$nonce;docker='';owned=[Collections.Generic.List[hashtable]]::new();containers=[Collections.Generic.List[object]]::new()}
  $actualCid = Invoke-LinuxHostBuildContainer $context 'control' $mounts @('python3','-B','/repo/scripts/boot/linux_host_freeze.py')
  if ($actualCid -cne $cid -or $context.owned.Count -ne 1 -or $context.containers.Count -ne 1 -or $script:FakeBuildCalls.Count -ne 4) { throw 'exact create/inspect/start/reinspect lifecycle differs' }
  $argv = $script:FakeBuildCalls[0].argv
  if ($argv -cnotcontains $script:LinuxHostRustImage -or $argv -cnotcontains '--pull' -or $argv -cnotcontains 'never' -or
      $argv -cnotcontains "org.mingos.nonce=$nonce" -or $argv -ccontains '--rm' -or $script:FakeBuildCalls[2].timeout -ne 1950) { throw 'fixed create identity or retained lifecycle differs' }
  $count++
  $script:FakeBuildCalls.Clear()
  $script:FakeBuildInspection = $inspection | ConvertTo-Json -Depth 20 | ConvertFrom-Json -AsHashtable
  $script:FakeBuildInspection.Id='c'*64
  Rejected { Invoke-LinuxHostBuildContainer $context 'wrong-id' $mounts @('python3','-B','never-start') } 'changed prelaunch container ID'
  if (@($script:FakeBuildCalls | Where-Object { $_.argv[0] -eq 'start' }).Count) { throw 'changed ID reached start' }
  $script:FakeBuildCalls.Clear()
  Rejected { New-LinuxHostBuild 'relative-repo' '' '' '' '' '' } 'invalid CLI repository'
  if ($script:FakeBuildCalls.Count) { throw 'invalid CLI arguments reached Docker' }
} finally { Set-Item Function:Invoke-LinuxHostDocker $originalDocker }

# Exercise the public sequence through a fake Docker transport. Only tiny test
# files under this new task directory are written; no Docker executable is used.
$repository = (Resolve-Path "$PSScriptRoot/../..").Path
$testRoot = Join-Path $repository ('artifacts/public-cold-control-' + [Guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path "$testRoot/inputs")
$testRoot = [IO.Path]::GetFullPath($testRoot)
[IO.File]::WriteAllText("$testRoot/inputs/node", 'fake transport control, not executable')
[IO.File]::WriteAllText("$testRoot/inputs/toolchain-observation.json", '{}')
[IO.File]::WriteAllText("$testRoot/backing.vhdx", 'test backing observation')
Write-LinuxHostJson "$testRoot/inputs/rust-image.json" @(@{Id=$script:LinuxHostRustImage;RootFS=@{Layers=@('fixed-layer')}})
$script:CoordinatorCalls = [Collections.Generic.List[object]]::new()
$script:CoordinatorInspections = @{}
$script:CurrentBuildLabel = ''
$script:PublicTestNode = "$testRoot/inputs/node"
function Get-FileHash([string]$LiteralPath, [string]$Algorithm) {
  if ([IO.Path]::GetFullPath($LiteralPath) -eq [IO.Path]::GetFullPath($script:PublicTestNode)) { return @{Hash='e32e2d16081cf644f88392934288086e7662529d0d43277c34f7aad2207cfafb'} }
  return Microsoft.PowerShell.Utility\Get-FileHash -LiteralPath $LiteralPath -Algorithm $Algorithm
}
function Invoke-LinuxHostDocker([string[]]$Arguments, [string]$Name, [int]$TimeoutSeconds=180, [string]$Executable='') {
  $script:CoordinatorCalls.Add(@{name=$Name;argv=$Arguments})
  $g = Get-Content -LiteralPath (Join-Path $script:LinuxHostEvidence 'generation.json') -Raw | ConvertFrom-Json -AsHashtable
  switch -Wildcard ($Name) {
    image-inspect { return ConvertTo-Json -InputObject @(@{Id=$script:LinuxHostRustImage;RootFS=@{Layers=@('fixed-layer')}}) -Depth 8 }
    existing-volumes { return "packages-control`ncargo-control`n" }
    *volume-inspect* {
      return ConvertTo-Json -InputObject @(@{Name=$Arguments[2];Driver='local';Labels=@{'org.mingos.generation'=$g.generation;'org.mingos.nonce'=$g.nonce}}) -Depth 8
    }
    volume-create-* { return $Arguments[-1] }
    *-create {
      $script:CurrentBuildLabel=$Name.Substring(0,$Name.Length-7)
      $id=('{0:x64}' -f ($script:CoordinatorInspections.Count+1))
      $requested=[Collections.Generic.List[object]]::new();$effective=[Collections.Generic.List[object]]::new()
      for($i=0;$i -lt $Arguments.Count;$i++) {
        if($Arguments[$i] -cne '--mount'){continue}
        $parts=@{}
        foreach($part in $Arguments[$i+1].Split(',')) {$pair=$part.Split('=',2);$parts[$pair[0]]=if($pair.Count -eq 2){$pair[1]}else{$true}}
        $ro=$parts.ContainsKey('readonly')
        $m=@{Type=$parts.type;Source=$parts.source;Target=$parts.target;ReadOnly=$ro}
        if($parts.ContainsKey('volume-subpath')){$m.VolumeOptions=@{Subpath=$parts['volume-subpath']}}
        $requested.Add($m)
        $actual=@{Type=$parts.type;Destination=$parts.target;RW=!$ro}
        if($parts.type -ceq 'volume'){$actual.Name=$parts.source}else{$actual.Source=$parts.source}
        $effective.Add($actual)
      }
      $isInit=$Arguments -ccontains '0:0'
      $fake=@{Id=$id;Image=$script:LinuxHostRustImage;Config=@{User=$(if($isInit){'0:0'}else{'65534:65534'});Labels=@{'org.mingos.generation'=$g.generation;'org.mingos.nonce'=$g.nonce}}
        State=@{Running=$false;ExitCode=0;OOMKilled=$false};Mounts=@($effective.ToArray())
        HostConfig=@{NetworkMode='none';ReadonlyRootfs=$true;Privileged=$false;NanoCpus=2000000000;Memory=4294967296;PidsLimit=256
          CapAdd=$(if($isInit){@('CHOWN')}else{@()});CapDrop=@('ALL');SecurityOpt=@('no-new-privileges');Binds=@();VolumesFrom=@()
          Tmpfs=$(if($isInit){$null}else{@{'/tmp'='rw,nosuid,nodev,size=512m,mode=1777'}});Mounts=@($requested.ToArray())}}
      $script:CoordinatorInspections[$id]=$fake
      return $id
    }
    *-inspect-* { return ConvertTo-Json -InputObject @($script:CoordinatorInspections[$Arguments[1]]) -Depth 20 }
    *-run { return '' }
    *-start { return $Arguments[1] }
    snapshot-export { Write-LinuxHostJson $Arguments[2] @{schemaVersion=1;literalEmptyTarget=$true;sourceFiles=1;linuxAvailableBytes=100000000000};return '' }
    prepared-export { [void](New-Item -ItemType Directory -Path $Arguments[2]);return '' }
    default { throw "unexpected coordinator command $Name" }
  }
}
try {
  New-LinuxHostBuild $repository "$testRoot/evidence" "$testRoot/inputs" 'packages-control' 'cargo-control' "$testRoot/backing.vhdx" $true
  $labels=@($script:CoordinatorCalls | Where-Object {$_.name -like '*-create' -and $_.argv[0] -eq 'create'} | ForEach-Object {$_.name})
  if (($labels -join ',') -cne 'snapshot-create,storage-probe-create,storage-reopen-create,prepare-create,build-create') {throw 'cold stage order differs'}
  if (!(Test-Path -LiteralPath "$testRoot/evidence/build-started.json")) {throw 'detached build identity was not retained'}
  $result=Get-Content -LiteralPath "$testRoot/evidence/orchestration-result.json" -Raw | ConvertFrom-Json -AsHashtable
  if ($result.status -cne 'prepared' -or $result.buildStarted -ne $true -or $result.finalSystemAccepted -ne $false) {throw 'prepared result claims differ'}
  $prepareCall=@($script:CoordinatorCalls | Where-Object {$_.name -ceq 'prepare-create'})[0].argv
  if ($prepareCall -cnotcontains '/cold-receipts/capacity.json' -or
      @($prepareCall | Where-Object {$_ -like 'type=bind,*target=/cold-receipts,readonly'}).Count -ne 1) {throw 'capacity receipt must use relocatable directory mount'}
  $callsBefore=$script:CoordinatorCalls.Count
  Rejected { New-LinuxHostBuild $repository "$testRoot/evidence" "$testRoot/inputs" 'packages-control' 'cargo-control' "$testRoot/backing.vhdx" } 'existing generation reuse'
  if ($script:CoordinatorCalls.Count -ne $callsBefore) {throw 'existing evidence reached Docker'}
  $count++
} finally {
  Set-Item Function:Invoke-LinuxHostDocker $originalDocker
  Remove-Item Function:Get-FileHash
  # Exact freshly-created test root under repo/artifacts; never generated from
  # Docker output or a source snapshot path.
  $resolved=[IO.Path]::GetFullPath($testRoot)
  $expectedParent=[IO.Path]::GetFullPath((Join-Path $repository 'artifacts')).TrimEnd('\')+'\'
  if(!$resolved.StartsWith($expectedParent,[StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetFileName($resolved) -notlike 'public-cold-control-*'){throw 'unsafe test cleanup root'}
  Remove-Item -LiteralPath $resolved -Recurse -Force
}
Write-Output "Public cold-generation controls passed: $count (no Docker, build or guest)"
