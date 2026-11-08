# Thin wrapper: install dependencies, do not start the CLI.
# Usage: .\scripts\install.ps1 [-Enhanced] [-NoVenv]
& (Join-Path $PSScriptRoot "start.ps1") install @args
exit $LASTEXITCODE
