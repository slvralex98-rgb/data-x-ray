$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Host 'Run setup.ps1 first.'
    exit 1
}
& $pythonPath -m data_xray.launcher @args
exit $LASTEXITCODE
