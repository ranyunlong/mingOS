#requires -Version 7.3
<#
.SYNOPSIS
Run one stage of a frozen Linux runtime generation, including portable replay.
.DESCRIPTION
Run new, storage-probe, storage-reopen, prepare and fixture in order. Inspect the
exported review-base.json and explicitly supply a reviewed JSON using ReviewPath
to bundle, or install that exact review in /prep yourself. This command never
approves a review. Continue with bundle, admission, pair, export, verify, replay.
Each stage is single-use, including failures. Every stage retains Docker captures
and its own attempt/result. RuntimeInputs and AssetsVolume must already be
verified; this command does not download tools or build geometry/probe assets.
ColdStatePath is a completed cold generation's generation.json. Python verifies
the original successful seven-command build before admitting runtime inputs.
Portable export stays in its dedicated named volume; verify/replay mount only
that sealed export read-only and a separate fresh result volume.
#>
[CmdletBinding()]
param(
  [Alias('Stage')][string]$RuntimeStage='',
  [string]$StatePath='',
  [string]$Repo='',
  [string]$EvidenceRoot='',
  [string]$RuntimeInputs='',
  [string]$ColdStatePath='',
  [string]$AssetsVolume='',
  [string]$PackagesVolume='',
  [string]$ToolsManifestName='',
  [string]$ReviewPath='',
  [string]$DockerExecutable=''
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
# The legacy helper defines $Stage. Keep the caller's stage in $RuntimeStage.
$runtimeArguments=@{RuntimeStage=$RuntimeStage;StatePath=$StatePath;Repo=$Repo;EvidenceRoot=$EvidenceRoot;RuntimeInputs=$RuntimeInputs
  ColdStatePath=$ColdStatePath;AssetsVolume=$AssetsVolume;PackagesVolume=$PackagesVolume;ToolsManifestName=$ToolsManifestName;ReviewPath=$ReviewPath;DockerExecutable=$DockerExecutable}
. "$PSScriptRoot/new-linux-host-build.ps1"
$script:LinuxHostToolsImage='sha256:a085efa9e8b4958b1943ee87857313f3d2666f696f730f242daef643347b0091'
$script:LinuxHostRuntimeTmpfs=@{'/tmp'='rw,nosuid,nodev,size=512m,mode=1777'
  '/run/mingos-runtime'='rw,nosuid,nodev,size=3g,mode=700,uid=65534,gid=65534'}
$script:LinuxHostRuntimeStages=@('new','storage-probe','storage-reopen','prepare','fixture','bundle','admission','pair','export','verify','replay')

function Resolve-LinuxHostRuntimeNewFile([string]$Path) {
  if($Path -notmatch '^[A-Za-z]:[\\/]' -or $Path -match '[,\x00-\x1f]' -or
     @($Path -split '[\\/]'|Where-Object {$_ -in @('.','..')}).Count){throw 'new absolute canonical state file required'}
  $full=[IO.Path]::GetFullPath($Path)
  if(Test-Path -LiteralPath $full){throw 'new state file required'}
  [void](Resolve-LinuxHostBuildPath ([IO.Path]::GetDirectoryName($full)) Directory)
  if([IO.Path]::GetExtension($full) -ine '.json'){throw 'JSON state file required'}
  return $full
}

function Assert-LinuxHostRuntimeState($State) {
  if($State.schemaVersion -ne 1 -or $State.generation -cnotmatch '^persistent-uninstall-[0-9]{8}-runtime-[a-f0-9]{16}$' -or
     $State.nonce -cnotmatch '^[a-f0-9]{32}$' -or $State.toolsImage -cne $script:LinuxHostToolsImage -or
     $State.finalSystemAccepted -ne $false){throw 'runtime state identity differs'}
  foreach($role in @('source','prep','work','export')){
    if($State.volumes[$role] -cne "$($State.generation)-$role"){throw 'runtime owned volume identity differs'}
  }
  foreach($name in @($State.packages,$State.assets,$State.coldVolumes.source,$State.coldVolumes.prep,$State.coldVolumes.target)){
    Assert-LinuxHostBuildName $name
  }
  foreach($name in @('repo','evidence','runtimeInputs')){[void](Resolve-LinuxHostBuildPath $State[$name] Directory)}
  if($State.coldReceipts){[void](Resolve-LinuxHostBuildPath $State.coldReceipts Directory)}
  Assert-LinuxHostRuntimeLeaf $State.toolsManifestName
}

function Assert-LinuxHostRuntimeLeaf([string]$Name) {
  if($Name -cnotmatch '^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$'){throw 'canonical tools manifest filename required'}
}

function New-LinuxHostRuntimeVolume($Context,[string]$Name,[string]$Label) {
  Assert-LinuxHostBuildName $Name
  $existing=@((Invoke-LinuxHostDocker @('volume','ls','--format','{{.Name}}') "$Label-existing-volumes" 180 $Context.docker)-split '\r?\n')
  if($Name -cin $existing){throw 'runtime output volume must be new'}
  [void](Invoke-LinuxHostDocker @('volume','create','--label',"org.mingos.generation=$($Context.generation)",'--label',"org.mingos.nonce=$($Context.nonce)",$Name) "$Label-volume-create" 180 $Context.docker)
  Assert-LinuxHostRuntimeVolume $Context $Name $Label
}

function Assert-LinuxHostRuntimeVolume($Context,[string]$Name,[string]$Label) {
  $v=@(ConvertFrom-Json -AsHashtable (Invoke-LinuxHostDocker @('volume','inspect',$Name) "$Label-volume-inspect" 180 $Context.docker))
  if($v.Count -ne 1 -or $v[0].Name -cne $Name -or $v[0].Driver -cne 'local' -or
     $v[0].Labels['org.mingos.generation'] -cne $Context.generation -or $v[0].Labels['org.mingos.nonce'] -cne $Context.nonce){throw 'runtime volume ownership differs'}
}

function Invoke-LinuxHostRuntimeContainer($Context,[string]$Label,[object[]]$Mounts,[string[]]$Command,[bool]$Initializer=$false) {
  $user=if($Initializer){'0:0'}else{'65534:65534'}
  $argv=@('create','--pull','never','--init','--name',"$($Context.generation)-$Label",'--label',"org.mingos.generation=$($Context.generation)",
    '--label',"org.mingos.nonce=$($Context.nonce)",'--network','none','--read-only','--user',$user,'--cpus','2','--memory','4g','--pids-limit','256',
    '--cap-drop','ALL','--security-opt','no-new-privileges')
  if($Initializer){$argv+=@('--cap-add','CHOWN')}
  else{foreach($name in @($script:LinuxHostRuntimeTmpfs.Keys|Sort-Object)){$argv+=@('--tmpfs',"${name}:$($script:LinuxHostRuntimeTmpfs[$name])")}}
  foreach($mount in $Mounts){
    $text="type=$($mount.type),source=$($mount.source),target=$($mount.target)"
    if($mount.readonly){$text+=',readonly'}
    if($mount.ContainsKey('subpath')){$text+=",volume-subpath=$($mount.subpath)"}
    $argv+=@('--mount',$text)
  }
  $cid=(Invoke-LinuxHostDocker ($argv+@($script:LinuxHostToolsImage)+$Command) "$Label-create" 180 $Context.docker).Trim()
  if($cid -cnotmatch '^[a-f0-9]{64}$'){throw 'exact created runtime container ID required'}
  $Context.owned.Add(@{cid=$cid;name=$Label})
  foreach($phase in @('before','after')){
    if($phase -ceq 'after'){[void](Invoke-LinuxHostDocker @('start','-a',$cid) "$Label-run" 1950 $Context.docker)}
    $v=@(ConvertFrom-Json -AsHashtable (Invoke-LinuxHostDocker @('inspect',$cid) "$Label-inspect-$phase" 180 $Context.docker))
    if($v.Count -ne 1){throw 'one exact runtime container inspection required'}
    Assert-LinuxHostBuildInspection $v[0] $cid $Context.generation $Context.nonce $Mounts $user $Initializer $script:LinuxHostToolsImage $script:LinuxHostRuntimeTmpfs
    if($phase -ceq 'after' -and ($v[0].State.Running -or $v[0].State.ExitCode -ne 0 -or $v[0].State.OOMKilled)){throw 'runtime stage did not complete successfully'}
  }
  $Context.containers.Add(@{label=$Label;cid=$cid;command=$Command;mounts=$Mounts})
  return $cid
}

function Get-LinuxHostRuntimeMounts($State) {
  $mounts=@(@{type='volume';source=$State.volumes.source;target='/repo';readonly=$true;subpath='repo'},
    @{type='volume';source=$State.volumes.prep;target='/prep';readonly=$false},
    @{type='volume';source=$State.coldVolumes.source;target='/coldrepo';readonly=$true;subpath='repo'},
    @{type='volume';source=$State.coldVolumes.prep;target='/coldbuild';readonly=$true},
    @{type='volume';source=$State.coldVolumes.target;target='/target';readonly=$true},
    @{type='volume';source=$State.packages;target='/packages';readonly=$true;subpath='packages'},
    @{type='volume';source=$State.assets;target='/assets';readonly=$true},
    @{type='bind';source=$State.runtimeInputs;target='/runtime';readonly=$true})
  if($State.coldReceipts){$mounts+=@{type='bind';source=$State.coldReceipts;target='/cold-receipts';readonly=$true}}
  return $mounts
}

function Invoke-LinuxHostRuntime([string]$RequestedStage,[string]$StateFile,[string]$Repository='',
    [string]$Evidence='',[string]$Inputs='',[string]$ColdState='',[string]$Assets='',[string]$Packages='',
    [string]$Review='',[string]$Docker='',[string]$ManifestName='') {
  $stageName=switch -CaseSensitive($RequestedStage){'probe'{'storage-probe'}'reopen'{'storage-reopen'}default{$RequestedStage}}
  if($stageName -cnotin $script:LinuxHostRuntimeStages){throw 'explicit supported runtime stage required'}
  if($Review -and $stageName -cne 'bundle'){throw 'explicit review input is accepted only by bundle'}
  if($stageName -ceq 'new'){
    if(!$ManifestName){$ManifestName='tools-manifest3.stdout.log'}
    Assert-LinuxHostRuntimeLeaf $ManifestName
    if(!$Repository){$Repository=(Resolve-Path "$PSScriptRoot/../..").Path}
    $Repository=Resolve-LinuxHostBuildPath $Repository Directory
    $Inputs=Resolve-LinuxHostBuildPath $Inputs Directory
    $ColdState=Resolve-LinuxHostBuildPath $ColdState File
    $StateFile=Resolve-LinuxHostRuntimeNewFile $StateFile
    $cold=Get-Content -LiteralPath $ColdState -Raw|ConvertFrom-Json -AsHashtable
    if(!$cold.ContainsKey('profile') -or $cold.profile -cne 'persistent-uninstall-linux-host'){throw 'completed uninstall cold profile required'}
    if($cold.ContainsKey('rustImage') -and $cold.rustImage -cne $script:LinuxHostRustImage){throw 'fixed original Rust image required'}
    foreach($name in @('source','prep','target')){Assert-LinuxHostBuildName $cold.volumes[$name]}
    if(!$Packages){$Packages=$cold.packagesVolume}
    Assert-LinuxHostBuildName $Packages
    Assert-LinuxHostBuildName $Assets
    if($Packages -cne $cold.packagesVolume){throw 'original cold package volume required'}
    foreach($name in @('snapshot_linux_host_uninstall_runtime.py','linux_host_uninstall_runtime.py','linux_host_uninstall_pair.py','linux_host_uninstall_export.py')){
      [void](Resolve-LinuxHostBuildPath (Join-Path $Repository "scripts/boot/$name") File)
    }
    foreach($name in @('tools-image.stdout.log',$ManifestName)){[void](Resolve-LinuxHostBuildPath (Join-Path $Inputs $name) File)}
    $image=@(Get-Content -LiteralPath (Join-Path $Inputs 'tools-image.stdout.log') -Raw|ConvertFrom-Json -AsHashtable)
    if($image.Count -ne 1 -or $image[0].Id -cne $script:LinuxHostToolsImage -or !$image[0].RootFS.Layers){throw 'verified fixed tools image receipt required'}
    $generation='persistent-uninstall-'+[DateTime]::UtcNow.ToString('yyyyMMdd')+'-runtime-'+[Guid]::NewGuid().ToString('N').Substring(0,16)
    if(!$Evidence){$Evidence=Join-Path $Repository "artifacts/$generation"}
    $Evidence=Resolve-LinuxHostBuildPath $Evidence NewDirectory
    $allowed=[IO.Path]::GetFullPath((Join-Path $Repository 'artifacts')).TrimEnd('\')+'\'
    if(!$Evidence.StartsWith($allowed,[StringComparison]::OrdinalIgnoreCase)){throw 'new runtime evidence under repo/artifacts required'}
    if($Docker){$Docker=Resolve-LinuxHostBuildPath $Docker File}
    [void](New-Item -ItemType Directory -Path $Evidence)
    $receipts=Join-Path ([IO.Path]::GetDirectoryName($ColdState)) 'receipts'
    if(!(Test-Path -LiteralPath $receipts -PathType Container)){$receipts=''}
    $state=@{schemaVersion=1;generation=$generation;nonce=[Guid]::NewGuid().ToString('N');repo=$Repository;evidence=$Evidence
      runtimeInputs=$Inputs;coldState=$ColdState;coldStateSha256=(Get-FileHash -LiteralPath $ColdState -Algorithm SHA256).Hash.ToLowerInvariant()
      coldVolumes=$cold.volumes;coldReceipts=$receipts;packages=$Packages;assets=$Assets;toolsImage=$script:LinuxHostToolsImage;toolsManifestName=$ManifestName;docker=$Docker
      volumes=@{source="$generation-source";prep="$generation-prep";work="$generation-work";export="$generation-export"};finalSystemAccepted=$false}
    Write-LinuxHostJson $StateFile $state
    Write-LinuxHostJson (Join-Path $Evidence 'generation.json') $state
  }else{
    $StateFile=Resolve-LinuxHostBuildPath $StateFile File
    $state=Get-Content -LiteralPath $StateFile -Raw|ConvertFrom-Json -AsHashtable
    Assert-LinuxHostRuntimeState $state
    foreach($pair in @(@($Repository,$state.repo),@($Evidence,$state.evidence),@($Inputs,$state.runtimeInputs),@($ColdState,$state.coldState),@($Assets,$state.assets),@($Packages,$state.packages),@($Docker,$state.docker),@($ManifestName,$state.toolsManifestName))){
      if($pair[0] -and $pair[0] -cne $pair[1]){throw 'runtime parameters differ from immutable state'}
    }
    if((Get-FileHash -LiteralPath $state.coldState -Algorithm SHA256).Hash.ToLowerInvariant() -cne $state.coldStateSha256){throw 'original cold generation state changed'}
  }
  $digest=(Get-FileHash -LiteralPath $StateFile -Algorithm SHA256).Hash.ToLowerInvariant()
  $script:LinuxHostEvidence=$state.evidence
  $attempt=Join-Path $state.evidence "$stageName-attempt.json"
  if(Test-Path -LiteralPath $attempt){throw 'runtime stage already attempted; preserve generation'}
  foreach($previous in $script:LinuxHostRuntimeStages){
    $started=Join-Path $state.evidence "$previous-attempt.json"
    if(!(Test-Path -LiteralPath $started)){continue}
    $resultFile=Join-Path $state.evidence "$previous-result.json"
    if(!(Test-Path -LiteralPath $resultFile)){throw 'previous runtime stage incomplete; preserve generation'}
    $record=Get-Content -LiteralPath $resultFile -Raw|ConvertFrom-Json -AsHashtable
    if($record.status -cne 'passed' -or $record.stateSha256 -cne $digest){throw 'previous runtime stage failed or state changed'}
  }
  $index=[Array]::IndexOf($script:LinuxHostRuntimeStages,$stageName)
  $last=if($stageName -cin @('verify','replay')){8}else{$index-1}
  for($i=0;$i -le $last;$i++){
    if(!(Test-Path -LiteralPath (Join-Path $state.evidence "$($script:LinuxHostRuntimeStages[$i])-result.json"))){throw 'required predecessor runtime stage missing'}
  }
  if($Review){$Review=Resolve-LinuxHostBuildPath $Review File}
  Write-LinuxHostJson $attempt @{stage=$stageName;generation=$state.generation;stateSha256=$digest;startedAt=[DateTime]::UtcNow.ToString('o');retryAllowed=$false;finalSystemAccepted=$false}
  $context=@{generation=$state.generation;nonce=$state.nonce;evidence=$state.evidence;docker=$state.docker;image=$script:LinuxHostToolsImage
    owned=[Collections.Generic.List[hashtable]]::new();containers=[Collections.Generic.List[object]]::new()}
  try{
    $image=@(ConvertFrom-Json -AsHashtable (Invoke-LinuxHostDocker @('image','inspect',$script:LinuxHostToolsImage) "$stageName-image-inspect" 180 $context.docker))
    if($image.Count -ne 1 -or $image[0].Id -cne $script:LinuxHostToolsImage -or !$image[0].RootFS.Layers){throw 'actual fixed tools image differs'}
    $copies=@()
    if($stageName -ceq 'new'){
      # Inspect before create: Docker otherwise auto-creates a missing named
      # input volume, which would conceal a mistyped frozen input name.
      $inputVolumes=@{coldSource=$state.coldVolumes.source;coldBuild=$state.coldVolumes.prep;coldTarget=$state.coldVolumes.target
        packages=$state.packages;assets=$state.assets}
      foreach($role in @($inputVolumes.Keys|Sort-Object)){
        $v=@(ConvertFrom-Json -AsHashtable (Invoke-LinuxHostDocker @('volume','inspect',$inputVolumes[$role]) "new-$role-input-volume-inspect" 180 $context.docker))
        if($v.Count -ne 1 -or $v[0].Name -cne $inputVolumes[$role] -or $v[0].Driver -cne 'local'){throw 'existing verified runtime input volume required'}
      }
      foreach($role in @('source','prep','work')){New-LinuxHostRuntimeVolume $context $state.volumes[$role] "new-$role"}
      $mounts=@(@{type='bind';source=$state.repo;target='/checkout';readonly=$true},
        @{type='volume';source=$state.packages;target='/packages';readonly=$true;subpath='packages'},
        @{type='volume';source=$state.volumes.source;target='/source';readonly=$false},
        @{type='volume';source=$state.volumes.prep;target='/prep';readonly=$false},
        @{type='volume';source=$state.volumes.work;target='/work';readonly=$false})
      $command=@('python3','-B','/checkout/scripts/boot/snapshot_linux_host_uninstall_runtime.py','--action','initialize')
      $copies=@(@{source='/source/runtime-source-snapshot.json';destination='runtime-source-snapshot.json'})
      $cid=Invoke-LinuxHostRuntimeContainer $context 'new' $mounts $command $true
    }elseif($stageName -cin @('verify','replay')){
      Assert-LinuxHostRuntimeVolume $context $state.volumes.export "$stageName-sealed"
      $resultsVolume="$($state.generation)-$stageName-results"
      New-LinuxHostRuntimeVolume $context $resultsVolume $stageName
      $initMounts=@(@{type='volume';source=$state.volumes.source;target='/repo';readonly=$true;subpath='repo'},
        @{type='volume';source=$resultsVolume;target='/results';readonly=$false})
      [void](Invoke-LinuxHostRuntimeContainer $context "$stageName-output-init" $initMounts @('python3','-B','/repo/scripts/boot/snapshot_linux_host_uninstall_runtime.py','--action','initialize-output','--output-volume','/results') $true)
      $mounts=@(@{type='volume';source=$state.volumes.export;target='/sealed';readonly=$true;subpath='export'},
        @{type='volume';source=$resultsVolume;target='/results';readonly=$false})
      $command=@('python3','-I','-B','/sealed/source/scripts/boot/linux_host_uninstall_export.py',$stageName,'--export','/sealed','--output',"/results/$stageName-result.json")
      $copies=@(@{source="/results/$stageName-result.json";destination="$stageName-portable-result.json"})
      $cid=Invoke-LinuxHostRuntimeContainer $context $stageName $mounts $command
    }else{
      foreach($role in @('source','prep','work')){Assert-LinuxHostRuntimeVolume $context $state.volumes[$role] "$stageName-$role"}
      $mounts=@(Get-LinuxHostRuntimeMounts $state)
      if($stageName -cin @('storage-probe','storage-reopen')){
        $command=@('python3','-B','/repo/scripts/boot/linux_host_freeze.py','--stage',$stageName,'--root','/prep')
        $copies=@(@{source="/prep/$stageName.json";destination="$stageName-storage.json"})
      }elseif($stageName -ceq 'prepare'){
        $command=@('python3','-B','/repo/scripts/boot/linux_host_uninstall_runtime.py','--stage','prepare','--root','/prep','--repo','/repo',
          '--build-root','/coldbuild','--build-freeze','/coldbuild/freeze.json','--target','/target','--packages-root','/packages',
          '--checker-dir','/assets/checker','--input1','/assets/input-boot1.json','--input2','/assets/input-boot2.json',
          '--tools-receipt','/runtime/tools-image.stdout.log','--tools-manifest',"/runtime/$($state.toolsManifestName)",'--probe-dir','/assets/probes')
        $copies=@(@{source='/prep/runtime-freeze.json';destination='runtime-freeze.json'})
      }elseif($stageName -ceq 'pair'){
        $mounts+=@{type='volume';source=$state.volumes.work;target='/work';readonly=$false}
        $command=@('python3','-B','/repo/scripts/boot/linux_host_uninstall_pair.py','--root','/prep','--repo','/repo')
        $copies=@(@{source='/prep/stages/pair/result.json';destination='pair-stage.json'},@{source='/work/mingos-boot-infra-20261025/cold-pair/result.json';destination='pair-boots.json'})
      }elseif($stageName -ceq 'export'){
        New-LinuxHostRuntimeVolume $context $state.volumes.export 'export-output'
        $initMounts=@(@{type='volume';source=$state.volumes.source;target='/repo';readonly=$true;subpath='repo'},
          @{type='volume';source=$state.volumes.export;target='/out';readonly=$false})
        [void](Invoke-LinuxHostRuntimeContainer $context 'export-output-init' $initMounts @('python3','-B','/repo/scripts/boot/snapshot_linux_host_uninstall_runtime.py','--action','initialize-output','--output-volume','/out') $true)
        $mounts+=@{type='volume';source=$state.volumes.work;target='/work';readonly=$true}
        $mounts+=@{type='volume';source=$state.volumes.export;target='/out';readonly=$false}
        $command=@('python3','-B','/repo/scripts/boot/linux_host_uninstall_export.py','create','--root','/prep','--repo','/repo','--cold-repo','/coldrepo',
          '--assets-root','/assets','--tools-root','/runtime','--export','/out/export')
        if($state.coldReceipts){$command+=@('--receipt-root','/cold-receipts=/cold-receipts')}
        $copies=@(@{source='/out/export/linux-export-manifest.json';destination='linux-export-manifest.json'})
      }elseif($stageName -ceq 'bundle' -and $Review){
        $mounts+=@{type='bind';source=$Review;target='/review-input.json';readonly=$true}
        $command=@('python3','-B','/repo/scripts/boot/snapshot_linux_host_uninstall_runtime.py','--action','bundle-reviewed','--root','/prep','--repo','/repo','--review','/review-input.json')
      }else{
        $command=@('python3','-B','/repo/scripts/boot/linux_host_uninstall_runtime.py','--stage',$stageName,'--root','/prep','--repo','/repo')
      }
      if($stageName -ceq 'fixture'){$copies=@(@{source='/prep/build/review-base.json';destination='review-base.json'},@{source='/prep/fixture-verification.json';destination='fixture-verification.json'})}
      if($stageName -ceq 'admission'){$copies=@(@{source='/prep/stages/admission/result.json';destination='admission-stage.json'},@{source='/prep/stages/admission/pair-boundary-result.json';destination='pair-boundary-result.json'})}
      if($stageName -ceq 'bundle'){$copies=@(@{source='/prep/bundle/bundle.json';destination='bundle.json'})}
      $cid=Invoke-LinuxHostRuntimeContainer $context $stageName $mounts $command
    }
    $n=0
    foreach($copy in $copies){
      [void](Invoke-LinuxHostDocker @('cp',"${cid}:$($copy.source)",(Join-Path $state.evidence $copy.destination)) "$stageName-copy-$n" 180 $context.docker)
      $n++
    }
    if((Get-FileHash -LiteralPath $StateFile -Algorithm SHA256).Hash.ToLowerInvariant() -cne $digest){throw 'runtime state changed during stage'}
    Write-LinuxHostJson (Join-Path $state.evidence "$stageName-result.json") @{status='passed';stage=$stageName;generation=$state.generation;stateSha256=$digest
      containers=@($context.containers.ToArray());exportVolume=$state.volumes.export;formalQualification=$false;finalSystemAccepted=$false}
    Write-Output "Runtime $stageName evidence: $($state.evidence)"
  }catch{
    $failure=$_;$cleanup=[Collections.Generic.List[string]]::new()
    Stop-LinuxHostBuildContainers $context $cleanup
    Write-LinuxHostJson (Join-Path $state.evidence "$stageName-result.json") @{status='failed';stage=$stageName;generation=$state.generation;stateSha256=$digest
      error=$failure.Exception.ToString();cleanupErrors=@($cleanup.ToArray());containers=@($context.containers.ToArray());formalQualification=$false;finalSystemAccepted=$false}
    throw $failure
  }
}

if($MyInvocation.InvocationName -ne '.'){
  Invoke-LinuxHostRuntime $runtimeArguments.RuntimeStage $runtimeArguments.StatePath $runtimeArguments.Repo $runtimeArguments.EvidenceRoot `
    $runtimeArguments.RuntimeInputs $runtimeArguments.ColdStatePath $runtimeArguments.AssetsVolume $runtimeArguments.PackagesVolume `
    $runtimeArguments.ReviewPath $runtimeArguments.DockerExecutable $runtimeArguments.ToolsManifestName
}
