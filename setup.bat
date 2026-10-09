@echo off
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo ========================================================
echo   TAPP - Palm-Rest Tap Detection Setup
echo ========================================================
echo.

REM 1. Check Python
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not installed or not in your PATH.
    echo Please install Python 3.9 or newer from https://www.python.org/
    pause
    exit /b 1
)

echo [*] Python detected:
python --version

REM 2. Install minimal dependencies
echo.
echo [*] Installing dependencies (numpy, sounddevice, scipy)...
python -m pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to install requirements.
    pause
    exit /b 1
)

REM 3. Run self-tests
echo.
echo [*] Running automated tests...
python test_core.py
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Unit tests failed.
    pause
    exit /b 1
)

REM 4. Build initial standalone tapp.exe executable
echo.
echo [*] Building native tapp.exe executable...
python -c "from tapp_app.builder import build_standalone_executable; ok, msg = build_standalone_executable(); print(msg)"

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
