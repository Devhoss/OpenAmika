@echo off
rem Starts OpenAmika in ONE console window: grace cleanup of an earlier instance, pre-flight lights,
rem then every service and component with its output streamed there (full logs in logs\).
rem Uses the brain python from settings.json to run launch.py; Ctrl+C in that window (or
rem Stop_OpenAmika.bat) stops everything the launcher started.
cd /d "%~dp0"
chcp 65001 >nul
if not exist "settings.json" (
    echo settings.json not found. Copy settings.example.json to settings.json and point it at your
    echo Python environments first ^(README.md, section "Install"^).
    pause
    exit /b 1
)
set "PY="
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "(Get-Content settings.json -Raw | ConvertFrom-Json).brain_python"`) do set "PY=%%P"
if not defined PY (
    echo Could not read brain_python from settings.json
    pause
    exit /b 1
)
if not exist "%PY%" (
    echo The brain python named in settings.json was not found: %PY%
    echo Create the environments first ^(README.md, section "Install"^) or fix the path in settings.json.
    pause
    exit /b 1
)
start "OpenAmika" "%PY%" -X utf8 "%~dp0launch.py" %*
echo OpenAmika is starting in its own window (the web UI opens when speech is ready).
