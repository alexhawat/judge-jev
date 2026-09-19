param(
    [ValidateSet("python", "rust", "")]
    [string]$Runtime = $env:JUDGE_JEV_RUNTIME
)

$Root = Split-Path -Parent $PSScriptRoot
$env:JUDGE_JEV_ROOT = $Root

if (-not $Runtime) {
    Write-Host "Select judge-jev runtime:"
    Write-Host "  1) python"
    Write-Host "  2) rust"
    $choice = Read-Host "Enter 1 or 2 [1]"
    $Runtime = if ($choice -eq "2") { "rust" } else { "python" }
}

New-Item -ItemType Directory -Force -Path (Join-Path $Root ".judge-jev") | Out-Null
Set-Content -Path (Join-Path $Root ".judge-jev/runtime") -Value $Runtime

switch ($Runtime) {
    "python" {
        Push-Location (Join-Path $Root "python")
        uv sync --dev
        Pop-Location
    }
    "rust" {
        Push-Location (Join-Path $Root "rust")
        cargo build --release
        Pop-Location
    }
    default { throw "Unknown runtime: $Runtime" }
}

Write-Host "Configured runtime=$Runtime"
