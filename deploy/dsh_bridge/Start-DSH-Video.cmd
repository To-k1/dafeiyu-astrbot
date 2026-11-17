@echo off
setlocal
if /i not "%USERNAME%"=="FishHomeRunner" exit /b 12
if not defined FISHHOME_VIDEO_JOB exit /b 13
if not defined FISHHOME_OPENCODE_KEY exit /b 14
set "NODE=%~dp0runtime\node.exe"
if not exist "%NODE%" set "NODE=C:\Program Files\nodejs\node.exe"
cd /d "%FISHHOME_VIDEO_JOB%" || exit /b 15
"%NODE%" "%~dp0node_modules\@deepseek-ai\dsh\lib\bin.js" web --patch "%FISHHOME_VIDEO_JOB%\patch.json" --host 127.0.0.1 --port 0 --no-open > "%FISHHOME_VIDEO_JOB%\worker.log" 2>&1
exit /b %errorlevel%
