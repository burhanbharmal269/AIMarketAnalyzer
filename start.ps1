# start.ps1 — Indian Options Research Desk
#
# Usage:   .\start.ps1
# Stop:    Ctrl+C
#
# What this does:
#   1. Always uses the .venv Python so all packages are available
#   2. Checks if port 8000 is already in use and offers to kill the old process
#   3. Launches uvicorn with --reload disabled (production) or enabled (dev)
#   4. Auto-restarts on crash with exponential back-off (up to 30s)

param(
    [switch]$Dev,            # pass -Dev to enable --reload (hot-reload for development)
    [int]$Port = 8000,
    [string]$Host = "127.0.0.1"
)

$ErrorActionPreference = "Continue"
$Host.UI.RawUI.WindowTitle = "Options Research Desk"

$APP     = "app.main:app"
$VENV    = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$UVICORN = Join-Path $PSScriptRoot ".venv\Scripts\uvicorn.exe"

# ── 1. Verify virtual environment ─────────────────────────────────────────────
if (-not (Test-Path $VENV)) {
    Write-Host "[ERROR] Virtual environment not found at .venv\" -ForegroundColor Red
    Write-Host "        Run:  python -m venv .venv  then  .venv\Scripts\pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}

# ── 2. Check if port is already occupied ──────────────────────────────────────
$occupied = netstat -ano 2>$null | Select-String ":$Port\s" | Select-String "LISTENING"
if ($occupied) {
    $pid_match = $occupied | ForEach-Object { ($_ -split '\s+')[-1] } | Select-Object -First 1
    Write-Host "[WARN] Port $Port is already in use by PID $pid_match" -ForegroundColor Yellow
    $kill = Read-Host "Kill it and continue? [y/N]"
    if ($kill -match '^[Yy]') {
        try { Stop-Process -Id ([int]$pid_match) -Force -ErrorAction Stop }
        catch { Write-Host "[WARN] Could not kill PID $pid_match - try manually" -ForegroundColor Yellow }
        Start-Sleep -Seconds 1
    } else {
        Write-Host "Aborted." -ForegroundColor Red
        exit 1
    }
}

# ── 3. Build uvicorn arguments ─────────────────────────────────────────────────
$uvicorn_args = @(
    "-m", "uvicorn",
    $APP,
    "--host", $Host,
    "--port", $Port,
    "--log-level", "info",
    "--timeout-graceful-shutdown", "10"
)

if ($Dev) {
    $uvicorn_args += "--reload"
    Write-Host "[DEV]  Hot-reload enabled - changes take effect immediately" -ForegroundColor Magenta
}

# ── 4. Banner ─────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Indian Options Research Desk" -ForegroundColor Cyan
Write-Host "  http://$Host`:$Port" -ForegroundColor Cyan
if ($Dev) {
    Write-Host "  Mode: DEVELOPMENT (hot-reload on)" -ForegroundColor Magenta
} else {
    Write-Host "  Mode: PRODUCTION" -ForegroundColor Green
}
Write-Host "  Press Ctrl+C to stop" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# ── 5. Run with auto-restart ──────────────────────────────────────────────────
$attempt    = 0
$back_off   = 3    # seconds, doubles each crash up to $max_back_off
$max_back   = 30

while ($true) {
    $attempt++
    $ts = Get-Date -Format "HH:mm:ss"
    Write-Host "[$ts] Starting server (attempt $attempt)..." -ForegroundColor Green

    & $VENV @uvicorn_args

    $exit_code = $LASTEXITCODE
    $ts = Get-Date -Format "HH:mm:ss"

    if ($exit_code -eq 0) {
        Write-Host "[$ts] Server stopped cleanly (exit 0)." -ForegroundColor Yellow
        break
    }

    # Ctrl+C produces exit code 0xC000013A (-1073741510) on Windows
    if ($exit_code -eq -1073741510 -or $exit_code -eq 130) {
        Write-Host "[$ts] Interrupted by user - exiting." -ForegroundColor Yellow
        break
    }

    Write-Host "[$ts] Server crashed (exit $exit_code). Restarting in ${back_off}s..." -ForegroundColor Red
    Start-Sleep -Seconds $back_off
    $back_off = [Math]::Min($back_off * 2, $max_back)
}
