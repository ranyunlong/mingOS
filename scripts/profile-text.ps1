param([string]$OutputDir=(Join-Path $PSScriptRoot '../artifacts/text-engine'))
$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$evidenceDir=[IO.Path]::GetFullPath($OutputDir)
$probeExe=Join-Path $taskRoot '.cache/rust-target/debug/examples/text_probe.exe'
if (!(Test-Path -LiteralPath $probeExe)) { throw 'Build: scripts/rust.ps1 build --locked --example text_probe --no-default-features --features text' }
New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null
$info=[Diagnostics.ProcessStartInfo]::new($probeExe)
$info.UseShellExecute=$false
$info.CreateNoWindow=$true
$info.RedirectStandardInput=$true
$info.RedirectStandardOutput=$true
$info.RedirectStandardError=$true
$info.WorkingDirectory=$taskRoot
$info.ArgumentList.Add((Join-Path $evidenceDir 'font-preview.png'))
$probe=[Diagnostics.Process]::new()
$probe.StartInfo=$info
$peakPrivate=0L
$peakWorking=0L
$clock=[Diagnostics.Stopwatch]::StartNew()
function Read-ProbeLine {
  $pending=$probe.StandardOutput.ReadLineAsync()
  while (!$pending.IsCompleted) {
    if ($clock.ElapsedMilliseconds -gt 30000) { throw 'Font probe exceeded 30 seconds' }
    $probe.Refresh()
    $script:peakPrivate=[Math]::Max($script:peakPrivate,$probe.PrivateMemorySize64)
    $script:peakWorking=[Math]::Max($script:peakWorking,$probe.PeakWorkingSet64)
    Start-Sleep -Milliseconds 2
  }
  $line=$pending.GetAwaiter().GetResult()
  if (!$line) { throw 'Font probe ended without a response' }
  return ($line | ConvertFrom-Json)
}
try {
  if (!$probe.Start()) { throw 'Font probe failed to start' }
  $stderr=$probe.StandardError.ReadToEndAsync()
  $before=Read-ProbeLine
  if ($before.phase -ne 'before') { throw 'Unexpected baseline handshake' }
  $probe.Refresh()
  $baselinePrivate=$probe.PrivateMemorySize64
  $baselineWorking=$probe.WorkingSet64
  $probe.StandardInput.WriteLine('continue')
  $result=Read-ProbeLine
  if ($result.phase -ne 'ready') { throw 'Unexpected font probe result' }
  $probe.Refresh()
  $peakPrivate=[Math]::Max($peakPrivate,$probe.PrivateMemorySize64)
  $peakWorking=[Math]::Max($peakWorking,$probe.PeakWorkingSet64)
  $afterPrivate=$probe.PrivateMemorySize64
  $afterWorking=$probe.WorkingSet64
  $probe.StandardInput.WriteLine('finish')
  if (!$probe.WaitForExit(5000)) { throw 'Font probe did not exit' }
  $stderr.GetAwaiter().GetResult() | Set-Content -LiteralPath (Join-Path $evidenceDir 'probe.stderr.txt')
  if ($probe.ExitCode -ne 0) { throw ('Font probe exit '+$probe.ExitCode) }
  $report=@{status='passed';recordedAt=(Get-Date).ToUniversalTime().ToString('o');probe=$result;baselinePrivateBytes=$baselinePrivate;afterPrivateBytes=$afterPrivate;sampledPeakPrivateBytes=$peakPrivate;baselineWorkingSetBytes=$baselineWorking;afterWorkingSetBytes=$afterWorking;osPeakWorkingSetBytes=$peakWorking;sampledPrivateIncreaseBytes=($peakPrivate-$baselinePrivate);privateQualificationTargetBytes=67108864;samplingIntervalMilliseconds=2;note='Process private-byte sampling and OS peak working set; not allocator accounting or a hard RSS guarantee. Includes font masks and explicit PNG preview work.';binarySha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $probeExe).Hash.ToLowerInvariant();fontSha256=(Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $taskRoot 'assets/fonts/NotoSansSC-Regular.otf')).Hash.ToLowerInvariant()}
  if ($report.sampledPrivateIncreaseBytes -gt $report.privateQualificationTargetBytes) { $report.status='qualification_target_exceeded' }
  $report | ConvertTo-Json -Depth 9 | Set-Content -LiteralPath (Join-Path $evidenceDir 'qualification.json')
  $report | ConvertTo-Json -Depth 9
  if ($report.status -ne 'passed') { throw 'Font private-memory qualification target exceeded' }
} finally {
  if ($probe.Id -and !$probe.HasExited) { $probe.Kill($true);$probe.WaitForExit(5000) | Out-Null }
  $probe.Dispose()
}
