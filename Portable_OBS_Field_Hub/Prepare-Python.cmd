@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)
if errorlevel 1 goto fail
:install
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 (
    echo This kit requires Python 3.11 or newer. Install 64-bit Python and recreate the kit environment.
    goto fail
)
".venv\Scripts\python.exe" -m pip install --no-index --find-links dependencies -r requirements.txt
if errorlevel 1 goto fail
echo Field Hub Python is ready.
pause
exit /b 0
:fail
echo Setup stopped. Check the preceding message and run Prepare-Python again.
pause
exit /b 1
