param([switch]$CoreOnly)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Install Docker Desktop and start its Linux container engine first.' }
docker info | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop is not running or its engine is unavailable.' }
if (-not (Test-Path '.env')) { Copy-Item '.env.example' '.env' }
docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose configuration is invalid. Check .env.' }
if ($CoreOnly) {
    docker compose up -d --build frontend backend control worker worker-control test-dependency broker-proxy mailpit
} else {
    docker compose up -d --build
}
if ($LASTEXITCODE -ne 0) { throw 'Startup failed. Run docker compose logs --tail=100.' }
Write-Host 'AutoPilot: http://localhost:5173'
Write-Host 'API docs: http://localhost:8000/docs'
Write-Host 'Run .\scripts\run-demo.ps1 for the no-key dry-run walkthrough.'
