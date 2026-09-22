param(
    [string]$Runtime = $env:JUDGE_JEV_RUNTIME
)

# uv and cargo are native executables: a non-zero exit from either does not throw
# in PowerShell, and nothing reads $LASTEXITCODE unless we do it ourselves. Without
# that check this script used to write .judge-jev/runtime and print
# "Configured runtime=..." even when the install itself had just failed, leaving a
# Windows user told setup worked right before every instruction that follows fails.
$ErrorActionPreference = "Stop"

# Write-Error is a terminating error under $ErrorActionPreference = "Stop", which
# would skip the explicit `exit <code>` right after it. Writing straight to stderr
# keeps that exit in control, the same way bash's `echo ... >&2` does not itself
# end the script.
function Write-ErrLine([string]$Message) {
    [Console]::Error.WriteLine($Message)
}

# Convert terminating filesystem/process errors into the shared operational code.
# Explicit `exit 11` usage paths below do not trigger this trap.
trap {
    Write-ErrLine ("judge-jev setup failed: " + $_.Exception.Message)
    exit 10
}

$Root = Split-Path -Parent $PSScriptRoot
$env:JUDGE_JEV_ROOT = $Root

if (-not $Runtime) {
    if ([Console]::IsInputRedirected) {
        $Runtime = "python"
    } else {
        Write-Host "Select judge-jev runtime:"
        Write-Host "  1) python"
        Write-Host "  2) rust"
        $choice = Read-Host "Enter 1 or 2 [1]"
        $Runtime = switch ($choice) { "" { "python" } "1" { "python" } "2" { "rust" } default { Write-ErrLine "Choose 1 or 2."; exit 11 } }
    }
}
if ($Runtime -notin @("python", "rust")) {
    Write-ErrLine "Unknown runtime: $Runtime (use python or rust)"
    exit 11
}

switch ($Runtime) {
    "python" {
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            Write-ErrLine "uv required; see https://docs.astral.sh/uv/"
            exit 10
        }
        Push-Location (Join-Path $Root "python")
        uv sync --locked --dev
        $exitCode = $LASTEXITCODE
        Pop-Location
        if ($exitCode -ne 0) {
            Write-ErrLine "uv sync --dev failed (exit $exitCode)"
            exit 10
        }
        $PyBinExe = Join-Path $Root "python/.venv/Scripts/judge-jev.exe"
        $PyBinCmd = Join-Path $Root "python/.venv/Scripts/judge-jev.cmd"
        $PyBinPlain = Join-Path $Root "python/.venv/bin/judge-jev"
        $PyBin = if (Test-Path $PyBinExe) { $PyBinExe } elseif (Test-Path $PyBinCmd) { $PyBinCmd } elseif (Test-Path $PyBinPlain) { $PyBinPlain } else { $null }
        if (-not $PyBin) {
            Write-ErrLine "Python bootstrap did not install judge-jev"
            exit 10
        }
        & $PyBin --version | Out-Null
        if ($LASTEXITCODE -ne 0) { exit 10 }
    }
    "rust" {
        if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
            Write-ErrLine "cargo required; install Rust toolchain"
            exit 10
        }
        Push-Location (Join-Path $Root "rust")
        cargo build --release
        $exitCode = $LASTEXITCODE
        Pop-Location
        if ($exitCode -ne 0) {
            Write-ErrLine "cargo build --release failed (exit $exitCode)"
            exit 10
        }
        $RustBinExe = Join-Path $Root "rust/target/release/judge-jev.exe"
        $RustBinCmd = Join-Path $Root "rust/target/release/judge-jev.cmd"
        $RustBinPlain = Join-Path $Root "rust/target/release/judge-jev"
        $RustBin = if (Test-Path $RustBinExe) { $RustBinExe } elseif (Test-Path $RustBinCmd) { $RustBinCmd } elseif (Test-Path $RustBinPlain) { $RustBinPlain } else { $null }
        if (-not $RustBin) {
            Write-ErrLine "Rust bootstrap did not build judge-jev"
            exit 10
        }
        & $RustBin --version | Out-Null
        if ($LASTEXITCODE -ne 0) { exit 10 }
    }
    default {
        Write-ErrLine "Unknown runtime: $Runtime (use python or rust)"
        exit 11
    }
}

$ConfigDir = Join-Path $Root ".judge-jev"
$TempRuntime = $null
try {
    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
    $TempRuntime = Join-Path $ConfigDir ("runtime.tmp." + [guid]::NewGuid().ToString("N"))
    Set-Content -Path $TempRuntime -Value $Runtime
    $RuntimeFile = Join-Path $ConfigDir "runtime"
    [System.IO.File]::Move($TempRuntime, $RuntimeFile, $true)
} finally {
    if ($TempRuntime -and (Test-Path $TempRuntime)) { Remove-Item -Force $TempRuntime }
}

Write-Host "Configured runtime=$Runtime at $(Join-Path $Root '.judge-jev/runtime')"
Write-Host "Export TYPESAFE_API_KEY for live mode, or pass --mock to judge-jev run."
