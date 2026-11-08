@echo off
setlocal
rem anonchains one-shot launcher for cmd.exe; all logic lives in start.ps1.
rem Usage: start.bat [check|install|test|<anonchains command>] [options]
set "PS=powershell.exe"
where pwsh >nul 2>nul && set "PS=pwsh"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set "CODE=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %CODE%
