<#
    Starts Jarvis.

    Deliberately does NOT elevate. Jarvis is designed to run with your normal
    user rights so that UAC stays a real boundary; if you launch this from an
    elevated terminal it will warn you and refuse to start.
#>

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

# --- refuse to run elevated ------------------------------------------------
$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host ""
    Write-Host "  This terminal is running as Administrator." -ForegroundColor Yellow
    Write-Host "  Jarvis runs unelevated on purpose - close this and open a normal" -ForegroundColor Yellow
    Write-Host "  PowerShell window, or just use the desktop shortcut." -ForegroundColor Yellow
    Write-Host ""
    exit 2
}

# --- locate uv -------------------------------------------------------------
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    foreach ($candidate in @(
        "$env:LOCALAPPDATA\hermes\bin\uv.exe",
        "$env:USERPROFILE\.local\bin\uv.exe",
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links\uv.exe"
    )) {
        if (Test-Path $candidate) { $uv = $candidate; break }
    }
}
if (-not $uv) {
    Write-Host "  uv not found. Install it with:  winget install astral-sh.uv" -ForegroundColor Red
    exit 1
}

# --- check the API key -----------------------------------------------------
if (-not (Test-Path "$root\.env")) {
    Write-Host "  No .env file. Copy .env.example to .env and add your OpenRouter key." -ForegroundColor Red
    exit 1
}

Push-Location $root
try {
    Write-Host "  Syncing dependencies..." -ForegroundColor DarkGray
    & $uv sync --quiet
    & $uv run python -m jarvis @args
}
finally {
    Pop-Location
}
