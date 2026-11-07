#requires -Version 7.3
<#
.SYNOPSIS
Create one independent Linux cold-build generation, optionally starting its build.
.DESCRIPTION
RuntimeInputs must already contain verified rust-image.json, node and
toolchain-observation.json. PackagesVolume must contain the independently
verified packages under packages/ (six for update, four for uninstall);
CargoVolume must contain the verified offline
Cargo home under cargo/. This command does not acquire or repair those inputs.
Every Docker command retains argv, output and result captures in a new evidence
directory. Failed volumes and containers are preserved; only this generation's
running containers may be stopped on failure. StartBuild starts asynchronously;
build-started.json identifies the container to inspect for its final outcome.
#>
[CmdletBinding()]
param(
  [string]$Repo = (Resolve-Path "$PSScriptRoot/../..").Path,
  [string]$EvidenceRoot = '',
  [string]$RuntimeInputs = '',
  [string]$PackagesVolume = '',
  [string]$CargoVolume = '',
  [string]$BackingVhd = '',
  [string]$DockerExecutable = '',
  [ValidateSet('persistent-update-linux-host','persistent-uninstall-linux-host')]
  [string]$ColdProfile = 'persistent-update-linux-host',
  [switch]$StartBuild
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. "$PSScriptRoot/linux-host-qualification.ps1" -Repo $Repo

function Assert-LinuxHostBuildName([string]$Name) {
  if ($Name -cnotmatch '^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,200}$') {
    throw 'canonical named Docker volume required'
  }
}

function Resolve-LinuxHostBuildPath([string]$Path, [ValidateSet('Directory','File','NewDirectory')][string]$Kind) {
  if ($Path -notmatch '^[A-Za-z]:[\\/]' -or $Path -match '[,\x00-\x1f]' -or
      @($Path -split '[\\/]' | Where-Object { $_ -in @('.', '..') }).Count) {
    throw 'absolute local Windows path without traversal or mount delimiters required'
  }
  $full = [IO.Path]::GetFullPath($Path)
  if ($full -eq [IO.Path]::GetPathRoot($full)) { throw 'non-root input path required' }
  $full = $full.TrimEnd('\','/')
  $current = if ($Kind -eq 'NewDirectory') { [IO.Path]::GetDirectoryName($full) } else { $full }
  if ($Kind -eq 'NewDirectory' -and (Test-Path -LiteralPath $full)) { throw 'new evidence directory required' }
  $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
  if (($Kind -eq 'Directory' -and !$item.PSIsContainer) -or ($Kind -eq 'File' -and $item.PSIsContainer)) {
    throw 'input path type differs'
  }
  while ($current) {
    $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'reparse point input path forbidden' }
    $parent = [IO.Path]::GetDirectoryName($current)
    if ($parent -eq $current) { break }
    $current = $parent
  }
  return $full
}

function Assert-LinuxHostResourceOwned($Inspection, [string]$Cid, [string]$Generation, [string]$Nonce, [string]$Image) {
  if ($Cid -cnotmatch '^[a-f0-9]{64}$' -or $Inspection.Id -cne $Cid -or $Inspection.Image -cne $Image -or
      $Inspection.Config.Labels['org.mingos.generation'] -cne $Generation -or
      $Inspection.Config.Labels['org.mingos.nonce'] -cne $Nonce) { throw 'owned container identity differs' }
}

function Assert-LinuxHostBuildInspection($Inspection, [string]$Cid, [string]$Generation,
                                        [string]$Nonce, [object[]]$Mounts, [string]$User, [bool]$Initializer,
                                        [string]$Image=$script:LinuxHostRustImage,
                                        [hashtable]$Tmpfs=@{'/tmp'='rw,nosuid,nodev,size=512m,mode=1777'}) {
  Assert-LinuxHostResourceOwned $Inspection $Cid $Generation $Nonce $Image
  $h = $Inspection.HostConfig
  if ($Inspection.Config.User -cne $User -or $h.NetworkMode -cne 'none' -or !$h.ReadonlyRootfs -or $h.Privileged -or
      $h.NanoCpus -ne 2000000000 -or $h.Memory -ne 4294967296 -or $h.PidsLimit -ne 256 -or
      @($h.CapDrop).Count -ne 1 -or $h.CapDrop[0] -cne 'ALL' -or
      @($h.SecurityOpt).Count -ne 1 -or $h.SecurityOpt[0] -cne 'no-new-privileges') { throw 'actual isolation differs' }
  $caps = @($h['CapAdd'] | Where-Object { $null -ne $_ })
  if (($Initializer -and ($caps.Count -ne 1 -or $caps[0] -cnotin @('CHOWN','CAP_CHOWN'))) -or
      (!$Initializer -and $caps.Count)) { throw 'actual capabilities differ' }
  if (@($h['Binds'] | Where-Object { $null -ne $_ }).Count -or
      @($h['VolumesFrom'] | Where-Object { $null -ne $_ }).Count) { throw 'unrequested legacy mounts' }
  if ($Initializer) { if ($h['Tmpfs']) { throw 'initializer tmpfs differs' } }
  else {
    if ($h['Tmpfs'].Count -ne $Tmpfs.Count) { throw 'runtime tmpfs count differs' }
    foreach ($name in $Tmpfs.Keys) { if ($h['Tmpfs'][$name] -cne $Tmpfs[$name]) { throw 'runtime tmpfs differs' } }
  }
  if (@($Inspection.Mounts).Count -ne $Mounts.Count -or @($h.Mounts).Count -ne $Mounts.Count) { throw 'actual mount count differs' }
  foreach ($mount in $Mounts) {
    $sources = @($mount.source)
    if ($mount.type -ceq 'bind' -and $mount.source -cmatch '^[A-Za-z]:\\') {
      $sources += '/run/desktop/mnt/host/' + $mount.source.Substring(0,1).ToLowerInvariant() + '/' + $mount.source.Substring(3).Replace('\','/')
    }
    $actual = @($Inspection.Mounts | Where-Object { $_['Destination'] -ceq $mount.target -and $_['Type'] -ceq $mount.type -and $_['RW'] -eq !$mount.readonly })
    $requested = @($h.Mounts | Where-Object { $_['Target'] -ceq $mount.target -and $_['Type'] -ceq $mount.type -and
      $_['Source'] -cin $sources -and ($_['ReadOnly'] -eq $true) -eq $mount.readonly })
    if ($actual.Count -ne 1 -or $requested.Count -ne 1) { throw ('actual mount differs: ' + $mount.target) }
    if ($mount.type -ceq 'volume') {
      if ($actual[0].Name -cne $mount.source) { throw 'actual named volume differs' }
      $subpath = if ($mount.ContainsKey('subpath')) { $mount.subpath } else { '' }
      $options = $requested[0]['VolumeOptions']
      $actualSubpath = if ($options -and $options.ContainsKey('Subpath')) { $options.Subpath } else { '' }
      if ($actualSubpath -cne $subpath) { throw 'actual volume subpath differs' }
    } elseif ($actual[0].Source -cnotin $sources) { throw 'actual bind source differs' }
  }
}

function Invoke-LinuxHostBuildContainer($Context, [string]$Label, [object[]]$Mounts, [string[]]$Command,
                                       [bool]$Initializer=$false, [bool]$Detached=$false) {
  $user = if ($Initializer) { '0:0' } else { '65534:65534' }
  $argv = @('create','--pull','never','--init','--name',"$($Context.generation)-$Label",
    '--label',"org.mingos.generation=$($Context.generation)",'--label',"org.mingos.nonce=$($Context.nonce)",
    '--network','none','--read-only','--user',$user,'--cpus','2','--memory','4g','--pids-limit','256',
    '--cap-drop','ALL','--security-opt','no-new-privileges')
  if ($Initializer) { $argv += @('--cap-add','CHOWN') }
  else { $argv += @('--tmpfs','/tmp:rw,nosuid,nodev,size=512m,mode=1777') }
  foreach ($mount in $Mounts) {
    $text = "type=$($mount.type),source=$($mount.source),target=$($mount.target)"
    if ($mount.readonly) { $text += ',readonly' }
    if ($mount.ContainsKey('subpath')) { $text += ",volume-subpath=$($mount.subpath)" }
    $argv += @('--mount',$text)
  }
  $cid = (Invoke-LinuxHostDocker ($argv + @($script:LinuxHostRustImage) + $Command) "$Label-create" 180 $Context.docker).Trim()
  if ($cid -cnotmatch '^[a-f0-9]{64}$') { throw 'one exact created container ID required' }
  $Context.owned.Add(@{cid=$cid;name=$Label})
  $before = @(ConvertFrom-Json -AsHashtable (Invoke-LinuxHostDocker @('inspect',$cid) "$Label-inspect-before" 180 $Context.docker))
  if ($before.Count -ne 1) { throw 'one exact created container inspection required' }
  Assert-LinuxHostBuildInspection $before[0] $cid $Context.generation $Context.nonce $Mounts $user $Initializer
  $Context.containers.Add(@{label=$Label;cid=$cid;command=$Command;mounts=$Mounts})
  if ($Detached) {
    [void](Invoke-LinuxHostDocker @('start',$cid) "$Label-start" 180 $Context.docker)
    Write-LinuxHostJson (Join-Path $Context.evidence 'build-started.json') @{cid=$cid;generation=$Context.generation;startedAt=[DateTime]::UtcNow.ToString('o');finalSystemAccepted=$false}
    return $cid
  }
  [void](Invoke-LinuxHostDocker @('start','-a',$cid) "$Label-run" 1950 $Context.docker)
  $after = @(ConvertFrom-Json -AsHashtable (Invoke-LinuxHostDocker @('inspect',$cid) "$Label-inspect-after" 180 $Context.docker))
  if ($after.Count -ne 1) { throw 'one exact completed container inspection required' }
  Assert-LinuxHostBuildInspection $after[0] $cid $Context.generation $Context.nonce $Mounts $user $Initializer
  if ($after[0].State.Running -or $after[0].State.ExitCode -ne 0 -or $after[0].State.OOMKilled) { throw "container failed: $Label" }
  return $cid
}

function Stop-LinuxHostBuildContainers($Context, $Errors) {
  $image = if ($Context.ContainsKey('image')) { $Context.image } else { $script:LinuxHostRustImage }
  foreach ($entry in $Context.owned) {
    try {
      if ($entry.cid -cnotmatch '^[a-f0-9]{64}$') { throw 'invalid owned cleanup container ID' }
      $owned = @(ConvertFrom-Json -AsHashtable (Invoke-LinuxHostDocker @('inspect',$entry.cid) ("cleanup-inspect-"+$entry.name) 15 $Context.docker))
      if ($owned.Count -ne 1) { throw 'one exact owned cleanup inspection required' }
      Assert-LinuxHostResourceOwned $owned[0] $entry.cid $Context.generation $Context.nonce $image
      if ($owned[0].State.Running) {
        [void](Invoke-LinuxHostDocker @('kill',$entry.cid) ("cleanup-kill-"+$entry.name) 15 $Context.docker)
        $after = @(ConvertFrom-Json -AsHashtable (Invoke-LinuxHostDocker @('inspect',$entry.cid) ("cleanup-after-"+$entry.name) 15 $Context.docker))
        if ($after.Count -ne 1) { throw 'one exact stopped cleanup inspection required' }
        Assert-LinuxHostResourceOwned $after[0] $entry.cid $Context.generation $Context.nonce $image
        if ($after[0].State.Running) { throw 'owned container remains running' }
      }
    } catch { $Errors.Add($_.Exception.ToString()) }
  }
}

function New-LinuxHostBuild([string]$Repository, [string]$Evidence, [string]$Inputs,
                            [string]$Packages, [string]$Cargo, [string]$Vhd,
                            [bool]$Build=$false, [string]$Docker='',
                            [string]$Profile='persistent-update-linux-host') {
  if ($Profile -cnotin @('persistent-update-linux-host','persistent-uninstall-linux-host')) {
    throw 'fixed Linux cold qualification profile required'
  }
  $profileArguments = if ($Profile -ceq 'persistent-uninstall-linux-host') { @('--profile',$Profile) } else { @() }
  $Repository = Resolve-LinuxHostBuildPath $Repository Directory
  $Inputs = Resolve-LinuxHostBuildPath $Inputs Directory
  $Vhd = Resolve-LinuxHostBuildPath $Vhd File
  if ([IO.Path]::GetExtension($Vhd) -ine '.vhdx') { throw 'actual Docker backing VHDX required' }
  foreach ($name in @($Packages,$Cargo)) { Assert-LinuxHostBuildName $name }
  if ($Packages -ceq $Cargo) { throw 'separate package and Cargo volumes required' }
  foreach ($name in @('linux_host_qualification.py','linux_host_freeze.py','linux_host_build.py','snapshot_linux_host_source.py')) {
    [void](Resolve-LinuxHostBuildPath (Join-Path $Repository "scripts/boot/$name") File)
  }
  foreach ($name in @('rust-image.json','node','toolchain-observation.json')) {
    [void](Resolve-LinuxHostBuildPath (Join-Path $Inputs $name) File)
  }
  $imageReceipt = @(Get-Content -LiteralPath (Join-Path $Inputs 'rust-image.json') -Raw | ConvertFrom-Json -AsHashtable)
  if ($imageReceipt.Count -ne 1 -or $imageReceipt[0].Id -cne $script:LinuxHostRustImage -or !$imageReceipt[0].RootFS.Layers) { throw 'one verified fixed Rust image receipt required' }
  $nodeSha = 'e32e2d16081cf644f88392934288086e7662529d0d43277c34f7aad2207cfafb'
  if ((Get-FileHash -LiteralPath (Join-Path $Inputs 'node') -Algorithm SHA256).Hash.ToLowerInvariant() -cne $nodeSha) { throw 'verified pinned replay Node bytes required' }
  if ($Docker) { $Docker = Resolve-LinuxHostBuildPath $Docker File }
  $prefix = if ($Profile -ceq 'persistent-uninstall-linux-host') { 'persistent-uninstall-' } else { 'persistent-update-' }
  $generation = $prefix + [DateTime]::UtcNow.ToString('yyyyMMdd') + '-linux-' + [Guid]::NewGuid().ToString('N').Substring(0,16)
  $nonce = [Guid]::NewGuid().ToString('N')
  if (!$Evidence) { $Evidence = Join-Path $Repository "artifacts/$generation" }
  $Evidence = Resolve-LinuxHostBuildPath $Evidence NewDirectory
  $allowed = [IO.Path]::GetFullPath((Join-Path $Repository 'artifacts')).TrimEnd('\') + '\'
  if (!$Evidence.StartsWith($allowed,[StringComparison]::OrdinalIgnoreCase)) { throw 'new evidence directory inside repo/artifacts required' }
  [void](New-Item -ItemType Directory -Path $Evidence -ErrorAction Stop)
  $script:LinuxHostEvidence = $Evidence
  $context = @{generation=$generation;nonce=$nonce;evidence=$Evidence;docker=$Docker
    containers=[Collections.Generic.List[object]]::new();owned=[Collections.Generic.List[hashtable]]::new()}
  $volumes = @{source="$generation-source";prep="$generation-prep";target="$generation-target"}
  Write-LinuxHostJson (Join-Path $Evidence 'generation.json') @{schemaVersion=1;generation=$generation;nonce=$nonce;volumes=$volumes
    repo=$Repository;runtimeInputs=$Inputs;packagesVolume=$Packages;cargoVolume=$Cargo;backingVhd=$Vhd
    rustImage=$script:LinuxHostRustImage;runtimeInputsAlreadyVerifiedRequired=$true;finalSystemAccepted=$false;profile=$Profile}
  try {
    $image = @(ConvertFrom-Json -AsHashtable (Invoke-LinuxHostDocker @('image','inspect',$script:LinuxHostRustImage) 'image-inspect' 180 $Docker))
    if ($image.Count -ne 1 -or $image[0].Id -cne $script:LinuxHostRustImage -or !$image[0].RootFS.Layers) { throw 'actual fixed Rust image differs' }
    foreach ($name in @($Packages,$Cargo)) {
      $capture = if ($name -ceq $Packages) { 'packages-volume-inspect' } else { 'cargo-volume-inspect' }
      $receipt = @(ConvertFrom-Json -AsHashtable (Invoke-LinuxHostDocker @('volume','inspect',$name) $capture 180 $Docker))
      if ($receipt.Count -ne 1 -or $receipt[0].Name -cne $name -or $receipt[0].Driver -cne 'local') { throw 'verified local input volume required' }
    }
    $existing = @((Invoke-LinuxHostDocker @('volume','ls','--format','{{.Name}}') 'existing-volumes' 180 $Docker) -split '\r?\n')
    foreach ($entry in $volumes.GetEnumerator()) {
      if ($entry.Value -cin $existing) { throw 'new volume name already exists' }
      [void](Invoke-LinuxHostDocker @('volume','create','--label',"org.mingos.generation=$generation",'--label',"org.mingos.nonce=$nonce",$entry.Value) ("volume-create-"+$entry.Key) 180 $Docker)
      $v = @(ConvertFrom-Json -AsHashtable (Invoke-LinuxHostDocker @('volume','inspect',$entry.Value) ("volume-inspect-"+$entry.Key) 180 $Docker))
      if ($v.Count -ne 1 -or $v[0].Name -cne $entry.Value -or $v[0].Driver -cne 'local' -or
          $v[0].Labels['org.mingos.generation'] -cne $generation -or $v[0].Labels['org.mingos.nonce'] -cne $nonce) { throw 'dedicated volume identity/labels differ' }
    }
    $initMounts = @(@{type='bind';source=$Repository;target='/checkout';readonly=$true},
      @{type='volume';source=$Packages;target='/packages';readonly=$true;subpath='packages'},
      @{type='volume';source=$volumes.source;target='/source';readonly=$false},
      @{type='volume';source=$volumes.prep;target='/prep';readonly=$false},
      @{type='volume';source=$volumes.target;target='/target';readonly=$false})
    $snapshot = Invoke-LinuxHostBuildContainer $context 'snapshot' $initMounts @('python3','-B','/checkout/scripts/boot/snapshot_linux_host_source.py') $true
    [void](Invoke-LinuxHostDocker @('cp',"${snapshot}:/source/snapshot.json",(Join-Path $Evidence 'snapshot.json')) 'snapshot-export' 180 $Docker)
    $snap = Get-Content -LiteralPath (Join-Path $Evidence 'snapshot.json') -Raw | ConvertFrom-Json -AsHashtable
    if ($snap.literalEmptyTarget -ne $true -or $snap.sourceFiles -lt 1 -or $snap.linuxAvailableBytes -lt 1) { throw 'actual source snapshot admission differs' }
    $disk = Get-Item -LiteralPath $Vhd
    $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($Vhd))
    $receipts = Join-Path $Evidence 'receipts'
    [void](New-Item -ItemType Directory -Path $receipts)
    $capacity = Join-Path $receipts 'capacity.json'
    Write-LinuxHostJson $capacity @{schemaVersion=1;observedAt=[DateTime]::UtcNow.ToString('o');backingDiskPath=$disk.FullName
      backingDiskLogicalLength=$disk.Length;hostFilesystem=$drive.DriveFormat;hostAvailableBytes=$drive.AvailableFreeSpace
      linuxAvailableBytes=$snap.linuxAvailableBytes;logicalLengthIsPhysicalAllocation=$false;finalSystemAccepted=$false}
    $mounts = @(@{type='volume';source=$volumes.source;target='/repo';readonly=$true;subpath='repo'},
      @{type='volume';source=$volumes.prep;target='/prep';readonly=$false},
      @{type='volume';source=$Packages;target='/packages';readonly=$true;subpath='packages'},
      @{type='volume';source=$Cargo;target='/cargo-cache';readonly=$false},
      @{type='volume';source=$Cargo;target='/cargo-cache/cargo/registry';readonly=$true;subpath='cargo/registry'},
      @{type='bind';source=$Inputs;target='/runtime';readonly=$true},
      @{type='bind';source=$receipts;target='/cold-receipts';readonly=$true})
    $common = @('python3','-B','/repo/scripts/boot/linux_host_freeze.py') + $profileArguments
    [void](Invoke-LinuxHostBuildContainer $context 'storage-probe' $mounts ($common+@('--stage','storage-probe','--root','/prep')))
    [void](Invoke-LinuxHostBuildContainer $context 'storage-reopen' $mounts ($common+@('--stage','storage-reopen','--root','/prep')))
    $prepared = Invoke-LinuxHostBuildContainer $context 'prepare' $mounts ($common+@('--stage','prepare','--root','/prep','--repo','/repo',
      '--cargo-home','/cargo-cache/cargo','--packages-root','/packages','--image-receipt','/runtime/rust-image.json',
      '--capacity-receipt','/cold-receipts/capacity.json','--node','/runtime/node','--node-sha256',$nodeSha,'--toolchain-receipt','/runtime/toolchain-observation.json'))
    [void](Invoke-LinuxHostDocker @('cp',"${prepared}:/prep",(Join-Path $Evidence 'prepared')) 'prepared-export' 180 $Docker)
    if ($Build) {
      $buildMounts = $mounts + @(@{type='volume';source=$volumes.target;target='/target';readonly=$false})
      [void](Invoke-LinuxHostBuildContainer $context 'build' $buildMounts (@('python3','-B','/repo/scripts/boot/linux_host_build.py',
        '--root','/prep','--repo','/repo','--freeze','/prep/freeze.json','--cargo-home','/cargo-cache/cargo','--target','/target','--packages-root','/packages') + $profileArguments) $false $true)
    }
    Write-LinuxHostJson (Join-Path $Evidence 'orchestration-result.json') @{status='prepared';buildStarted=$Build;containers=$context.containers;volumes=$volumes;formalQualification=$false;finalSystemAccepted=$false}
    Write-Output "Generation evidence: $Evidence"
  } catch {
    $original = $_
    $cleanup = [Collections.Generic.List[string]]::new()
    Stop-LinuxHostBuildContainers $context $cleanup
    Write-LinuxHostJson (Join-Path $Evidence 'orchestration-result.json') @{status='failed';error=$original.Exception.ToString();cleanupErrors=@($cleanup.ToArray());containers=$context.containers;volumes=$volumes;formalQualification=$false;finalSystemAccepted=$false}
    throw $original
  }
}

if ($MyInvocation.InvocationName -ne '.') {
  New-LinuxHostBuild $Repo $EvidenceRoot $RuntimeInputs $PackagesVolume $CargoVolume $BackingVhd $StartBuild.IsPresent $DockerExecutable $ColdProfile
}
