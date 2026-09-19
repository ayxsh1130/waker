$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
docker compose down
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose could not stop the stack.' }
Write-Host 'Services stopped. Database, model, and artifact volumes are retained.'
