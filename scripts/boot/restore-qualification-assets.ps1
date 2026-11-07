[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$AssetRoot,
  [string]$Manifest = '',
  [switch]$ValidateOnly,
  [switch]$LoadImages,
  [switch]$RestoreVolume,
  [string]$VolumeHelperImage = 'debian:trixie-slim'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$script:ExpectedImageIds = @{
  rust = 'sha256:868a411f3e53b95fcdfb0604031e0172e51cf7d1ae2bce8aa0ea1c0bb82f0130'
  tools = 'sha256:2440560d810a834637cfeeb1057f7f471c40caa8e532645e00e063cbc8ec639a'
  openssl = 'sha256:3646133f4b4d2e007dd3eee28d9db6336c29f6ccb31b9f8a49b88bc0a70051be'
}

function Fail([string]$Message) {
  throw ('QUALIFICATION_ASSET_RESTORE_BLOCKED: ' + $Message)
}

if ($LoadImages -or $RestoreVolume) {
  Fail 'mutating restore is disabled pending safe archive provenance and ownership verification'
}

function Assert-Hash([string]$Value, [string]$Name) {
  if ($Value -notmatch '^([0-9a-f]{64})$') { Fail ($Name + ' must be 64 lowercase hex characters') }
}

function Resolve-SafeAssetPath([string]$Root, [string]$Relative, [string]$Name) {
  if ([string]::IsNullOrWhiteSpace($Relative) -or
      [IO.Path]::IsPathRooted($Relative) -or
      $Relative -match '(^|[\\\/])\.\.([\\\/]|$)') {
    Fail ($Name + ' must be a relative path inside AssetRoot')
  }
  $rootFull = [IO.Path]::GetFullPath($Root)
  $candidate = [IO.Path]::GetFullPath((Join-Path $rootFull ($Relative -replace '/', '\')))
  if (!$candidate.StartsWith($rootFull.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
    Fail ($Name + ' escapes AssetRoot')
  }
  $parts = $candidate.Substring($rootFull.TrimEnd('\').Length + 1).Split('\')
  $cursor = $rootFull
  foreach ($part in $parts) {
    $cursor = Join-Path $cursor $part
    if (Test-Path -LiteralPath $cursor) {
      $item = Get-Item -LiteralPath $cursor -Force
      if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Fail ($Name + ' contains a reparse point')
      }
    }
  }
  return $candidate
}

function Assert-ExactProperties([object]$Value, [string[]]$Expected, [string]$Name) {
  if ($null -eq $Value) { Fail ($Name + ' is missing') }
  $actual = @($Value.PSObject.Properties.Name | Sort-Object)
  $wanted = @($Expected | Sort-Object)
  if ((Compare-Object $actual $wanted)) {
    Fail ($Name + ' has unexpected or missing fields')
  }
}

function Read-Manifest([string]$Path) {
  if (!(Test-Path -LiteralPath $Path -PathType Leaf)) { Fail 'manifest file is missing' }
  $raw = [IO.File]::ReadAllBytes($Path)
  if ($raw.Length -gt 16384) { Fail 'manifest exceeds 16 KiB' }
  try { $value = [Text.Encoding]::UTF8.GetString($raw) | ConvertFrom-Json } catch { Fail 'manifest is not valid JSON' }
  $manifestFields = @($value.PSObject.Properties.Name)
  if (($manifestFields | Where-Object { $_ -notin @('images', 'schemaVersion', 'volume') }) -or
      ('images' -notin $manifestFields) -or ('schemaVersion' -notin $manifestFields)) {
    Fail 'manifest has unexpected or missing fields'
  }
  $volume = if ('volume' -in $manifestFields) { $value.volume } else { $null }
  if ($value.schemaVersion -ne 1) { Fail 'unsupported manifest schemaVersion' }
  if ($value.images -isnot [array] -or $value.images.Count -ne 3) { Fail 'manifest must contain exactly three images' }
  $required = @('rust', 'tools', 'openssl')
  $seen = @{}
  foreach ($entry in $value.images) {
    Assert-ExactProperties $entry @('archive', 'archiveSha256', 'imageId', 'key') 'image entry'
    if ($entry.key -notin $required -or $seen.ContainsKey($entry.key)) { Fail 'image keys must be unique rust/tools/openssl' }
    $seen[$entry.key] = $true
    if ($entry.imageId -notmatch '^sha256:[0-9a-f]{64}$') { Fail ($entry.key + ' imageId is invalid') }
    if ($entry.imageId -ne $script:ExpectedImageIds[$entry.key]) { Fail ($entry.key + ' imageId differs from the qualification contract') }
    Assert-Hash $entry.archiveSha256 ($entry.key + ' archiveSha256')
  }
  foreach ($key in $required) { if (!$seen.ContainsKey($key)) { Fail ('missing image: ' + $key) } }
  if ($null -ne $volume) {
    Assert-ExactProperties $volume @('archive', 'archiveSha256', 'name') 'volume entry'
    if ($volume.name -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$') { Fail 'volume name is invalid' }
    Assert-Hash $volume.archiveSha256 'volume archiveSha256'
  }
  if ('volume' -notin $manifestFields) {
    $value | Add-Member -NotePropertyName volume -NotePropertyValue $null
  }
  return $value
}

function Assert-Archive([object]$Entry, [string]$Root) {
  $path = Resolve-SafeAssetPath $Root ([string]$Entry.archive) ([string]$Entry.key + ' archive')
  if (!(Test-Path -LiteralPath $path -PathType Leaf)) { Fail ($Entry.key + ' archive is missing') }
  $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($actual -ne $Entry.archiveSha256) { Fail ($Entry.key + ' archive SHA-256 mismatch') }
  return $path
}

$root = [IO.Path]::GetFullPath($AssetRoot)
if (!(Test-Path -LiteralPath $root -PathType Container)) { Fail 'AssetRoot is missing' }
if ([string]::IsNullOrWhiteSpace($Manifest)) { $Manifest = Join-Path $root 'manifest.json' }
$manifestFull = [IO.Path]::GetFullPath($Manifest)
if (!$manifestFull.StartsWith($root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
  Fail 'manifest must be inside AssetRoot'
}
$manifest = Resolve-SafeAssetPath $root $manifestFull.Substring($root.TrimEnd('\').Length + 1) 'manifest'
$record = Read-Manifest $manifest
$archives = @{}
foreach ($entry in $record.images) { $archives[$entry.key] = Assert-Archive $entry $root }

if ($null -ne $record.volume) {
  $volumeArchive = Resolve-SafeAssetPath $root ([string]$record.volume.archive) 'volume archive'
  if (!(Test-Path -LiteralPath $volumeArchive -PathType Leaf)) { Fail 'volume archive is missing' }
  $actual = (Get-FileHash -LiteralPath $volumeArchive -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($actual -ne $record.volume.archiveSha256) { Fail 'volume archive SHA-256 mismatch' }
}

$result = [ordered]@{
  schemaVersion = 1
  status = 'archive-hashes-matched'
  assetRoot = $root
  manifest = $manifest
  images = @($record.images | ForEach-Object { [ordered]@{ key = $_.key; imageId = $_.imageId; archive = $_.archive } })
  volume = if ($null -eq $record.volume) { $null } else { [ordered]@{ name = $record.volume.name; archive = $record.volume.archive } }
  loadImages = [bool]$LoadImages
  restoreVolume = [bool]$RestoreVolume
}
$result | ConvertTo-Json -Depth 8
