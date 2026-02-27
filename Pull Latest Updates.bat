@echo off
cd /d "%~dp0"

echo ================================================
echo   Pull Latest Updates from GitHub
echo ================================================
echo.

echo Checking for updates...
git pull

echo.
if %errorlevel% == 0 (
    echo  Done! This device is now up to date.
) else (
    echo  Pull failed. Check your internet connection and try again.
)

echo.
pause
