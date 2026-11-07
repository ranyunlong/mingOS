#requires -Version 7.3
<# Ignored single-generation prototype: new -> pair. No reset, repair, retry or reopen CLI.
   Reuses the existing retained Linux runtime container and volume helpers.
   No guest is started merely by importing, freezing, parsing or testing this file. #>
[CmdletBinding()]
param([string]$Action='',[string]$Evidence='',[string]$PackVolume='',[string]$PackManifestSha256='',
      [string]$HelperRepo=(Resolve-Path "$PSScriptRoot/../..").Path,[string]$DockerExecutable='')
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$developerArgs=@{Action=$Action;Evidence=$Evidence;PackVolume=$PackVolume;PackManifestSha256=$PackManifestSha256;DockerExecutable=$DockerExecutable}
$script:DeveloperPrototype=$PSScriptRoot
$script:DeveloperHelperRepo=[IO.Path]::GetFullPath($HelperRepo)
. "$HelperRepo/scripts/boot/new-linux-host-runtime.ps1"
$script:DeveloperBaseInspection=(Get-Item Function:Assert-LinuxHostBuildInspection).ScriptBlock
$script:DeveloperExpected=$null
$script:DeveloperSourceNames=@('scripts/boot/developer_vm.py','scripts/boot/developer_pack.py','scripts/boot/developer_profile.py',
  'scripts/boot/bootlib.py','scripts/boot/infra.py','scripts/boot/data_disk.py','scripts/boot/vm.py','scripts/boot/isolation_profiles.py',
  'probes/linux-isolation-source-v2/profile.json','probes/linux-isolation-source-v2/probe.rs',
  'crates/ming-app-host/examples/isolation_probe_v2.rs','Cargo.lock','rust-toolchain.toml','developer_vm_driver.py')
$script:DeveloperInitialize=@'
import os
paths=('/work','/results')
fds=[]
try:
 for p in paths:
  fd=os.open(p,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);fds.append(fd)
  if os.listdir(fd):raise ValueError('new output roots must be empty')
 for fd in fds:
  os.fchmod(fd,0o700);os.fsync(fd);os.fchown(fd,65534,65534);os.fsync(fd)
finally:
 for fd in fds:os.close(fd)
'@
$script:DeveloperCopySource=@'
import hashlib,json,os,stat,sys
from pathlib import Path
source=Path('/frozen');target=Path('/source');pin=sys.argv[1]
raw=(source/'source-manifest.json').read_bytes()
if hashlib.sha256(raw).hexdigest()!=pin:raise ValueError('source manifest pin differs')
manifest=json.loads(raw);files=manifest['files']
if list(target.iterdir()):raise ValueError('source volume must be empty')
for name in sorted(files):
 if any(p in ('','.','..') for p in name.split('/')) or '\\' in name or ':' in name:raise ValueError('unsafe source name')
 path=source/name;info=path.lstat()
 if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('literal source required')
 data=path.read_bytes();record=files[name]
 if len(data)!=record['bytes'] or hashlib.sha256(data).hexdigest()!=record['sha256']:raise ValueError('source bytes differ')
 destination=target/name;destination.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
 with destination.open('xb') as out:
  out.write(data);out.flush();os.fchmod(out.fileno(),0o644);os.fsync(out.fileno());os.fchown(out.fileno(),65534,65534);os.fsync(out.fileno())
with (target/'source-manifest.json').open('xb') as out:
 out.write(raw);out.flush();os.fchmod(out.fileno(),0o644);os.fsync(out.fileno());os.fchown(out.fileno(),65534,65534);os.fsync(out.fileno())
