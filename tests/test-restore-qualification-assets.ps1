$ErrorActionPreference = 'Stop'
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

Write-Output 'restore qualification asset validation tests passed'
