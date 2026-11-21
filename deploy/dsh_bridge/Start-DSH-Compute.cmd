@echo off
setlocal
if /i not "%USERNAME%"=="FishHomeRunner" exit /b 12
if not defined FISHHOME_DSH_GATEWAY_TOKEN if not defined FISHHOME_OPENCODE_KEY exit /b 13
if "%~1"=="59935" (
    set "DSH_HOME=%LOCALAPPDATA%\FishHome\DSH-Commission"
    set "WORKSPACE=%LOCALAPPDATA%\FishHome\commission-files"
    set "PATCH=m5-commission.yml"
) else if "%~1"=="59934" (
    set "DSH_HOME=%LOCALAPPDATA%\FishHome\DSH"
    set "WORKSPACE=%LOCALAPPDATA%\FishHome\m3-file-check"
    set "PATCH=m5-file-only.yml"
) else exit /b 14
if defined FISHHOME_DSH_WORKSPACE set "WORKSPACE=%FISHHOME_DSH_WORKSPACE%"
set "DSH_PERMISSION_MODE=workspace-write"
rem This loopback Web UI is accessed via SSH forwarding. Use the browser directory picker.
set "SSH_TTY=fishhome-forwarded-web"
set "PATH=%~dp0runtime;%PATH%"
if not exist "%DSH_HOME%" mkdir "%DSH_HOME%" || exit /b 1
cd /d "%WORKSPACE%" || exit /b 1
set "NODE=%~dp0runtime\node.exe"
if not exist "%NODE%" set "NODE=C:\Program Files\nodejs\node.exe"
set EXTRA=
if not "%~2"=="" set EXTRA=--patch "%~2"
if not "%~3"=="" set EXTRA=%EXTRA% --patch "%~3"
if not "%~4"=="" set EXTRA=%EXTRA% --patch "%~4"
"%NODE%" "%~dp0node_modules\@deepseek-ai\dsh\lib\bin.js" web --patch "%~dp0%PATCH%" %EXTRA% --host 127.0.0.1 --port %~1 --no-open > "%DSH_HOME%\web-start.log" 2>&1
exit /b %errorlevel%
