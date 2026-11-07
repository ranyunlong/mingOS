#requires -Version 7.3
$ErrorActionPreference='Stop'
$public=Join-Path $PSScriptRoot 'new-linux-host-runtime.ps1'
if(!(Test-Path -LiteralPath $public)){throw 'public complete runtime coordinator required'}
$RuntimeStage='pair'
. $public -RuntimeStage pair
if($RuntimeStage -cne 'pair'){throw 'dot-source overwrote requested runtime stage'}
$count=1
function Reject-Runtime([scriptblock]$Action,[string]$Name){
  $bad=$false;try{& $Action}catch{$bad=$true};if(!$bad){throw "accepted $Name"};$script:count++
}
$repository=(Resolve-Path "$PSScriptRoot/../..").Path
$testRoot=Join-Path $repository ('artifacts/public-runtime-control-'+[Guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path "$testRoot/inputs")
[void](New-Item -ItemType Directory -Path "$testRoot/cold/receipts")
$originalDocker=(Get-Item Function:Invoke-LinuxHostDocker).ScriptBlock
$script:RuntimeFakeCalls=[Collections.Generic.List[object]]::new()
$script:RuntimeFakeInspections=@{}
$script:RuntimeFakeVolumes=@{}
$script:RuntimeFailure=''
$script:RuntimeCleanupFailure=$false
$script:RuntimeWrongImage=$false
function Invoke-LinuxHostDocker([string[]]$Arguments,[string]$Name,[int]$TimeoutSeconds=180,[string]$Executable=''){
  $script:RuntimeFakeCalls.Add(@{name=$Name;argv=$Arguments;timeout=$TimeoutSeconds})
  switch -Wildcard($Name){
    *image-inspect {return ConvertTo-Json -InputObject @(@{Id=$(if($script:RuntimeWrongImage){'sha256:wrong'}else{$script:LinuxHostToolsImage});RootFS=@{Layers=@('fixed-layer')}}) -Depth 10}
    *existing-volumes {return ($script:RuntimeFakeVolumes.Keys -join "`n")}
    *volume-create* {
      $script:RuntimeFakeVolumes[$Arguments[-1]]=@{Name=$Arguments[-1];Driver='local';Labels=@{'org.mingos.generation'=$Arguments[3].Split('=',2)[1];'org.mingos.nonce'=$Arguments[5].Split('=',2)[1]}}
      return $Arguments[-1]
    }
    *volume-inspect* {
      $v=$script:RuntimeFakeVolumes[$Arguments[2]]
      if(!$v){$v=@{Name=$Arguments[2];Driver='local';Labels=@{}}}
      return ConvertTo-Json -InputObject @($v) -Depth 10
    }
    *-create {
      $id=('{0:x64}' -f ($script:RuntimeFakeInspections.Count+1))
      $requested=[Collections.Generic.List[object]]::new();$effective=[Collections.Generic.List[object]]::new();$labels=@{};$tmpfs=@{}
      for($i=0;$i -lt $Arguments.Count;$i++){
        if($Arguments[$i] -ceq '--label'){$pair=$Arguments[$i+1].Split('=',2);$labels[$pair[0]]=$pair[1]}
        if($Arguments[$i] -ceq '--tmpfs'){$pair=$Arguments[$i+1].Split(':',2);$tmpfs[$pair[0]]=$pair[1]}
        if($Arguments[$i] -cne '--mount'){continue}
        $parts=@{};foreach($part in $Arguments[$i+1].Split(',')){$pair=$part.Split('=',2);$parts[$pair[0]]=if($pair.Count -eq 2){$pair[1]}else{$true}}
        $ro=$parts.ContainsKey('readonly');$m=@{Type=$parts.type;Source=$parts.source;Target=$parts.target;ReadOnly=$ro}
        if($parts.ContainsKey('volume-subpath')){$m.VolumeOptions=@{Subpath=$parts['volume-subpath']}}
        $requested.Add($m);$actual=@{Type=$parts.type;Destination=$parts.target;RW=!$ro}
        if($parts.type -ceq 'volume'){$actual.Name=$parts.source}else{$actual.Source=$parts.source};$effective.Add($actual)
      }
      $isInit=$Arguments -ccontains '0:0'
      $script:RuntimeFakeInspections[$id]=@{Id=$id;Image=$script:LinuxHostToolsImage;Config=@{User=$(if($isInit){'0:0'}else{'65534:65534'});Labels=$labels}
        State=@{Running=$false;ExitCode=0;OOMKilled=$false};Mounts=@($effective.ToArray());HostConfig=@{NetworkMode='none';ReadonlyRootfs=$true;Privileged=$false
          NanoCpus=2000000000;Memory=4294967296;PidsLimit=256;CapAdd=$(if($isInit){@('CHOWN')}else{@()});CapDrop=@('ALL');SecurityOpt=@('no-new-privileges')
          Binds=@();VolumesFrom=@();Tmpfs=$(if($isInit){$null}else{$tmpfs});Mounts=@($requested.ToArray())}}
      return $id
    }
    cleanup-* {
      if($script:RuntimeCleanupFailure){throw 'EXPECTED_CLEANUP_FAILURE'}
      if($Arguments[0] -ceq 'kill'){$script:RuntimeFakeInspections[$Arguments[1]].State.Running=$false;return ''}
      return ConvertTo-Json -InputObject @($script:RuntimeFakeInspections[$Arguments[1]]) -Depth 25
    }
    *-inspect-* {return ConvertTo-Json -InputObject @($script:RuntimeFakeInspections[$Arguments[1]]) -Depth 25}
    *-run {
      if($Name -ceq $script:RuntimeFailure){$script:RuntimeFakeInspections[$Arguments[2]].State.Running=$true;throw 'EXPECTED_PRIMARY_FAILURE'}
      return ''
    }
    *-copy-* {[IO.File]::WriteAllText($Arguments[2], '{}');return ''}
    default {throw "unexpected runtime fake command: $Name"}
  }
}
try{
  Write-LinuxHostJson "$testRoot/inputs/tools-image.stdout.log" @(@{Id=$script:LinuxHostToolsImage;RootFS=@{Layers=@('fixed-layer')}})
  Write-LinuxHostJson "$testRoot/inputs/tools-manifest3.stdout.log" @{schemaVersion=1;files=@{'/usr/bin/tool'=@{sha256=('a'*64);bytes=1}}}
  Write-LinuxHostJson "$testRoot/cold/generation.json" @{generation='cold-control';nonce=('a'*32);volumes=@{source='cold-source';prep='cold-prep';target='cold-target'};packagesVolume='packages-control';rustImage=$script:LinuxHostRustImage}
  $state="$testRoot/state.json"
  [IO.File]::WriteAllText("$testRoot/explicit-review.json", '{"reviewed":true,"reviewer":"root control"}')
  Invoke-LinuxHostRuntime 'new' $state $repository "$testRoot/evidence" "$testRoot/inputs" "$testRoot/cold/generation.json" 'assets-control' 'packages-control'
  foreach($testStageName in @('storage-probe','storage-reopen','prepare','fixture')){Invoke-LinuxHostRuntime $testStageName $state}
  $saved=Get-Content -LiteralPath $state -Raw|ConvertFrom-Json -AsHashtable
  if($saved.toolsManifestName -cne 'tools-manifest3.stdout.log' -or
     @($script:RuntimeFakeCalls|Where-Object {$_.name -ceq 'prepare-create'})[0].argv -cnotcontains '/runtime/tools-manifest3.stdout.log'){throw 'tools manifest was not durably selected'}
  $before=$script:RuntimeFakeCalls.Count
  Reject-Runtime {Invoke-LinuxHostRuntime -RequestedStage bundle -StateFile $state -ManifestName tools-manifest2.stdout.log} 'changed frozen manifest selection'
  Reject-Runtime {Assert-LinuxHostRuntimeLeaf '../manifest.json'} 'manifest path traversal'
  if($script:RuntimeFakeCalls.Count -ne $before){throw 'changed manifest selection reached Docker'}
  $before=$script:RuntimeFakeCalls.Count
  Reject-Runtime {Invoke-LinuxHostRuntime 'pair' $state} 'pair before admission'
  if($script:RuntimeFakeCalls.Count -ne $before){throw 'incomplete prerequisite reached Docker'}
  Invoke-LinuxHostRuntime -RequestedStage bundle -StateFile $state -Review "$testRoot/explicit-review.json"
  foreach($testStageName in @('admission','pair','export','verify','replay')){Invoke-LinuxHostRuntime $testStageName $state}
  $bundleCall=@($script:RuntimeFakeCalls|Where-Object {$_.name -ceq 'bundle-create'})[0].argv
  if($bundleCall -cnotcontains 'bundle-reviewed' -or $bundleCall -cnotcontains '/review-input.json' -or
     @($bundleCall|Where-Object {$_ -like 'type=bind,*target=/review-input.json,readonly'}).Count -ne 1){throw 'explicit root review was not passed verbatim readonly'}
  $pairCall=@($script:RuntimeFakeCalls|Where-Object {$_.name -ceq 'pair-create'})[0].argv
  if(@($pairCall|Where-Object {$_ -like 'type=volume,*target=/work' }).Count -ne 1){throw 'pair needs its sole writable work mount'}
  $exportCall=@($script:RuntimeFakeCalls|Where-Object {$_.name -ceq 'export-create'})[0].argv
  if($exportCall -cnotcontains '--export' -or $exportCall -cnotcontains '/out/export' -or $exportCall -cnotcontains '/cold-receipts=/cold-receipts' -or
     @($exportCall|Where-Object {$_ -like 'type=volume,*target=/work,readonly'}).Count -ne 1){throw 'actual portable export must have output, receipt mapping and readonly work'}
  foreach($testStageName in @('verify','replay')){
    $call=@($script:RuntimeFakeCalls|Where-Object {$_.name -ceq "$testStageName-create"})[0].argv
    if($call -cnotcontains '/sealed/source/scripts/boot/linux_host_export.py' -or $call -cnotcontains "/results/$testStageName-result.json" -or
       @($call|Where-Object {$_ -like 'type=volume,*target=/sealed,readonly,volume-subpath=export'}).Count -ne 1 -or
       @($call|Where-Object {$_ -like 'type=volume,*target=/repo*'}).Count){throw 'independent sealed replay mounts differ'}
  }
  if(@($script:RuntimeFakeCalls|Where-Object {$_.name -ceq 'pair-run' -and $_.timeout -eq 1950}).Count -ne 1){throw 'pair outer1950 missing'}
  $before=$script:RuntimeFakeCalls.Count
  Reject-Runtime {Invoke-LinuxHostRuntime 'pair' $state} 'second pair attempt'
  if($script:RuntimeFakeCalls.Count -ne $before){throw 'second stage attempt reached Docker'}
  $count++
  $script:RuntimeWrongImage=$true
  $before=$script:RuntimeFakeCalls.Count
  Reject-Runtime {Invoke-LinuxHostRuntime 'new' "$testRoot/wrong-image-state.json" $repository "$testRoot/wrong-image-evidence" "$testRoot/inputs" "$testRoot/cold/generation.json" 'assets-control' 'packages-control'} 'changed tools image'
  $newCalls=@($script:RuntimeFakeCalls|Select-Object -Skip $before)
  if($newCalls.Count -ne 1 -or $newCalls[0].name -cne 'new-image-inspect'){throw 'wrong tools image reached resource creation'}
  $script:RuntimeWrongImage=$false
  Invoke-LinuxHostRuntime 'new' "$testRoot/fail-state.json" $repository "$testRoot/fail-evidence" "$testRoot/inputs" "$testRoot/cold/generation.json" 'assets-control' 'packages-control'
  $script:RuntimeFailure='storage-probe-run';$script:RuntimeCleanupFailure=$true
  $errorRecord=$null
  try{Invoke-LinuxHostRuntime 'probe' "$testRoot/fail-state.json"}catch{$errorRecord=$_}
  if(!$errorRecord -or $errorRecord.Exception.Message -notlike '*EXPECTED_PRIMARY_FAILURE*'){throw 'cleanup masked primary stage failure'}
  $result=Get-Content -LiteralPath "$testRoot/fail-evidence/storage-probe-result.json" -Raw|ConvertFrom-Json -AsHashtable
  if($result.status -cne 'failed' -or $result.cleanupErrors.Count -ne 1 -or $result.cleanupErrors[0] -notlike '*EXPECTED_CLEANUP_FAILURE*'){throw 'cleanup error not retained separately'}
  $count++
  $script:RuntimeCleanupFailure=$false
  $failedState=Get-Content -LiteralPath "$testRoot/fail-state.json" -Raw|ConvertFrom-Json -AsHashtable
  $lastId=@($script:RuntimeFakeCalls|Where-Object {$_.name -ceq 'storage-probe-run'})[-1].argv[2]
  $cleanupContext=@{generation=$failedState.generation;nonce=$failedState.nonce;docker='';image=$script:LinuxHostToolsImage;owned=@(@{cid=$lastId;name='control'})}
  $errors=[Collections.Generic.List[string]]::new()
  Stop-LinuxHostBuildContainers $cleanupContext $errors
  if($errors.Count -or $script:RuntimeFakeInspections[$lastId].State.Running -or
     @($script:RuntimeFakeCalls|Where-Object {$_.name -ceq 'cleanup-after-control'}).Count -ne 1){throw 'owned cleanup did not reinspect stopped container'}
  $count++
  $script:RuntimeFakeInspections[$lastId].State.Running=$true
  $script:RuntimeFakeInspections[$lastId].Config.Labels['org.mingos.nonce']='changed'
  $before=$script:RuntimeFakeCalls.Count
  Stop-LinuxHostBuildContainers $cleanupContext $errors
  if($errors.Count -ne 1 -or @($script:RuntimeFakeCalls|Select-Object -Skip $before|Where-Object {$_.argv[0] -ceq 'kill'}).Count){throw 'changed ownership was killed'}
  $count++
}finally{
  Set-Item Function:Invoke-LinuxHostDocker $originalDocker
  $resolved=[IO.Path]::GetFullPath($testRoot);$parent=[IO.Path]::GetFullPath((Join-Path $repository 'artifacts')).TrimEnd('\')+'\'
  if(!$resolved.StartsWith($parent,[StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetFileName($resolved) -notlike 'public-runtime-control-*'){throw 'unsafe test cleanup root'}
  Remove-Item -LiteralPath $resolved -Recurse -Force
}
Write-Output "Public runtime coordinator controls passed: $count (fake Docker only)"
