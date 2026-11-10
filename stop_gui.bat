@echo off
setlocal
title Exp Agent GUI - Stop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0GUI\stop.ps1"
set "ExitCode=%ERRORLEVEL%"
if not "%ExitCode%"=="0" (
    echo.
    echo [ERROR] Failed to stop GUI. Exit code: %ExitCode%
    pause
)
exit /b %ExitCode%
