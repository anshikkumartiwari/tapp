@echo off
cd /d "%~dp0"

REM Fix any corrupted or broken quotes in PATH
set "PATH=%PATH:"=%"

if not defined DOTNET_ROOT (
    if exist "%LOCALAPPDATA%\Microsoft\dotnet" (
        set "DOTNET_ROOT=%LOCALAPPDATA%\Microsoft\dotnet"
    )
)
if defined DOTNET_ROOT set "PATH=%DOTNET_ROOT%;%PATH%"

REM If standalone executable exists, launch it directly
if exist tapp.exe (
    start "" tapp.exe
    exit /b 0
)

REM Otherwise, attempt to run via .NET SDK if available
where dotnet >nul 2>&1
if %ERRORLEVEL% equ 0 (
    dotnet run --project TappActionApp\TappActionApp.csproj -c Release
    exit /b 0
)

echo [ERROR] tapp.exe not found and .NET SDK not detected.
echo Please run setup.bat or run.bat first to calibrate and build tapp.exe.
pause
exit /b 1
