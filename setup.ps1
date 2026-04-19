#Requires -Version 5.1
<#
.SYNOPSIS
    Telecode Setup — Telegram bot + llama.cpp proxy + system tray on Windows.
.DESCRIPTION
    Creates an isolated Python venv inside the install dir, installs
    requirements.txt, checks that llama-server is reachable, and registers
    a scheduled task `Telecode` that launches pythonw.exe main.py at logon
    (no console window). Telecode owns llama-server, the proxy, and the
    MCP server as child processes — one tray icon, one process tree.

    The venv isolates Telegram/Qt/pyte deps from your system Python.
.PARAMETER InstallDir
    Where everything lives. Defaults to ~/.telecode (the repo dir when
    this script is run from a clone).
.PARAMETER LlamaBinary
    Path or name of the llama-server executable. Defaults to
    "llama-server" (resolved on PATH). Set to an absolute path if the
    binary isn't on PATH.
.PARAMETER SkipTask
    Skip scheduled-task registration (for development — you run telecode
    manually from a terminal).
#>
param(
    [string]$InstallDir  = "$env:USERPROFILE\.telecode",
    [string]$LlamaBinary = "llama-server",
    [switch]$SkipTask
)

$ErrorActionPreference = "Stop"

function Step($msg) { Write-Host "`n>>> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    [OK] $msg"   -ForegroundColor Green }
function Warn($msg) { Write-Host "    [WARN] $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "    [FAIL] $msg" -ForegroundColor Red; exit 1 }

Write-Host @"

  Telecode Setup
  Telegram bot + llama.cpp proxy + tray UI
  =========================================

"@ -ForegroundColor Magenta

# ─── Prerequisites ──────────────────────────────────────────────────

Step "Checking prerequisites"

$pythonExe = $null
$candidates = @()
foreach ($name in @("python3.exe", "python.exe")) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
}
if (Get-Command py.exe -ErrorAction SilentlyContinue) { $candidates += "py.exe" }
$pyenvRoot = "$env:USERPROFILE\.pyenv\pyenv-win\versions"
if (Test-Path $pyenvRoot) {
    Get-ChildItem $pyenvRoot -Directory | Sort-Object Name -Descending | ForEach-Object {
        $p = Join-Path $_.FullName "python.exe"
        if (Test-Path $p) { $candidates += $p }
    }
}
foreach ($p in @(
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe"
)) { if (Test-Path $p) { $candidates += $p } }

foreach ($c in $candidates) {
    try {
        $ver = if ($c -eq "py.exe") { py -3 --version 2>&1 } else { & $c --version 2>&1 }
        if ($ver -match 'Python 3\.(1[0-9]|[2-9][0-9])') { $pythonExe = $c; break }
    } catch {}
}
if (-not $pythonExe) { Fail "Python 3.10+ not found. Install from https://python.org" }
Ok "Python: $(& $pythonExe --version 2>&1) ($pythonExe)"

# git isn't strictly required, but telecode's /sessions list + CC rely on it
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Warn "git not found — some backends (Claude Code) won't be fully functional"
} else {
    Ok "git available"
}

# llama-server — warn-only (telecode auto_start defaults to false; user
# can set llamacpp.binary to an absolute path later)
$resolved = $null
try { $resolved = (Get-Command $LlamaBinary -ErrorAction Stop).Source } catch {}
if ($resolved) {
    Ok "llama-server: $resolved"
} elseif (Test-Path $LlamaBinary) {
    Ok "llama-server: $LlamaBinary (absolute path)"
} else {
    Warn "llama-server not found on PATH and '$LlamaBinary' does not exist"
    Warn "  → Set llamacpp.binary in settings.json to an absolute path, or"
    Warn "  → prebuild llama.cpp and put llama-server on PATH"
}

# ─── Install dir ─────────────────────────────────────────────────────

Step "Install directory: $InstallDir"
if (-not (Test-Path "$InstallDir\main.py")) {
    Fail "main.py not found at $InstallDir — run setup.ps1 from the repo root"
}
Ok "Ready"

