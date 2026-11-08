# Thin wrapper: run the offline pytest suite.
# Usage: .\scripts\test.ps1 [-NoVenv] [-Enhanced]
& (Join-Path $PSScriptRoot "start.ps1") test @args
exit $LASTEXITCODE
