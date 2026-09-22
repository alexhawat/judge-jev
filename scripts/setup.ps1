param(
    [ValidateSet("python", "rust", "")]
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

$Root = Split-Path -Parent $PSScriptRoot
$env:JUDGE_JEV_ROOT = $Root

if (-not $Runtime) {
    Write-Host "Select judge-jev runtime:"
    Write-Host "  1) python"
    Write-Host "  2) rust"
    $choice = Read-Host "Enter 1 or 2 [1]"
    $Runtime = switch ($choice) { "" { "python" } "1" { "python" } "2" { "rust" } default { Write-ErrLine "Choose 1 or 2."; exit 11 } }
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
        & (Join-Path $Root "python/.venv/Scripts/judge-jev.exe") --version | Out-Null
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
        & (Join-Path $Root "rust/target/release/judge-jev.exe") --version | Out-Null
        if ($LASTEXITCODE -ne 0) { exit 10 }
    }
    default {
        Write-ErrLine "Unknown runtime: $Runtime (use python or rust)"
        exit 11
    }
}

$ConfigDir = Join-Path $Root ".judge-jev"
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
$TempRuntime = Join-Path $ConfigDir ("runtime.tmp." + [guid]::NewGuid().ToString("N"))
Set-Content -Path $TempRuntime -Value $Runtime
Move-Item -Force -Path $TempRuntime -Destination (Join-Path $ConfigDir "runtime")

Write-Host "Configured runtime=$Runtime at $(Join-Path $Root '.judge-jev/runtime')"
Write-Host "Export TYPESAFE_API_KEY for live mode, or pass --mock to judge-jev run."
