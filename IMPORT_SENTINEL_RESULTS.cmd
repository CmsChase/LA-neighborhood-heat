@echo off
setlocal
title Validate, import, and resume Sentinel-2 results

if "%~1"=="" (
    echo Paste the full path to the copied result folder or returned .zip file:
    set /p "RESULT_SOURCE=> "
) else (
    set "RESULT_SOURCE=%~1"
)

if not exist "%RESULT_SOURCE%" (
    echo Result source not found: %RESULT_SOURCE%
    pause
    exit /b 1
)

if exist "%RESULT_SOURCE%\NUL" goto import_directory

"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\import_portable_sentinel_results.py" --project-root "%~dp0" --archive "%RESULT_SOURCE%" --audit-if-complete
goto import_done

:import_directory
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\import_portable_sentinel_results.py" --project-root "%~dp0" --source-directory "%RESULT_SOURCE%" --audit-if-complete --resume-dashboard

:import_done
if errorlevel 1 (
    echo Validation, import, or resume failed. Existing project data was not overwritten.
    pause
    exit /b 1
)

echo Return workflow finished. Read the status above for the exact resume point.
pause
