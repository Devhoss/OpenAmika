# Installs the Silicon Labs CP210x USB-UART driver for the Zigbee dongle THROUGH WINDOWS UPDATE
# (Microsoft's signed copy; nothing is downloaded from third-party sites). Needs administrator rights:
#   powershell -ExecutionPolicy Bypass -File Install_CP210x_WindowsUpdate.ps1
# Used when the direct download from silabs.com is blocked for scripts (their CDN answers 403).
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Start-Transcript -Path (Join-Path $here 'setup_admin.log') -Append -Force | Out-Null
Write-Host "CP210x driver via Windows Update" -ForegroundColor Cyan
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Write-Host "This script must run as administrator." -ForegroundColor Red; Stop-Transcript | Out-Null; Read-Host "Press Enter to close"; exit 1 }

function Dongle-Port { Get-CimInstance Win32_SerialPort -EA SilentlyContinue | Where-Object { $_.PNPDeviceID -like 'USB\VID_10C4&PID_EA60*' } | Select-Object -First 1 }
if (Dongle-Port) { Write-Host ("[ok] already working: {0}" -f (Dongle-Port).DeviceID) -ForegroundColor Green; Stop-Transcript | Out-Null; Read-Host "Press Enter to close"; exit 0 }

try {
    Write-Host "[..] Asking Windows Update for driver updates (devices with missing drivers)…"
    $session = New-Object -ComObject Microsoft.Update.Session
    $searcher = $session.CreateUpdateSearcher()
    $searcher.ServerSelection = 3                                   # ssOthers → the service below
    $searcher.ServiceID = '7971f918-a847-4430-9279-4a52d1efe18d'     # Microsoft Update
    $searcher.SearchScope = 1                                       # machine-wide
    $result = $searcher.Search("IsInstalled=0 and Type='Driver'")
    $matches = @()
    foreach ($u in $result.Updates) {
        $hw = @($u.DriverHardwareID)
        $text = ($u.Title + ' ' + $u.DriverProvider + ' ' + $u.DriverManufacturer + ' ' + ($hw -join ' ')).ToLower()
        if ($text -match 'silicon lab|cp210|vid_10c4') { $matches += $u; Write-Host ("     candidate: {0} ({1}, {2})" -f $u.Title, $u.DriverProvider, $u.DriverVerDate) }
    }
    if (-not $matches.Count) {
        Write-Host "[!!] Windows Update offered no CP210x driver for this device." -ForegroundColor Yellow
        Write-Host "     Manual route: Device Manager → 'SONOFF Dongle Plus MG24' → Update driver → Search automatically," -ForegroundColor Yellow
        Write-Host "     or download CP210x_Universal_Windows_Driver.zip from silabs.com in a browser, unzip, right-click silabser.inf → Install." -ForegroundColor Yellow
    } else {
        $coll = New-Object -ComObject Microsoft.Update.UpdateColl
        foreach ($u in $matches) { if (-not $u.EulaAccepted) { $u.AcceptEula() }; $coll.Add($u) | Out-Null }
        Write-Host "[..] Downloading…"
        $dl = $session.CreateUpdateDownloader(); $dl.Updates = $coll; $dr = $dl.Download()
        Write-Host ("     download result code {0}" -f $dr.ResultCode)
        Write-Host "[..] Installing…"
        $inst = $session.CreateUpdateInstaller(); $inst.Updates = $coll; $ir = $inst.Install()
        Write-Host ("     install result code {0} (2 = succeeded)" -f $ir.ResultCode)
        & pnputil /scan-devices | Out-Null
        Start-Sleep -Seconds 3
        if (Dongle-Port) { Write-Host ("[ok] Zigbee dongle is now {0}" -f (Dongle-Port).DeviceID) -ForegroundColor Green }
        else { Write-Host "[!!] Installed, but no COM port yet — unplug and replug the dongle." -ForegroundColor Yellow }
    }
} catch {
    Write-Host ("[!!] Windows Update route failed: {0}" -f $_.Exception.Message) -ForegroundColor Red
}
Write-Host "Done."
Stop-Transcript | Out-Null
Read-Host "Press Enter to close"
