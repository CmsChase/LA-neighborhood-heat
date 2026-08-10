@echo off
setlocal
chcp 65001 >nul
title Sentinel-2 feature builder
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0portable_sentinel_templates\setup_and_launch.ps1"
if errorlevel 1 (
  echo.
  echo Start failed. Keep this window open and read the error above.
  pause
)

