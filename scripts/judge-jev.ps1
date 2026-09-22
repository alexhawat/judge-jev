# PowerShell mirror of scripts/judge-jev (bash). Keep the two in lockstep: same
# runtime resolution, same dispatch, same exit-code contract.
#
# PowerShell does not propagate a native command's exit code as its own by default,
# and $LASTEXITCODE is not read automatically -- the whole point of this CLI is its
# exit codes (0 pass .. 4 skip, 10/11 not a verdict), so every branch below reads
# $LASTEXITCODE explicitly and exits with it. A `fail` verdict has to come back as
# exit 1, not a silently-swallowed 0.
$ErrorActionPreference = "Stop"

# Write-Error raises a terminating error, and with $ErrorActionPreference = "Stop"
# that unwinds the script immediately -- the `exit <code>` line right after it would
# never run, so the exit code we are trying to forward gets replaced by whatever
# exit code an uncaught PowerShell error happens to produce. Writing straight to
# stderr keeps the explicit `exit` in control, the same way bash's `echo ... >&2`
# does not itself end the script.
function Write-ErrLine([string]$Message) {
    [Console]::Error.WriteLine($Message)
}

trap {
    Write-ErrLine ("judge-jev launcher failed: " + $_.Exception.Message)
    exit 10
}

$Root = Split-Path -Parent $PSScriptRoot
$env:JUDGE_JEV_ROOT = $Root
$RuntimeFile = Join-Path $Root ".judge-jev/runtime"

if ($env:JUDGE_JEV_RUNTIME) {
    $Runtime = $env:JUDGE_JEV_RUNTIME
} elseif (Test-Path $RuntimeFile) {
    $Runtime = (Get-Content -Raw $RuntimeFile).Trim()
} else {
    $Runtime = "python"
}

$Guided = @("init", "doctor", "reply", "trajectory", "input", "explain", "history", "tune")
$Command = $null
$Previous = $null
foreach ($ArgValue in $args) {
    if ($Previous -eq "--format" -and $ArgValue -eq "human") { $Runtime = "python" }
    $Previous = $ArgValue
    if ($ArgValue -in @("--verbose", "--debug")) { continue }
    if (-not $Command) { $Command = $ArgValue }
}
if ($Command -and $Guided -contains $Command) { $Runtime = "python" }
if ($args.Count -eq 0 -and -not [Console]::IsInputRedirected) { $Runtime = "python" }

switch ($Runtime) {
    "python" {
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            Write-ErrLine "judge-jev: uv is required; run scripts/setup.ps1"
            exit 10
        }
        # Always reconcile with the lock; an existing executable does not prove
        # dependencies still match this checkout.
        $SyncArgs = @("sync", "--project", (Join-Path $Root "python"), "--locked", "--inexact", "--quiet")
        if ($Command -eq "tune" -and $args -contains "--live") {
            $SyncArgs += @("--extra", "tuning")
        }
        uv @SyncArgs
        if ($LASTEXITCODE -ne 0) { exit 10 }
        # Resolve after sync: on a fresh Windows checkout the executable did not
        # exist before uv created .venv/Scripts.
        $PyBinExe = Join-Path $Root "python/.venv/Scripts/judge-jev.exe"
        $PyBinCmd = Join-Path $Root "python/.venv/Scripts/judge-jev.cmd"
        $PyBinPlain = Join-Path $Root "python/.venv/bin/judge-jev"
        $PyBin = if (Test-Path $PyBinExe) { $PyBinExe } elseif (Test-Path $PyBinCmd) { $PyBinCmd } else { $PyBinPlain }
        if (-not (Test-Path $PyBin)) {
            Write-ErrLine "judge-jev: Python bootstrap did not install $PyBin"
            exit 10
        }
        & $PyBin @args
        exit $LASTEXITCODE
    }
    "rust" {
        # cargo's own binary name has no extension on Linux/macOS (including
        # pwsh there) and `.exe` on Windows; check both so this script works
        # wherever pwsh does.
        $BinExe = Join-Path $Root "rust/target/release/judge-jev.exe"
        $BinCmd = Join-Path $Root "rust/target/release/judge-jev.cmd"
        $BinPlain = Join-Path $Root "rust/target/release/judge-jev"
        $Bin = if (Test-Path $BinExe) { $BinExe } elseif (Test-Path $BinCmd) { $BinCmd } elseif (Test-Path $BinPlain) { $BinPlain } else { $null }

        if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
            Write-ErrLine "judge-jev: cargo is required; run scripts/setup.ps1"
            exit 10
        }
        cargo build --manifest-path (Join-Path $Root "rust/Cargo.toml") --release --quiet
            $buildExit = $LASTEXITCODE
            if ($buildExit -ne 0) {
                Write-ErrLine "cargo build --release failed (exit $buildExit)"
                exit 10
            }
        $Bin = if (Test-Path $BinExe) { $BinExe } elseif (Test-Path $BinCmd) { $BinCmd } else { $BinPlain }
        if (-not (Test-Path $Bin)) {
            Write-ErrLine "judge-jev: Rust bootstrap did not build $Bin"
            exit 10
        }

        & $Bin @args
        exit $LASTEXITCODE
    }
    default {
        Write-ErrLine "Unknown runtime '$Runtime'. Run scripts/setup.ps1 first."
        exit 10
    }
}
