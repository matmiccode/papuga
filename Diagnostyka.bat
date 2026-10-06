@echo off
chcp 65001 >nul
setlocal
title Whisper Automat — diagnostyka
cd /d "%~dp0"

if not exist ".venv/Scripts/python.exe" (
    echo.
    echo   Brak środowiska. Uruchom najpierw setup.bat
    echo.
    pause
    exit /b 1
)

set "PYTHONPATH=%~dp0src"
set "PYTHONIOENCODING=utf-8"
".venv/Scripts/python.exe" -m whisper_automat --doctor

echo.
pause
