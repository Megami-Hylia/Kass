param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('api', 'worker', 'web')]
    [string]$Component
)

$ErrorActionPreference = 'Stop'
$cassRoot = Split-Path -Parent $PSScriptRoot
$cassEnvFile = Join-Path $cassRoot '.env'
if (Test-Path -LiteralPath $cassEnvFile) {
    foreach ($cassLine in Get-Content -LiteralPath $cassEnvFile) {
        $cassText = $cassLine.Trim()
        if (!$cassText -or $cassText.StartsWith('#')) { continue }
        $cassParts = $cassText -split '=', 2
        if ($cassParts.Length -eq 2) {
            $cassValue = $cassParts[1].Trim().Trim('"').Trim("'")
            Set-Item -Path "Env:$($cassParts[0].Trim())" -Value $cassValue
        }
    }
}

$cassDbPassword = if ($env:POSTGRES_PASSWORD) { $env:POSTGRES_PASSWORD } else { 'cass-local-dev-change-me' }
$cassDbPort = if ($env:POSTGRES_PORT) { $env:POSTGRES_PORT } else { '5432' }
$cassApiPort = if ($env:API_PORT) { $env:API_PORT } else { '8000' }
if (!$env:DATABASE_URL) {
    $cassEncodedPassword = [Uri]::EscapeDataString($cassDbPassword)
    $env:DATABASE_URL = "postgresql+psycopg://cass:${cassEncodedPassword}@127.0.0.1:${cassDbPort}/cass"
}
$env:MEDIA_ROOT = Join-Path $cassRoot 'data/media'
if (!$env:ALLOWED_ORIGINS) { $env:ALLOWED_ORIGINS = 'http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080' }
if (!$env:COOKIE_SECURE) { $env:COOKIE_SECURE = 'false' }

if ($Component -eq 'web') {
    Set-Location (Join-Path $cassRoot 'frontend')
    & npm.cmd run dev -- --host 127.0.0.1
    exit $LASTEXITCODE
}

$cassPython = Join-Path $cassRoot 'backend/.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $cassPython)) {
    throw 'Ambiente Python mancante. Dalla cartella cass: python -m venv backend/.venv; backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt'
}
Set-Location (Join-Path $cassRoot 'backend')
if ($Component -eq 'api') {
    & $cassPython -m uvicorn app.main:app --host 127.0.0.1 --port $cassApiPort --reload
} else {
    & $cassPython -m app.worker
}
exit $LASTEXITCODE
