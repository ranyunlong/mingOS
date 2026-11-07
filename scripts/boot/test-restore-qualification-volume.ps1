param([string]$PythonExecutable = '')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
  $PythonExecutable = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
}
$draft = Join-Path $PSScriptRoot 'restore-qualification-assets.ps1'
$fixture = Join-Path $PSScriptRoot 'generate_ps_fixture.py'
$fake = Join-Path $PSScriptRoot 'fake_volume_docker.py'
$parent = Join-Path $PSScriptRoot '../../artifacts/qualification-restore-controls'
New-Item -ItemType Directory -Path $parent -Force | Out-Null
$testRoot = Join-Path $parent ('ps-' + [guid]::NewGuid().ToString('N'))
$bin = Join-Path $testRoot 'bin'
New-Item -ItemType Directory -Path $bin -Force | Out-Null
$launcher = @'
using System;
using System.Diagnostics;
using System.Text;
using System.IO;
using System.Threading.Tasks;
public static class Program {
  static string Q(string input) {
    StringBuilder value = new StringBuilder("\""); int slashes = 0;
    foreach (char c in input) {
      if (c == '\\') { slashes++; continue; }
      if (c == '"') { value.Append('\\', slashes * 2 + 1); value.Append('"'); }
      else { value.Append('\\', slashes); value.Append(c); }
      slashes = 0;
    }
    value.Append('\\', slashes * 2); value.Append('"'); return value.ToString();
  }
  public static int Main(string[] args) {
    StringBuilder command = new StringBuilder("-B ");
    command.Append(Q(Environment.GetEnvironmentVariable("MINGOS_TEST_FAKE"))); command.Append(" ");
    command.Append(Q(Environment.GetEnvironmentVariable("MINGOS_TEST_STATE"))); command.Append(" ");
    command.Append(Q(Environment.GetEnvironmentVariable("MINGOS_TEST_SCENARIO")));
    foreach (string arg in args) { command.Append(" "); command.Append(Q(arg)); }
    ProcessStartInfo start = new ProcessStartInfo(Environment.GetEnvironmentVariable("MINGOS_TEST_PYTHON"), command.ToString());
    start.UseShellExecute = false; start.CreateNoWindow = true;
    start.RedirectStandardInput = true; start.RedirectStandardOutput = true; start.RedirectStandardError = true;
    Process child = Process.Start(start);
    Task input = Task.Factory.StartNew(() => {
      try { Console.OpenStandardInput().CopyTo(child.StandardInput.BaseStream); }
      catch (IOException) { }
      finally { child.StandardInput.Close(); }
    });
    Task output = Task.Factory.StartNew(() => { child.StandardOutput.BaseStream.CopyTo(Console.OpenStandardOutput()); });
    Task error = Task.Factory.StartNew(() => { child.StandardError.BaseStream.CopyTo(Console.OpenStandardError()); });
    child.WaitForExit(); Task.WaitAll(input, output, error); return child.ExitCode;
  }
}
'@
$launcherPath = Join-Path $testRoot 'fake-docker.cs'
[IO.File]::WriteAllText($launcherPath, $launcher, [Text.UTF8Encoding]::new($false))
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compiler /nologo /target:exe ("/out:" + (Join-Path $bin 'docker.exe')) $launcherPath | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to compile fake Docker subprocess launcher' }
$powershell = (Get-Command powershell.exe -CommandType Application | Select-Object -First 1).Source
$oldPath = $env:PATH
$oldModulePath = $env:PSModulePath
$oldPython = $env:MINGOS_TEST_PYTHON
$oldFake = $env:MINGOS_TEST_FAKE
$oldState = $env:MINGOS_TEST_STATE
$oldScenario = $env:MINGOS_TEST_SCENARIO
$passed = 0
$caseResults = [Collections.Generic.List[object]]::new()

