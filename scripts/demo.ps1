#requires -version 5.1
<#
.SYNOPSIS
    Levanta Vigia completo (backend + frontend) con un solo comando para una
    demo en vivo -- ver docs/produccion-readiness.md, checklist "demo-ready"
    punto 1.

.DESCRIPTION
    Reemplaza los 4 comandos manuales en 2 terminales documentados en
    HANDOFF.md ("Cómo levantar todo para verlo") por un solo script que:
      1. Verifica si Docker responde (docker ps) -- si no, WARN, no falla.
         ZAP/Nuclei degradan con gracia sin Docker (tools/_shared.py::
         ToolNotInstalledError ya lo maneja), así que el resto de la demo
         (dashboard, cumplimiento, datos pre-cargados via seed_demo.py)
         sigue funcionando igual.
      2. Arranca el backend (uvicorn) en un job de background.
      3. Espera a que GET /health responda 200 antes de asumir que sirve.
      4. Arranca el frontend (vite) en otro job de background.
      5. Abre el navegador apuntando al frontend, solo cuando ambos ya
         respondieron.

    Puertos poco comunes (mismos que HANDOFF.md, no chocan con otros
    proyectos del ecosistema Freestyle): backend 48173, frontend 48174.

.PARAMETER SkipBrowser
    No abre el navegador automáticamente al final.

.EXAMPLE
    ./scripts/demo.ps1
#>

