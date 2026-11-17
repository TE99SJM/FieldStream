# Windows PowerShell 5.1+. The .cmd execution policy applies only to its child process.
$ErrorActionPreference = 'Stop'
$kit = Split-Path $PSScriptRoot -Parent
$fieldAddress = '192.168.77.10'
$stateDir = Join-Path $env:LOCALAPPDATA 'ForgeFieldHub'
$receiverDir = Join-Path $kit 'receiver'
$receiver = Join-Path $receiverDir 'mediamtx.exe'
$config = Join-Path $receiverDir 'mediamtx.yml'
$watchdog = Join-Path $PSScriptRoot 'watchdog.py'
$panel = Join-Path $kit 'control\panel.py'
$scenes = Join-Path $kit 'control\scenes.py'
$lock = New-Object System.Threading.Mutex($false, 'Local\ForgeFieldHubStartup')
$locked = $false
$result = 0

function Find-Listener([string]$Address, [int]$Port) {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -eq $Address } | Select-Object -First 1
}

function Wait-Listener([string]$Address, [int]$Port, $Process) {
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        $item = Find-Listener $Address $Port
        if ($item) { return $item }
        if ($Process -and $Process.HasExited) { throw "Startup failed on $Address port $Port. See logs in $stateDir" }
        Start-Sleep -Milliseconds 300
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for $Address port $Port. See logs in $stateDir"
}

function Start-Worker([string]$Executable, [string]$Script, [string]$Label) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    Start-Process -FilePath $Executable -ArgumentList ('"' + $Script + '"') -WorkingDirectory $kit `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $stateDir "$Label-$stamp.out.log") `
        -RedirectStandardError (Join-Path $stateDir "$Label-$stamp.err.log")
}

