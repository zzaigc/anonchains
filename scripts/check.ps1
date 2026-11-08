# Thin wrapper: report environment only (exit 1 when incomplete).
# Usage: .\scripts\check.ps1 [-NoVenv]
& (Join-Path $PSScriptRoot "start.ps1") check @args
exit $LASTEXITCODE
