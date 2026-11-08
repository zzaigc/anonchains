@echo off
setlocal
rem Check Python, dependencies, config and venv without changing anything.
set "PS=powershell.exe"
where pwsh >nul 2>nul && set "PS=pwsh"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" check %*
set "CODE=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %CODE%
