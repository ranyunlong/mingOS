# Dot-source this file from the repository root to configure local build caches:
#   . .\scripts\dev-env.ps1
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$cache = Join-Path $taskRoot '.cache'
$javaHome = 'C:\Program Files\Microsoft\jdk-21.0.13.21-hotspot'

if (!(Test-Path -LiteralPath (Join-Path $javaHome 'bin\java.exe'))) {
  $javaHome = $env:JAVA_HOME
}
if (!$javaHome -or !(Test-Path -LiteralPath (Join-Path $javaHome 'bin\java.exe'))) {
  throw 'JDK 21 is required; set JAVA_HOME to a JDK 21 installation.'
}

$env:JAVA_HOME = $javaHome
$env:GRADLE_USER_HOME = Join-Path $cache 'gradle-user'
$env:CARGO_HOME = Join-Path $cache 'cargo'
$env:RUSTUP_HOME = Join-Path $cache 'rustup'
$env:CARGO_TARGET_DIR = Join-Path $cache 'rust-target'
$env:DOCKER_CONFIG = Join-Path $cache 'docker-config'
$pythonExe = Join-Path $cache 'python\runtime\python.exe'
if (Test-Path -LiteralPath $pythonExe) {
  $env:MING_PYTHON = $pythonExe
}
$javaTmp = Join-Path $cache 'java-tmp'
$env:JAVA_TOOL_OPTIONS = "-Djdk.net.unixdomain.tmpdir=`"$javaTmp`""
$env:PATH = (Join-Path $env:CARGO_HOME 'bin') + ';' + $env:PATH

foreach ($path in @($env:GRADLE_USER_HOME, $env:CARGO_HOME, $env:RUSTUP_HOME,
                    $env:CARGO_TARGET_DIR, $env:DOCKER_CONFIG, $javaTmp)) {
  New-Item -ItemType Directory -Path $path -Force | Out-Null
}

Write-Output ('MingOS environment configured: ' + $taskRoot)
Write-Output ('JAVA_HOME=' + $env:JAVA_HOME)
Write-Output ('GRADLE_USER_HOME=' + $env:GRADLE_USER_HOME)
Write-Output ('CARGO_HOME=' + $env:CARGO_HOME)
Write-Output ('RUSTUP_HOME=' + $env:RUSTUP_HOME)
Write-Output ('CARGO_TARGET_DIR=' + $env:CARGO_TARGET_DIR)
Write-Output ('DOCKER_CONFIG=' + $env:DOCKER_CONFIG)
if ($env:MING_PYTHON) { Write-Output ('MING_PYTHON=' + $env:MING_PYTHON) }
