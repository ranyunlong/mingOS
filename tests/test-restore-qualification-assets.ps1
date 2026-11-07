param([string]$PythonExecutable = '')
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
  $PythonExecutable = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
}
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$script = Join-Path $repo 'scripts\boot\restore-qualification-assets.ps1'

function Invoke-Validation([hashtable]$Manifest, [int]$ExpectedExitCode) {
  $root = Join-Path ([IO.Path]::GetTempPath()) ('mingos-restore-test-' + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path (Join-Path $root 'images') -Force | Out-Null
  try {
    foreach ($entry in $Manifest.images) {
      if ($entry.archive -match '(^|[\\/])\.\.([\\/]|$)') {
        $entry.archiveSha256 = ('0' * 64)
        continue
      }
      $path = Join-Path $root $entry.archive
      New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($path)) -Force | Out-Null
      [IO.File]::WriteAllBytes($path, [Text.Encoding]::UTF8.GetBytes($entry.key))
      $entry.archiveSha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $manifestPath = Join-Path $root 'manifest.json'
    $json = $Manifest | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($manifestPath, $json, [Text.UTF8Encoding]::new($false))
    $stdoutPath = Join-Path $root 'stdout.txt'
    $stderrPath = Join-Path $root 'stderr.txt'
    $process = Start-Process powershell.exe -ArgumentList @(
      '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $script,
      '-AssetRoot', $root, '-Manifest', $manifestPath, '-ValidateOnly'
    ) -Wait -PassThru -NoNewWindow -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    if ($process.ExitCode -ne $ExpectedExitCode) {
      $output = [IO.File]::ReadAllText($stdoutPath) + [IO.File]::ReadAllText($stderrPath)
      throw "Expected exit $ExpectedExitCode, got $($process.ExitCode). Output: $output"
    }
    if ($ExpectedExitCode -eq 0) {
      $result = [IO.File]::ReadAllText($stdoutPath) | ConvertFrom-Json
      if ($result.status -ne 'archive-hashes-matched' -or $result.loadImages -or $result.restoreVolume) {
        throw 'Read-only validation overstated the verified archive boundary'
      }
    }
  } finally {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
  }
}

$fixedIds = @{
  rust = 'sha256:868a411f3e53b95fcdfb0604031e0172e51cf7d1ae2bce8aa0ea1c0bb82f0130'
  tools = 'sha256:2440560d810a834637cfeeb1057f7f471c40caa8e532645e00e063cbc8ec639a'
  openssl = 'sha256:3646133f4b4d2e007dd3eee28d9db6336c29f6ccb31b9f8a49b88bc0a70051be'
}
$valid = @{
  schemaVersion = 1
  images = @(
    @{ key = 'rust'; archive = 'images/rust.tar'; imageId = $fixedIds.rust },
    @{ key = 'tools'; archive = 'images/tools.tar'; imageId = $fixedIds.tools },
    @{ key = 'openssl'; archive = 'images/openssl.tar'; imageId = $fixedIds.openssl }
  )
}
Invoke-Validation $valid 0

$traversal = $valid.Clone()
$traversal.images = @($valid.images | ForEach-Object { $_.Clone() })
$traversal.images[0].archive = '../outside.tar'
Invoke-Validation $traversal 1

$duplicate = $valid.Clone()
$duplicate.images = @($valid.images | ForEach-Object { $_.Clone() })
$duplicate.images[1].key = 'rust'
Invoke-Validation $duplicate 1

$missing = $valid.Clone()
$missing.images = @($valid.images[0], $valid.images[1])
Invoke-Validation $missing 1

$wrongImageId = $valid.Clone()
$wrongImageId.images = @($valid.images | ForEach-Object { $_.Clone() })
$wrongImageId.images[0].imageId = ('sha256:' + ('f' * 64))
Invoke-Validation $wrongImageId 1

function Invoke-RejectedAction([string]$Action, [switch]$ConflictingMode) {
  $root = Join-Path ([IO.Path]::GetTempPath()) ('mingos-restore-disabled-' + [guid]::NewGuid().ToString('N'))
  $bin = Join-Path $root 'bin'
  $marker = Join-Path $root 'docker-called.txt'
  New-Item -ItemType Directory -Path (Join-Path $root 'images'), $bin -Force | Out-Null
  $previousPath = $env:PATH
  $previousMarker = $env:MINGOS_TEST_DOCKER_MARKER
  try {
    $source = Join-Path $root 'fake-docker.cs'
    [IO.File]::WriteAllText($source, @'
using System;
using System.IO;
public static class Program {
  public static int Main(string[] args) {
    File.WriteAllText(Environment.GetEnvironmentVariable("MINGOS_TEST_DOCKER_MARKER"), String.Join(" ", args));
    return 42;
  }
}
'@)
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
    if (!(Test-Path -LiteralPath $compiler)) { throw 'C# compiler required for fake docker test' }
    & $compiler /nologo /target:exe ("/out:" + (Join-Path $bin 'docker.exe')) $source | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Failed to compile fake docker' }

    $images = @($valid.images | ForEach-Object { $_.Clone() })
    foreach ($entry in $images) {
      $path = Join-Path $root $entry.archive
      [IO.File]::WriteAllBytes($path, [Text.Encoding]::UTF8.GetBytes($entry.key))
      $entry.archiveSha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $volumeArchive = Join-Path $root 'volume.tar'
    [IO.File]::WriteAllText($volumeArchive, 'test-volume')
    $manifest = @{
      schemaVersion = 1
      images = $images
      volume = @{
        name = 'mingos-test-owned-volume'
        archive = 'volume.tar'
        archiveSha256 = (Get-FileHash -LiteralPath $volumeArchive -Algorithm SHA256).Hash.ToLowerInvariant()
      }
    }
    $manifestPath = Join-Path $root 'manifest.json'
    [IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    $stdoutPath = Join-Path $root 'stdout.txt'
    $stderrPath = Join-Path $root 'stderr.txt'
    $env:PATH = $bin
    $env:MINGOS_TEST_DOCKER_MARKER = $marker
    $powershell = Join-Path $PSHOME 'powershell.exe'
    $childArguments = @(
      '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $script,
      '-AssetRoot', $root, '-Manifest', $manifestPath, ('-' + $Action)
    )
    if ($Action -eq 'LoadImages') {
      $childArguments += @('-PythonExecutable', $PythonExecutable)
      if ($ConflictingMode) { $childArguments += '-ValidateOnly' }
    }
    $process = Start-Process $powershell -ArgumentList $childArguments -Wait -PassThru -NoNewWindow -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    $output = [IO.File]::ReadAllText($stdoutPath) + [IO.File]::ReadAllText($stderrPath)
    $expectedFailure = if ($Action -eq 'RestoreVolume') { 'mutating restore is disabled' } elseif ($ConflictingMode) { 'cannot combine' } else { 'invalid Docker save archive' }
    if ($process.ExitCode -eq 0 -or $output -notmatch $expectedFailure) {
      throw "$Action did not report the expected admission boundary: $output"
    }
    if (Test-Path -LiteralPath $marker) { throw "$Action invoked docker before refusal" }
  } finally {
    $env:PATH = $previousPath
    $env:MINGOS_TEST_DOCKER_MARKER = $previousMarker
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
  }
}

Invoke-RejectedAction 'RestoreVolume'
Invoke-RejectedAction 'LoadImages'
Invoke-RejectedAction 'LoadImages' -ConflictingMode

$outsideRoot = Join-Path ([IO.Path]::GetTempPath()) ('mingos-restore-outside-' + [guid]::NewGuid().ToString('N'))
$outsideManifest = Join-Path ([IO.Path]::GetTempPath()) ('mingos-restore-manifest-' + [guid]::NewGuid().ToString('N') + '.json')
New-Item -ItemType Directory -Path (Join-Path $outsideRoot 'images') -Force | Out-Null
try {
  $outsideImages = @()
  foreach ($entry in $valid.images) {
    $path = Join-Path $outsideRoot $entry.archive
    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($path)) -Force | Out-Null
    [IO.File]::WriteAllBytes($path, [Text.Encoding]::UTF8.GetBytes($entry.key))
    $outsideImages += @{
      key = $entry.key
      archive = $entry.archive
      imageId = $entry.imageId
      archiveSha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    }
  }
  $outsideManifestValue = @{ schemaVersion = 1; images = $outsideImages }
  [IO.File]::WriteAllText($outsideManifest, ($outsideManifestValue | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
  $stdoutPath = Join-Path $outsideRoot 'stdout.txt'
  $stderrPath = Join-Path $outsideRoot 'stderr.txt'
  $process = Start-Process powershell.exe -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $script,
    '-AssetRoot', $outsideRoot, '-Manifest', $outsideManifest, '-ValidateOnly'
  ) -Wait -PassThru -NoNewWindow -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
  if ($process.ExitCode -ne 1) { throw 'Manifest outside AssetRoot was accepted' }
} finally {
  Remove-Item -LiteralPath $outsideRoot -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $outsideManifest -Force -ErrorAction SilentlyContinue
}

Write-Output 'restore qualification asset validation tests passed'
