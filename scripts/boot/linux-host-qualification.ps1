#requires -Version 7.3
[CmdletBinding()]
param(
  [ValidateSet('source-check', 'storage-admission', 'prepare', 'build', 'bundle', 'admission', 'pair', 'export', 'verify')]
  [string]$Stage = 'source-check',
  [string]$Root = '',
  [string]$Repo = (Resolve-Path "$PSScriptRoot/../..").Path
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$script:LinuxHostRustImage = 'sha256:88624fbbfcb6b7ac6f1207491053cbe6fd435262f0ce547443fb36760c7e9d80'

function Assert-LinuxHostContainer($Inspection, [string]$Repository, [string]$Volume) {
  $h = $Inspection.HostConfig
  if ($Inspection.Image -cne $script:LinuxHostRustImage -or $Inspection.Config.User -cne '65534:65534' -or
      $h.NetworkMode -cne 'none' -or $h.ReadonlyRootfs -ne $true -or $h.Privileged -ne $false -or
      $h.NanoCpus -ne 2000000000 -or $h.Memory -ne 4294967296 -or $h.PidsLimit -ne 256 -or
      @($h['CapAdd'] | Where-Object { $null -ne $_ }).Count -ne 0 -or @($h.CapDrop).Count -ne 1 -or $h.CapDrop[0] -cne 'ALL' -or
      @($h.SecurityOpt).Count -ne 1 -or $h.SecurityOpt[0] -cne 'no-new-privileges') {
    throw 'LINUX_HOST_BLOCKED: container identity, privileges or resources changed'
  }
  if (@($h['Binds'] | Where-Object { $null -ne $_ }).Count -ne 0 -or
      @($h['VolumesFrom'] | Where-Object { $null -ne $_ }).Count -ne 0) {
    throw 'LINUX_HOST_BLOCKED: extra bind or inherited volume requested'
  }
  $mounts = @($h.Mounts)
  $source = @($mounts | Where-Object { $_.Type -ceq 'bind' -and $_.Target -ceq '/repo' -and $_.Source -ceq $Repository -and $_.ReadOnly -eq $true })
  $prep = @($mounts | Where-Object { $_.Type -ceq 'volume' -and $_.Target -ceq '/prep' -and $_.Source -ceq $Volume -and $_['ReadOnly'] -ne $true })
  if ($mounts.Count -ne 2 -or $source.Count -ne 1 -or $prep.Count -ne 1) {
    throw 'LINUX_HOST_BLOCKED: exact read-only source and dedicated prep mounts required'
  }
  $effective = @($Inspection['Mounts'])
  $effectiveSource = @($effective | Where-Object { $_['Type'] -ceq 'bind' -and $_['Destination'] -ceq '/repo' -and $_['Source'] -ceq $Repository -and $_['RW'] -eq $false })
  $effectivePrep = @($effective | Where-Object { $_['Type'] -ceq 'volume' -and $_['Destination'] -ceq '/prep' -and $_['Name'] -ceq $Volume -and $_['RW'] -eq $true })
  if ($effective.Count -ne 2 -or $effectiveSource.Count -ne 1 -or $effectivePrep.Count -ne 1) {
    throw 'LINUX_HOST_BLOCKED: effective source/prep mounts differ'
  }
  if ($h['Tmpfs'].Count -ne 1 -or $h['Tmpfs']['/tmp'] -cne 'rw,nosuid,nodev,size=128m,mode=1777') {
    throw 'LINUX_HOST_BLOCKED: exact runtime tmpfs required'
  }
}

function Assert-LinuxHostOwned($Inspection, [string]$Cid, [string]$Generation, [string]$Nonce) {
  if ($Cid -cnotmatch '^[a-f0-9]{64}$' -or $Inspection.Id -cne $Cid -or
      $Inspection.Image -cne $script:LinuxHostRustImage -or
      $Inspection.Config.Labels['org.mingos.generation'] -cne $Generation -or
      $Inspection.Config.Labels['org.mingos.nonce'] -cne $Nonce) { throw 'LINUX_HOST_BLOCKED: owned container identity differs' }
}

function Assert-LinuxHostInitializer($Inspection, [string]$Volume) {
  $h = $Inspection.HostConfig
  $mounts = @($h['Mounts'])
  $effective = @($Inspection['Mounts'])
  if ($Inspection.Image -cne $script:LinuxHostRustImage -or $Inspection.Config.User -cne '0:0' -or
      $h.NetworkMode -cne 'none' -or !$h.ReadonlyRootfs -or $h.Privileged -or
      $h.NanoCpus -ne 2000000000 -or $h.Memory -ne 4294967296 -or $h.PidsLimit -ne 256 -or
      @($h.CapAdd).Count -ne 1 -or @('CHOWN', 'CAP_CHOWN') -cnotcontains $h.CapAdd[0] -or
      @($h.CapDrop).Count -ne 1 -or $h.CapDrop[0] -cne 'ALL' -or
      @($h.SecurityOpt).Count -ne 1 -or $h.SecurityOpt[0] -cne 'no-new-privileges' -or
      @($h['Binds'] | Where-Object { $null -ne $_ }).Count -or
      @($h['VolumesFrom'] | Where-Object { $null -ne $_ }).Count -or $h['Tmpfs'] -or
      $mounts.Count -ne 1 -or $mounts[0]['Type'] -cne 'volume' -or $mounts[0]['Source'] -cne $Volume -or
      $mounts[0]['Target'] -cne '/prep' -or $mounts[0]['ReadOnly'] -eq $true -or
      $effective.Count -ne 1 -or $effective[0]['Type'] -cne 'volume' -or $effective[0]['Name'] -cne $Volume -or
      $effective[0]['Destination'] -cne '/prep' -or $effective[0]['RW'] -ne $true) {
    throw 'LINUX_HOST_BLOCKED: initializer isolation or mount differs'
  }
}

function Write-LinuxHostJson([string]$Path, $Value) {
  $raw = [Text.Encoding]::UTF8.GetBytes(($Value | ConvertTo-Json -Depth 30) + "`n")
  $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
  try { $stream.Write($raw); $stream.Flush($true) } finally { $stream.Dispose() }
}

function Invoke-LinuxHostDocker([string[]]$Arguments, [string]$Name, [int]$TimeoutSeconds = 180, [string]$Executable = '') {
  $base = Join-Path $script:LinuxHostEvidence $Name
  $started = [DateTime]::UtcNow.ToString('o')
  Write-LinuxHostJson "$base.command.json" @{ command = @('docker') + $Arguments; startedAt = $started; timeoutSeconds = $TimeoutSeconds }
  $process = [Diagnostics.Process]::new()
  $stdout = $null; $stderr = $null; $outTask = $null; $errTask = $null
  $cancellation = [Threading.CancellationTokenSource]::new()
  $watch = [Diagnostics.Stopwatch]::StartNew()
  $timedOut = $false
  $code = $null; $startedProcess = $false; $failure = $null
  $cleanupErrors = [Collections.Generic.List[string]]::new()
  try {
    if ($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 1950) { throw 'invalid native command timeout' }
    if (!$Executable) {
      $matches = @(Get-Command docker.exe -CommandType Application -ErrorAction Stop)
      if ($matches.Count -ne 1) { throw 'unique docker.exe required' }
      $Executable = $matches[0].Source
    }
    $process.StartInfo.FileName = $Executable
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    foreach ($argument in $Arguments) { $process.StartInfo.ArgumentList.Add($argument) }
    $stdout = [IO.File]::Open("$base.stdout.log", [IO.FileMode]::CreateNew)
    $stderr = [IO.File]::Open("$base.stderr.log", [IO.FileMode]::CreateNew)
    [void]$process.Start()
    $startedProcess = $true
    $outTask = $process.StandardOutput.BaseStream.CopyToAsync($stdout, 81920, $cancellation.Token)
    $errTask = $process.StandardError.BaseStream.CopyToAsync($stderr, 81920, $cancellation.Token)
    $remaining = [Math]::Max(0, $TimeoutSeconds * 1000 - [int]$watch.ElapsedMilliseconds)
    if (!$process.WaitForExit($remaining)) { $timedOut = $true; throw 'native process timeout' }
    $code = $process.ExitCode
    $remaining = [Math]::Max(0, $TimeoutSeconds * 1000 - [int]$watch.ElapsedMilliseconds)
    if (![Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($outTask, $errTask), $remaining)) {
      $timedOut = $true; throw 'native output capture timeout'
    }
  } catch { $failure = $_.Exception.ToString() }
  finally {
    $cleanupWatch = [Diagnostics.Stopwatch]::StartNew()
    if ($startedProcess) {
      try {
        if (!$process.HasExited) { $process.Kill($true) }
        if (!$process.WaitForExit(5000)) { throw 'native process cleanup timeout' }
        $code = $process.ExitCode
      } catch { $cleanupErrors.Add($_.Exception.ToString()) }
    }
    $cancellation.Cancel()
    $tasks = @($outTask, $errTask | Where-Object { $null -ne $_ })
    if ($tasks.Count) {
      try {
        $remaining = [Math]::Max(0, 5000 - [int]$cleanupWatch.ElapsedMilliseconds)
        if (![Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]$tasks, $remaining)) { throw 'output cleanup timeout' }
      } catch { $cleanupErrors.Add($_.Exception.ToString()) }
    }
    foreach ($stream in @($stdout, $stderr)) {
      if ($null -ne $stream) {
        try { $stream.Flush($true); $stream.Dispose() } catch { $cleanupErrors.Add($_.Exception.ToString()) }
      }
    }
    $process.Dispose(); $cancellation.Dispose()
    Write-LinuxHostJson "$base.result.json" @{ exitCode = $code; timedOut = $timedOut; error = $failure; cleanupErrors = @($cleanupErrors.ToArray()); startedAt = $started; finishedAt = [DateTime]::UtcNow.ToString('o') }
  }
  if ($failure -or $cleanupErrors.Count -or $timedOut -or $code -ne 0) { throw "LINUX_HOST_BLOCKED: docker $Name failed; preserve generation" }
  return [IO.File]::ReadAllText("$base.stdout.log")
}

