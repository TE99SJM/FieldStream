@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 goto :failed
)
".venv\Scripts\python.exe" -m pip install --only-binary=:all: -r requirements.txt
if errorlevel 1 goto :failed
echo.
echo Installed. Connected Windows capture devices:
".venv\Scripts\python.exe" main.py devices
if errorlevel 1 goto :failed
echo.
echo Next, match your device names in server_config.yml. See README.md.
pause
exit /b 0
:failed
echo Setup could not finish. Check the error above and README.md.
pause
exit /b 1
