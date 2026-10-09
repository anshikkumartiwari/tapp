@echo off
cd /d "%~dp0"

REM Detect Python executable (checks local appdata, program files, py launcher, and PATH)
set "PYTHON_EXE="

if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
    goto :python_found
)

for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%D\python.exe" (
        set "PYTHON_EXE=%%D\python.exe"
        set "PATH=%%D;%%D\Scripts;%PATH%"
        goto :python_found
    )
)

for /d %%D in ("%ProgramFiles%\Python3*") do (
    if exist "%%D\python.exe" (
        set "PYTHON_EXE=%%D\python.exe"
        set "PATH=%%D;%%D\Scripts;%PATH%"
        goto :python_found
    )
)

where py >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "PYTHON_EXE=py"
    goto :python_found
)

where python >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "PYTHON_EXE=python"
    goto :python_found
)

:python_found
echo ========================================================
echo   Launching Tapp Calibration and Test Wizard...
echo ========================================================
echo.

if "%PYTHON_EXE%"=="" (
    echo [ERROR] Python not found in system PATH or standard folders.
    echo Please make sure Python 3.9+ is installed from https://www.python.org/
    pause
    exit /b 1
)

"%PYTHON_EXE%" main.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo Application exited with error code %ERRORLEVEL%.
    pause
)
