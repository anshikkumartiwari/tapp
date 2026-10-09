@echo off
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo ========================================================
echo   TAPP — Palm-Rest Hit Detection Test App Setup
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
echo [*] Installing minimal dependencies (numpy, sounddevice, scipy)...
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

echo.
echo ========================================================
echo   Setup completed successfully!
echo   Launch the application anytime by running: run.bat
echo ========================================================
echo.
pause