function Invoke-Case([string]$Name, [string]$FixtureMode, [string]$Scenario, [string[]]$Flags, [bool]$Succeed, [string]$Message) {
  $caseRoot = Join-Path $testRoot $Name
  $fakeRoot = Join-Path $caseRoot 'fake'
  New-Item -ItemType Directory -Path $fakeRoot -Force | Out-Null
  & $PythonExecutable -B $fixture $caseRoot $FixtureMode
  if ($LASTEXITCODE -ne 0) { throw ('Fixture creation failed: ' + $Name) }
  $env:MINGOS_TEST_STATE = $fakeRoot
  $env:MINGOS_TEST_SCENARIO = $Scenario
  $stdout = Join-Path $caseRoot 'stdout.log'
  $stderr = Join-Path $caseRoot 'stderr.log'
  $receiptRoot = Join-Path $caseRoot 'retained-receipts'
  $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $draft,
    '-AssetRoot', (Join-Path $caseRoot 'assets'), '-PythonExecutable', $PythonExecutable,
    '-ReceiptRoot', $receiptRoot) + $Flags
  $process = Start-Process -FilePath $powershell -ArgumentList $arguments -Wait -PassThru -NoNewWindow -RedirectStandardOutput $stdout -RedirectStandardError $stderr
  $text = [IO.File]::ReadAllText($stdout) + [IO.File]::ReadAllText($stderr)
  if (($process.ExitCode -eq 0) -ne $Succeed) { throw "$Name returned unexpected exit $($process.ExitCode): $text" }
  if ($Message -and $text -notmatch $Message) { throw "$Name did not report expected boundary $Message : $text" }
  $eventsPath = Join-Path $fakeRoot 'events.jsonl'
  $events = @(if (Test-Path -LiteralPath $eventsPath) { [IO.File]::ReadAllLines($eventsPath) | ForEach-Object { $_ | ConvertFrom-Json } })
  foreach ($event in $events) {
    if (@($event.argv | Where-Object { $_ -in @('rm', 'rmi', 'prune', '--rm', 'volume') }).Count) {
      throw "$Name issued a resource removal or standalone volume command"
    }
  }
  if ($Name -eq 'success') {
    $value = [IO.File]::ReadAllText($stdout) | ConvertFrom-Json
    if ($value.status -ne 'volume-restored-readonly-verified' -or $value.formalQualification -or $value.finalSystemAccepted -or $value.subpath -ne 'payload' -or $value.volumeName -eq 'caller-existing-cache') {
      throw 'Public success overstated its boundary or adopted caller volume'
    }
  } elseif ($Name -eq 'validate-only') {
    $value = [IO.File]::ReadAllText($stdout) | ConvertFrom-Json
    if ($value.status -ne 'archive-hashes-matched' -or $value.restoreVolume -or $value.loadImages -or $events.Count) {
      throw 'Read-only validation overstated its verified boundary'
    }
  } elseif ($Name -in @('invalid-archive', 'mutable-image', 'conflict-validate', 'conflict-load')) {
    if ($events.Count) { throw "$Name called Docker before admission" }
  } elseif ($Name -in @('worker-failure', 'rw-verifier')) {
    $value = [IO.File]::ReadAllText($stdout) | ConvertFrom-Json
    if ($value.status -ne 'failed' -or $value.finalSystemAccepted -or !$value.creatorId -or !$value.restoreContainerId) {
      throw "$Name lost failed resource identity"
    }
    if (!(Get-ChildItem -LiteralPath $receiptRoot -Filter '*failure.json')) { throw "$Name lost durable failure receipt" }
  }
  $caseResults.Add([ordered]@{
    name = $Name; exitCode = $process.ExitCode; expectedSuccess = $Succeed
    stdoutPath = $stdout; stderrPath = $stderr; dockerEvents = $events.Count
    stdoutSha256 = (Get-FileHash -LiteralPath $stdout -Algorithm SHA256).Hash.ToLowerInvariant()
    stderrSha256 = (Get-FileHash -LiteralPath $stderr -Algorithm SHA256).Hash.ToLowerInvariant()
  })
  $script:passed++
}

try {
  # The child is Windows PowerShell even when this harness runs under pwsh.
  # Pin its built-in modules before reducing PATH to the fake Docker launcher.
  $env:PSModulePath = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\Modules'
  $env:PATH = $bin
  $env:MINGOS_TEST_PYTHON = $PythonExecutable
  $env:MINGOS_TEST_FAKE = $fake
  Invoke-Case 'conflict-validate' 'valid' 'success' @('-ValidateOnly', '-RestoreVolume') $false 'mutually exclusive'
  Invoke-Case 'conflict-load' 'valid' 'success' @('-LoadImages', '-RestoreVolume') $false 'mutually exclusive'
  Invoke-Case 'mutable-image' 'valid' 'success' @('-RestoreVolume', '-VolumeHelperImage', 'debian:trixie-slim') $false 'exact fixed helper'
  Invoke-Case 'invalid-archive' 'bad-inner' 'success' @('-RestoreVolume') $false 'QUALIFICATION_ASSET_RESTORE_BLOCKED'
  Invoke-Case 'validate-only' 'bad-inner' 'success' @('-ValidateOnly') $true 'archive-hashes-matched'
  Invoke-Case 'success' 'valid' 'success' @('-RestoreVolume') $true 'volume-restored-readonly-verified'
  Invoke-Case 'worker-failure' 'valid' 'fail-restore' @('-RestoreVolume') $false 'QUALIFICATION_ASSET_RESTORE_BLOCKED'
  Invoke-Case 'rw-verifier' 'valid' 'verify-rw' @('-RestoreVolume') $false 'QUALIFICATION_ASSET_RESTORE_BLOCKED'
} finally {
  $env:PATH = $oldPath
  $env:PSModulePath = $oldModulePath
  $env:MINGOS_TEST_PYTHON = $oldPython
  $env:MINGOS_TEST_FAKE = $oldFake
  $env:MINGOS_TEST_STATE = $oldState
  $env:MINGOS_TEST_SCENARIO = $oldScenario
}
$receipt = [ordered]@{
  schemaVersion = 1; status = 'passed'; testsRun = $passed; passed = $passed
  scope = 'PowerShell public entry with real fake CLI subprocesses; no live Docker resources'
  formalQualification = $false; finalSystemAccepted = $false; retainedRoot = $testRoot
  cases = @($caseResults.ToArray())
  sourceSha256 = [ordered]@{
    publicEntry = (Get-FileHash -LiteralPath $draft -Algorithm SHA256).Hash.ToLowerInvariant()
    controls = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
    fakeDocker = (Get-FileHash -LiteralPath $fake -Algorithm SHA256).Hash.ToLowerInvariant()
  }
}
$receiptPath = Join-Path $testRoot 'receipt.json'
$receiptBytes = [Text.UTF8Encoding]::new($false).GetBytes(($receipt | ConvertTo-Json -Depth 10))
$receiptFile = [IO.File]::Open($receiptPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
try { $receiptFile.Write($receiptBytes, 0, $receiptBytes.Length); $receiptFile.Flush($true) } finally { $receiptFile.Dispose() }
Write-Output "RestoreVolume public controls passed: $passed; retained fixtures: $testRoot"
