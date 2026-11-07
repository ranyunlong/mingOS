#requires -Version 7.3
$ErrorActionPreference='Stop'
$path=Join-Path $PSScriptRoot 'developer-vm-outer.ps1'
if(!(Test-Path -LiteralPath $path)){throw 'fixed outer coordinator is missing'}
. $path -HelperRepo (Resolve-Path "$PSScriptRoot/../..").Path
$script:controls=0
function Assert-Control($Value,[string]$Message){if(!$Value){throw $Message};$script:controls++}
function Reject-Control([scriptblock]$Action,[string]$Message){$bad=$false;try{&$Action}catch{$bad=$true};Assert-Control $bad $Message}
Reject-Control {Invoke-DeveloperOuter -Action reset -Evidence 'unused'} 'unlisted stage accepted'
Reject-Control {Invoke-DeveloperOuter -Action new -Evidence 'unused' -PackVolume missing -PackManifestSha256 ('A'*64)} 'bad pin reached daemon'
$script:calls=[Collections.Generic.List[object]]::new();$script:volumes=@{};$script:containers=@{};$script:failRun='';$script:wrongCommand=$false;$script:wrongResultPin=$false;$script:imageMutation=''
$script:volumes['sealed-pack']=@{Name='sealed-pack';Driver='local';Scope='local';CreatedAt='2026-11-07T00:00:00Z';Mountpoint='/var/lib/docker/volumes/sealed-pack/_data';Labels=@{};Options=$null}
function Invoke-LinuxHostDocker([string[]]$Arguments,[string]$Name,[int]$TimeoutSeconds=180,[string]$Executable=''){
  $script:calls.Add(@{name=$Name;argv=$Arguments;timeout=$TimeoutSeconds})
  switch($Arguments[0]){
    'image' {
      $dto=@{Id=$script:LinuxHostToolsImage;Os='linux';Architecture='amd64';RootFS=@{Type='layers';Layers=@('sha256:fixed-layer')};Config=@{Env=@('PATH=/usr/bin')}}
      if($script:imageMutation -ceq 'volume'){$dto.Config['Volumes']=@{'/unrequested'=@{}}}
      if($script:imageMutation -ceq 'entrypoint'){$dto.Config['Entrypoint']=@('/unrequested')}
      return ConvertTo-Json -Depth 20 -InputObject @($dto)
    }
    'volume' {
      if($Arguments[1] -ceq 'ls'){return ($script:volumes.Keys -join "`n")}
      if($Arguments[1] -ceq 'create'){
        $volume=$Arguments[-1];$labels=@{}
        for($i=0;$i -lt $Arguments.Count;$i++){if($Arguments[$i] -ceq '--label'){$k,$v=$Arguments[$i+1].Split('=',2);$labels[$k]=$v}}
        $script:volumes[$volume]=@{Name=$volume;Driver='local';Scope='local';CreatedAt='2026-11-07T00:00:01Z';Mountpoint="/var/lib/docker/volumes/$volume/_data";Labels=$labels;Options=$null};return $volume
      }
      if(!$script:volumes.ContainsKey($Arguments[2])){throw 'synthetic missing input volume'}
      return ConvertTo-Json -Depth 20 -InputObject @($script:volumes[$Arguments[2]])
    }
    'create' {
      $cid=('{0:x64}' -f ($script:containers.Count+1));$mounts=@();$requested=@();$labels=@{};$tmpfs=@{}
      for($i=0;$i -lt $Arguments.Count;$i++){
        if($Arguments[$i] -ceq '--label'){$k,$v=$Arguments[$i+1].Split('=',2);$labels[$k]=$v}
        if($Arguments[$i] -ceq '--tmpfs'){$k,$v=$Arguments[$i+1].Split(':',2);$tmpfs[$k]=$v}
        if($Arguments[$i] -cne '--mount'){continue}
        $p=@{};foreach($field in $Arguments[$i+1].Split(',')){$pair=$field.Split('=',2);$p[$pair[0]]=if($pair.Count -eq 2){$pair[1]}else{$true}}
        if($p.type -ceq 'volume' -and !$script:volumes.ContainsKey($p.source)){throw 'create reached missing volume'}
        $ro=$p.ContainsKey('readonly');$m=@{Type=$p.type;Source=$p.source;Target=$p.target;ReadOnly=$ro}
        if($p.ContainsKey('volume-subpath')){$m.VolumeOptions=@{Subpath=$p['volume-subpath']}}
        $requested+=,$m;$actual=@{Type=$p.type;Destination=$p.target;RW=!$ro}
        if($p.type -ceq 'volume'){$actual.Name=$p.source;$actual.Driver='local'}else{$actual.Source=$p.source};$mounts+=,$actual
      }
      $isInit=$Arguments -ccontains '0:0';$pos=[Array]::IndexOf($Arguments,$script:LinuxHostToolsImage)
      $cmd=@($Arguments[($pos+1)..($Arguments.Count-1)]);if($script:wrongCommand){$cmd=@('wrong')}
      $script:containers[$cid]=@{Id=$cid;Image=$script:LinuxHostToolsImage;Config=@{Image=$script:LinuxHostToolsImage;User=$(if($isInit){'0:0'}else{'65534:65534'});Labels=$labels;Cmd=$cmd}
        State=@{Status='created';Running=$false;ExitCode=0;OOMKilled=$false;Dead=$false;Restarting=$false};Mounts=$mounts
        HostConfig=@{Init=$true;NetworkMode='none';ReadonlyRootfs=$true;Privileged=$false;NanoCpus=2000000000;Memory=4294967296;PidsLimit=256
          CapDrop=@('ALL');CapAdd=$(if($isInit){@('CHOWN')}else{@()});SecurityOpt=@('no-new-privileges');Binds=@();VolumesFrom=@();Devices=@();DeviceRequests=@();Links=@();PortBindings=@{};ExtraHosts=@();AutoRemove=$false;RestartPolicy=@{Name='no';MaximumRetryCount=0};Tmpfs=$(if($isInit){$null}else{$tmpfs});Mounts=$requested}}
      return $cid
    }
    'inspect' {return ConvertTo-Json -Depth 30 -InputObject @($script:containers[$Arguments[1]])}
    'start' {
      if($Name -ceq $script:failRun){$script:containers[$Arguments[2]].State.Running=$true;throw 'synthetic timeout'}
      $script:containers[$Arguments[2]].State.Status='exited';return ''
    }
    'kill' {$script:containers[$Arguments[1]].State.Running=$false;$script:containers[$Arguments[1]].State.Status='exited';return ''}
    'cp' {
      $saved=Get-Content -LiteralPath (Join-Path $script:LinuxHostEvidence 'generation.json') -Raw|ConvertFrom-Json -AsHashtable
      Write-DeveloperExclusive $Arguments[2] @{schemaVersion=1;status='developer-preview-pair-observed';normalBoots=2;sessionDurationMs=15000;normalMarkerReopenVerified=$true
        applicationPersistenceVerified=$false;deliveryAccepted=$false;diagnosticOnly=$true;instrumentedQualification=$false;formalQualification=$false;finalSystemAccepted=$false
        sourceManifestSha256=$saved.sourceManifestSha256;packManifestSha256=$(if($script:wrongResultPin){'0'*64}else{$saved.packManifestSha256})};return ''
    }
    default {throw "unexpected fake docker: $Name"}
  }
}
$repo=(Resolve-Path "$PSScriptRoot/../..").Path
$artifactRoot=Join-Path $repo 'artifacts';[void][IO.Directory]::CreateDirectory($artifactRoot)
$root=Join-Path $artifactRoot ('developer-outer-controls-'+[Guid]::NewGuid().ToString('N'));[void](New-Item -ItemType Directory -Path $root)
foreach($mutation in @('volume','entrypoint')){
  $script:imageMutation=$mutation;$before=$script:calls.Count
  Reject-Control {Invoke-DeveloperOuter -Action new -Evidence (Join-Path $root "image-$mutation") -PackVolume sealed-pack -PackManifestSha256 ('a'*64)} "image $mutation accepted"
  $rejection=Get-Content -LiteralPath (Join-Path $root "image-$mutation/new-result.json") -Raw|ConvertFrom-Json -AsHashtable
  Assert-Control ($rejection.error -match 'fixed plain Linux amd64 tools image required') "image $mutation rejected for wrong reason"
  Assert-Control (@($script:calls|Select-Object -Skip $before|Where-Object {$_.argv[0] -ceq 'create' -or ($_.argv[0] -ceq 'volume' -and $_.argv[1] -ceq 'create')}).Count -eq 0) "image $mutation reached resource create"
}
$script:imageMutation=''
$evidence=Join-Path $root 'passed'
Invoke-DeveloperOuter -Action new -Evidence $evidence -PackVolume sealed-pack -PackManifestSha256 ('a'*64)
$state=Get-Content -LiteralPath "$evidence/generation.json" -Raw|ConvertFrom-Json -AsHashtable
Assert-Control ($state.volumes.work -cne $state.volumes.results) 'work/results must be distinct'
Assert-Control ($state.sourceFiles.ContainsKey('probes/linux-isolation-source-v2/profile.json')) 'profile omitted from source freeze'
Assert-Control ($state.sourceFiles.ContainsKey('Cargo.lock') -and $state.sourceFiles.ContainsKey('crates/ming-app-host/examples/isolation_probe_v2.rs')) 'small bound source inputs omitted'
Invoke-DeveloperOuter -Action pair -Evidence $evidence
$pair=@($script:calls|Where-Object {$_.name -ceq 'pair-create'})[0]
Assert-Control ($pair.argv -ccontains '/usr/bin/python3' -and $pair.argv -ccontains '-I' -and $pair.argv -ccontains '-B') 'isolated fixed Python missing'
Assert-Control (@($pair.argv|Where-Object {$_ -like 'type=volume,*target=/repo,readonly'}).Count -eq 1) 'source must be readonly'
Assert-Control (@($pair.argv|Where-Object {$_ -like 'type=volume,*target=/pack,readonly'}).Count -eq 1) 'pack must be readonly'
Assert-Control (@($pair.argv|Where-Object {$_ -like "type=volume,source=$($state.volumes.work),target=/work"}).Count -eq 1) 'work identity changed'
Assert-Control (@($pair.argv|Where-Object {$_ -like "type=volume,source=$($state.volumes.results),target=/results"}).Count -eq 1) 'results identity changed'
Assert-Control (@($script:calls|Where-Object {$_.name -ceq 'pair-run' -and $_.timeout -eq 1950}).Count -eq 1) 'outer1950 missing'
$before=$script:calls.Count
Reject-Control {Invoke-DeveloperOuter -Action pair -Evidence $evidence} 'repeated pair accepted'
Assert-Control ($before -eq $script:calls.Count) 'repeated stage reached daemon'
Reject-Control {Invoke-DeveloperOuter -Action reopen -Evidence $evidence} 'prototype unexpectedly exposes reopen'
$missing=Join-Path $root 'missing';$before=$script:calls.Count
Reject-Control {Invoke-DeveloperOuter -Action new -Evidence $missing -PackVolume absent -PackManifestSha256 ('a'*64)} 'missing pack accepted'
Assert-Control (@($script:calls|Select-Object -Skip $before|Where-Object {$_.argv[0] -ceq 'create'}).Count -eq 0) 'missing pack reached container create'
Reject-Control {Invoke-DeveloperOuter -Action new -Evidence $missing -PackVolume sealed-pack -PackManifestSha256 ('a'*64)} 'failed evidence reused'
$failed=Join-Path $root 'failed';Invoke-DeveloperOuter -Action new -Evidence $failed -PackVolume sealed-pack -PackManifestSha256 ('a'*64)
$script:failRun='pair-run';Reject-Control {Invoke-DeveloperOuter -Action pair -Evidence $failed} 'pair failure accepted';$script:failRun=''
$failure=Get-Content -LiteralPath "$failed/pair-result.json" -Raw|ConvertFrom-Json -AsHashtable
Assert-Control ($failure.status -ceq 'failed' -and $failure.resourcesPreserved) 'failure resources/receipt not retained'
Assert-Control (@($script:calls|Where-Object {$_.name -clike 'cleanup-inspect-*'}).Count -gt 0) 'failed runtime lacks final inspection'
Assert-Control (@($script:calls|Where-Object {$_.name -clike 'cleanup-after-*'}).Count -gt 0) 'failed owned runtime lacks after-kill inspection'
$before=$script:calls.Count;Reject-Control {Invoke-DeveloperOuter -Action pair -Evidence $failed} 'failed pair retried'
Assert-Control ($before -eq $script:calls.Count) 'failed retry reached daemon'
$drift=Join-Path $root 'drift';Invoke-DeveloperOuter -Action new -Evidence $drift -PackVolume sealed-pack -PackManifestSha256 ('a'*64)
$saved=Get-Content -LiteralPath "$drift/generation.json" -Raw|ConvertFrom-Json -AsHashtable;$script:volumes[$saved.volumes.work].CreatedAt='different'
Reject-Control {Invoke-DeveloperOuter -Action pair -Evidence $drift} 'replaced work volume accepted'
$cmd=Join-Path $root 'command';Invoke-DeveloperOuter -Action new -Evidence $cmd -PackVolume sealed-pack -PackManifestSha256 ('a'*64)
$script:wrongCommand=$true;$before=$script:calls.Count;Reject-Control {Invoke-DeveloperOuter -Action pair -Evidence $cmd} 'changed actual command accepted';$script:wrongCommand=$false
Assert-Control (@($script:calls|Select-Object -Skip $before|Where-Object {$_.name -ceq 'pair-run'}).Count -eq 0) 'changed command reached start'
$pin=Join-Path $root 'pin';Invoke-DeveloperOuter -Action new -Evidence $pin -PackVolume sealed-pack -PackManifestSha256 ('a'*64)
$script:wrongResultPin=$true;Reject-Control {Invoke-DeveloperOuter -Action pair -Evidence $pin} 'driver result with mismatched pack pin accepted';$script:wrongResultPin=$false
Assert-Control (@($script:calls|Where-Object {$_.argv[0] -cin @('rm','rmi','pull','build','run')}).Count -eq 0) 'unexpected mutating daemon operation'
Write-Output "Developer outer controls passed: $script:controls (synthetic only; guestBooted=false)"
