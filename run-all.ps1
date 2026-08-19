param(
    [switch]$Rebuild
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontend = Join-Path $root "frontend"

Set-Location $root

if ($Rebuild) {
    docker compose up --build -d
} else {
    docker compose up -d
}

docker compose exec -T backend alembic upgrade head

# One-time cleanup for older compose versions that started the frontend by
# default. The host dev server below is the sole owner of port 3010.
docker compose stop frontend 2>$null

Write-Host "Frontend: http://localhost:3010"
Write-Host "Backend:  http://localhost:8010/health"
Write-Host "Check stack with: docker compose ps"
Write-Host "Keep this terminal open while the frontend is running."

Set-Location $frontend
& npm.cmd run dev
