@echo off
chcp 65001 >nul
setlocal
title Whisper Automat — instalacja środowiska
cd /d "%~dp0"

call :znajdz_pythona
if defined PY goto :mam_pythona

rem --- Pythona nie ma: spróbujmy go zainstalować zamiast odsyłać użytkownika ---
where winget >nul 2>nul || goto :brak_pythona

echo.
echo   ============================================================
echo     Nie znaleziono Pythona na tym komputerze.
echo   ============================================================
echo.
echo     Mogę zainstalować go teraz automatycznie przez winget
echo     (Python 3.12, około 30 MB).
echo.
set "ODP="
set /p "ODP=  Zainstalować Pythona? [T/n]: "
if /i "%ODP%"=="n" goto :brak_pythona

echo.
echo   Instaluję Pythona 3.12 — może pojawić się pytanie o zgodę...
echo.
winget install --id Python.Python.3.12 -e --source winget --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto :brak_pythona

rem winget dopisuje Pythona do PATH, ale to okno zna jeszcze starą wersję
call :znajdz_pythona
if defined PY goto :mam_pythona

echo.
echo   ============================================================
echo     Python zainstalowany.
echo   ============================================================
echo.
echo     Zamknij to okno i uruchom setup.bat jeszcze raz — PATH
echo     odświeża się dopiero w nowo otwartym oknie.
echo.
pause
exit /b 0

:brak_pythona
echo.
echo   ============================================================
echo     Potrzebny jest Python 3.9 lub nowszy.
echo   ============================================================
echo.
echo     Pobierz go z:  https://www.python.org/downloads/windows/
echo     Podczas instalacji ZAZNACZ opcję "Add python.exe to PATH".
echo.
echo     Potem uruchom setup.bat jeszcze raz.
echo.
pause
exit /b 1

:mam_pythona
set "PYTHONIOENCODING=utf-8"
%PY% "src/whisper_automat/install/bootstrap.py" %*
set "CODE=%ERRORLEVEL%"

echo.
pause
exit /b %CODE%

rem ---------------------------------------------------------------------
:znajdz_pythona
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
exit /b 0
