param([Parameter(Mandatory=$true)][ValidatePattern('^[a-z0-9-]+$')][string]$Stage)
$ErrorActionPreference='Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$evidence = Join-Path $taskRoot "artifacts/app-data/rust-$Stage-windows"
New-Item -ItemType Directory -Path $evidence -ErrorAction Stop | Out-Null
$compiler = Join-Path $taskRoot '.cache/rustup/toolchains/1.99.1-x86_64-pc-windows-msvc/bin/rustc.exe'
$cargo = Join-Path $taskRoot '.cache/rustup/toolchains/1.99.1-x86_64-pc-windows-msvc/bin/cargo.exe'
$env:CARGO_HOME=Join-Path $taskRoot '.cache/cargo'
$env:RUSTC=$compiler
$env:CARGO_INCREMENTAL='0'
$env:RUSTFLAGS='-Dwarnings'
$inputs=@('crates/ming-app-host/src/app_data.rs','crates/ming-app-host/tests/app_data.rs','probes/app-data-sdk/windows-rust.ps1','probes/app-data-sdk/rust/Cargo.toml','probes/app-data-sdk/rust/Cargo.lock')
$hashes=[ordered]@{}
foreach($inputPath in $inputs){
 $source=Join-Path $taskRoot $inputPath
 $hashes[$inputPath]=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
 $target=Join-Path $evidence "sources/$inputPath"
 New-Item -ItemType Directory -Path (Split-Path $target) -Force | Out-Null
 Copy-Item -LiteralPath $source -Destination $target
}
$productionArgs=@('--crate-type=lib','--edition=2024','-Dwarnings',(Join-Path $taskRoot $inputs[0]),'-o',(Join-Path $evidence 'app_data.rlib'))
$testArgs=@('test','--offline','--manifest-path',(Join-Path $taskRoot 'probes/app-data-sdk/rust/Cargo.toml'),'--target-dir',(Join-Path $taskRoot 'artifacts/app-data/windows-target'),'--','--test-threads=1')
$report=[ordered]@{ stage=$Stage;status='running';inputs=$hashes;compiler=((& $compiler --version --verbose) -join "
");commands=@();startedAt=[DateTime]::UtcNow.ToString('o') }
try {
 foreach($job in @(@{executable=$compiler;argv=$productionArgs;log='production'},@{executable=$cargo;argv=$testArgs;log='tests'})){
  $output=& $job.executable @($job.argv) 2>&1
  $code=$LASTEXITCODE
  $output | Out-File -LiteralPath (Join-Path $evidence ($job.log+'.log')) -Encoding utf8
  $output | Write-Output
  $report.commands+=@{executable=$job.executable;argv=$job.argv;exitCode=$code}
  if($code -ne 0){throw "Command failed: $($job.log), exit $code"}
 }
 foreach($inputPath in $inputs){ if((Get-FileHash -LiteralPath (Join-Path $taskRoot $inputPath) -Algorithm SHA256).Hash.ToLowerInvariant() -ne $hashes[$inputPath]){throw "Input changed: $inputPath"} }
 $report.status='passed'
} catch { $report.status='failed';$report.error=$_.Exception.Message;throw }
finally { $report.finishedAt=[DateTime]::UtcNow.ToString('o');$report | ConvertTo-Json -Depth 10 | Out-File -LiteralPath (Join-Path $evidence 'receipt.json') -Encoding utf8 }
