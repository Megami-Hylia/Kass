. (Join-Path $PSScriptRoot 'desktop-common.ps1')
$launcherLock = $null
try {
    if (-not (Test-Path -LiteralPath $KassConfigFile)) { Write-Host 'Kass non e ancora stato avviato.'; exit 0 }
    $launcherLock = Get-KassLock
    $config = Get-Content -LiteralPath $KassConfigFile -Raw | ConvertFrom-Json
    if (Test-Path -LiteralPath $KassStateFile) {
        $state = Get-Content -LiteralPath $KassStateFile -Raw | ConvertFrom-Json
        if ($state.instance_id -ne $config.instance_id) { throw 'Stato processi non corrispondente; nessun processo e stato fermato.' }
        Stop-VerifiedKassProcess $state.worker $config
        Stop-VerifiedKassProcess $state.api $config
        Remove-Item -LiteralPath $KassStateFile -Force
    }
    if ((Test-Path -LiteralPath (Join-Path $KassPgBin 'pg_ctl.exe')) -and (Test-KassPostgresOwnership)) {
        $pgProcess = Start-KassProcess (Join-Path $KassPgBin 'pg_ctl.exe') @('stop', '-D', $KassData, '-m', 'fast', '-w', '-t', '45') 'postgres-stop'
        if (-not $pgProcess.WaitForExit(55000) -or (Test-KassPostgresOwnership)) { throw 'PostgreSQL non ha completato la chiusura. Consulta .local\logs\postgres-stop-error.log.' }
    }
    Write-Host 'Kass arrestato. I tuoi MP3, playlist e preferiti sono conservati in .local.'
} catch {
    Write-Host "ERRORE: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    if ($null -ne $launcherLock) { $launcherLock.Dispose() }
}
