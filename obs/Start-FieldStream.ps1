# Windows PowerShell 5.1+; run in a PowerShell window.
[CmdletBinding()]
param(
    [switch]$Setup,
    [string]$ObsPath = "$env:ProgramFiles\obs-studio\bin\64bit\obs64.exe",
    [string]$PythonPath = "",
    [string]$FfmpegPath = "ffmpeg",
    [ValidateSet("gopro2", "gopro3", "dji")]
    [string[]]$Cameras = @("gopro2", "gopro3", "dji"),
    [ValidateRange(4, 600)][int]$StaleSeconds = 12,
    [ValidateRange(0, 3600)][int]$PixelFreezeSeconds = 0
)
$ErrorActionPreference = "Stop"

function Resolve-Application([string]$Name) {
    $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $command) { throw "Cannot find $Name. Check the installation and supplied path." }
    return $command.Source
}

function Get-RtmpListeners {
    @(Get-NetTCPConnection -State Listen -LocalPort 1935 -ErrorAction SilentlyContinue)
}

$serverDir = Join-Path $PSScriptRoot "server"
$serverExe = (Get-Item -LiteralPath (Join-Path $serverDir "mediamtx.exe")).FullName
$null = Get-Item -LiteralPath (Join-Path $serverDir "mediamtx.yml")
$ObsPath = Resolve-Application $ObsPath
$monitor = Join-Path $PSScriptRoot "Monitor-Feeds.py"
if (-not $Setup) {
    $null = Get-Item -LiteralPath $monitor
    if (-not $PythonPath) {
        $venv = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
        if (Test-Path -LiteralPath $venv) { $PythonPath = $venv } else { $PythonPath = "python" }
    }
    $PythonPath = Resolve-Application $PythonPath
    $FfmpegPath = Resolve-Application $FfmpegPath
    & $PythonPath -c "import obsws_python"
    if ($LASTEXITCODE -ne 0) { throw "Install requirements.txt into the selected Python environment." }
}

$listeners = @(Get-RtmpListeners)
if ($listeners.Count -eq 0) {
    $server = Start-Process -FilePath $serverExe -ArgumentList "mediamtx.yml" -WorkingDirectory $serverDir -PassThru
    $deadline = (Get-Date).AddSeconds(12)
    do {
        Start-Sleep -Milliseconds 300
        if ($server.HasExited) { throw "MediaMTX exited. From server\ run .\mediamtx.exe mediamtx.yml to see its error." }
        $listeners = @(Get-RtmpListeners)
    } while ($listeners.Count -eq 0 -and (Get-Date) -lt $deadline)
}
if ($listeners.Count -eq 0) { throw "No TCP listener appeared on port 1935." }
foreach ($listener in $listeners) {
    $owner = Get-Process -Id $listener.OwningProcess
    if ($owner.Path -ne $serverExe) { throw "TCP 1935 is owned by another program (PID $($owner.Id))." }
}
Write-Host "MediaMTX is listening on TCP 1935."

$runningObs = Get-Process -Name "obs64" -ErrorAction SilentlyContinue
if (-not $runningObs) {
    $startArgs = @{ FilePath = $ObsPath; WorkingDirectory = (Split-Path -Parent $ObsPath) }
    if (-not $Setup) {
        $startArgs.ArgumentList = '--profile "Field Stream" --collection "Field Stream" --scene "Combined Multiview"'
    }
    Start-Process @startArgs
} elseif (-not $Setup) {
    Write-Host "Reusing open OBS. The monitor waits for the Field Stream profile and collection."
}
if ($Setup) {
    Write-Host "In OBS create/select Field Stream under Profile and Scene Collection."
    Write-Host "Enable WebSocket authentication. Follow the guide to provision scenes."
    return
}

$guard = New-Object System.Threading.Mutex($false, "Local\FieldStreamMonitor")
$held = $false
try {
    try { $held = $guard.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $held = $true }
    if (-not $held) { throw "Another Field Stream launcher is already monitoring." }
    $monitorArgs = @($monitor, "--ffmpeg", $FfmpegPath, "--stale", "$StaleSeconds",
                     "--pixel-freeze", "$PixelFreezeSeconds", "--cameras") + $Cameras
    & $PythonPath @monitorArgs
    if ($LASTEXITCODE -ne 0) { throw "Monitor exited with code $LASTEXITCODE." }
} finally {
    if ($held) { $guard.ReleaseMutex() }
    $guard.Dispose()
}
