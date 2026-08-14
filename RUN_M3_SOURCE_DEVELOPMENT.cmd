@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "M3_RUNTIME_TMP=%~dp0data\interim\multicity\m3_source_development\runtime\.tmp"
if not exist "%M3_RUNTIME_TMP%" mkdir "%M3_RUNTIME_TMP%"
set "TEMP=%M3_RUNTIME_TMP%"
set "TMP=%M3_RUNTIME_TMP%"
set "TMPDIR=%M3_RUNTIME_TMP%"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found: .venv\Scripts\python.exe
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "scripts\run_m3_source_development_dashboard.py" --project-root "%~dp0" --port 8772
if errorlevel 1 (
  echo.
  echo M3 source-development dashboard failed. Read the message above.
  pause
)
endlocal
