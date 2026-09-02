@echo off
setlocal
for %%I in ("%~dp0..\..\..") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%"
set "OMP_NUM_THREADS=1"
set "MKL_NUM_THREADS=1"
set "OPENBLAS_NUM_THREADS=1"
set "NUMEXPR_NUM_THREADS=1"
set "GDAL_NUM_THREADS=1"
set "PYTHONPATH=%PROJECT_ROOT%\src;%PROJECT_ROOT%\.venv\Lib\site-packages"
set "PYTHON=%PROJECT_ROOT%\exports\FINAL_RESULT\runtime\python\python.exe"
if not exist "%PYTHON%" goto :missing

"%PYTHON%" scripts\authorize_m3_source_predictor_daymet_order_repair_v1.py --project-root . --check-acquisition >nul 2>nul
if not errorlevel 1 goto :offline

"%PYTHON%" scripts\run_m3_source_predictor_sentinel_game_laptop_v1.py --project-root . --check-authorization
if errorlevel 1 goto :failed
"%PYTHON%" scripts\run_m3_source_predictor_sentinel_game_laptop_v1.py --project-root . --phase online_acquisition --start
if errorlevel 1 goto :failed

:offline
"%PYTHON%" scripts\authorize_m3_source_predictor_daymet_order_repair_v1.py --project-root . --check-acquisition
if errorlevel 1 goto :failed
"%PYTHON%" scripts\run_m3_source_predictor_daymet_order_repair_v1.py --project-root . --phase offline_assembly --start
if errorlevel 1 goto :failed
"%PYTHON%" scripts\authorize_m3_source_predictor_daymet_order_repair_v1.py --project-root . --check-completion
if errorlevel 1 goto :failed
echo.
echo Predictor extension completed and authenticated.
pause
exit /b 0

:missing
echo Bundled Python runtime is missing. This historical launcher requires the excluded transfer package.
pause
exit /b 1

:failed
echo.
echo Worker stopped or verification failed. Do not rebuild or delete the queue.
pause
exit /b 1
