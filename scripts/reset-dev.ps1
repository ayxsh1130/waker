param([switch]$DeleteData)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not $DeleteData) { throw 'Destructive reset requires -DeleteData. This removes incidents, experiments, generated task artifacts, model cache, and service data.' }
$answer = Read-Host 'Type DELETE to permanently remove this local AutoPilot data'
if ($answer -cne 'DELETE') { throw 'Reset cancelled.' }
docker compose down --volumes --remove-orphans
if ($LASTEXITCODE -ne 0) { throw 'Reset failed.' }
Write-Host 'Local AutoPilot volumes removed. Your .env and source files are retained.'
