@echo off
cd /d "%~dp0"
echo ========================================================
echo   Launching Tapp Tap Actions (Native C# / .NET 8 App)...
echo ========================================================
echo.

if not defined DOTNET_ROOT (
    if exist "%LOCALAPPDATA%\Microsoft\dotnet" (
        set "DOTNET_ROOT=%LOCALAPPDATA%\Microsoft\dotnet"
    )
)
if defined DOTNET_ROOT set "PATH=%DOTNET_ROOT%;%PATH%"

dotnet run --project TappActionApp\TappActionApp.csproj -c Release
