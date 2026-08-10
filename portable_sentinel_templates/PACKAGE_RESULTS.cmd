@echo off
setlocal
chcp 65001 >nul
title Package Sentinel-2 results
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0portable_sentinel_templates\package_results.ps1"
if errorlevel 1 (
  echo.
  echo Packaging failed. Keep this window open and read the error above.
  pause
)

