@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0automation\Start-FieldHub.ps1"
set "fieldhub_result=%errorlevel%"
echo.
pause
exit /b %fieldhub_result%
