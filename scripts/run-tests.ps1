$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
docker build -f backend/Dockerfile --target test -t autopilot-tests:local .
if ($LASTEXITCODE -ne 0) { throw 'Backend test image build failed.' }
docker run --rm autopilot-tests:local ruff check app tests alembic /services
if ($LASTEXITCODE -ne 0) { throw 'Python lint failed.' }
docker run --rm autopilot-tests:local pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed.' }
docker build -f frontend/Dockerfile -t autopilot-frontend-check:local .
if ($LASTEXITCODE -ne 0) { throw 'Frontend lint, type checking, or build failed.' }
Write-Host 'Backend checks and frontend build passed. This command does not prove a distributed Docker run; run smoke.py after startup.'
