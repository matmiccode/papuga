@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv/Scripts/pythonw.exe" (
    echo.
    echo   Brak środowiska. Uruchom najpierw setup.bat
    echo.
    pause
    exit /b 1
)

set "PYTHONPATH=%~dp0src"
start "Whisper Automat" ".venv/Scripts/pythonw.exe" -m whisper_automat %*
exit /b 0
