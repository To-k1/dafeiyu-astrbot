@echo off
setlocal
if /i not "%USERNAME%"=="FishHomeRunner" exit /b 12
if not defined FISHHOME_DSH_GATEWAY_TOKEN exit /b 13
if "%~1"=="59935" (
    set "DSH_HOME=%LOCALAPPDATA%\FishHome\DSH-Commission"
    set "WORKSPACE=%LOCALAPPDATA%\FishHome\commission-files"
    set "PATCH=m5-commission.yml"
) else if "%~1"=="59934" (
    set "DSH_HOME=%LOCALAPPDATA%\FishHome\DSH"
    set "WORKSPACE=%LOCALAPPDATA%\FishHome\m3-file-check"
    set "PATCH=m5-file-only.yml"
) else exit /b 14
set "DSH_PERMISSION_MODE=workspace-write"
if not exist "%DSH_HOME%" mkdir "%DSH_HOME%" || exit /b 1
cd /d "%WORKSPACE%" || exit /b 1
set "NODE=%~dp0runtime\node.exe"
if not exist "%NODE%" set "NODE=C:\Program Files\nodejs\node.exe"
"%NODE%" "%~dp0node_modules\@deepseek-ai\dsh\lib\bin.js" web --patch "%~dp0%PATCH%" --patch "%~2" --host 127.0.0.1 --port %~1 --no-open > "%DSH_HOME%\web-start.log" 2>&1
exit /b %errorlevel%
