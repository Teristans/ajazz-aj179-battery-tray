@echo off
rem Launch the AJAZZ AJ179 battery tray without a console window.
rem Works with a local .venv, the py launcher, or pythonw on PATH.
setlocal
set "HERE=%~dp0"

if exist "%HERE%.venv\Scripts\pythonw.exe" goto use_venv
where pyw >nul 2>&1 && goto use_py
where pythonw >nul 2>&1 && goto use_pythonw
goto no_python

:use_venv
start "" "%HERE%.venv\Scripts\pythonw.exe" "%HERE%aj179_tray.py"
exit /b 0

:use_py
start "" pyw -3 "%HERE%aj179_tray.py"
exit /b 0

:use_pythonw
start "" pythonw "%HERE%aj179_tray.py"
exit /b 0

:no_python
echo Python was not found.
echo Install Python 3.9+ from https://www.python.org/downloads/windows/
echo then double-click setup.bat once, and start.bat afterwards.
pause
exit /b 1
