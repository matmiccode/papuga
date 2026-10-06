@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem Ten sam program co Uruchom.bat, ale jako wydanie publiczne (Papuga):
rem z odnośnikami, sprawdzaniem aktualizacji na GitHubie i pobieraniem
rem modelu, gdy go brak. Patrz src\whisper_automat\core\wydanie.py.

if not exist ".venv/Scripts/pythonw.exe" (
    echo.
    echo   Brak środowiska. Uruchom najpierw setup.bat
    echo.
    pause
    exit /b 1
)

set "PYTHONPATH=%~dp0src"
set "WHISPER_AUTOMAT_WYDANIE=papuga"
start "Papuga" ".venv/Scripts/pythonw.exe" -m whisper_automat %*
exit /b 0
