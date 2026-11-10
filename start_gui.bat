@echo off
setlocal
if not exist "%~dp0start_gui.vbs" (
    echo [ERROR] start_gui.vbs not found.
    pause
    exit /b 1
)
wscript.exe "%~dp0start_gui.vbs"
exit /b %ERRORLEVEL%
