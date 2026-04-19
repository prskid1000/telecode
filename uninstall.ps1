#Requires -Version 5.1
<#
.SYNOPSIS
    Uninstall Telecode — kills services, removes the scheduled task,
    optionally removes the venv + data dir.
#>
param(
    [string]$InstallDir = "$env:USERPROFILE\.telecode"
)

function Ok($msg)   { Write-Host "  [OK] $msg"   -ForegroundColor Green }
function Warn($msg) { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }

Write-Host "`n  Telecode Uninstaller`n" -ForegroundColor Cyan

# 1. Stop + remove the Telecode scheduled task (gives telecode a chance
#    to clean up its children via _post_shutdown).
$existing = Get-ScheduledTask -TaskName 'Telecode' -ErrorAction SilentlyContinue
if ($existing) {
    Stop-ScheduledTask       -TaskName 'Telecode' -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Unregister-ScheduledTask -TaskName 'Telecode' -Confirm:$false -ErrorAction SilentlyContinue
    Ok "Removed scheduled task: Telecode"
} else {
    Warn "Scheduled task 'Telecode' not registered"
}

# 2. Kill orphaned children scoped to $InstallDir only — don't touch
#    python.exe instances owned by other tools / IDEs.
#    Telecode's Job Object normally reaps these, but if the task was
#    killed ungracefully they can linger.
$venv = Join-Path $InstallDir "telecode-venv"
$targets = @('pythonw', 'python', 'llama-server', 'claude')
$killed = 0
foreach ($n in $targets) {
    Get-Process -Name $n -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and (
            $_.Path.StartsWith($venv, [System.StringComparison]::OrdinalIgnoreCase) -or
            $_.Path.StartsWith($InstallDir, [System.StringComparison]::OrdinalIgnoreCase)
        )
    } | ForEach-Object {
        try { $_ | Stop-Process -Force -ErrorAction SilentlyContinue; $killed++ } catch {}
    }
}
# Also catch llama-server regardless of path — it's usually in ~/.llama
# or the build dir outside $InstallDir, but it was spawned by telecode
# so we want it gone too. Only kill if no matching window title from a
# user's manual run.
Get-Process -Name 'llama-server' -ErrorAction SilentlyContinue | ForEach-Object {
    try { $_ | Stop-Process -Force -ErrorAction SilentlyContinue; $killed++ } catch {}
}
Ok "Killed $killed orphan processes"

# 3. Data dir (logs, store, runtime-overrides.json, llama-state.json)
$dataDir = Join-Path $InstallDir "data"
if (Test-Path $dataDir) {
    $confirm = Read-Host "  Delete data dir $dataDir (logs, store, runtime-overrides.json, llama-state.json)? (y/N)"
    if ($confirm -eq 'y') {
        Remove-Item -Recurse -Force $dataDir -ErrorAction SilentlyContinue
        Ok "Removed $dataDir"
    }
}

# 4. venv
if (Test-Path $venv) {
    $confirm = Read-Host "  Delete venv $venv (~200 MB)? (y/N)"
    if ($confirm -eq 'y') {
        Remove-Item -Recurse -Force $venv -ErrorAction SilentlyContinue
        Ok "Removed $venv"
    }
}

# 5. settings.json — keep by default; user may have a real bot_token in it
$settingsFile = Join-Path $InstallDir "settings.json"
if (Test-Path $settingsFile) {
    Warn "settings.json kept at $settingsFile (contains your bot_token — delete manually if needed)"
}

# 6. Install dir itself — only offer if the repo clone isn't still active
if (Test-Path $InstallDir) {
    $gitDir = Join-Path $InstallDir ".git"
    if (Test-Path $gitDir) {
        Warn "Install dir $InstallDir is a git clone — kept. Delete manually with: rm -rf $InstallDir"
    } else {
        $confirm = Read-Host "  Delete install directory $InstallDir? (y/N)"
        if ($confirm -eq 'y') {
            Remove-Item -Recurse -Force $InstallDir
            Ok "Removed $InstallDir"
        }
    }
}

Write-Host "`n  Uninstall complete.`n" -ForegroundColor Green