function Confirm-PanelOwner($Listener) {
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($Listener.OwningProcess)"
    if (!$owner.CommandLine -or $owner.CommandLine.IndexOf($panel, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        throw 'Port 8088 is occupied by another program. Close the conflict or return to the original kit directory.'
    }
}

function Find-FreshWatchdog {
    $statusFile = Join-Path $stateDir 'watchdog-status.json'
    if (!(Test-Path $statusFile)) { return $null }
    try {
        $info = Get-Content $statusFile -Raw | ConvertFrom-Json
        $age = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [double]$info.updated
        if ($age -lt -2 -or $age -gt 10) { return $null }
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$info.pid)"
        if ($owner.CommandLine -and $owner.CommandLine.IndexOf($watchdog, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $info }
    } catch { return $null }
    return $null
}

try {
    try { $locked = $lock.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $locked = $true }
    if (!$locked) { throw 'The field kit is already starting in another window.' }
    $null = New-Item -ItemType Directory -Path $stateDir -Force
    $nic = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $fieldAddress -ErrorAction SilentlyContinue |
        Where-Object { $_.AddressState -eq 'Preferred' -and $_.PrefixLength -eq 24 } | Select-Object -First 1
    if (!$nic) { throw "Connect the field LAN and assign $fieldAddress/24 to that adapter, then relaunch." }
    Write-Host "Field network: $($nic.InterfaceAlias), $fieldAddress/24"

    $python = Join-Path $kit '.venv\Scripts\python.exe'
    if (!(Test-Path $python)) { $python = (Get-Command python.exe -ErrorAction Stop).Source }
    & $python -c 'import sys,obsws_python; sys.exit(0 if sys.version_info >= (3,11) else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'Prepare the Python 3.11+ environment with this kit''s requirements.txt first.' }
    $ffmpeg = $env:FIELDHUB_FFMPEG
    if (!$ffmpeg) { $ffmpeg = Join-Path $kit 'tools\ffmpeg.exe' }
    if (!(Test-Path $ffmpeg)) { $ffmpeg = (Get-Command ffmpeg.exe -ErrorAction Stop).Source }
    $env:FIELDHUB_FFMPEG = $ffmpeg
    foreach ($file in @($receiver, $config, $panel, $scenes, $watchdog)) {
        if (!(Test-Path $file -PathType Leaf)) { throw "Missing kit file: $file" }
    }

    $rtmp = Find-Listener $fieldAddress 1935
    if (!$rtmp) {
        $started = Start-Worker $receiver $config 'receiver'
        $rtmp = Wait-Listener $fieldAddress 1935 $started
    }
    $owner = Get-Process -Id $rtmp.OwningProcess
    if ($owner.Path -ne $receiver) { throw "Another process owns $fieldAddress port 1935. Leave that process alone and resolve the port conflict." }
    foreach ($port in @(8554, 9997, 9998)) {
        $local = Wait-Listener '127.0.0.1' $port $owner
        if ($local.OwningProcess -ne $owner.Id) { throw "Another process owns loopback port $port." }
    }
    # Use a proxy-free local client so corporate proxy settings cannot intercept the request.
    $api = New-Object System.Net.WebClient
    try { $api.Proxy = $null; $paths = $api.DownloadString('http://127.0.0.1:9997/v3/paths/list') | ConvertFrom-Json }
    finally { $api.Dispose() }
    if ($null -eq $paths.PSObject.Properties['items']) { throw 'Unexpected response from MediaMTX local API.' }
    Write-Host "Receiver ready, process $($owner.Id)."

    $obsProcess = Get-Process obs64 -ErrorAction SilentlyContinue | Select-Object -First 1
    if (!$obsProcess) {
        $obsExe = $env:FIELDHUB_OBS_EXE
        if (!$obsExe) { $obsExe = Join-Path $env:ProgramFiles 'obs-studio\bin\64bit\obs64.exe' }
        if (!(Test-Path $obsExe)) { throw 'Install 64-bit OBS Studio or set FIELDHUB_OBS_EXE to its full executable path.' }
        $obsProcess = Start-Process $obsExe -WorkingDirectory (Split-Path $obsExe -Parent) -PassThru
    }
    $socketDeadline = [DateTime]::UtcNow.AddSeconds(40)
    do {
        $socket = Get-NetTCPConnection -LocalPort 4455 -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $_.OwningProcess -eq $obsProcess.Id } | Select-Object -First 1
        if ($socket) { break }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $socketDeadline)
    if (!$socket) { throw 'In OBS, open Tools > WebSocket Server Settings. Enable the authenticated server on port 4455, then relaunch.' }

    $secretFile = Join-Path $stateDir 'obs-password.dpapi'
    $newSecret = $null
    if (!$env:FIELDHUB_OBS_PASSWORD) {
        if (Test-Path $secretFile) { $secure = ConvertTo-SecureString (Get-Content $secretFile -Raw).Trim() }
        else { $secure = Read-Host 'OBS WebSocket password (remembered for this Windows user)' -AsSecureString; $newSecret = $secure }
        $env:FIELDHUB_OBS_PASSWORD = [Net.NetworkCredential]::new('', $secure).Password
    }
    $check = & $python $watchdog --check-obs
    if ($LASTEXITCODE -ne 0 -or $check -ne 'OK') {
        throw "Could not authenticate to OBS. Verify the password. To reset the remembered password, remove $secretFile and relaunch."
    }
    if ($newSecret) { ConvertFrom-SecureString $newSecret | Set-Content $secretFile -Encoding ASCII }
    $check = & $python $watchdog --prepare-collection
    if ($LASTEXITCODE -ne 0) { throw 'Could not open the dedicated Field Hub collection. Check OBS for a dialog and retry.' }
    if ($check -eq 'BUSY') { throw 'Another collection has an active output. Finish that session before opening the Field Hub collection.' }
    if ($check -notin @('IDLE', 'ACTIVE')) { throw 'Unknown OBS output state. Field scenes were not changed.' }
    if ($check -eq 'IDLE') {
        & $python $scenes build
        if ($LASTEXITCODE -ne 0) { throw 'Field scene configuration failed. Check the selected OBS collection and the message above.' }
    } else { Write-Host 'An output is already active. Using the existing scene collection.' }

    $panelListener = Find-Listener $fieldAddress 8088
    if (!$panelListener) {
        $panelProcess = Start-Worker $python $panel 'panel'
        $panelListener = Wait-Listener $fieldAddress 8088 $panelProcess
    }
    Confirm-PanelOwner $panelListener
    $current = Find-FreshWatchdog
    if (!$current) {
        $null = Start-Worker $python $watchdog 'watchdog'
        $deadline = [DateTime]::UtcNow.AddSeconds(20)
        do {
            Start-Sleep -Milliseconds 500
            $current = Find-FreshWatchdog
        } while (!$current -and [DateTime]::UtcNow -lt $deadline)
        if (!$current) { throw "Watchdog did not authenticate or report healthy startup. See $stateDir\watchdog.log" }
    }
    Write-Host "Watchdog ready, process $($current.pid). Private touch pairing link:"
    & $python $panel --show-url
    if ($LASTEXITCODE -ne 0) { throw 'Panel is listening, but its pairing URL could not be displayed.' }
    Write-Host 'Field hub ready. Check Preview and audio before pressing Start Streaming in OBS.'
} catch {
    Write-Host "Field hub: $($_.Exception.Message)" -ForegroundColor Red
    $result = 1
} finally {
    if ($locked) { $lock.ReleaseMutex() }
    $lock.Dispose()
}
exit $result