param(
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendPort = 48173
$FrontendPort = 48174
$HealthUrl = "http://localhost:$BackendPort/health"
$FrontendUrl = "http://localhost:$FrontendPort"

Write-Host "=== Vigia demo.ps1 ===" -ForegroundColor Cyan
Write-Host "Repo: $RepoRoot"

# --- 1. Docker: verificar, advertir, nunca fallar -----------------------
Write-Host "`n[1/5] Verificando Docker..." -ForegroundColor Yellow
$dockerOk = $false
try {
    $null = docker ps 2>&1
    if ($LASTEXITCODE -eq 0) {
        $dockerOk = $true
        Write-Host "Docker responde -- ZAP/Nuclei/Trivy/Grype disponibles si se necesitan en vivo." -ForegroundColor Green
    }
} catch {
    # docker.exe ni siquiera está en PATH -- mismo caso, solo warn.
}
if (-not $dockerOk) {
    Write-Host "ADVERTENCIA: Docker no responde (docker ps falló o no está instalado)." -ForegroundColor Red
    Write-Host "El escaneo activo en vivo (ZAP/Nuclei/Trivy/Grype) no va a funcionar hoy," -ForegroundColor Red
    Write-Host "pero el resto de Vigia (dashboard, login, hallazgos, reportes de" -ForegroundColor Red
    Write-Host "cumplimiento) funciona igual con datos pre-cargados -- corre" -ForegroundColor Red
    Write-Host "'py scripts/seed_demo.py' si todavía no lo hiciste. Continuando de todas formas." -ForegroundColor Red
}

# --- 2. Backend -----------------------------------------------------------
Write-Host "`n[2/5] Arrancando backend (uvicorn, puerto $BackendPort)..." -ForegroundColor Yellow

$env:CORS_ORIGINS = "http://localhost:$FrontendPort"

$backendJob = Start-Job -Name "vigia-backend" -ScriptBlock {
    param($RepoRoot, $Port, $CorsOrigins)
    Set-Location $RepoRoot
    $env:CORS_ORIGINS = $CorsOrigins
    py -m uvicorn api.main:app --port $Port
} -ArgumentList $RepoRoot, $BackendPort, $env:CORS_ORIGINS

Write-Host "Job de backend arrancado (Job Id $($backendJob.Id)). Esperando a que /health responda..."

# --- 3. Esperar /health, no asumir que ya sirve ----------------------------
$maxWaitSeconds = 60
$elapsed = 0
$healthy = $false
while ($elapsed -lt $maxWaitSeconds) {
    Start-Sleep -Seconds 2
    $elapsed += 2

    # Si el job murió (ej. puerto ocupado, error de import), no tiene sentido
    # seguir esperando 60s -- fallar rápido y mostrar el motivo real.
    if ($backendJob.State -eq "Failed" -or $backendJob.State -eq "Completed") {
        Write-Host "`nEl job de backend terminó antes de tiempo (estado: $($backendJob.State))." -ForegroundColor Red
        Receive-Job -Job $backendJob
        exit 1
    }

    try {
        $resp = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 3
        if ($resp.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    } catch {
        # Todavia no arranco -- normal en los primeros segundos, seguir esperando.
    }
    Write-Host "  ...esperando ($elapsed/${maxWaitSeconds}s)"
}

if (-not $healthy) {
    Write-Host "`nEl backend no respondio en $HealthUrl tras ${maxWaitSeconds}s." -ForegroundColor Red
    Write-Host "Output del job de backend hasta ahora:" -ForegroundColor Red
    Receive-Job -Job $backendJob
    Write-Host "`nPara detener el job manualmente: Stop-Job -Id $($backendJob.Id); Remove-Job -Id $($backendJob.Id)"
    exit 1
}

Write-Host "Backend listo -- $HealthUrl respondio 200." -ForegroundColor Green

# --- 4. Frontend ------------------------------------------------------------
Write-Host "`n[3/5] Preparando frontend..." -ForegroundColor Yellow

$frontendDir = Join-Path $RepoRoot "frontend"
$envLocalPath = Join-Path $frontendDir ".env.local"
"VITE_API_URL=http://localhost:$BackendPort" | Set-Content -Path $envLocalPath -Encoding utf8
Write-Host "Escrito $envLocalPath -> VITE_API_URL=http://localhost:$BackendPort"

$nodeModulesPath = Join-Path $frontendDir "node_modules"
if (-not (Test-Path $nodeModulesPath)) {
    Write-Host "node_modules no existe -- corriendo 'npm install' (una sola vez, puede tardar)..." -ForegroundColor Yellow
    Push-Location $frontendDir
    npm install
    Pop-Location
}

Write-Host "`n[4/5] Arrancando frontend (vite, puerto $FrontendPort)..." -ForegroundColor Yellow

$frontendJob = Start-Job -Name "vigia-frontend" -ScriptBlock {
    param($FrontendDir, $Port)
    Set-Location $FrontendDir
    npx vite --port $Port --strictPort
} -ArgumentList $frontendDir, $FrontendPort

# Vite arranca rápido (segundos), pero igual verificamos por HTTP en vez de
# asumir un tiempo fijo -- mismo principio que con el backend arriba.
$maxWaitSeconds = 30
$elapsed = 0
$frontendReady = $false
while ($elapsed -lt $maxWaitSeconds) {
    Start-Sleep -Seconds 1
    $elapsed += 1
    if ($frontendJob.State -eq "Failed" -or $frontendJob.State -eq "Completed") {
        Write-Host "`nEl job de frontend terminó antes de tiempo (estado: $($frontendJob.State))." -ForegroundColor Red
        Receive-Job -Job $frontendJob
        exit 1
    }
    try {
        $resp = Invoke-WebRequest -Uri $FrontendUrl -UseBasicParsing -TimeoutSec 2
        if ($resp.StatusCode -eq 200) {
            $frontendReady = $true
            break
        }
    } catch {
        # Normal mientras vite termina de arrancar.
    }
}

if (-not $frontendReady) {
    Write-Host "`nEl frontend no respondio en $FrontendUrl tras ${maxWaitSeconds}s -- revisa el job manualmente:" -ForegroundColor Red
    Write-Host "Receive-Job -Id $($frontendJob.Id)"
} else {
    Write-Host "Frontend listo -- $FrontendUrl respondio 200." -ForegroundColor Green
}

# --- 5. Listo ---------------------------------------------------------------
Write-Host "`n[5/5] Vigia esta arriba." -ForegroundColor Cyan
Write-Host "  Backend:  http://localhost:$BackendPort  (job id $($backendJob.Id))"
Write-Host "  Frontend: $FrontendUrl  (job id $($frontendJob.Id))"
Write-Host "`nPara detener todo: Get-Job | Stop-Job; Get-Job | Remove-Job"
Write-Host "Cuenta de demo (si corriste scripts/seed_demo.py): demo@vigia.local / DemoVigia2026"

if (-not $SkipBrowser -and $frontendReady) {
    Start-Process $FrontendUrl
}