for directory in sorted((p for p in target.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True)+[target]:
 fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:
  os.fchmod(fd,0o700 if directory==target else 0o755);os.fsync(fd);os.fchown(fd,65534,65534);os.fsync(fd)
 finally:os.close(fd)
'@
$script:DeveloperBootstrap=@'
import hashlib,runpy,sys
from pathlib import Path
path=Path('/repo/developer_vm_driver.py');raw=path.read_bytes()
if hashlib.sha256(raw).hexdigest()!=sys.argv[1]:raise ValueError('trusted driver pin differs')
sys.argv=[str(path),*sys.argv[2:]]
exec(compile(raw,str(path),'exec'),{'__name__':'__main__','__file__':str(path)})
'@

function ConvertTo-DeveloperCanonical($Value) {
  if($Value -is [Collections.IDictionary]){
    $ordered=[ordered]@{};foreach($key in @($Value.Keys|Sort-Object -CaseSensitive)){$ordered[$key]=ConvertTo-DeveloperCanonical $Value[$key]};return $ordered
  }
  if($Value -is [Collections.IEnumerable] -and $Value -isnot [string]){
    $items=@(foreach($entry in $Value){ConvertTo-DeveloperCanonical $entry});return ,$items
  }
  return $Value
}
function Get-DeveloperObjectHash($Value) {
  $raw=[Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -Depth 100 -Compress -InputObject (ConvertTo-DeveloperCanonical $Value)))
  return [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($raw)).ToLowerInvariant()
}
function Get-DeveloperFileHash([string]$Path){return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}
function Write-DeveloperExclusive([string]$Path,$Value){
  $raw=[Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -Depth 100 -InputObject $Value))
  $stream=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
  try{$stream.Write($raw);$stream.Flush($true)}finally{$stream.Dispose()}
}
function Assert-LinuxHostBuildInspection($Inspection,[string]$Cid,[string]$Generation,[string]$Nonce,[object[]]$Mounts,[string]$User,[bool]$Initializer,
  [string]$Image=$script:LinuxHostToolsImage,[hashtable]$Tmpfs=$script:LinuxHostRuntimeTmpfs){
  & $script:DeveloperBaseInspection $Inspection $Cid $Generation $Nonce $Mounts $User $Initializer $Image $Tmpfs
  if(!$script:DeveloperExpected){throw 'fixed developer command context required'}
  if($Inspection.Config.Image -cne $Image -or $Inspection.Config['Entrypoint'] -or
     (Get-DeveloperObjectHash @($Inspection.Config.Cmd)) -cne (Get-DeveloperObjectHash @($script:DeveloperExpected.command))){throw 'actual developer executable/command differs'}
  $h=$Inspection.HostConfig
  if($h.Init -ne $true -or $h.AutoRemove -ne $false -or (Get-DeveloperObjectHash $h.RestartPolicy) -cne (Get-DeveloperObjectHash @{Name='no';MaximumRetryCount=0})){throw 'init/retention/restart differs'}
  foreach($key in @('Devices','DeviceRequests','Links','PortBindings','ExtraHosts')){if($null -ne $h[$key] -and $h[$key].Count -gt 0){throw 'extra developer attachment'}}
}
function Get-DeveloperVolume($Context,[string]$Name,[string]$Label){
  Assert-LinuxHostBuildName $Name
  $v=@(ConvertFrom-Json -AsHashtable (Invoke-LinuxHostDocker @('volume','inspect',$Name) "$Label-volume-inspect" 180 $Context.docker))
  if($v.Count -ne 1 -or $v[0].Name -cne $Name -or $v[0].Driver -cne 'local' -or $v[0].Scope -cne 'local' -or
     !$v[0].CreatedAt -or !$v[0].Mountpoint -or $v[0]['Options']){throw 'existing plain local volume required'}
  return $v[0]
}
function Assert-DeveloperVolumes($Context,$Identities,[string]$Label){
  foreach($name in @($Identities.Keys|Sort-Object)){
    $actual=Get-DeveloperVolume $Context $name "$Label-$name"
    if((Get-DeveloperObjectHash $actual) -cne (Get-DeveloperObjectHash $Identities[$name])){throw 'frozen volume identity changed'}
  }
}
function Invoke-DeveloperContainer($Context,[string]$Label,[object[]]$Mounts,[string[]]$Command,[bool]$Initializer=$false){
  $selected=@{}
  foreach($m in $Mounts){if($m.type -ceq 'volume'){$selected[$m.source]=$Context.volumeIdentities[$m.source]}}
  Assert-DeveloperVolumes $Context $selected "$Label-before-create"
  $script:DeveloperExpected=@{command=$Command}
  try{return Invoke-LinuxHostRuntimeContainer $Context $Label $Mounts $Command $Initializer}
  finally{$script:DeveloperExpected=$null}
}
function Get-DeveloperCommand($State,[string]$Stage){
  return @('/usr/bin/env','PATH=/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL=C.UTF-8','TMPDIR=/tmp','/usr/bin/python3','-I','-B','-c',
    $script:DeveloperBootstrap,$State.sourceFiles['developer_vm_driver.py'].sha256,$Stage,$State.sourceManifestSha256,$State.packManifestSha256)
}
function New-DeveloperSource([string]$Evidence){
  $snapshot=Join-Path $Evidence 'trusted-source';[void](New-Item -ItemType Directory -Path $snapshot)
  $files=@{}
  foreach($name in $script:DeveloperSourceNames){
    $source=if($name -ceq 'developer_vm_driver.py'){Join-Path $script:DeveloperPrototype $name}else{Join-Path $script:DeveloperHelperRepo $name}
    $source=Resolve-LinuxHostBuildPath $source File;$before=Get-DeveloperFileHash $source
    $destination=Join-Path $snapshot $name;[void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination))
    [IO.File]::Copy($source,$destination,$false)
    if((Get-DeveloperFileHash $source) -cne $before -or (Get-DeveloperFileHash $destination) -cne $before){throw 'trusted source changed while copying'}
    $files[$name]=@{sha256=$before;bytes=(Get-Item -LiteralPath $destination).Length}
  }
  Write-DeveloperExclusive (Join-Path $snapshot 'source-manifest.json') @{schemaVersion=1;profile='developer-vm-trusted-source-v1';files=$files}
  return @{path=$snapshot;files=$files;sha256=(Get-DeveloperFileHash (Join-Path $snapshot 'source-manifest.json'))}
}
function Get-DeveloperHostSources {
  $sources=@{};$paths=@((Join-Path $script:DeveloperPrototype 'developer-vm-outer.ps1'))
  foreach($name in @('new-linux-host-runtime.ps1','new-linux-host-build.ps1','linux-host-qualification.ps1')){$paths+=Join-Path $script:DeveloperHelperRepo "scripts/boot/$name"}
  foreach($path in $paths){$sources[$path]=Get-DeveloperFileHash (Resolve-LinuxHostBuildPath $path File)}
  return $sources
}
function Assert-DeveloperState($State,[string]$Evidence){
  if($State.schemaVersion -ne 1 -or $State.profile -cne 'developer-vm-outer-v1' -or $State.generation -cnotmatch '^developer-preview-[0-9]{8}-[a-f0-9]{16}$' -or
     $State.nonce -cnotmatch '^[a-f0-9]{32}$' -or $State.evidence -cne $Evidence -or $State.toolsImage -cne $script:LinuxHostToolsImage -or
     $State.packManifestSha256 -cnotmatch '^[a-f0-9]{64}$' -or $State.sourceManifestSha256 -cnotmatch '^[a-f0-9]{64}$' -or
     $State.formalQualification -ne $false -or $State.finalSystemAccepted -ne $false){throw 'closed developer state identity differs'}
  foreach($role in @('source','work','results')){if($State.volumes[$role] -cne "$($State.generation)-$role"){throw 'owned developer volume differs'}}
  if((Get-DeveloperObjectHash (Get-DeveloperHostSources)) -cne (Get-DeveloperObjectHash $State.hostSources)){throw 'trusted host harness changed'}
  if((Get-DeveloperFileHash (Join-Path $Evidence 'trusted-source/source-manifest.json')) -cne $State.sourceManifestSha256){throw 'host source manifest changed'}
  foreach($name in $State.sourceFiles.Keys){
    $path=Resolve-LinuxHostBuildPath (Join-Path $Evidence "trusted-source/$name") File
    if((Get-DeveloperFileHash $path) -cne $State.sourceFiles[$name].sha256 -or (Get-Item -LiteralPath $path).Length -ne $State.sourceFiles[$name].bytes){throw 'host source snapshot changed'}
  }
}
function Invoke-DeveloperOuter([string]$Action,[string]$Evidence,[string]$PackVolume='',[string]$PackManifestSha256='',[string]$DockerExecutable=''){
  if($Action -cnotin @('new','pair')){throw 'explicit new or pair action required'}
  if($Action -ceq 'new' -and $PackManifestSha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'exact external pack manifest SHA256 required'}
  if($Action -ceq 'pair' -and ($PackVolume -or $PackManifestSha256 -or $DockerExecutable)){throw 'pair accepts only its immutable evidence generation'}
  $Evidence=Resolve-LinuxHostBuildPath $Evidence $(if($Action -ceq 'new'){'NewDirectory'}else{'Directory'})
  $allowed=[IO.Path]::GetFullPath((Join-Path $script:DeveloperHelperRepo 'artifacts')).TrimEnd('\')+'\'
  if(!$Evidence.StartsWith($allowed,[StringComparison]::OrdinalIgnoreCase)){throw 'new evidence under repo artifacts required'}
  if($Action -ceq 'new'){
    Assert-LinuxHostBuildName $PackVolume
    if($DockerExecutable){$DockerExecutable=Resolve-LinuxHostBuildPath $DockerExecutable File}
    [void](New-Item -ItemType Directory -Path $Evidence)
    $generation='developer-preview-'+[DateTime]::UtcNow.ToString('yyyyMMdd')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,16)
    $state=@{schemaVersion=1;profile='developer-vm-outer-v1';generation=$generation;nonce=[Guid]::NewGuid().ToString('N');evidence=$Evidence;docker=$DockerExecutable
      toolsImage=$script:LinuxHostToolsImage;packVolume=$PackVolume;packManifestSha256=$PackManifestSha256;hostSources=(Get-DeveloperHostSources)
      volumes=@{source="$generation-source";work="$generation-work";results="$generation-results"};formalQualification=$false;finalSystemAccepted=$false}
  }else{
    $stateFile=Resolve-LinuxHostBuildPath (Join-Path $Evidence 'generation.json') File
    $seal=Get-Content -LiteralPath (Join-Path $Evidence 'generation.sha256.json') -Raw|ConvertFrom-Json -AsHashtable
    if($seal.sha256 -cne (Get-DeveloperFileHash $stateFile)){throw 'immutable generation seal differs'}
    $state=Get-Content -LiteralPath $stateFile -Raw|ConvertFrom-Json -AsHashtable;Assert-DeveloperState $state $Evidence
    $prior=Get-Content -LiteralPath (Join-Path $Evidence 'new-result.json') -Raw|ConvertFrom-Json -AsHashtable
    if($prior.status -cne 'passed' -or $prior.stateSha256 -cne $seal.sha256){throw 'new stage incomplete/failed'}
  }
  $attempt=Join-Path $Evidence "$Action-attempt.json"
  if(Test-Path -LiteralPath $attempt){throw 'single-use stage already attempted; preserve generation'}
  Write-DeveloperExclusive $attempt @{schemaVersion=1;action=$Action;generation=$state.generation;retryAllowed=$false;finalSystemAccepted=$false}
  $script:LinuxHostEvidence=$Evidence
  $context=@{generation=$state.generation;nonce=$state.nonce;evidence=$Evidence;docker=$state.docker;image=$script:LinuxHostToolsImage
    owned=[Collections.Generic.List[hashtable]]::new();containers=[Collections.Generic.List[object]]::new();volumeIdentities=@{}}
  try{
    $image=@(ConvertFrom-Json -AsHashtable (Invoke-LinuxHostDocker @('image','inspect',$script:LinuxHostToolsImage) "$Action-image-inspect" 180 $context.docker))
    if($image.Count -ne 1 -or $image[0].Id -cne $script:LinuxHostToolsImage -or $image[0].Os -cne 'linux' -or $image[0].Architecture -cne 'amd64' -or
       !$image[0].RootFS.Layers -or $image[0].Config['Volumes'] -or $image[0].Config['Entrypoint']){throw 'fixed plain Linux amd64 tools image required'}
    if($Action -ceq 'new'){
      $state.imageIdentity=$image[0];$state.imageIdentitySha256=Get-DeveloperObjectHash $image[0]
      $context.volumeIdentities[$PackVolume]=Get-DeveloperVolume $context $PackVolume 'new-pack-input'
      $snapshot=New-DeveloperSource $Evidence;$state.sourceFiles=$snapshot.files;$state.sourceManifestSha256=$snapshot.sha256
      $hostHarness=Join-Path $Evidence 'host-harness';[void](New-Item -ItemType Directory -Path $hostHarness)
      foreach($path in $state.hostSources.Keys){
        $copy=Join-Path $hostHarness ([IO.Path]::GetFileName($path));[IO.File]::Copy($path,$copy,$false)
        if((Get-DeveloperFileHash $copy) -cne $state.hostSources[$path] -or (Get-DeveloperFileHash $path) -cne $state.hostSources[$path]){throw 'trusted host helper changed while freezing'}
      }
      foreach($role in @('source','work','results')){
        New-LinuxHostRuntimeVolume $context $state.volumes[$role] "new-$role"
        $context.volumeIdentities[$state.volumes[$role]]=Get-DeveloperVolume $context $state.volumes[$role] "new-$role-record"
      }
      $state.volumeIdentities=$context.volumeIdentities;$state.volumeIdentitiesSha256=Get-DeveloperObjectHash $state.volumeIdentities
      $sourceMounts=@(@{type='bind';source=$snapshot.path;target='/frozen';readonly=$true},@{type='volume';source=$state.volumes.source;target='/source';readonly=$false})
      [void](Invoke-DeveloperContainer $context 'new-source-copy' $sourceMounts @('/usr/bin/python3','-I','-B','-c',$script:DeveloperCopySource,$state.sourceManifestSha256) $true)
      $newMounts=@(@{type='volume';source=$state.volumes.work;target='/work';readonly=$false},@{type='volume';source=$state.volumes.results;target='/results';readonly=$false})
      [void](Invoke-DeveloperContainer $context 'new-output-init' $newMounts @('/usr/bin/python3','-I','-B','-c',$script:DeveloperInitialize) $true)
      $verifyMounts=@(@{type='volume';source=$state.volumes.source;target='/repo';readonly=$true},@{type='volume';source=$PackVolume;target='/pack';readonly=$true})
      [void](Invoke-DeveloperContainer $context 'new-inputs-verify' $verifyMounts (Get-DeveloperCommand $state verify))
      Assert-DeveloperState $state $Evidence;Assert-DeveloperVolumes $context $state.volumeIdentities 'new-after'
      Write-DeveloperExclusive (Join-Path $Evidence 'generation.json') $state
      Write-DeveloperExclusive (Join-Path $Evidence 'generation.sha256.json') @{sha256=(Get-DeveloperFileHash (Join-Path $Evidence 'generation.json'))}
    }else{
      if((Get-DeveloperObjectHash $image[0]) -cne $state.imageIdentitySha256){throw 'fixed tools image inspection changed'}
      $context.volumeIdentities=$state.volumeIdentities;Assert-DeveloperVolumes $context $context.volumeIdentities 'pair-before'
      $mounts=@(@{type='volume';source=$state.volumes.source;target='/repo';readonly=$true},@{type='volume';source=$state.packVolume;target='/pack';readonly=$true},
        @{type='volume';source=$state.volumes.work;target='/work';readonly=$false},@{type='volume';source=$state.volumes.results;target='/results';readonly=$false})
      $cid=Invoke-DeveloperContainer $context 'pair' $mounts (Get-DeveloperCommand $state pair)
      $copied=Join-Path $Evidence 'developer-pair-driver-result.json'
      [void](Invoke-LinuxHostDocker @('cp',"${cid}:/results/developer-pair-result.json",$copied) 'pair-copy-result' 180 $context.docker)
      $observed=Get-Content -LiteralPath $copied -Raw|ConvertFrom-Json -AsHashtable
      if($observed.status -cne 'developer-preview-pair-observed' -or $observed.normalBoots -ne 2 -or $observed.sessionDurationMs -ne 15000 -or
         $observed.sourceManifestSha256 -cne $state.sourceManifestSha256 -or $observed.packManifestSha256 -cne $state.packManifestSha256 -or
         $observed.normalMarkerReopenVerified -ne $true -or $observed.applicationPersistenceVerified -ne $false -or $observed.deliveryAccepted -ne $false -or
         $observed.formalQualification -ne $false -or $observed.finalSystemAccepted -ne $false){throw 'bounded preview pair receipt differs'}
      Assert-DeveloperState $state $Evidence;Assert-DeveloperVolumes $context $context.volumeIdentities 'pair-after'
    }
    Write-DeveloperExclusive (Join-Path $Evidence "$Action-result.json") @{status='passed';action=$Action;stateSha256=(Get-DeveloperFileHash (Join-Path $Evidence 'generation.json'))
      resourcesPreserved=$true;containers=@($context.containers.ToArray());formalQualification=$false;finalSystemAccepted=$false;applicationPersistenceVerified=$false;deliveryAccepted=$false}
  }catch{
    $failure=$_;$cleanup=[Collections.Generic.List[string]]::new();Stop-LinuxHostBuildContainers $context $cleanup
    Write-DeveloperExclusive (Join-Path $Evidence "$Action-result.json") @{status='failed';action=$Action;resourcesPreserved=$true;error=$failure.Exception.ToString()
      cleanupErrors=@($cleanup.ToArray());owned=@($context.owned.ToArray());containers=@($context.containers.ToArray());formalQualification=$false;finalSystemAccepted=$false}
    throw $failure
  }
}
if($MyInvocation.InvocationName -ne '.') {Invoke-DeveloperOuter @developerArgs}
