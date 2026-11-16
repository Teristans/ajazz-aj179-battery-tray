@echo off
rem Shrink an existing .venv. Safe to re-run.
rem Removes pip and Pillow codecs this project never imports.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto no_venv

call ".venv\Scripts\python.exe" tools\slim_venv.py %*
echo.
pause
exit /b 0

:no_venv
echo No .venv found. Run setup.bat first.
pause
exit /b 1
