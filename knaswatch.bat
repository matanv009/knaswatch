@echo off
REM KnasWatch launcher. Runs the tool with the project's own virtual environment,
REM so it works from any shell and any directory without activating anything.
REM
REM With arguments it behaves exactly like "python -m knaswatch ..." and never
REM prompts, so scheduled and scripted runs are unaffected. With no arguments
REM (a double-click in Explorer) it shows a menu and keeps the window open.
setlocal
set "HERE=%~dp0"
set "VENV_PY=%HERE%.venv\Scripts\python.exe"

if not exist "%VENV_PY%" goto :no_venv
if "%~1"=="" goto :menu

"%VENV_PY%" -m knaswatch %*
exit /b %ERRORLEVEL%


:menu
cls
echo.
echo   ==========================================
echo      KnasWatch - Israeli traffic fine check
echo   ==========================================
echo.
echo     1.  First-time setup (ID + licence, Telegram, daily check)
echo     2.  Connect Telegram notifications
echo     3.  Check now, all profiles
echo     4.  Status (configuration and last results)
echo     5.  Add another person
echo     6.  Rename a person
echo     7.  Who gets the alerts
echo     8.  Send alerts to another person's Telegram
echo     9.  Daily "all clear" message on/off
echo    10.  Turn on the daily automatic check
echo    11.  Turn off the daily automatic check
echo     0.  Exit
echo.
set "CHOICE="
set /p "CHOICE=  Choose 0-11 and press Enter: "

REM Empty means the user just pressed Enter, or that there is no interactive
REM input at all (piped/redirected). Exit rather than loop forever.
if not defined CHOICE exit /b 0

if "%CHOICE%"=="1" set "ARGS=setup"            & goto :run
if "%CHOICE%"=="2" set "ARGS=telegram"         & goto :run
if "%CHOICE%"=="3" set "ARGS=check --all"      & goto :run
if "%CHOICE%"=="4" set "ARGS=status"           & goto :run
if "%CHOICE%"=="5" set "ARGS=add-profile"      & goto :run
if "%CHOICE%"=="6" set "ARGS=rename-profile"   & goto :run
if "%CHOICE%"=="7" set "ARGS=recipients"       & goto :run
if "%CHOICE%"=="8" set "ARGS=add-recipient"    & goto :run
if "%CHOICE%"=="9" set "ARGS=config --toggle-all-clear" & goto :run
if "%CHOICE%"=="10" set "ARGS=schedule"        & goto :run
if "%CHOICE%"=="11" set "ARGS=unschedule"      & goto :run
if "%CHOICE%"=="0" exit /b 0

echo.
echo   Please type a number from 0 to 11.
echo.
pause
goto :menu


:run
echo.
"%VENV_PY%" -m knaswatch %ARGS%
echo.
echo   ------------------------------------------
pause
goto :menu


:no_venv
echo.
echo   KnasWatch is not installed yet - the .venv folder is missing.
echo   Open a terminal in this folder and run these three commands:
echo.
echo       python -m venv .venv
echo       .venv\Scripts\python.exe -m pip install -r requirements.txt
echo       .venv\Scripts\python.exe -m playwright install chromium
echo.
if "%~1"=="" pause
exit /b 1
