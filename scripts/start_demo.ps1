param([switch]$Restart)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    $projectPython = Join-Path $projectRoot '.venv-ml\Scripts\python.exe'
}
$runtimePath = Join-Path $projectRoot 'runtime'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Create .venv and install the requirements from README.md first.'
}
New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
$previousMode = $env:AXON_STORAGE_MODE
$previousBackendUrl = $env:BACKEND_URL
$env:AXON_STORAGE_MODE = 'memory'
$env:BACKEND_URL = 'http://127.0.0.1:8000'
try {
    Push-Location $projectRoot
    try {
        & $projectPython -c "import streamlit, fastapi; from pathlib import Path; from ml.bearing.bundle import load_bearing_bundle; print('Bundle:',load_bearing_bundle(Path('ml/artifacts/bearing/active')).metadata['model_version'])"
        if ($LASTEXITCODE -ne 0) { throw 'Dependency or model validation failed. Install the requirements in README.md.' }
    } finally { Pop-Location }
    $services = @(
        @{ Name='api'; Port=8000; Marker='backend.main:app'; Args=@('-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8000') },
        @{ Name='dashboard'; Port=8501; Marker='dashboard/app.py'; Args=@('-m','streamlit','run','dashboard/app.py','--server.address','127.0.0.1','--server.port','8501','--server.headless','true') }
    )
    foreach ($service in $services) {
        $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $service.Port -ErrorAction SilentlyContinue)
        if ($Restart -and $listeners.Count -gt 0) {
            foreach ($owner in ($listeners.OwningProcess | Select-Object -Unique)) {
                $process = Get-CimInstance Win32_Process -Filter "ProcessId=$owner"
                if ($process.CommandLine -notlike "*$($service.Marker)*") {
                    throw "Port $($service.Port) belongs to another application; it was not stopped."
                }
                Stop-Process -Id $owner -Force
            }
            $listeners = @()
        }
        if ($listeners.Count -eq 0) {
            $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
            Start-Process -FilePath $projectPython -ArgumentList $service.Args -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimePath "$($service.Name)-$stamp.log") -RedirectStandardError (Join-Path $runtimePath "$($service.Name)-$stamp.error.log") | Out-Null
        }
    }
    $healthy = $false
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        try {
            $ready = Invoke-RestMethod 'http://127.0.0.1:8000/ready' -TimeoutSec 2
            $model = Invoke-RestMethod 'http://127.0.0.1:8000/diagnostics/model-status' -TimeoutSec 3
            $verifyPage = Invoke-WebRequest 'http://127.0.0.1:8000/app/' -UseBasicParsing -TimeoutSec 2
            $page = Invoke-WebRequest 'http://127.0.0.1:8501/_stcore/health' -UseBasicParsing -TimeoutSec 2
            if ($ready.status -eq 'ready' -and $model.available -and $verifyPage.StatusCode -eq 200 -and $page.StatusCode -eq 200) {
                $healthy = $true
                break
            }
        } catch { }
        Start-Sleep -Seconds 1
    }
    if (-not $healthy) { throw "Startup did not complete. Inspect logs in $runtimePath." }
    Write-Output "Dashboard: http://127.0.0.1:8501 | AXON Verify: http://127.0.0.1:8000/app/ | Model: $($model.model_version) | Storage: $($ready.storage)"
    Write-Output "Replay runner: `"$projectPython`" -m edge.bearing_runner --transport loopback --interval 5"
} finally {
    $env:AXON_STORAGE_MODE = $previousMode
    $env:BACKEND_URL = $previousBackendUrl
}
