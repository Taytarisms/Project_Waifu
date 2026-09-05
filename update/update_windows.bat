@echo off
setlocal EnableExtensions EnableDelayedExpansion

for %%I in ("%~dp0..") do set "FILES_ROOT=%%~fI"
for %%I in ("%FILES_ROOT%\..") do set "APP_ROOT=%%~fI"
set "PYTHON_EXE=%FILES_ROOT%\python_embeded\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
set "UPDATER=%FILES_ROOT%\system_setup\update_app.py"
set "PENDING_UPDATER=%FILES_ROOT%\update\update_app.new.py"
set "CHANNEL=%~1"
set "NO_PAUSE="
if /I "%~2"=="nopause" set "NO_PAUSE=1"
if not defined CHANNEL set "CHANNEL=latest"

if /I "%CHANNEL%"=="rollback" goto rollback
if /I "%CHANNEL%"=="stable" (
    set "SOURCE_ARGS=--source release --branch main"
    echo Project Waifu stable updater
    echo This channel follows the newest published release, which may be older than main.
) else if /I "%CHANNEL%"=="latest" (
    set "SOURCE_ARGS=--source branch --branch main"
    echo Project Waifu latest updater
    echo This channel follows the current main branch.
) else (
    echo Unknown update channel: %CHANNEL%
    goto failed
)

if not exist "%UPDATER%" (
    echo Updater not found: %UPDATER%
    goto failed
)

echo.
echo Checking for updates...
"%PYTHON_EXE%" -s "%UPDATER%" !SOURCE_ARGS! check
if errorlevel 1 goto failed

echo.
choice /M "Apply the update now"
if errorlevel 2 goto cancelled

echo.
choice /M "Install Python dependency updates if requirements.txt changed"
set "DEPS_FLAG="
if not errorlevel 2 set "DEPS_FLAG=--install-deps"

:run_update
echo.
"%PYTHON_EXE%" -s "%UPDATER%" !SOURCE_ARGS! update --yes !DEPS_FLAG!
set "UPDATE_EXIT=!ERRORLEVEL!"
if "!UPDATE_EXIT!"=="10" goto install_updater
if not "!UPDATE_EXIT!"=="0" goto failed
goto completed

:install_updater
echo.
echo Installing the updated updater and resuming...
if exist "%PENDING_UPDATER%" (
    move /Y "%PENDING_UPDATER%" "%UPDATER%" >nul
    if errorlevel 1 goto failed
)
"%PYTHON_EXE%" -s "%UPDATER%" --skip-self-update !SOURCE_ARGS! update --yes !DEPS_FLAG!
if errorlevel 1 goto failed
goto completed

:rollback
echo Project Waifu update rollback
if not exist "%UPDATER%" (
    echo Updater not found: %UPDATER%
    goto failed
)
echo.
"%PYTHON_EXE%" -s "%UPDATER%" rollback
if errorlevel 1 goto failed
goto completed

:cancelled
echo.
echo Update cancelled.
goto finish_ok

:completed
echo.
echo Operation completed successfully.
goto finish_ok

:failed
echo.
echo Operation failed. No incomplete application update should remain; if needed,
echo run rollback_update.bat to restore the newest updater backup.
call :maybe_pause
exit /b 1

:finish_ok
call :maybe_pause
exit /b 0

:maybe_pause
if not defined NO_PAUSE pause
exit /b 0
