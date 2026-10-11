param(
    [Parameter(Mandatory=$true)][string]$InputPath,
    [string]$ConfigDirectory = (Join-Path $env:USERPROFILE '.perdix'),
    [switch]$ValidateOnly
)
$ErrorActionPreference = 'Stop'
try {
    $inputData = [System.IO.File]::ReadAllText($InputPath) | ConvertFrom-Json
    $provider = [string]$inputData.provider
    if ($provider -notin @('anthropic', 'openai')) { throw '지원하지 않는 공급자입니다.' }
    $key = ([string]$inputData.api_key).Trim()
    $credentialsPath = Join-Path $ConfigDirectory 'credentials.json'
    $preferencesPath = Join-Path $ConfigDirectory 'preferences.json'
    $credentials = [pscustomobject]@{}
    if (Test-Path -LiteralPath $credentialsPath) {
        $credentials = [System.IO.File]::ReadAllText($credentialsPath) | ConvertFrom-Json
        if ($credentials -isnot [pscustomobject]) { throw '기존 키 파일 형식을 확인하세요.' }
    }
    $entry = $credentials.PSObject.Properties[$provider]
    if (-not $key -and $entry -and $entry.Value) {
        if ($entry.Value -is [string]) { $key = $entry.Value }
        else { $key = [string]$entry.Value.api_key }
    }
    if (-not $key -or $key -match '\s') { throw '사용할 공급자의 API 키를 입력하세요. 기존 키가 있으면 빈 칸으로 둘 수 있습니다.' }
    if ($ValidateOnly) { exit 0 }
    $preferences = [pscustomobject]@{}
    if (Test-Path -LiteralPath $preferencesPath) {
        $preferences = [System.IO.File]::ReadAllText($preferencesPath) | ConvertFrom-Json
        if ($preferences -isnot [pscustomobject]) { throw '기존 기본 설정 파일 형식을 확인하세요.' }
    }
    foreach ($name in @('anthropic', 'openai')) {
        if (-not $credentials.PSObject.Properties[$name]) {
            $credentials | Add-Member -NotePropertyName $name -NotePropertyValue ([pscustomobject]@{api_key=''})
        }
    }
    if ($credentials.$provider -isnot [pscustomobject]) {
        $credentials.$provider = [pscustomobject]@{}
    }
    $credentials.$provider | Add-Member -NotePropertyName 'api_key' -NotePropertyValue $key -Force
    $defaultModel = if ($provider -eq 'openai') { 'gpt-6.1-sol' } else { 'claude-opus-5-5' }
    $preferences | Add-Member -NotePropertyName 'default_model' -NotePropertyValue $defaultModel -Force
    New-Item -ItemType Directory -Force $ConfigDirectory | Out-Null
    # JSON 변환과 검증을 모두 마친 후 같은 폴더에서 원자적으로 교체한다.
    foreach ($item in @(@($credentialsPath, $credentials), @($preferencesPath, $preferences))) {
        $target = [string]$item[0]
        $temporary = $target + '.' + [guid]::NewGuid() + '.tmp'
        $backup = $temporary + '.bak'
        try {
            [System.IO.File]::WriteAllText($temporary, ($item[1] | ConvertTo-Json -Depth 32), [System.Text.UTF8Encoding]::new($false))
            # Windows PowerShell 5.1은 null 백업 경로를 빈 문자열로 변환하므로 실제 임시 경로를 쓴다.
            if (Test-Path -LiteralPath $target) { [System.IO.File]::Replace($temporary, $target, $backup) }
            else { [System.IO.File]::Move($temporary, $target) }
        } finally {
            if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
            if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup }
        }
    }
    exit 0
} catch {
    # 입력 키나 원본 JSON을 오류 출력에 포함하지 않는다.
    [Console]::Error.WriteLine('설정을 저장하지 못했습니다. API 키 입력과 기존 JSON 파일의 형식 및 쓰기 권한을 확인하세요.')
    exit 1
}
