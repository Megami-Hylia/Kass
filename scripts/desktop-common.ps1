$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$script:KassRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$script:KassLocal = Join-Path $KassRoot '.local'
$script:KassRunner = Join-Path $PSScriptRoot 'desktop.py'
$script:KassPython = Join-Path $KassLocal 'venv\Scripts\python.exe'
$script:KassPgBin = Join-Path $KassLocal 'postgres-runtime\node_modules\@embedded-postgres\windows-x64\native\bin'
$script:KassData = Join-Path $KassLocal 'postgres-data'
$script:KassConfigFile = Join-Path $KassLocal 'cass.json'
$script:KassStateFile = Join-Path $KassLocal 'processes.json'

function Write-KassJson($Path, $Value) {
    [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
}

function Get-KassLock {
    [IO.Directory]::CreateDirectory($KassLocal) | Out-Null
    try {
        return [IO.File]::Open((Join-Path $KassLocal 'launcher.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch {
        throw 'Un altro avvio o arresto di Kass e gia in corso. Attendi qualche minuto e riprova.'
    }
}

function ConvertTo-KassArgument([string]$Value) {
    # Windows CreateProcess quoting: preserve spaces, quotes and trailing slashes.
    return '"' + [regex]::Replace([regex]::Replace($Value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
}

function Start-KassProcess([string]$File, [string[]]$Arguments, [string]$LogName) {
    $argumentLine = ($Arguments | ForEach-Object { ConvertTo-KassArgument $_ }) -join ' '
    $process = Start-Process -FilePath $File -ArgumentList $argumentLine -WorkingDirectory $KassRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $KassLocal "logs\$LogName.log") -RedirectStandardError (Join-Path $KassLocal "logs\$LogName-error.log")
    # Cache the native handle before a short-lived pg_ctl process exits.
    $null = $process.Handle
    return $process
}

function Get-KassProcessRecord($Process, [string]$Role) {
    return @{ pid = $Process.Id; role = $Role; started_utc = $Process.StartTime.ToUniversalTime().ToString('o') }
}

function Get-VerifiedKassProcess($Record, $Config) {
    if ($null -eq $Record) { return $null }
    $process = Get-Process -Id ([int]$Record.pid) -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    if ($process.StartTime.ToUniversalTime() -ne ([datetime]$Record.started_utc).ToUniversalTime()) { return $null }
    $info = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)"
    if ($null -eq $info -or [string]::IsNullOrWhiteSpace($info.CommandLine)) { return $null }
    if ($info.CommandLine.IndexOf($KassRunner, [StringComparison]::OrdinalIgnoreCase) -lt 0 -or
        $info.CommandLine.IndexOf([string]$Config.instance_id, [StringComparison]::Ordinal) -lt 0 -or
        $info.CommandLine -notmatch ('--role["\s]+["\s]*' + [regex]::Escape([string]$Record.role) + '(?:["\s]|$)')) { return $null }
    return $process
}

function Test-KassPort([int]$Port) {
    $client = New-Object Net.Sockets.TcpClient
    try {
        $wait = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $wait.AsyncWaitHandle.WaitOne(300)) { return $false }
        $client.EndConnect($wait)
        return $true
    } catch { return $false } finally { $client.Dispose() }
}

function Test-KassHealth($Config) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$($Config.app_port)/api/health" -TimeoutSec 3
        return ($health.status -eq 'ok' -and $health.database -eq 'postgresql' -and $health.instance_id -eq $Config.instance_id)
    } catch { return $false }
}

function Stop-VerifiedKassProcess($Record, $Config) {
    $process = Get-VerifiedKassProcess $Record $Config
    if ($null -eq $process) { return }
    # Capture and verify ancestry before terminating any conversion descendants.
    $allProcesses = @(Get-CimInstance Win32_Process)
    $descendants = New-Object 'System.Collections.Generic.List[object]'
    $pending = New-Object 'System.Collections.Generic.Queue[uint32]'
    $pending.Enqueue([uint32]$process.Id)
    while ($pending.Count -gt 0) {
        $parentId = $pending.Dequeue()
        foreach ($child in $allProcesses) {
            if ($child.ParentProcessId -eq $parentId -and $child.CreationDate.ToUniversalTime() -ge $process.StartTime.ToUniversalTime()) {
                $descendants.Add($child)
                $pending.Enqueue([uint32]$child.ProcessId)
            }
        }
    }
    # Stop the owned parent first so it cannot create new child processes.
    if ($null -ne (Get-VerifiedKassProcess $Record $Config)) { Stop-Process -Id $process.Id -Force }
    foreach ($child in $descendants) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId = $($child.ProcessId)"
        if ($null -ne $current -and $current.CreationDate -eq $child.CreationDate -and $current.CommandLine -eq $child.CommandLine) {
            Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}

function Test-KassPostgresOwnership {
    $pidFile = Join-Path $KassData 'postmaster.pid'
    if (-not (Test-Path -LiteralPath $pidFile)) { return $false }
    $lines = @(Get-Content -LiteralPath $pidFile)
    if ($lines.Count -lt 3 -or $lines[0] -notmatch '^\d+$') { throw 'Il file di stato PostgreSQL non e valido; non arresto alcun processo.' }
    if ([IO.Path]::GetFullPath($lines[1]).TrimEnd('\', '/') -ne $KassData.TrimEnd('\', '/')) { throw 'La cartella dati PostgreSQL non corrisponde a questa installazione.' }
    $info = Get-CimInstance Win32_Process -Filter "ProcessId = $($lines[0])"
    if ($null -eq $info) { return $false }
    $expected = Join-Path $KassPgBin 'postgres.exe'
    $normalizedCommand = ([string]$info.CommandLine).Replace('/', '\')
    if ($info.ExecutablePath -ne $expected -or $normalizedCommand.IndexOf($KassData, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        throw 'Il PID PostgreSQL appartiene a un altro processo; nessun arresto automatico.'
    }
    return $true
}
