@echo off
REM One-click install for KnasWatch: creates the virtual environment, installs
REM the four dependencies, and downloads the browser Playwright needs.
REM Safe to run again - it repairs a half-finished install.
setlocal
cd /d "%~dp0"

echo.
echo   ==========================================
echo      KnasWatch - installation
echo   ==========================================
echo.

set "PY="
where py >nul 2>&1
if %ERRORLEVEL%==0 set "PY=py -3"
if not defined PY (
    where python >nul 2>&1
    if %ERRORLEVEL%==0 set "PY=python"
)

if not defined PY (
    echo   Python was not found on this computer.
    echo.
    echo   Install Python 3.10 or newer from https://www.python.org/downloads/
    echo   During installation, tick "Add python.exe to PATH".
    echo   Then run this file again.
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo   [1/3] Creating the virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 goto :failed
) else (
    echo   [1/3] Virtual environment already exists.
)

echo   [2/3] Installing dependencies...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 goto :failed

echo   [3/3] Downloading the browser (this can take a few minutes)...
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto :failed

echo.
echo   Checking the install...
".venv\Scripts\python.exe" -c "import playwright, keyring, platformdirs, httpx" 2>nul
if errorlevel 1 goto :failed

echo.
echo   Done. Now run knaswatch.bat and choose option 1 to set up.
echo.
pause
exit /b 0

:failed
echo.
echo   Installation failed. The messages above say why.
echo   The usual causes are no internet connection, or a Python older than 3.10.
echo.
pause
exit /b 1
