@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-FishHome.ps1"
if errorlevel 1 echo Installation did not finish. See the error above, then run this file again.
pause
