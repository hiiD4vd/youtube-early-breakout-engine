param(
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontend = Join-Path $root "frontend"

Set-Location $root

if ($SkipBuild) {
    docker compose up -d
} else {
    docker compose up --build -d
}

docker compose exec -T backend alembic upgrade head

Start-Process -FilePath "powershell.exe" -WindowStyle Hidden -ArgumentList @(
    "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $frontend "scripts\start-dev.ps1")
)

Write-Host "Frontend: http://localhost:3010"
Write-Host "Backend:  http://localhost:8010/health"
Write-Host "Check stack with: docker compose ps"
