param(
    [string]$ReleaseDirectory = (Join-Path $PSScriptRoot '../dist/perdix-windows-x64'),
    [string]$InnoCompiler
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$testRoot = Join-Path $env:TEMP ('perdix installer ' + [guid]::NewGuid())
New-Item -ItemType Directory $testRoot | Out-Null
$compiler = $InnoCompiler
if (-not $compiler) { $compiler = Join-Path $env:LOCALAPPDATA 'Programs/Inno Setup 6/ISCC.exe' }
$releasePath = (Resolve-Path -LiteralPath $ReleaseDirectory).Path
& $compiler /Q "/DTestRoot=$testRoot" "/DReleaseDir=$releasePath" (Join-Path $PSScriptRoot 'windows/installer.iss')
if ($LASTEXITCODE -ne 0) { throw 'Test installer compilation failed' }
$installer = Join-Path $repoRoot 'dist/Perdix-Setup-test.exe'
$inputPath = Join-Path $testRoot 'input.json'
$configRoot = Join-Path $testRoot 'profile/.perdix'
$credentialsPath = Join-Path $configRoot 'credentials.json'
$appPath = Join-Path $testRoot 'app/perdix.exe'
$vsixPath = Join-Path $testRoot 'app/perdix-vscode-0.0.1.vsix'

function Invoke-TestInstall([string]$LogName) {
    $logPath = Join-Path $testRoot $LogName
    $process = Start-Process -FilePath $installer -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', ('/CONFIG="' + $inputPath + '"'), ('/LOG="' + $logPath + '"')) -WindowStyle Hidden -PassThru -Wait
    return $process.ExitCode
}

[System.IO.File]::WriteAllText($inputPath, '{"provider":"openai","api_key":""}')
if ((Invoke-TestInstall 'empty.log') -eq 0) { throw 'Empty credentials should block first installation' }
if (Test-Path -LiteralPath $appPath) { throw 'Program installed without credentials' }

[System.IO.File]::WriteAllText($inputPath, '{"provider":"openai","api_key":"test-installer-key-only"}')
if ((Invoke-TestInstall 'install.log') -ne 0) { throw 'Installation failed' }
$credentials = [System.IO.File]::ReadAllText($credentialsPath) | ConvertFrom-Json
if ($credentials.openai.api_key -ne 'test-installer-key-only') { throw 'Key not saved' }
$preferences = Get-Content (Join-Path $configRoot 'preferences.json') -Raw | ConvertFrom-Json
if ($preferences.default_model -ne 'gpt-6.1-sol') { throw 'Default model not set' }
$desktop = Join-Path $testRoot 'desktop'
if (@(Get-ChildItem -LiteralPath $desktop -Filter '*.lnk').Count -ne 3) { throw 'Desktop shortcuts missing' }
if (-not (Test-Path -LiteralPath (Join-Path $testRoot 'start-menu/Perdix.lnk'))) { throw 'Start menu shortcut missing' }
if (-not (Test-Path -LiteralPath $vsixPath)) { throw 'VS Code extension package missing' }
if (-not (Test-Path -LiteralPath (Join-Path $testRoot 'vscode-installed.txt'))) { throw 'VS Code extension setup was not run' }
$shell = New-Object -ComObject WScript.Shell
if ($shell.CreateShortcut((Join-Path $desktop 'Perdix.lnk')).TargetPath -ne [System.IO.Path]::GetFullPath($appPath)) { throw 'Incorrect desktop target' }
& $appPath --version
if ($LASTEXITCODE -ne 0) { throw 'Installed binary failed' }

# 재설치 시 입력을 비워도 기존 키가 유지되어야 한다.
[System.IO.File]::WriteAllText($inputPath, '{"provider":"openai","api_key":""}')
if ((Invoke-TestInstall 'upgrade.log') -ne 0) { throw 'Upgrade failed' }
$credentials = [System.IO.File]::ReadAllText($credentialsPath) | ConvertFrom-Json
if ($credentials.openai.api_key -ne 'test-installer-key-only') { throw 'Upgrade lost credentials' }
foreach ($log in (Get-ChildItem -LiteralPath $testRoot -Filter '*.log')) {
    if ([System.IO.File]::ReadAllText($log.FullName).Contains('test-installer-key-only')) { throw 'Key leaked into installer log' }
}
$uninstaller = Join-Path $testRoot 'app/unins000.exe'
$uninstallProcess = Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -WindowStyle Hidden -PassThru -Wait
if ($uninstallProcess.ExitCode -ne 0) { throw 'Uninstall failed' }
if (Test-Path -LiteralPath $appPath) { throw 'Uninstall left program behind' }
if (Test-Path -LiteralPath (Join-Path $desktop 'Perdix.lnk')) { throw 'Uninstall left shortcut behind' }
if (-not (Test-Path -LiteralPath $credentialsPath)) { throw 'Uninstall removed user credentials' }
Write-Output 'PASS: missing-key rejection, install, API key, default model, shortcuts, VS Code extension, upgrade, log privacy, uninstall with credentials preserved'