# ─── Telecode venv ───────────────────────────────────────────────────

Step "Installing Telecode dependencies"
$venv       = Join-Path $InstallDir "telecode-venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
$reqFile    = Join-Path $InstallDir "requirements.txt"

if (-not (Test-Path $reqFile)) { Fail "requirements.txt missing at $reqFile" }

if (-not (Test-Path $venvPython)) {
    Write-Host "    Creating venv..." -ForegroundColor DarkGray
    & $pythonExe -m venv $venv
}

Write-Host "    pip install (idempotent — skips on re-run)..." -ForegroundColor DarkGray
& $venvPython -m pip install --upgrade pip --quiet 2>&1 | Out-Null
& "$venv\Scripts\pip.exe" install -r $reqFile --quiet 2>&1 | Out-Null

# Quick sanity: import the critical deps
$importCheck = & $venvPython -c "import telegram, aiohttp, pyte, PySide6, aiofiles; print('ok')" 2>&1
if ($importCheck -notmatch 'ok') {
    Fail "venv smoke test failed: $importCheck"
}
Ok "venv ready: $venv"

# ─── Seed settings.json if missing ───────────────────────────────────

Step "Settings"
$settingsFile = Join-Path $InstallDir "settings.json"
$exampleFile  = Join-Path $InstallDir "settings.example.json"
if (-not (Test-Path $settingsFile)) {
    if (Test-Path $exampleFile) {
        Copy-Item $exampleFile $settingsFile
        Ok "Copied settings.example.json → settings.json"
        Warn "Edit $settingsFile to set telegram.bot_token, group_id, and llamacpp.binary"
    } else {
        Warn "settings.json missing and no settings.example.json to copy from"
    }
} else {
    Ok "settings.json exists"
}

# ─── Data dir ────────────────────────────────────────────────────────

$dataDir = Join-Path $InstallDir "data"
New-Item -ItemType Directory -Path (Join-Path $dataDir "logs") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $dataDir "logs\requests") -Force | Out-Null
Ok "Data dir: $dataDir"

# ─── Scheduled task ──────────────────────────────────────────────────

if ($SkipTask) {
    Warn "Skipping scheduled task registration (per -SkipTask)"
    Step "Done. Launch manually with:"
    Write-Host "    $venv\Scripts\python.exe $InstallDir\main.py" -ForegroundColor Gray
    exit 0
}

Step "Registering scheduled task: Telecode"

# pythonw.exe = GUI binary, no console window. The task runs hidden at
# logon with RestartCount=3 so transient crashes bounce back automatically.
$pythonwExe = $venvPython -replace 'python\.exe$','pythonw.exe'
if (-not (Test-Path $pythonwExe)) {
    Warn "pythonw.exe not found next to $venvPython — falling back to python.exe"
    $pythonwExe = $venvPython
}
$entryPoint = Join-Path $InstallDir "main.py"

$taskName = 'Telecode'
$username = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue | ForEach-Object {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

$action    = New-ScheduledTaskAction -Execute $pythonwExe -Argument "`"$entryPoint`"" -WorkingDirectory $InstallDir
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User $username
$settings  = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
                -RestartCount 3 `
                -RestartInterval (New-TimeSpan -Minutes 1) `
                -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $username -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
Ok "Scheduled task registered (auto-start at logon)"

# ─── Start now ───────────────────────────────────────────────────────

Step "Starting Telecode"
Start-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Ok "Running"

# ─── Done ────────────────────────────────────────────────────────────

Write-Host @"

  =========================================
  Setup complete!
  =========================================

  Telecode is running — look for the tray icon (bottom-right).
  Left-click to open the settings window; right-click for subsystem
  submenus (Llama / Proxy / MCP / Bot).

  Logs:      $dataDir\logs\telecode.log  (prev run: telecode.log.prev)
  llama:     $dataDir\logs\llama.log
  Requests:  $dataDir\logs\requests\     (populated when proxy.debug=true)

  If the bot silent-exits on startup, check data\logs\telecode.log.prev
  for the traceback from the previous run.

"@ -ForegroundColor Green
