param(
    [switch]$CheckOnly,
    [switch]$TestInstaller,
    [switch]$KeepBuildFiles,
    [string]$InnoCompiler
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$outputRoot = Join-Path $repoRoot 'dist'
$workRoot = Join-Path $outputRoot ('.windows-build-' + [guid]::NewGuid())
$releaseRoot = Join-Path $workRoot 'release'
$buildRoot = Join-Path $workRoot 'python'

if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem) {
    throw 'Build requires 64-bit Windows.'
}
foreach ($program in @('node', 'pnpm', 'bun', 'uv')) {
    if (-not (Get-Command $program -ErrorAction SilentlyContinue)) {
        throw "Required build tool missing: $program. See README.md."
    }
}
$nodeVersion = & node -p 'process.versions.node'
if ($LASTEXITCODE -ne 0 -or [version]$nodeVersion -lt [version]'22.0.0') {
    throw 'Node.js 22 or newer is required.'
}
if (-not $InnoCompiler) {
    $candidates = @(
        "$env:LOCALAPPDATA/Programs/Inno Setup 6/ISCC.exe",
        "${env:ProgramFiles(x86)}/Inno Setup 6/ISCC.exe",
        "$env:ProgramFiles/Inno Setup 6/ISCC.exe"
    )
    $InnoCompiler = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $InnoCompiler) {
        $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($command) { $InnoCompiler = $command.Source }
    }
}
if (-not $InnoCompiler -or -not (Test-Path -LiteralPath $InnoCompiler -PathType Leaf)) {
    throw 'Inno Setup 6 is required. Install it or pass -InnoCompiler with the path to ISCC.exe.'
}
$InnoCompiler = (Resolve-Path -LiteralPath $InnoCompiler).Path
Write-Host 'Build tools OK: Node.js, pnpm, Bun, uv, Inno Setup.'
if ($CheckOnly) { return }

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed: $LASTEXITCODE" }
}

Push-Location $repoRoot
try {
    New-Item -ItemType Directory -Force $releaseRoot, $buildRoot | Out-Null
    Write-Host '[1/5] Installing locked dependencies...'
    Invoke-Checked 'pnpm' @('install', '--frozen-lockfile')
    Invoke-Checked 'uv' @('sync', '--locked', '--project', 'server')
    Write-Host '[2/5] Building CLI...'
    Invoke-Checked 'pnpm' @('--filter', '@perdix/protocol', 'build')
    Invoke-Checked 'bun' @('build', './packages/cli/src/main.ts', '--compile', '--target=bun-windows-x64', '--define', 'process.env.DEV=false', '--define', 'process.env.NODE_ENV="production"', '--outfile', (Join-Path $releaseRoot 'perdix.exe'))
    Write-Host '[3/5] Building Python server...'
    Invoke-Checked 'uv' @('run', '--project', 'server', '--locked', '--with', 'pyinstaller==6.22.3', 'pyinstaller', '--noconfirm', '--clean', '--onedir', '--name', 'server', '--distpath', $releaseRoot, '--workpath', $buildRoot, '--specpath', $buildRoot, '--paths', (Join-Path $repoRoot 'server/src'), '--collect-all', 'acp', '--collect-all', 'anthropic', '--collect-all', 'openai', '--collect-all', 'jsonschema_specifications', (Join-Path $repoRoot 'server/scripts/frozen_entry.py'))
    Move-Item -LiteralPath (Join-Path $releaseRoot 'server/server.exe') -Destination (Join-Path $releaseRoot 'server/perdix-server.exe') -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'windows-README.txt') -Destination (Join-Path $releaseRoot 'README.txt')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'windows/credentials.example.json') -Destination $releaseRoot -Force
    Write-Host '[4/5] Building installer...'
    & (Join-Path $PSScriptRoot 'build-installer.ps1') -ReleaseDirectory $releaseRoot -InnoCompiler $InnoCompiler
    Write-Host '[5/5] Checking binary...'
    Invoke-Checked (Join-Path $releaseRoot 'perdix.exe') @('--version')
    if ($TestInstaller) {
        & (Join-Path $PSScriptRoot 'test-windows-installer.ps1') -ReleaseDirectory $releaseRoot -InnoCompiler $InnoCompiler
        Move-Item -LiteralPath (Join-Path $outputRoot 'Perdix-Setup-test.exe') -Destination $workRoot
    }
    $installerPath = Join-Path $outputRoot 'Perdix-Setup-windows-x64.exe'
    $sha256 = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($installerPath)
    try {
        $hash = [BitConverter]::ToString($sha256.ComputeHash($stream)).Replace('-', '')
        Write-Host "SHA256: $hash"
    } finally {
        $stream.Dispose()
        $sha256.Dispose()
    }
    if ($KeepBuildFiles) {
        Write-Host "Build files retained: $workRoot"
    } else {
        $resolvedWork = (Resolve-Path -LiteralPath $workRoot).Path
        $expectedPrefix = (Resolve-Path -LiteralPath $outputRoot).Path + '\.windows-build-'
        if (-not $resolvedWork.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase) -or
            ((Get-Item -LiteralPath $resolvedWork).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Unsafe cleanup path: $resolvedWork"
        }
        try {
            Add-Type -AssemblyName Microsoft.VisualBasic
            [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($resolvedWork,
                [Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs,
                [Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin)
            Write-Host 'Temporary build files moved to Recycle Bin.'
        } catch {
            Write-Warning "Installer ready, but temporary files could not be recycled. Retained: $resolvedWork"
        }
    }
    Write-Host "Ready: $outputRoot\Perdix-Setup-windows-x64.exe"
} catch {
    Write-Warning "Build failed. Diagnostic files retained at: $workRoot"
    throw
} finally {
    Pop-Location
}
