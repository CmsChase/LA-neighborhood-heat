@echo off
setlocal
title Import completed Sentinel-2 results

if "%~1"=="" (
    echo Paste the full path to the returned .zip file, then press Enter:
    set /p "RESULT_ZIP=> "
) else (
    set "RESULT_ZIP=%~1"
)

if not exist "%RESULT_ZIP%" (
    echo Result ZIP not found: %RESULT_ZIP%
    pause
    exit /b 1
)

"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\import_portable_sentinel_results.py" --project-root "%~dp0" --archive "%RESULT_ZIP%"
if errorlevel 1 (
    echo Import failed. The returned ZIP was not accepted.
    pause
    exit /b 1
)

"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\audit_multicity_predictor_readiness.py" --project-root "%~dp0" --write-report
if errorlevel 1 (
    echo Import finished, but predictor readiness audit failed.
    pause
    exit /b 1
)

echo Result import and predictor readiness audit completed.
pause
