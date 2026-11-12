@echo off
setlocal
if /i not "%USERNAME%"=="FishHomeRunner" exit /b 12
if not defined FISHHOME_DSH_GATEWAY_TOKEN exit /b 13
set "DSH_HOME=%LOCALAPPDATA%\FishHome\DSH-Commission"
set "DSH_PERMISSION_MODE=workspace-write"
if not exist "%DSH_HOME%" mkdir "%DSH_HOME%" || exit /b 1
cd /d "%LOCALAPPDATA%\FishHome\commission-files" || exit /b 1
"C:\Program Files\nodejs\node.exe" "%~dp0node_modules\@deepseek-ai\dsh\lib\bin.js" web --patch "%~dp0m5-commission.yml" --host 127.0.0.1 --port 59935 --no-open > "%DSH_HOME%\web-start.log" 2>&1
exit /b %errorlevel%
