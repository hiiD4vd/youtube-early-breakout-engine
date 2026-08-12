param(
    [int]$Port = 3010
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$distDirName = if ($env:NEXT_DIST_DIR) { $env:NEXT_DIST_DIR } else { ".next-dashboard" }
$env:NEXT_DIST_DIR = $distDirName
$nextCache = Join-Path $projectRoot $distDirName

# Next dev has one mutable build directory.  Starting a second server while
# the first one owns that directory is what produced stale HTML references to
# CSS/JS files and the "white, unstyled" screen.  This launcher owns port
# 3010, stops only its listener, clears the stale dev artefacts in the
# dedicated dashboard distDir, then starts exactly one clean server.
$existing = netstat -ano -p tcp | Select-String (":$Port\\s") | ForEach-Object {
    $parts = ($_ -split "\s+") | Where-Object { $_ }
    if ($parts.Count -ge 5 -and $parts[3] -eq "LISTENING") { [int]$parts[-1] }
} | Select-Object -Unique

foreach ($processId in $existing) {
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Milliseconds 800
if (Test-Path $nextCache) {
    Remove-Item -LiteralPath $nextCache -Recurse -Force
}

Write-Host "Starting one clean Next.js server on http://localhost:$Port using distDir=$distDirName"
& npm.cmd run dev
