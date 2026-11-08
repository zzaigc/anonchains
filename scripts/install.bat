@echo off
setlocal
rem Install anonchains dependencies only (no CLI start). Extra args go to start.ps1.
set "PS=powershell.exe"
where pwsh >nul 2>nul && set "PS=pwsh"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" install %*
set "CODE=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %CODE%
