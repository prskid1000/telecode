<#
.SYNOPSIS
  Build targets of a llama.cpp checkout with the Visual Studio toolchain, gently (few jobs) and until nothing is left to do.

.DESCRIPTION
  Finds Visual Studio with vswhere, loads the x64 compiler environment, and runs `cmake --build` on an existing CMake build folder
  (create it once with the patcher card in the tray, or `cmake -S . -B build-telecode -G Ninja -DGGML_CUDA=ON`).
  A CUDA build of llama.cpp compiles for a long time; -Jobs 2 keeps a laptop usable and cool. The command runs again until ninja reports that
  there is nothing to do, because a build that was stopped half way (a closed window, a tool timeout) leaves the last steps undone.

.PARAMETER Source    the checkout, default build\llama.cpp (relative to the telecode repo)
.PARAMETER BuildDir  the CMake build folder inside the checkout, default build-telecode
.PARAMETER Target    cmake targets, default llama-server llama-bench (e.g. llama-fit-params, llama-cli)
.PARAMETER Jobs      parallel compile jobs, default 2
.PARAMETER Repeat    how many times to run the build at most, default 4
.PARAMETER Log       the full output of the last run, default <Source>\<BuildDir>\build.log

.EXAMPLE
  powershell -NoProfile -File tools\build_llama.ps1 -Target llama-server,llama-bench,llama-fit-params -Jobs 4
#>
param(
    [string]$Source = "build\llama.cpp",
    [string]$BuildDir = "build-telecode",
    [string[]]$Target = @("llama-server", "llama-bench"),
    [int]$Jobs = 2,
    [int]$Repeat = 4,
    [string]$Log = ""
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$src = if ([System.IO.Path]::IsPathRooted($Source)) { $Source } else { Join-Path $repo $Source }
if (-not $Log) { $Log = Join-Path (Join-Path $src $BuildDir) "build.log" }

$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) { throw "vswhere.exe not found: install Visual Studio with the C++ workload" }
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw "no Visual Studio with the C++ tools found" }
$vcvars = Join-Path $vs "VC\Auxiliary\Build\vcvars64.bat"
$cmake = Join-Path $vs "Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
if (-not (Test-Path $cmake)) { $cmake = "cmake" }

$targets = $Target -join " "
for ($i = 1; $i -le $Repeat; $i++) {
    $cmd = "call `"$vcvars`" >nul 2>&1 && cd /d `"$src`" && `"$cmake`" --build `"$BuildDir`" --target $targets -j $Jobs > `"$Log`" 2>&1"
    cmd /c $cmd
    $code = $LASTEXITCODE
    $tail = Get-Content $Log -Tail 3 -ErrorAction SilentlyContinue
    Write-Host "run $i exit $code : $($tail | Select-Object -Last 1)"
    if ($code -ne 0) { Write-Host "errors:"; Select-String -Path $Log -Pattern "error C|error:|FAILED" | Select-Object -First 8 | ForEach-Object { $_.Line }; exit $code }
    if (($tail -join "`n") -match "no work to do") { Write-Host "up to date"; exit 0 }
}
Write-Host "still not up to date after $Repeat runs (run again)"
exit 0
