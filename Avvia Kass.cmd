@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0Kass.exe" (
  echo Kass.exe non e' presente in questa cartella.
  echo Estrai l'intera cartella Kass dallo ZIP prima di avviare il programma.
  echo Mantieni Kass.exe insieme alle DLL e alle cartelle del programma.
  pause
  exit /b 1
)
start "" "%~dp0Kass.exe"
if errorlevel 1 (
  echo Impossibile aprire Kass.exe. Controlla che la cartella sia completa.
  pause
  exit /b 1
)
exit /b 0
