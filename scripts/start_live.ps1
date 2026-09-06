$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Runner = Join-Path $Root "scripts\run_live.py"
$LogDir = Join-Path $Root "logs"
$LogFile = Join-Path $LogDir "live-session.log"

if (-not (Test-Path $Python)) {
    throw "Virtual environment not found at $Python. Run: python -m venv .venv and install requirements first."
}
if (-not (Test-Path (Join-Path $Root ".env"))) {
    throw ".env not found. Copy .env.example to .env and configure Alpaca/Telegram first."
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
"`n===== START $(Get-Date -Format o) =====" | Out-File -FilePath $LogFile -Append -Encoding utf8
& $Python $Runner *>> $LogFile
