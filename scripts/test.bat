@echo off
setlocal
rem Run the offline pytest suite (installs pytest if missing).
set "PS=powershell.exe"
where pwsh >nul 2>nul && set "PS=pwsh"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" test %*
set "CODE=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %CODE%
