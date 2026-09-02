@echo off
setlocal
for %%I in ("%~dp0..\..\..") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found: .venv\Scripts\python.exe
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "scripts\run_multicity_external_target_dashboard.py"
if errorlevel 1 (
  echo.
  echo Three-city external target dashboard failed. Read the message above.
  pause
)
endlocal
