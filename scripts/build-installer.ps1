param(
    [string]$ReleaseDirectory = (Join-Path $PSScriptRoot '../dist/perdix-windows-x64'),
    [string]$InnoCompiler
)
$ErrorActionPreference = 'Stop'
$compiler = $InnoCompiler
if (-not $compiler) { $compiler = Join-Path $env:LOCALAPPDATA 'Programs/Inno Setup 6/ISCC.exe' }
if (-not (Test-Path -LiteralPath $compiler)) {
    $compiler = (Get-Command ISCC.exe -ErrorAction Stop).Source
}
$releasePath = (Resolve-Path -LiteralPath $ReleaseDirectory).Path
& $compiler /Q "/DReleaseDir=$releasePath" (Join-Path $PSScriptRoot 'windows/installer.iss')
if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
Get-Item (Join-Path $PSScriptRoot '../dist/Perdix-Setup-windows-x64.exe') | Select-Object FullName, Length
