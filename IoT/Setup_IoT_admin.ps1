# OpenAmika smart home — the two installs that need administrator rights, done once:
#   1. Silicon Labs CP210x USB-UART driver (the SONOFF Zigbee Dongle Plus MG24 is a CP2102N; without it
#      Windows shows the dongle with "code 28" and no COM port).
#   2. Eclipse Mosquitto, the MQTT broker zigbee2mqtt and OpenAmika's smart-home server talk through.
# Everything else (zigbee2mqtt, the OpenAmika IoT server, configuration) needs no elevation and is set up
# by the OpenAmika launcher / this folder. Run from an elevated PowerShell:
#   powershell -ExecutionPolicy Bypass -File Setup_IoT_admin.ps1
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$log = Join-Path $here 'setup_admin.log'
Start-Transcript -Path $log -Force | Out-Null
Write-Host "OpenAmika smart home — administrator setup" -ForegroundColor Cyan

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Write-Host "This script must run as administrator." -ForegroundColor Red; Stop-Transcript | Out-Null; Read-Host "Press Enter to close"; exit 1 }

# ── 1. CP210x driver ───────────────────────────────────────────────────────────────────────
$dongle = Get-PnpDevice -PresentOnly -EA SilentlyContinue | Where-Object { $_.InstanceId -like 'USB\VID_10C4&PID_EA60*' }
$havePort = Get-CimInstance Win32_SerialPort -EA SilentlyContinue | Where-Object { $_.PNPDeviceID -like 'USB\VID_10C4&PID_EA60*' }
if ($havePort) {
    Write-Host ("[ok] CP210x driver already working: {0}" -f $havePort.DeviceID) -ForegroundColor Green
} else {
    Write-Host "[..] Installing the Silicon Labs CP210x Universal Windows Driver…"
    $tmp = Join-Path $env:TEMP ("cp210x-" + [guid]::NewGuid().ToString('n'))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    $zip = Join-Path $tmp 'CP210x_Universal_Windows_Driver.zip'
    try {
        Invoke-WebRequest -Uri 'https://www.silabs.com/documents/public/software/CP210x_Universal_Windows_Driver.zip' -OutFile $zip -UseBasicParsing
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $inf = Get-ChildItem $tmp -Recurse -Filter 'silabser.inf' | Select-Object -First 1
        if (-not $inf) { throw "silabser.inf not found in the downloaded archive" }
        & pnputil /add-driver $inf.FullName /install
        & pnputil /scan-devices | Out-Null
        Start-Sleep -Seconds 3
        $port = Get-CimInstance Win32_SerialPort -EA SilentlyContinue | Where-Object { $_.PNPDeviceID -like 'USB\VID_10C4&PID_EA60*' }
        if ($port) { Write-Host ("[ok] Zigbee dongle is now {0}" -f $port.DeviceID) -ForegroundColor Green }
        else { Write-Host "[!!] Driver installed but no COM port yet — unplug and replug the dongle, then check Device Manager." -ForegroundColor Yellow }
    } catch {
        Write-Host ("[!!] Driver install failed: {0}" -f $_.Exception.Message) -ForegroundColor Red
        Write-Host "     Manual route: download CP210x_Universal_Windows_Driver.zip from silabs.com, right-click silabser.inf → Install." -ForegroundColor Yellow
    }
}

# ── 2. Mosquitto ───────────────────────────────────────────────────────────────────────────
$mosq = 'C:\Program Files\mosquitto\mosquitto.exe'
if (Test-Path $mosq) {
    Write-Host "[ok] Mosquitto already installed" -ForegroundColor Green
} else {
    Write-Host "[..] Installing Eclipse Mosquitto with winget…"
    & winget install --id EclipseFoundation.Mosquitto -e --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
    if (Test-Path $mosq) { Write-Host "[ok] Mosquitto installed" -ForegroundColor Green }
    else { Write-Host "[!!] Mosquitto not found after install — get it from https://mosquitto.org/download/" -ForegroundColor Red }
}
# The installer registers a Windows service that may take port 1883 with its own config; OpenAmika's launcher runs
# mosquitto itself (IoT\mosquitto.conf, local only). Leave the service off so the two don't fight over the port.
$svc = Get-Service -Name mosquitto -EA SilentlyContinue
if ($svc) {
    if ($svc.Status -ne 'Stopped') { Stop-Service mosquitto -Force -EA SilentlyContinue }
    Set-Service mosquitto -StartupType Manual -EA SilentlyContinue
    Write-Host "[ok] mosquitto Windows service set to manual (the OpenAmika launcher starts the broker)" -ForegroundColor Green
}

Write-Host ""
Write-Host "Done. Back in OpenAmika: run Start_OpenAmika.bat — the launcher finds the COM port, starts the broker and zigbee2mqtt, and your Zigbee devices should appear." -ForegroundColor Cyan
Stop-Transcript | Out-Null
Read-Host "Press Enter to close"
