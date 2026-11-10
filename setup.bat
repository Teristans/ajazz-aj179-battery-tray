@echo off
rem One-time setup: create .venv and install dependencies.
setlocal
cd /d "%~dp0"

where py >nul 2>&1 && (py -3 -m venv .venv) || (python -m venv .venv)
if not exist ".venv\Scripts\python.exe" goto failed

call ".venv\Scripts\python.exe" -m pip install --upgrade pip
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed

echo.
echo Done. Double-click start.bat to run.
pause
exit /b 0

:failed
echo.
echo Setup failed. Make sure Python 3.9+ is installed and on PATH.
pause
exit /b 1
