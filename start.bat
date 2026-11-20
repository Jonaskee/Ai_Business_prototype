@echo off
rem GramIQ starten op Windows: dubbelklik op dit bestand.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title GramIQ

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo   Python is niet gevonden.
  echo   Installeer Python 3.10 of nieuwer via https://www.python.org/downloads/
  echo   en vink tijdens de installatie "Add python.exe to PATH" aan.
  echo.
  pause
  exit /b 1
)

%PY% start.py %*
if errorlevel 1 (
  echo.
  pause
)
