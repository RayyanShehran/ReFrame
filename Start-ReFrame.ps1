param([switch]$Check, [switch]$Production)
$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Error 'Backend dependencies are missing. In backend, run: uv sync --locked'
    exit 1
}
$launcherArgs = @()
if ($Check) { $launcherArgs += '--check' }
if ($Production) { $launcherArgs += '--production' }
# Windows PowerShell 5.1 turns a native command's stderr into error records when output is
# redirected; uvicorn logs to stderr, so 'Stop' would end the launcher on its first log line.
$ErrorActionPreference = 'Continue'
& $python (Join-Path $PSScriptRoot 'backend\start_local.py') @launcherArgs
exit $LASTEXITCODE
