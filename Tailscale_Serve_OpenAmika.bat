@echo off
rem Expose this OpenAmika over your tailnet via HTTPS (Tailscale Serve).
rem   Tailscale_Serve_OpenAmika.bat        -> add the mapping (persists across reboots)
rem   Tailscale_Serve_OpenAmika.bat off    -> remove it
rem The app itself stays bound to 127.0.0.1; tailscaled terminates HTTPS with a real
rem certificate, so the microphone works from phones (getUserMedia needs a secure context).
cd /d "%~dp0"
set "SERVE_PORT=8447"

where tailscale >nul 2>nul
if errorlevel 1 (
  echo Tailscale CLI not found. Install Tailscale and sign in first.
  pause
  exit /b 1
)

set "CHAT="
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "(Get-Content settings.json -Raw | ConvertFrom-Json).chat_port"`) do set "CHAT=%%p"
if "%CHAT%"=="" set "CHAT=53000"

if /i "%~1"=="off" (
  tailscale serve --https=%SERVE_PORT% off
  echo Tailscale Serve mapping on port %SERVE_PORT% removed.
  pause
  exit /b 0
)

tailscale serve --bg --https=%SERVE_PORT% http://127.0.0.1:%CHAT%
if errorlevel 1 (
  echo Serve failed. Is HTTPS enabled for your tailnet (MagicDNS + HTTPS certificates)?
  pause
  exit /b 1
)

set "TSHOST="
for /f "usebackq delims=" %%h in (`powershell -NoProfile -Command "(tailscale status --json | ConvertFrom-Json).Self.DNSName.TrimEnd('.')"`) do set "TSHOST=%%h"
echo.
echo OpenAmika on your tailnet (any of your devices):
echo     https://%TSHOST%:%SERVE_PORT%/
echo The mapping is tailnet-only and persists across reboots. Remove with:
echo     Tailscale_Serve_OpenAmika.bat off
pause
