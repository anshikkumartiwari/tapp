@echo off
cd /d "%~dp0"
echo ========================================================
echo   Launching Tapp Tap Actions (Lenovo IdeaPad Utility)...
echo ========================================================
echo.

where python >nul 2>&1
if %ERRORLEVEL% equ 0 (
    python action_app.py
    goto end
)

where py >nul 2>&1
if %ERRORLEVEL% equ 0 (
    py action_app.py
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
