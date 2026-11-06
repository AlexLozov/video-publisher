# Runs every check required before merging into main (see docs/rules.md).
# Usage from the repository root:  powershell -ExecutionPolicy Bypass -File scripts\check.ps1
# Skip the browser E2E tests with -SkipE2E.

param([switch]$SkipE2E)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$failed = @()

function Step([string]$name, [string]$dir, [scriptblock]$cmd) {
    Write-Host "`n=== $name ===" -ForegroundColor Cyan
    Push-Location $dir
    try {
        & $cmd
        if ($LASTEXITCODE -ne 0) { $script:failed += $name }
    } finally {
        Pop-Location
    }
}

Step "ruff check"        $root { & $py -m ruff check . }
Step "ruff format"       $root { & $py -m ruff format --check . }
Step "mypy"              $root { & $py -m mypy }
Step "mypy desktop"      $root { & $py -m mypy --strict desktop\run.py }
Step "pytest + coverage" $root { & $py -m pytest }
$ui = Join-Path $root "ui"
Step "ui typecheck"      $ui { npm run typecheck }
Step "vitest + coverage" $ui { npm run test:coverage }
Step "ui build"          $ui { npm run build }
if (-not $SkipE2E) {
    Step "playwright e2e" $ui { npm run e2e }
}

if ($failed.Count -gt 0) {
    Write-Host "`nFAILED: $($failed -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host "`nAll checks passed." -ForegroundColor Green
