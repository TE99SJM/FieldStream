@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  echo Drag the short OBS grid recording onto Measure-Sync.cmd after using calibration\Sync-Target.html.
  pause
  exit /b 2
)
if not exist ".venv\Scripts\python.exe" (
  echo Run Prepare-Python.cmd first.
  pause
  exit /b 2
)
".venv\Scripts\python.exe" calibration\measure.py %*
set "result=%errorlevel%"
pause
exit /b %result%
