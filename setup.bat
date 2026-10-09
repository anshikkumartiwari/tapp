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
echo   TAPP - Palm-Rest Tap Detection Setup
echo ========================================================
echo.

if "%PYTHON_EXE%"=="" (
    echo [ERROR] Python is not installed or could not be found.
    echo Please install Python 3.9 or newer from https://www.python.org/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo [*] Python detected: "%PYTHON_EXE%"
"%PYTHON_EXE%" --version
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to execute Python.
    pause
    exit /b 1
)

REM 2. Install dependencies
echo.
echo [*] Installing dependencies (numpy, sounddevice, scipy)...
"%PYTHON_EXE%" -m pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to install requirements.
    pause
    exit /b 1
)

REM 3. Run self-tests
echo.
echo [*] Running automated tests...
"%PYTHON_EXE%" test_core.py
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Unit tests failed.
    pause
    exit /b 1
)

REM 4. Build initial standalone tapp.exe executable
echo.
echo [*] Building native tapp.exe executable...
"%PYTHON_EXE%" -c "from tapp_app.builder import build_standalone_executable; ok, msg = build_standalone_executable(); print(msg)"

echo.
echo ========================================================
echo   Setup completed successfully!
echo.
echo   Next steps:
echo   1. Run run.bat to calibrate sensitivity and test accuracy.
echo   2. Run tapp.bat (or double-click tapp.exe) for daily use.
echo ========================================================
echo.
pause
