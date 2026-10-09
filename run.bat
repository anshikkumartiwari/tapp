@echo off
cd /d "%~dp0"
echo ========================================================
echo   Launching Tapp Calibration and Test Wizard...
echo ========================================================
echo.

where python >nul 2>&1
if %ERRORLEVEL% equ 0 (
    python main.py
    goto end
)

where py >nul 2>&1
if %ERRORLEVEL% equ 0 (
    py main.py
    goto end
)

echo [ERROR] Python not found in PATH!
echo Please make sure Python is installed and added to PATH.
pause
exit /b 1

:end
if %ERRORLEVEL% neq 0 (
    echo.
    echo Application exited with error code %ERRORLEVEL%.
    pause
)
