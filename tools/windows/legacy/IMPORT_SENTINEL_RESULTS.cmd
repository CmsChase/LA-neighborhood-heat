@echo off
setlocal
title Validate and import historical Sentinel-2 results
for %%I in ("%~dp0..\..\..") do set "PROJECT_ROOT=%%~fI"

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

"%PROJECT_ROOT%\.venv\Scripts\python.exe" "%PROJECT_ROOT%\scripts\import_portable_sentinel_results.py" --project-root "%PROJECT_ROOT%" --archive "%RESULT_SOURCE%" --audit-if-complete
goto import_done

:import_directory
"%PROJECT_ROOT%\.venv\Scripts\python.exe" "%PROJECT_ROOT%\scripts\import_portable_sentinel_results.py" --project-root "%PROJECT_ROOT%" --source-directory "%RESULT_SOURCE%" --audit-if-complete --resume-dashboard

:import_done
if errorlevel 1 (
    echo Validation, import, or resume failed. Existing project data was not overwritten.
    pause
    exit /b 1
)

echo Return workflow finished. Read the status above for the exact resume point.
pause
