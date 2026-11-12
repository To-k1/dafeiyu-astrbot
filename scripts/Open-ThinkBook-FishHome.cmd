@echo off
setlocal
cd /d "%~dp0.."
"%~dp0..\..\dev\astrbot-4.29.4\.venv\Scripts\python.exe" -B -m deploy.desktop_manager.thinkbook %*
if errorlevel 1 pause
