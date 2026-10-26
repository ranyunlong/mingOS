# Project-local Windows bootstrap; all downloads are checked against official SHA-256.
$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$cache=Join-Path $taskRoot '.cache'
$downloadDir=Join-Path $cache 'rust-bootstrap'
New-Item -ItemType Directory -Path $downloadDir -Force | Out-Null
$curl=(Get-Command curl.exe -ErrorAction Stop).Source
function Get-OfficialText([string]$Url,[string]$Name) {
  $file=Join-Path $downloadDir $Name
  & $curl --fail --location --connect-timeout 10 --max-time 45 --retry 1 --silent --show-error --output $file $Url
  if($LASTEXITCODE -ne 0){throw "Download failed: $Name"}
  return [IO.File]::ReadAllText($file)
}
function Save-VerifiedDownload([string]$Url,[string]$Expected,[string]$Destination) {
  if($Expected -notmatch '^[0-9a-f]{64}$'){throw 'Invalid official checksum'}
  if((Test-Path -LiteralPath $Destination) -and (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant() -eq $Expected){return}
  $response=Invoke-WebRequest -Uri $Url -Method Head -TimeoutSec 20
  $size=[long]($response.Headers['Content-Length'] | Select-Object -First 1)
  if($size -lt 1){throw 'Missing download size'}
  $parts=Join-Path $downloadDir $Expected
  New-Item -ItemType Directory -Path $parts -Force | Out-Null
  $count=[int][Math]::Ceiling($size/1048576)
  $results=0..($count-1) | ForEach-Object -Parallel {
    $index=$_;$begin=[long]$index*1048576;$end=[Math]::Min($begin+1048575,$using:size-1)
    $file=Join-Path $using:parts ($index.ToString('D4')+'.part')
    if(!(Test-Path -LiteralPath $file) -or (Get-Item -LiteralPath $file).Length -ne ($end-$begin+1)) {
      & $using:curl --fail --location --connect-timeout 10 --max-time 60 --retry 1 --retry-delay 1 --silent --show-error --range "$begin-$end" --output $file $using:Url
    }
    [pscustomobject]@{Ok=((Test-Path -LiteralPath $file) -and (Get-Item -LiteralPath $file).Length -eq ($end-$begin+1))}
  } -ThrottleLimit 12
  if(@($results | Where-Object {-not $_.Ok}).Count){throw 'Download parts incomplete; rerun to resume'}
  New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($Destination)) -Force | Out-Null
  $output=[IO.File]::Create($Destination)
  try {foreach($index in 0..($count-1)){$partStream=[IO.File]::OpenRead((Join-Path $parts ($index.ToString('D4')+'.part')));try{$partStream.CopyTo($output)}finally{$partStream.Dispose()}}}finally{$output.Dispose()}
  if((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Expected){throw 'Official checksum mismatch'}
  Write-Output ('Verified: '+[IO.Path]::GetFileName($Url))
}
$env:CARGO_HOME=Join-Path $cache 'cargo'
$env:RUSTUP_HOME=Join-Path $cache 'rustup'
$env:RUSTUP_INIT_SKIP_PATH_CHECK='yes'
$initUrl='https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe'
$initHash=((Get-OfficialText ($initUrl+'.sha256') 'rustup-init.sha256').Trim() -split '\s+')[0]
$init=Join-Path $downloadDir 'rustup-init.exe'
Save-VerifiedDownload $initUrl $initHash $init
if(!(Test-Path -LiteralPath (Join-Path $env:CARGO_HOME 'bin\rustup.exe'))) {
  & $init -y --no-modify-path --default-toolchain none --profile minimal
  if($LASTEXITCODE -ne 0){throw 'Rustup bootstrap failed'}
}
$channelUrl='https://static.rust-lang.org/dist/channel-rust-1.99.1.toml'
$manifestHash=((Get-OfficialText ($channelUrl+'.sha256') 'channel.sha256').Trim() -split '\s+')[0]
$manifestFile=Join-Path $downloadDir 'channel.toml'
Save-VerifiedDownload $channelUrl $manifestHash $manifestFile
$manifest=[IO.File]::ReadAllText($manifestFile)
foreach($component in @('rustc','cargo','rust-std','rustfmt-preview','clippy-preview')) {
  $header='[pkg.'+$component+'.target.x86_64-pc-windows-msvc]'
  $start=$manifest.IndexOf($header,[StringComparison]::Ordinal)
  if($start -lt 0){throw "Missing component: $component"}
  $end=$manifest.IndexOf('[',$start+$header.Length)
  if($end -lt 0){$end=$manifest.Length}
  $block=$manifest.Substring($start,$end-$start)
  $url=[regex]::Match($block,'(?m)^xz_url = "([^"]+)"').Groups[1].Value
  $hash=[regex]::Match($block,'(?m)^xz_hash = "([^"]+)"').Groups[1].Value
  Save-VerifiedDownload $url $hash (Join-Path $env:RUSTUP_HOME ('downloads\'+$hash))
}
& (Join-Path $env:CARGO_HOME 'bin\rustup.exe') toolchain install 1.99.1 --profile minimal --component rustfmt --component clippy --no-self-update
if($LASTEXITCODE -ne 0){throw 'Rust toolchain install failed'}
& (Join-Path $env:CARGO_HOME 'bin\cargo.exe') --version
