@echo off
setlocal
cd /d "%~dp0"
set "OMP_NUM_THREADS=1"
set "MKL_NUM_THREADS=1"
set "OPENBLAS_NUM_THREADS=1"
set "NUMEXPR_NUM_THREADS=1"
set "GDAL_NUM_THREADS=1"
set "PYTHONPATH=%CD%\src;%CD%\.venv\Lib\site-packages"
set "PYTHON=%CD%\exports\FINAL_RESULT\runtime\python\python.exe"
if not exist "%PYTHON%" (
  echo Bundled Python runtime is missing.
  pause
  exit /b 1
)
"%PYTHON%" scripts\run_m3_source_predictor_sentinel_game_laptop_v1.py --project-root . --check-authorization
if errorlevel 1 (
  echo Authorization or migrated state verification failed.
  pause
  exit /b 1
)
"%PYTHON%" scripts\run_m3_source_predictor_sentinel_game_laptop_v1.py --project-root . --phase online_acquisition --start
echo.
echo Worker stopped. Check the output above before restarting.
pause
