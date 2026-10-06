@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop.ps1" %*
if errorlevel 1 (
  echo.
  echo Arresto incompleto. Leggi il messaggio qui sopra.
  pause
)
