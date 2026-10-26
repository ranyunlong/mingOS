param([Parameter(ValueFromRemainingArguments=$true)][string[]]$CargoArgs)
$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$env:CARGO_HOME=Join-Path $taskRoot '.cache\cargo'
$env:RUSTUP_HOME=Join-Path $taskRoot '.cache\rustup'
$env:CARGO_TARGET_DIR=Join-Path $taskRoot '.cache\rust-target'
$env:PATH=(Join-Path $env:CARGO_HOME 'bin')+';'+$env:PATH
if($IsWindows) {
  $vswhere=Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
  $vs= & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
  if(!$vs){throw 'MSVC Build Tools are required. Use a Visual Studio C++ developer environment.'}
  $vc=(Get-ChildItem -LiteralPath (Join-Path $vs 'VC\Tools\MSVC') -Directory | Sort-Object Name -Descending | Select-Object -First 1).FullName
  $sdk=Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10'
  $sdkVersion=(Get-ChildItem -LiteralPath (Join-Path $sdk 'Lib') -Directory | Where-Object {Test-Path -LiteralPath (Join-Path $_.FullName 'um\x64\kernel32.lib')} | Sort-Object Name -Descending | Select-Object -First 1).Name
  if(!$vc -or !$sdkVersion){throw 'MSVC or Windows SDK is incomplete'}
  $env:PATH=(Join-Path $vc 'bin\Hostx64\x64')+';'+(Join-Path $sdk ('bin\'+$sdkVersion+'\x64'))+';'+$env:PATH
  $env:LIB=(Join-Path $vc 'lib\x64')+';'+(Join-Path $sdk ('Lib\'+$sdkVersion+'\ucrt\x64'))+';'+(Join-Path $sdk ('Lib\'+$sdkVersion+'\um\x64'))
  $env:INCLUDE=(Join-Path $vc 'include')+';'+(Join-Path $sdk ('Include\'+$sdkVersion+'\ucrt'))+';'+(Join-Path $sdk ('Include\'+$sdkVersion+'\shared'))+';'+(Join-Path $sdk ('Include\'+$sdkVersion+'\um'))
}
Push-Location -LiteralPath $taskRoot
try {& (Join-Path $env:CARGO_HOME 'bin\cargo.exe') @CargoArgs;exit $LASTEXITCODE}finally{Pop-Location}
