@echo off
setlocal
if not exist "%~dp0FishHome.exe" (
    echo FishHome.exe was not found next to this script.
    pause
    exit /b 1
)
start "" "%~dp0FishHome.exe" --start-all
