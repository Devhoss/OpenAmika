# OpenAmika setup for Windows: her brain, her ears (speech recognition), her memory and her eyes, with their models,
# settings.json and brain\.env. Her voice and her Live2D model come next, from scripts\setup_tts.py.
#
# Run it in PowerShell from the OpenAmika folder:
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 --cpu           # no NVIDIA GPU
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 --vision off    # without her camera eyes
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 --check         # what is missing; changes nothing
#
# This script only finds Python 3.11 and runs scripts\setup_core.py with it (every option above is passed through);
# that one does the work and ends with a summary: one OK / -- / !! line per part, each !! with its fix. Running it
# again is safe: what is already installed is checked and skipped, and downloads resume.

$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
if (-not (Test-Path -LiteralPath (Join-Path $root 'launch.py'))) {
    Write-Host '[setup] This script belongs in the scripts folder of OpenAmika; run it from there.'
    exit 1
}

# Python 3.11: the py launcher first (python.org installs it), then python3.11 / python on PATH. The Microsoft Store
# placeholder "python" (it only opens the Store) prints nothing and is skipped.
$python = $null
foreach ($candidate in @('py -3.11', 'python3.11', 'python')) {
    $parts = $candidate -split ' '
    $exe = $parts[0]
    $pre = @($parts | Select-Object -Skip 1)
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    $version = & $exe @pre -c 'import sys; print(sys.version_info[0], sys.version_info[1])' 2>$null
    if ($LASTEXITCODE -eq 0 -and "$version".Trim() -eq '3 11') {
        $python = @($exe) + $pre
        break
    }
}

if (-not $python) {
    Write-Host ''
    Write-Host '[setup] Python 3.11 was not found. OpenAmika needs Python 3.11 (64-bit):'
    Write-Host '          - from python.org: https://www.python.org/downloads/release/python-3119/'
    Write-Host '            (Windows installer, 64-bit; tick "Add python.exe to PATH" on its first page)'
    Write-Host '          - or in this window:  winget install -e --id Python.Python.3.11'
    Write-Host '        Then open a NEW PowerShell window in this folder and run this script again.'
    exit 1
}

$exe = $python[0]
$pre = @($python | Select-Object -Skip 1)
Write-Host ("[setup] using " + ($python -join ' '))
& $exe @pre -X utf8 (Join-Path $root 'scripts\setup_core.py') @args
exit $LASTEXITCODE
