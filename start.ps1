# start.ps1 — Indian Options Research Desk
# Auto-restarts the server if it crashes. Run this instead of uvicorn directly.
#
# Usage:  .\start.ps1
# Stop:   Ctrl+C  (may need two presses to exit the loop)

$ErrorActionPreference = "Continue"
$Host.UI.RawUI.WindowTitle = "Options Research Desk"

$port    = 8000
$host_ip = "127.0.0.1"
$app     = "app.main:app"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Indian Options Research Desk" -ForegroundColor Cyan
Write-Host "  http://$host_ip`:$port" -ForegroundColor Cyan
Write-Host "  Press Ctrl+C to stop" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$attempt = 0
while ($true) {
    $attempt++
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] Starting server (attempt $attempt)..." -ForegroundColor Green

    # Activate virtual environment if present
    $venv = Join-Path $PSScriptRoot ".venv\Scripts\Activate.ps1"
    if (Test-Path $venv) {
        & $venv
    }

    # Start uvicorn
    & py -m uvicorn $app --host $host_ip --port $port --log-level info

    $exit_code = $LASTEXITCODE
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

    if ($exit_code -eq 0) {
        Write-Host "[$timestamp] Server stopped cleanly. Exiting." -ForegroundColor Yellow
        break
    }

    Write-Host "[$timestamp] Server crashed (exit code $exit_code). Restarting in 5 seconds..." -ForegroundColor Red
    Start-Sleep -Seconds 5
}
