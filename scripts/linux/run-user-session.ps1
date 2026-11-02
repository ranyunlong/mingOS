$ErrorActionPreference = 'Stop'

$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($identity -match 'codexsandboxoffline') {
    throw "Run this script from the Docker Desktop user's normal PowerShell session, not $identity."
}

$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $root
$run = Join-Path 'artifacts' ('linux-user-session-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))

docker version
python -B scripts/linux-container.py --build-profile release --mode all --renderer cpu --output $run
