@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found: .venv\Scripts\python.exe
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "scripts\run_multicity_source_target_dashboard.py" --auto-start
if errorlevel 1 (
  echo.
  echo LA source-target dashboard failed. Read the message above.
  pause
)
endlocal
