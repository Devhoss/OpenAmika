# Installs the Silicon Labs CP210x USB-UART driver for the Zigbee dongle (SONOFF Dongle Plus MG24).
# Needs administrator rights:  powershell -ExecutionPolicy Bypass -File Install_CP210x_driver.ps1
# Order tried:
#   1. Windows Update (Microsoft's signed copy) - the plain Windows Update channel, then Microsoft Update;
#   2. a driver zip you downloaded in a browser (silabs.com serves browsers only): any
#      CP210x*Windows*Driver*.zip in this folder or in your Downloads folder is unpacked and installed.
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Start-Transcript -Path (Join-Path $here 'setup_admin.log') -Append -Force | Out-Null
Write-Host "CP210x driver for the Zigbee dongle" -ForegroundColor Cyan
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Write-Host "This script must run as administrator." -ForegroundColor Red; Stop-Transcript | Out-Null; Read-Host "Press Enter to close"; exit 1 }

function Dongle-Port { Get-CimInstance Win32_SerialPort -EA SilentlyContinue | Where-Object { $_.PNPDeviceID -like 'USB\VID_10C4&PID_EA60*' } | Select-Object -First 1 }
function Finish($msg, $color) { Write-Host $msg -ForegroundColor $color; Write-Host "Done."; Stop-Transcript | Out-Null; Read-Host "Press Enter to close" }
if (Dongle-Port) { Finish ("[ok] already working: " + (Dongle-Port).DeviceID) Green; exit 0 }

# -- 1. Windows Update ------------------------------------------------------------------
$installed = $false
foreach ($channel in @(@{Name='Windows Update'; Sel=2; Svc=$null}, @{Name='Microsoft Update'; Sel=3; Svc='7971f918-a847-4430-9279-4a52d1efe18d'})) {
    if ($installed) { break }
    try {
        Write-Host ("[..] Asking {0} for a driver for the dongle..." -f $channel.Name)
        $session = New-Object -ComObject Microsoft.Update.Session
        $searcher = $session.CreateUpdateSearcher()
        $searcher.ServerSelection = $channel.Sel
        if ($channel.Svc) { try { (New-Object -ComObject Microsoft.Update.ServiceManager).AddService2($channel.Svc, 7, "") | Out-Null } catch {}; $searcher.ServiceID = $channel.Svc }
        $result = $searcher.Search("IsInstalled=0 and Type='Driver'")
        $found = @()
        foreach ($u in $result.Updates) {
            $hw = @($u.DriverHardwareID)
            $text = ($u.Title + ' ' + $u.DriverProvider + ' ' + $u.DriverManufacturer + ' ' + ($hw -join ' ')).ToLower()
            if ($text -match 'silicon lab|cp210|vid_10c4') { $found += $u; Write-Host ("     candidate: {0} ({1}, {2})" -f $u.Title, $u.DriverProvider, $u.DriverVerDate) }
        }
        if (-not $found.Count) { Write-Host ("     {0} offered no CP210x driver ({1} driver updates listed)." -f $channel.Name, $result.Updates.Count) -ForegroundColor Yellow; continue }
        $coll = New-Object -ComObject Microsoft.Update.UpdateColl
        foreach ($u in $found) { if (-not $u.EulaAccepted) { $u.AcceptEula() }; $coll.Add($u) | Out-Null }
        $dl = $session.CreateUpdateDownloader(); $dl.Updates = $coll; $dr = $dl.Download()
        $inst = $session.CreateUpdateInstaller(); $inst.Updates = $coll; $ir = $inst.Install()
        Write-Host ("     install result code {0} (2 = succeeded)" -f $ir.ResultCode)
        $installed = ($ir.ResultCode -eq 2)
    } catch {
        Write-Host ("     {0} route failed: {1}" -f $channel.Name, $_.Exception.Message) -ForegroundColor Yellow
    }
}

# -- 2. a zip downloaded in a browser -----------------------------------------------------
if (-not $installed) {
    $zip = Get-ChildItem -Path $here, (Join-Path $env:USERPROFILE 'Downloads') -Filter 'CP210x*Driver*.zip' -File -EA SilentlyContinue |
           Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($zip) {
        try {
            Write-Host ("[..] Installing from {0}" -f $zip.FullName)
            $tmp = Join-Path $env:TEMP ("cp210x-" + [guid]::NewGuid().ToString('n'))
            Expand-Archive -Path $zip.FullName -DestinationPath $tmp -Force
            $inf = Get-ChildItem $tmp -Recurse -Filter 'silabser.inf' | Select-Object -First 1
            if (-not $inf) { throw "silabser.inf not found inside the zip" }
            & pnputil /add-driver $inf.FullName /install
            $installed = $true
        } catch { Write-Host ("[!!] zip install failed: {0}" -f $_.Exception.Message) -ForegroundColor Red }
    } else {
        Write-Host "[!!] No driver zip found. Download CP210x_Universal_Windows_Driver.zip from" -ForegroundColor Yellow
        Write-Host "     https://www.silabs.com/developer-tools/usb-to-uart-bridge-vcp-drivers?tab=downloads" -ForegroundColor Yellow
        Write-Host "     (save it in Downloads or in this IoT folder) and run this script again." -ForegroundColor Yellow
    }
}

& pnputil /scan-devices | Out-Null
Start-Sleep -Seconds 3
if (Dongle-Port) { Finish ("[ok] Zigbee dongle is now " + (Dongle-Port).DeviceID + " - run Start_OpenAmika.bat") Green }
elseif ($installed) { Finish "[!!] Driver installed but no COM port yet - unplug and replug the dongle, then run Start_OpenAmika.bat" Yellow }
else { Finish "[!!] Driver not installed yet." Red }
