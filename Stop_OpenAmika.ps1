# Stops only this folder's OpenAmika processes:
#   1. whatever the launcher recorded in logs\instance.json (services such as TTS, the MQTT broker,
#      zigbee2mqtt, Hermes, nanoMuse - matched by pid AND executable path, so a reused pid is never hit);
#   2. the core components, by script path on the command line (launcher, brain, speech, embedder, vision).
# Never by port or window title, so other python processes and other OpenAmika checkouts are untouched.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$stopped = 0
$instance = Join-Path $root 'logs\instance.json'
if (Test-Path $instance) {
    try { $recorded = (Get-Content $instance -Raw | ConvertFrom-Json).processes } catch { $recorded = @() }
    foreach ($entry in @($recorded)) {
        if (-not $entry.pid) { continue }
        $live = Get-CimInstance Win32_Process -Filter "ProcessId=$($entry.pid)" -ErrorAction SilentlyContinue
        if (-not $live) { continue }
        $exe = [string]$entry.exe
        $match = ($live.ExecutablePath -and $exe -and ($live.ExecutablePath.ToLower() -eq $exe.ToLower())) -or
                 ($live.CommandLine -and $exe -and $live.CommandLine.ToLower().Contains($exe.ToLower()))
        if ($match) {
            Write-Host ("Stopping PID {0}: {1}" -f $entry.pid, $entry.name)
            & taskkill /PID $entry.pid /T /F | Out-Null
            $stopped++
        }
    }
    Remove-Item $instance -ErrorAction SilentlyContinue
}
$targets = @(
    (Join-Path $root 'launch.py'),
    (Join-Path $root 'brain\webui.py'),
    (Join-Path $root 'speech\api.py'),
    (Join-Path $root 'brain\embed_server.py'),
    (Join-Path $root 'vision\vlm_service.py'),
    (Join-Path $root 'messengers\service.py')
)
Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" | ForEach-Object {
    $cl = $_.CommandLine
    if (-not $cl) { return }
    foreach ($t in $targets) {
        if ($cl.ToLower().Contains($t.ToLower())) {
            Write-Host ("Stopping PID {0}: {1}" -f $_.ProcessId, $t)
            try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; $script:stopped++ } catch {
                Write-Host ("  could not stop PID {0}: {1}" -f $_.ProcessId, $_.Exception.Message)
            }
            break
        }
    }
}
Write-Host ("OpenAmika stop complete. Processes stopped: {0}" -f $stopped)
# A component started from an administrator window is invisible to a normal shell (its command line cannot
# be read), so it survives the matching above. If a core port is still held by python.exe, say so.
try {
    Start-Sleep -Seconds 2      # let the stopped processes release their ports before looking
    $settings = Get-Content (Join-Path $root 'settings.json') -Raw | ConvertFrom-Json
    $ports = @(5215)   # messengers
    if ($settings.brain_port) { $ports += [int]$settings.brain_port } else { $ports += 5202 }
    if ($settings.chat_port) { $ports += [int]$settings.chat_port } else { $ports += 53000 }
    if ($settings.embedder -and $settings.embedder.port) { $ports += [int]$settings.embedder.port }
    if ($settings.vision -and $settings.vision.port -and $settings.vision.enabled -ne $false) { $ports += [int]$settings.vision.port }
    $held = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $ports -contains $_.LocalPort } |
        Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue } |
        Where-Object { $_.ProcessName -like 'python*' }
    if ($held) {
        Write-Host ("Still running: python pid(s) {0} hold OpenAmika ports - started from an administrator window. " -f (($held | ForEach-Object { $_.Id }) -join ', ')) -NoNewline
        Write-Host "Run Stop_OpenAmika.bat as administrator (right-click) or close that window."
    }
} catch {}
