@echo off
setlocal
title Exp Agent GUI
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0GUI\start.ps1" -OpenBrowser
set "ExitCode=%ERRORLEVEL%"
if not "%ExitCode%"=="0" (
    echo.
    echo [ERROR] GUI startup failed or was interrupted. Exit code: %ExitCode%
    pause
)
exit /b %ExitCode%
