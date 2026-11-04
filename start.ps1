# -----------------------------------------------------------------------------
#  Bonsai Portable - start.ps1
#  Windows launcher. Python 3.11+ is required for the gateway and web UI.
#  Usage:  .\start.ps1 [launcher args]
# -----------------------------------------------------------------------------

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

Write-Host ""
Write-Host "  Bonsai Portable AI" -ForegroundColor Green
Write-Host "  1-bit - local - no install" -ForegroundColor DarkGray
Write-Host ""

$python = $null
foreach ($cmd in @("python", "python3", "py")) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if (-not $found) { continue }
    try {
        $ver = & $found.Source --version 2>&1
    } catch {
        continue
    }
    if ($ver -match "Python 3\.") {
        $python = $found.Source
        break
    }
}

if (-not $python) {
    Write-Host "  ERROR: Python 3.11+ was not found." -ForegroundColor Red
    Write-Host "  Install it from https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "  During installation, enable 'Add Python to PATH'." -ForegroundColor Yellow
    Read-Host "  Press Enter to close"
    exit 1
}

Write-Host "  Starting with Python: $python" -ForegroundColor Cyan
& $python "launcher.py" @args
exit $LASTEXITCODE
