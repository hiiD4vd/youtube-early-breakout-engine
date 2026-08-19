param(
    [int]$Port = 3010
)

$ErrorActionPreference = "Stop"
# Never kill an arbitrary process automatically. The old launcher could kill
# Docker's port proxy or another Next.js process, leaving port and cache state
# inconsistent while Docker Desktop still reported its engine as running.
$existing = netstat -ano -p tcp | Select-String (":$Port\\s") | ForEach-Object {
    $parts = ($_ -split "\\s+") | Where-Object { $_ }
    if ($parts.Count -ge 5 -and $parts[3] -eq "LISTENING") { [int]$parts[-1] }
} | Select-Object -Unique

if ($existing) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:$Port" -TimeoutSec 3
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
            Write-Host "Frontend is already running on http://localhost:$Port (PID: $($existing -join ', '))."
            Write-Host "Reuse that server; no second Next.js process was started."
            exit 0
        }
    } catch {
        # Occupied but unhealthy: report it instead of guessing which process
        # outside this project is safe to terminate.
    }
    throw "Port $Port is used by PID $($existing -join ', ') but is not a healthy frontend. Stop that process explicitly, then run npm run dev again."
}

Write-Host "Starting Next.js dev server on http://localhost:$Port"
& npm.cmd run dev:server