function Invoke-LinuxHostQualification([string]$RequestedStage, [string]$RequestedRoot, [string]$Repository) {
  # Remaining stages cannot allocate resources until their admission is integrated.
  if ($RequestedStage -cne 'source-check') { throw 'LINUX_HOST_BLOCKED: stage not integrated; no build or guest started' }
  $Repository = [IO.Path]::GetFullPath($Repository).TrimEnd('\')
  if ($Repository.Contains(',') -or !(Test-Path -LiteralPath "$Repository/scripts/boot/linux_host_qualification.py" -PathType Leaf)) {
    throw 'LINUX_HOST_BLOCKED: repository path is invalid'
  }
  $id = 'persistent-update-20261107-linux-' + [Guid]::NewGuid().ToString('N').Substring(0, 16)
  $nonce = [Guid]::NewGuid().ToString('N')
  if (!$RequestedRoot) { $RequestedRoot = Join-Path $Repository "artifacts/$id" }
  $RequestedRoot = [IO.Path]::GetFullPath($RequestedRoot)
  $allowed = [IO.Path]::GetFullPath((Join-Path $Repository 'artifacts')).TrimEnd('\') + '\'
  if (!$RequestedRoot.StartsWith($allowed, [StringComparison]::OrdinalIgnoreCase)) { throw 'LINUX_HOST_BLOCKED: evidence root must be a new directory inside repo/artifacts' }
  # New-Item without Force refuses an existing successful or failed generation.
  [void](New-Item -ItemType Directory -Path $RequestedRoot -ErrorAction Stop)
  $script:LinuxHostEvidence = $RequestedRoot
  $volume = "$id-prep"
  $container = "$id-source-check"
  $init = "$id-init"
  $owned = [Collections.Generic.List[hashtable]]::new()
  $cleanupErrors = [Collections.Generic.List[string]]::new()
  $originalFailure = $null
  $status = 'failed'
  try {
    Write-LinuxHostJson (Join-Path $RequestedRoot 'generation.json') @{
      schemaVersion = 1; generation = $id; nonce = $nonce; stage = $RequestedStage
      repo = $Repository; volume = $volume; container = $container; initContainer = $init
      rustImage = $script:LinuxHostRustImage; finalSystemAccepted = $false
    }
    $driveRoot = [IO.Path]::GetPathRoot($RequestedRoot)
    $drive = [IO.DriveInfo]::new($driveRoot)
    Write-LinuxHostJson (Join-Path $RequestedRoot 'windows-storage.json') @{
      path = $driveRoot; format = $drive.DriveFormat; totalBytes = $drive.TotalSize
      availableBytes = $drive.AvailableFreeSpace; observationOnly = $true
      dockerBackingStoreAdmitted = $false; coldBuildPeakAdmitted = $false
    }
    [void](Invoke-LinuxHostDocker @('image', 'inspect', $script:LinuxHostRustImage) 'image-inspect')
    [void](Invoke-LinuxHostDocker @('volume', 'create', '--label', "org.mingos.generation=$id", '--label', "org.mingos.nonce=$nonce", $volume) 'create-volume')
    $inspection = @(ConvertFrom-Json -InputObject (Invoke-LinuxHostDocker @('volume', 'inspect', $volume) 'volume-inspect') -AsHashtable)
    if ($inspection.Count -ne 1 -or $inspection[0].Labels['org.mingos.generation'] -cne $id -or $inspection[0].Labels['org.mingos.nonce'] -cne $nonce) { throw 'LINUX_HOST_BLOCKED: dedicated volume labels differ' }
    $initCode = 'import os; p="/prep"; assert not os.listdir(p), "fresh volume not empty"; os.chmod(p,0o700); os.chown(p,65534,65534)'
    $initId = (Invoke-LinuxHostDocker @('create', '--pull', 'never', '--name', $init, '--label', "org.mingos.generation=$id", '--label', "org.mingos.nonce=$nonce", '--network', 'none', '--read-only', '--user', '0:0', '--cpus', '2', '--memory', '4g', '--pids-limit', '256', '--cap-drop', 'ALL', '--cap-add', 'CHOWN', '--security-opt', 'no-new-privileges', '--mount', "type=volume,source=$volume,target=/prep", $script:LinuxHostRustImage, 'python3', '-I', '-B', '-c', $initCode) 'create-initializer').Trim()
    $owned.Add(@{name=$init; cid=$initId})
    $initBefore = @(ConvertFrom-Json -InputObject (Invoke-LinuxHostDocker @('inspect', $initId) 'initializer-before') -AsHashtable)
    if ($initBefore.Count -ne 1) { throw 'LINUX_HOST_BLOCKED: unique initializer required' }
    Assert-LinuxHostOwned $initBefore[0] $initId $id $nonce
    Assert-LinuxHostInitializer $initBefore[0] $volume
    [void](Invoke-LinuxHostDocker @('start', '-a', $initId) 'initialize-volume')
    $initAfter = @(ConvertFrom-Json -InputObject (Invoke-LinuxHostDocker @('inspect', $initId) 'initializer-after') -AsHashtable)
    if ($initAfter.Count -ne 1 -or $initAfter[0].State.Running -or $initAfter[0].State.ExitCode -ne 0 -or $initAfter[0].State.OOMKilled) { throw 'LINUX_HOST_BLOCKED: initializer did not exit cleanly' }
    Assert-LinuxHostOwned $initAfter[0] $initId $id $nonce
    Assert-LinuxHostInitializer $initAfter[0] $volume
    $containerId = (Invoke-LinuxHostDocker @('create', '--pull', 'never', '--name', $container, '--label', "org.mingos.generation=$id", '--label', "org.mingos.nonce=$nonce", '--network', 'none', '--read-only', '--user', '65534:65534', '--cpus', '2', '--memory', '4g', '--pids-limit', '256', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m,mode=1777', '--mount', "type=bind,source=$Repository,target=/repo,readonly", '--mount', "type=volume,source=$volume,target=/prep", '--workdir', '/repo', $script:LinuxHostRustImage, 'python3', '-B', 'scripts/boot/linux_host_qualification.py', '--stage', 'source-check', '--root', '/prep', '--repo', '/repo') 'create-source-check').Trim()
    $owned.Add(@{name=$container; cid=$containerId})
    $inspection = @(ConvertFrom-Json -InputObject (Invoke-LinuxHostDocker @('inspect', $container) 'container-before') -AsHashtable)
    if ($inspection.Count -ne 1) { throw 'LINUX_HOST_BLOCKED: unique container required' }
    Assert-LinuxHostOwned $inspection[0] $containerId $id $nonce
    Assert-LinuxHostContainer $inspection[0] $Repository $volume
    [void](Invoke-LinuxHostDocker @('start', '-a', $container) 'source-check')
    $after = @(ConvertFrom-Json -InputObject (Invoke-LinuxHostDocker @('inspect', $container) 'container-after') -AsHashtable)
    Assert-LinuxHostContainer $after[0] $Repository $volume
    Assert-LinuxHostOwned $after[0] $containerId $id $nonce
    if ($after[0].State.Running -or $after[0].State.ExitCode -ne 0 -or $after[0].State.OOMKilled) { throw 'LINUX_HOST_BLOCKED: source-check did not exit cleanly' }
    [void](Invoke-LinuxHostDocker @('cp', "${container}:/prep/source-check.json", (Join-Path $RequestedRoot 'source-check.json')) 'export-source-check')
    $status = 'source-check-passed'
  } catch { $originalFailure = $_ }
  finally {
    if ($status -eq 'failed') { Stop-LinuxHostOwnedContainers $owned $id $nonce $cleanupErrors }
    Write-LinuxHostJson (Join-Path $RequestedRoot 'result.json') @{
      schemaVersion = 1; status = $status; generation = $id; evidence = $RequestedRoot
      error = $(if ($null -ne $originalFailure) { $originalFailure.Exception.ToString() } else { $null })
      cleanupErrors = @($cleanupErrors.ToArray()); ownedContainers = @($owned.ToArray())
      formalQualification = $false; finalSystemAccepted = $false
    }
  }
  if ($null -ne $originalFailure) { throw $originalFailure }
  if ($cleanupErrors.Count) { throw 'LINUX_HOST_BLOCKED: owned cleanup failed; preserve generation' }
  Write-Output "Source check evidence: $RequestedRoot"
}

function Stop-LinuxHostOwnedContainers($Owned, [string]$Generation, [string]$Nonce, $Errors) {
  foreach ($entry in $Owned) {
    try {
      if ($entry.cid -cnotmatch '^[a-f0-9]{64}$') { throw 'invalid owned container ID' }
      $inspected = @(ConvertFrom-Json -InputObject (Invoke-LinuxHostDocker @('inspect', $entry.cid) ("cleanup-inspect-" + $entry.name) 15) -AsHashtable)
      if ($inspected.Count -ne 1 -or $inspected[0].Id -cne $entry.cid -or
          $inspected[0].Image -cne $script:LinuxHostRustImage -or
          $inspected[0].Config.Labels['org.mingos.generation'] -cne $Generation -or
          $inspected[0].Config.Labels['org.mingos.nonce'] -cne $Nonce) { throw 'owned container identity differs; refusing stop' }
      if ($inspected[0].State.Running) {
        [void](Invoke-LinuxHostDocker @('kill', $entry.cid) ("cleanup-kill-" + $entry.name) 15)
        $after = @(ConvertFrom-Json -InputObject (Invoke-LinuxHostDocker @('inspect', $entry.cid) ("cleanup-after-" + $entry.name) 15) -AsHashtable)
        if ($after.Count -ne 1 -or $after[0].Id -cne $entry.cid -or $after[0].State.Running) { throw 'owned container remains running' }
      }
    } catch { $Errors.Add($_.Exception.ToString()) }
  }
}

if ($MyInvocation.InvocationName -ne '.') { Invoke-LinuxHostQualification $Stage $Root $Repo }
