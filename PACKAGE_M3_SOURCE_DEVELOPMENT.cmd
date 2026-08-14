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
echo Creating a paused, hash-verified transfer folder...
".venv\Scripts\python.exe" "scripts\package_m3_source_development.py" --project-root "%~dp0"
if errorlevel 1 (
  echo.
  echo Packaging failed. Use the dashboard to request Safe Pause, wait for zero active tasks, then retry.
  pause
  exit /b 1
)
echo.
echo Transfer folder created under exports\M3_SOURCE_DEVELOPMENT_OFFICE.
pause
endlocal
