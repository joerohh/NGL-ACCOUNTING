@echo off
cd /d "%~dp0"

echo ================================================
echo   Push Updates to All Devices
echo ================================================
echo.

:: Stage all changes (respects .gitignore)
git add -A

:: Check if there's anything to commit
git diff --cached --quiet
if %errorlevel% == 0 (
    echo No new changes to push. Everything is already up to date.
    echo.
    pause
    exit /b 0
)

:: Show what's being committed so the user can sanity-check
echo Files to be committed:
echo ------------------------------------------------
git diff --cached --name-status
echo ------------------------------------------------
echo.

:: Get timestamp via PowerShell (wmic is deprecated on Windows 11)
for /f "delims=" %%I in ('powershell -NoProfile -Command "Get-Date -Format \"yyyy-MM-dd HH:mm\""') do set TIMESTAMP=%%I

git commit -m "update: saved on %TIMESTAMP%"

echo.
echo Pushing to GitHub...
git push

echo.
if %errorlevel% == 0 (
    echo  Done! Other devices can now run "Pull Latest Updates.bat" to sync.
) else (
    echo  Push failed. Check your internet connection and try again.
)

echo.
pause
