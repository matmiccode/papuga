@echo off
chcp 65001 >nul
setlocal
title Whisper Automat — tryb konsolowy
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

if "%~1"=="" (
    echo.
    echo   Przeciągnij plik audio lub wideo na ten plik .bat,
    echo   albo podaj ścieżkę jako argument.
    echo.
    pause
    exit /b 1
)

".venv/Scripts/python.exe" -m whisper_automat --cli %*

echo.
pause
