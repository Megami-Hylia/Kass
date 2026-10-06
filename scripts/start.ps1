param([switch]$NoBrowser, [switch]$SkipInstall)
. (Join-Path $PSScriptRoot 'desktop-common.ps1')
$launcherLock = $null
try {
    $launcherLock = Get-KassLock
    [IO.Directory]::CreateDirectory((Join-Path $KassLocal 'logs')) | Out-Null
    if (-not (Test-Path -LiteralPath $KassConfigFile)) {
        $bytes = New-Object byte[] 32
        $random = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $random.GetBytes($bytes) } finally { $random.Dispose() }
        Write-KassJson $KassConfigFile @{
            instance_id = [guid]::NewGuid().ToString('N'); app_port = 8765; postgres_port = 55433
            postgres_password = ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
        }
    }
    $config = Get-Content -LiteralPath $KassConfigFile -Raw | ConvertFrom-Json
    $state = @{ instance_id = $config.instance_id; api = $null; worker = $null }
    if (Test-Path -LiteralPath $KassStateFile) {
        $saved = Get-Content -LiteralPath $KassStateFile -Raw | ConvertFrom-Json
        if ($saved.instance_id -ne $config.instance_id) { throw 'Stato processi non corrispondente alla configurazione locale.' }
        $state.api = $saved.api
        $state.worker = $saved.worker
    }
    $api = Get-VerifiedKassProcess $state.api $config
    $worker = Get-VerifiedKassProcess $state.worker $config
    if ($null -ne $api -and (Test-KassHealth $config) -and $null -ne $worker) {
        Write-Host "Kass e gia aperto: http://127.0.0.1:$($config.app_port)"
        if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$($config.app_port)" }
        exit 0
    }
    if ($null -ne $api -and -not (Test-KassHealth $config)) { throw 'Kass risulta avviato ma non risponde. Usa Arresta Kass.cmd e poi riaprilo; consulta .local\logs.' }
    if ($null -eq $api -and (Test-KassPort $config.app_port)) { throw "La porta $($config.app_port) e occupata da un altro processo. Non lo modifico. Cambia app_port in .local\cass.json a Kass fermo." }

    foreach ($command in @('ffmpeg.exe', 'ffprobe.exe')) {
        if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { throw "Manca $command nel PATH. Consulta i prerequisiti in README.md e riapri Kass dopo l'installazione." }
    }
    if (-not (Test-Path -LiteralPath $KassPython)) {
        if ($SkipInstall) { throw 'Runtime Python assente. Ripeti senza -SkipInstall.' }
        if (-not (Get-Command python.exe -ErrorAction SilentlyContinue)) { throw 'Installa Python 3.12 o successivo con Aggiungi al PATH, poi riapri Kass.' }
        Write-Host 'Preparazione Python locale (solo al primo avvio)...'
        & python.exe -m venv (Join-Path $KassLocal 'venv')
        if ($LASTEXITCODE -ne 0) { throw 'Creazione ambiente Python non riuscita.' }
    }
    $requirements = Join-Path $KassRoot 'backend\requirements.lock'
    if (-not (Test-Path -LiteralPath $requirements)) { $requirements = Join-Path $KassRoot 'backend\requirements.txt' }
    $requirementsHash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
    $requirementsStamp = Join-Path $KassLocal 'requirements.sha256'
    $installPython = -not (Test-Path -LiteralPath $requirementsStamp)
    if (-not $installPython) { $installPython = (Get-Content -LiteralPath $requirementsStamp -Raw).Trim() -ne $requirementsHash }
    if ($installPython -and -not $SkipInstall) {
        Write-Host 'Installazione dipendenze audio e API...'
        & $KassPython -m pip install --disable-pip-version-check -r $requirements
        if ($LASTEXITCODE -ne 0) { throw 'Installazione Python non riuscita. Controlla la connessione e riprova.' }
        [IO.File]::WriteAllText($requirementsStamp, $requirementsHash)
    }

    if (-not (Test-Path -LiteralPath (Join-Path $KassPgBin 'pg_ctl.exe'))) {
        foreach ($command in @('node.exe', 'npm.cmd')) {
            if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { throw "Manca $command per preparare PostgreSQL. Consulta README.md." }
        }
        if ($SkipInstall) { throw 'PostgreSQL locale assente. Ripeti senza -SkipInstall.' }
        $runtime = Join-Path $KassLocal 'postgres-runtime'
        [IO.Directory]::CreateDirectory($runtime) | Out-Null
        Write-KassJson (Join-Path $runtime 'package.json') @{ name = 'cass-local-postgres'; private = $true; version = '1.0.0'; dependencies = @{ '@embedded-postgres/windows-x64' = '17.10.0-beta.17' } }
        Write-Host 'Preparazione PostgreSQL 17 locale (nessun servizio Windows)...'
        & npm.cmd install --prefix $runtime --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Installazione PostgreSQL non riuscita. Controlla la connessione e riprova.' }
    }

    $frontend = Join-Path $KassRoot 'frontend'
    # A clean desktop copy ships a ready bundle and needs no Node or build scan.
    if (Test-Path -LiteralPath (Join-Path $frontend 'src')) {
    $frontendStamp = Join-Path $KassLocal 'frontend.sha256'
    $sourceFiles = @(Get-ChildItem -LiteralPath (Join-Path $frontend 'src') -File -Recurse)
    $sourceFiles += @(Get-ChildItem -LiteralPath $frontend -File | Where-Object { $_.Extension -in @('.json', '.ts', '.html') })
    if (Test-Path -LiteralPath (Join-Path $frontend 'public')) { $sourceFiles += @(Get-ChildItem -LiteralPath (Join-Path $frontend 'public') -File -Recurse) }
    $fingerprint = (($sourceFiles | Sort-Object FullName | ForEach-Object { $_.FullName.Substring($frontend.Length) + ':' + (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }) -join "`n")
    $buildNeeded = -not (Test-Path -LiteralPath $frontendStamp) -or -not (Test-Path -LiteralPath (Join-Path $frontend 'dist\index.html'))
    if (-not $buildNeeded) { $buildNeeded = (Get-Content -LiteralPath $frontendStamp -Raw) -ne $fingerprint }
    if ($buildNeeded -and -not $SkipInstall) {
        foreach ($command in @('node.exe', 'npm.cmd')) {
            if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { throw "Manca $command per compilare l'interfaccia." }
        }
        Write-Host 'Preparazione interfaccia Kass...'
        Push-Location $frontend
        try {
            & npm.cmd ci --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw 'Installazione interfaccia non riuscita.' }
            & npm.cmd run build
            if ($LASTEXITCODE -ne 0) { throw 'Build interfaccia non riuscito.' }
            [IO.File]::WriteAllText($frontendStamp, $fingerprint)
        } finally { Pop-Location }
    }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $frontend 'dist\index.html'))) { throw 'Interfaccia non presente. Estrai il pacchetto completo o ricompila la versione sorgente.' }

    if (-not (Test-Path -LiteralPath (Join-Path $KassData 'PG_VERSION'))) {
        Write-Host 'Creazione del database personale...'
        $passwordFile = Join-Path $KassLocal 'initdb-password.tmp'
        try {
            [IO.File]::WriteAllText($passwordFile, [string]$config.postgres_password, (New-Object Text.UTF8Encoding($false)))
            & (Join-Path $KassPgBin 'initdb.exe') -D $KassData -U cass --encoding=UTF8 --locale=C --auth-host=scram-sha-256 --auth-local=scram-sha-256 "--pwfile=$passwordFile"
            if ($LASTEXITCODE -ne 0) { throw 'Inizializzazione PostgreSQL non riuscita. Consulta README.md.' }
        } finally {
            if (Test-Path -LiteralPath $passwordFile) { Remove-Item -LiteralPath $passwordFile -Force }
        }
    }
    if (-not (Test-KassPostgresOwnership)) {
        if (Test-KassPort $config.postgres_port) { throw "La porta PostgreSQL $($config.postgres_port) e gia occupata. Cambia postgres_port in .local\cass.json a Kass fermo." }
        $pgOptions = "-h 127.0.0.1 -p $($config.postgres_port) -c password_encryption=scram-sha-256"
        $pgProcess = Start-KassProcess (Join-Path $KassPgBin 'pg_ctl.exe') @('start', '-D', $KassData, '-l', (Join-Path $KassLocal 'logs\postgres.log'), '-o', $pgOptions, '-w', '-t', '45') 'postgres-start'
        if (-not $pgProcess.WaitForExit(55000) -or -not (Test-KassPostgresOwnership)) { throw 'PostgreSQL non parte. Consulta .local\logs\postgres.log.' }
    }
    & $KassPython $KassRunner --role init-db --cass-instance $config.instance_id
    if ($LASTEXITCODE -ne 0) { throw 'Preparazione database Kass non riuscita.' }

    if ($null -eq $api) {
        Write-Host 'Avvio Kass sul tuo PC...'
        $api = Start-KassProcess $KassPython @($KassRunner, '--role', 'api', '--cass-instance', $config.instance_id) 'api'
        $state.api = Get-KassProcessRecord $api 'api'
        Write-KassJson $KassStateFile $state
        $healthy = $false
        for ($attempt = 0; $attempt -lt 45; $attempt++) {
            if (Test-KassHealth $config) { $healthy = $true; break }
            if ($api.HasExited) { break }
            Start-Sleep -Milliseconds 250
        }
        if (-not $healthy) { throw 'Kass non risponde. Consulta .local\logs\api-error.log, poi usa Arresta Kass.cmd prima di riprovare.' }
    }
    if ($null -eq $worker) {
        $worker = Start-KassProcess $KassPython @($KassRunner, '--role', 'worker', '--cass-instance', $config.instance_id) 'worker'
        $state.worker = Get-KassProcessRecord $worker 'worker'
        Write-KassJson $KassStateFile $state
        Start-Sleep -Milliseconds 250
        if ($worker.HasExited) { throw 'Il worker audio non parte. Consulta .local\logs\worker-error.log.' }
    }
    Write-Host "Kass e pronto: http://127.0.0.1:$($config.app_port)"
    Write-Host 'Per chiuderlo completamente, usa Arresta Kass.cmd. Libreria e playlist restano salvate.'
    if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$($config.app_port)" }
} catch {
    Write-Host "ERRORE: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    if ($null -ne $launcherLock) { $launcherLock.Dispose() }
}
