# Read the same per-user credential saved by the field launcher for offline calibration.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root '.venv\Scripts\python.exe'
if (!(Test-Path $python)) { throw 'Run Prepare-Python.cmd first.' }
if (-not $env:FIELDHUB_OBS_PASSWORD) {
    $file = Join-Path $env:LOCALAPPDATA 'ForgeFieldHub\obs-password.dpapi'
    if (Test-Path $file) { $secret = ConvertTo-SecureString ((Get-Content $file -Raw).Trim()) }
    else { $secret = Read-Host 'OBS WebSocket password' -AsSecureString }
    $env:FIELDHUB_OBS_PASSWORD = [Net.NetworkCredential]::new('', $secret).Password
}
& $python (Join-Path $root 'control\scenes.py') @args
exit $LASTEXITCODE
